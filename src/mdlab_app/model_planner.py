"""Session-bound model drafts and server-qualified terminal states. No query tools."""

import copy
import re
import threading
from contextlib import contextmanager
from datetime import datetime, timezone

from pydantic import ValidationError

from .errors import ProcessingError, utc_timestamp
from .ingestion import canonical, digest
from .inventory_language import LOT, UTC, explicit_inventory, semantic_question
from .model_adapter import decode_response, deepseek_request, request_body
from .model_contracts import CAPABILITY_VERSION, ModelDraft, PLANNING_CONTRACT, PROMPT_VERSION, SYSTEM_PROMPT
from .plan_contracts import hash_object
from .plan_service import new_id

UNSUPPORTED = {
    "QUALITY_NOT_SUPPORTED": r"是否合格|是否通过|能否放行|良率|不良率|测试结果|quality|yield|root\s*cause|根因|报警|\b(?:pass(?:ed)?|fail(?:ed|ure)?|release|reject(?:ed|ion)?)\b",
    "SUBSET_NOT_SUPPORTED": r"只[查看要列].{0,12}(?:失败|通过|不良|PASS|FAIL)|(?:前|后|top\s*)\s*\d+\s*(?:条|个|项|operations?)|只看.{0,12}工序|仅.{0,8}(?:TEST_[ABC]|MACHINE-)|排除|隐藏|省略|\b(?:exclud\w*|except|omit|only|without|hide|filter\w*)\b",
    "NEGATED_REQUEST_NOT_SUPPORTED": r"不要|别列|不列|禁止|\b(?:not|never|don['’]t)\b",
    "OTHER_INTENT_NOT_SUPPORTED": r"追溯|SN-[A-Za-z0-9_-]+|对比|比较|compare|trace|删除|修改|写入|更新数据|执行\s*(?:SQL|Python|Shell)|\b(?:DELETE|UPDATE|INSERT)\b",
    "EXCLUSIVE_CUTOFF_NOT_SUPPORTED": r"严格早于|早于|之前|\bbefore\b|不含.{0,6}边界",
}
MESSAGES = {
    "ready": "已生成待核对草稿；尚未确认范围、查询业务数据或发布答案。",
    "needs_clarification": "问题仍有阻塞信息需要补充。请修改问题后重新理解；本次未生成范围计划。",
    "unsupported": "当前自然语言入口仅支持单个 LOT 的全部可见作业清单；原问题未执行，也未自动改查其他问题。",
}
QUESTIONS = {
    "LOT_REQUIRED": "请在问题中写出一个确切的 LOT 编号，例如 LOT-0001。",
    "LOT_AMBIGUOUS": "问题含多个 LOT，请明确只查询哪一个；系统不会替你选择。",
    "UTC_REQUIRED": "请写出确切 UTC 截止时刻，例如 2026-09-07T00:00:00Z；日期、现在或本地时间不能代替它。",
    "UTC_AMBIGUOUS": "问题含多个截止时刻，请明确只使用哪一个。",
    "INTENT_UNRESOLVED": "尚未明确一个受支持的清单意图；如果需要作业清单，请明确是否列出该 LOT 截至指定时刻的全部可见作业。",
    "QUESTION_FORM_NOT_SUPPORTED": "当前仅支持有限中英文完整句式，不能忽略未识别的附加内容。请重新写明原意，例如：截至 2026-09-07T00:00:00Z，列出 LOT-0001 的全部可见作业。",
}


def timestamp():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def literal_scope(question):
    lots, times = list(dict.fromkeys(LOT.findall(question))), list(dict.fromkeys(UTC.findall(question)))
    for value in times:
        utc_timestamp(value)
    reasons = []
    if len(lots) != 1:
        reasons.append("LOT_REQUIRED" if not lots else "LOT_AMBIGUOUS")
    if len(times) != 1:
        reasons.append("UTC_REQUIRED" if not times else "UTC_AMBIGUOUS")
    semantic = semantic_question(question)
    blockers = [code for code, expression in UNSUPPORTED.items() if re.search(expression, semantic, re.IGNORECASE)]
    if not reasons and not blockers and not explicit_inventory(semantic):
        inventory = re.search(r"作业|\boperations?\b|\boperation\s+inventory\b", semantic, re.IGNORECASE)
        complete = re.search(r"全部|所有|完整|全量|\ball\b|\bcomplete\b|\bevery\b", semantic, re.IGNORECASE)
        reasons.append("QUESTION_FORM_NOT_SUPPORTED" if inventory and complete else "INTENT_UNRESOLVED")
    return lots, times, reasons, blockers


class PlannerService:
    def __init__(self, plans, config, transport=deepseek_request):
        self.plans, self.config, self.transport = plans, config, transport
        self.capacity = threading.Lock()

    def configuration(self):
        return {"enabled": self.config.enabled, "provider": "deepseek", "model": self.config.model,
                "prompt_version": PROMPT_VERSION, "answer_published": False,
                "notice": "模型只生成有限草稿；业务计算、确认与答案发布仍由服务端控制。"}

    @contextmanager
    def transaction(self):
        with self.plans.lock, self.plans.transaction() as con:
            con.execute("CREATE TABLE IF NOT EXISTS nl_proposals (id TEXT PRIMARY KEY, owner TEXT NOT NULL, document TEXT NOT NULL, digest TEXT NOT NULL)")
            con.execute("CREATE TABLE IF NOT EXISTS nl_calls (id TEXT PRIMARY KEY, owner TEXT NOT NULL, document TEXT NOT NULL, digest TEXT NOT NULL)")
            con.execute("CREATE TABLE IF NOT EXISTS nl_adoptions_v1 (proposal_id TEXT PRIMARY KEY, request_id TEXT NOT NULL, card_digest TEXT NOT NULL, revision INTEGER NOT NULL)")
            yield con

    def check(self, owner, bundle=None):
        if not self.config.enabled:
            raise ProcessingError("MODEL_DISABLED", "Model planning is disabled; use the manual form")
        self.plans._permission(owner)
        bundle = bundle or self.plans.bundles.default
        artifact = self.plans.bundles.require(bundle)
        if artifact["planning_profile"] != self.config.profile():
            raise ProcessingError("MODEL_CONFIGURATION_CHANGED", "Model profile changed; create a new proposal")
        return bundle

    def _save_call(self, con, owner, receipt, *, insert=False):
        if insert:
            con.execute("INSERT INTO nl_calls VALUES (?,?,?,?)", (receipt["call_id"], owner, canonical(receipt), hash_object(receipt)))
        else:
            con.execute("UPDATE nl_calls SET document=?,digest=? WHERE id=? AND owner=?",
                        (canonical(receipt), hash_object(receipt), receipt["call_id"], owner))

    def _call(self, owner, bundle, question):
        receipt = {"call_id": new_id("model-call-"), "provider": "deepseek", "model": self.config.model,
                   "profile_digest": hash_object(self.config.profile()), "contract_bundle_hash": bundle,
                   "prompt_version": PROMPT_VERSION, "prompt_digest": hash_object(SYSTEM_PROMPT),
                   "schema_digest": hash_object(PLANNING_CONTRACT["output_schema"]),
                   "question_digest": hash_object(question), "started_at": timestamp(), "finished_at": None,
                   "state": "running", "attempts": 1, "error_code": None,
                   "response_digest": None, "usage": None, "returned_model": None, "answer_published": False}
        with self.transaction() as con:
            self.check(owner, bundle)
            self._save_call(con, owner, receipt, insert=True)
        try:
            raw = self.transport(self.config, request_body(self.config, question))
            receipt["response_digest"] = digest(raw) if isinstance(raw, bytes) else None
            draft, usage, actual_model = decode_response(raw)
            receipt.update(usage=usage, returned_model=actual_model)
            if actual_model != self.config.model:
                raise ProcessingError("MODEL_PROTOCOL_ERROR", "Provider returned an unexpected model identity")
            parsed = ModelDraft.model_validate(draft)
            receipt["state"] = "completed"
        except (ProcessingError, ValidationError) as exc:
            error = exc if isinstance(exc, ProcessingError) else ProcessingError("MODEL_PROTOCOL_ERROR", "Model draft failed the strict schema")
            receipt.update(state="failed", error_code=error.code, finished_at=timestamp())
            with self.transaction() as con:
                self._save_call(con, owner, receipt)
            raise ProcessingError(error.code, str(error), {"model_call": receipt}) from exc
        receipt["finished_at"] = timestamp()
        with self.transaction() as con:
            self._save_call(con, owner, receipt)
        return parsed, receipt

    def reject_call(self, owner, receipt, code, message):
        receipt.update(state="failed", error_code=code)
        with self.transaction() as con:
            self._save_call(con, owner, receipt)
        raise ProcessingError(code, message, {"model_call": receipt})

    def propose(self, owner, question):
        if not question.strip():
            raise ProcessingError("INVALID_ARGUMENT", "Question must not be blank")
        with self.transaction():
            bundle = self.check(owner)
        lots, times, reasons, blockers = literal_scope(question)
        parsed, receipt = None, None
        status = "unsupported" if blockers else "needs_clarification" if reasons else "ready"
        if status == "ready":
            if not self.capacity.acquire(blocking=False):
                raise ProcessingError("MODEL_BUSY", "Another model request is in progress; no request was sent")
            try:
                parsed, receipt = self._call(owner, bundle, question)
            finally:
                self.capacity.release()
            if parsed.intent_key == "unresolved":
                # Every question reaching inference passed the same full grammar.
                self.reject_call(owner, receipt, "MODEL_PROTOCOL_ERROR", "Model did not resolve an explicit supported request")
            elif parsed.lot_id != lots[0] or parsed.as_of != times[0]:
                self.reject_call(owner, receipt, "MODEL_SCOPE_MISMATCH", "Model changed an explicit entity or cutoff; no draft was adopted")
        now = self.plans.clock()
        doc = {"proposal_id": new_id("nl-"), "original_input": question,
               "original_input_label": "用户原话（未经事实背书）", "status": status,
               "reason_codes": blockers or reasons, "questions": [QUESTIONS[code] for code in reasons],
               "notice": MESSAGES[status], "normalized_scope": {"intent_key": "lot_operation_inventory", "lot_id": lots[0], "as_of": times[0]} if status == "ready" else None,
               "contract_bundle_hash": bundle, "profile_digest": hash_object(self.config.profile()),
               "capability_version": CAPABILITY_VERSION, "model_call": receipt, "model_called": receipt is not None,
               "created_at_epoch": now, "expires_at_epoch": now + 1800,
               "answer_published": False}
        with self.transaction() as con:
            self.check(owner, bundle)
            con.execute("INSERT INTO nl_proposals VALUES (?,?,?,?)", (doc["proposal_id"], owner, canonical(doc), hash_object(doc)))
        return self.view(doc)

    @staticmethod
    def view(doc):
        return {**copy.deepcopy(doc), "proposal_digest": hash_object(doc)}

    def _read(self, con, owner, identity):
        self.plans._permission(owner)
        row = con.execute("SELECT * FROM nl_proposals WHERE id=? AND owner=?", (identity, owner)).fetchone()
        if row is None:
            raise ProcessingError("PLAN_NOT_FOUND", "Natural-language proposal not found in this session")
        try:
            doc = strict_document(row)
        except (ValueError, TypeError, KeyError) as exc:
            raise ProcessingError("PLAN_INTEGRITY_ERROR", "Stored model proposal failed integrity validation") from exc
        self.check(owner, doc["contract_bundle_hash"])
        if self.plans.clock() >= doc["expires_at_epoch"]:
            raise ProcessingError("PLAN_EXPIRED", "Proposal expired; understand the question again")
        if doc["profile_digest"] != hash_object(self.config.profile()):
            raise ProcessingError("MODEL_CONFIGURATION_CHANGED", "Model profile changed; understand the question again")
        return doc

    def read(self, owner, identity):
        with self.transaction() as con:
            return self.view(self._read(con, owner, identity))

    def preflight(self, owner, identity, proposal_digest):
        with self.transaction() as con:
            doc = self._read(con, owner, identity)
            if hash_object(doc) != proposal_digest:
                raise ProcessingError("STALE_CONFIRMATION_CARD", "Proposal changed; inspect it again")
            if doc["status"] != "ready" or not doc["normalized_scope"]:
                raise ProcessingError("PLAN_NOT_READY", "Clarify or submit a supported question before preflight")
            adoption = con.execute("SELECT * FROM nl_adoptions_v1 WHERE proposal_id=?", (identity,)).fetchone()
            if adoption:
                card = self.plans._draft(con, owner, adoption["request_id"])
                if card["revision"] != adoption["revision"] or card["card_digest"] != adoption["card_digest"]:
                    raise ProcessingError("STALE_CONFIRMATION_CARD", "Derived card was revised; create a new model proposal or use the revised form")
                self.plans._check(owner, card)
                return self.plans._view(card)
            scope = doc["normalized_scope"]
            origin = {"proposal_id": identity, "proposal_digest": proposal_digest,
                      "model_call": doc["model_call"], "profile_digest": doc["profile_digest"]}
            card = self.plans._create(con, owner, doc["original_input"], scope["lot_id"], scope["as_of"], planning_origin=origin)
            # Keep proposal digest stable: adoption is a separate server mapping.
            con.execute("INSERT INTO nl_adoptions_v1 VALUES (?,?,?,?)", (identity, card["request_id"], card["card_digest"], card["revision"]))
            return card


def strict_document(row):
    from .model_adapter import strict_json
    doc = strict_json(row["document"])
    if not isinstance(doc, dict) or hash_object(doc) != row["digest"]:
        raise ValueError()
    return doc
