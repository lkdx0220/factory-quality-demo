"""Plan-derived read-only DAG, bounded subprocess calls and durable attempts.

One API process per control database. A new process marks unfinished executions
interrupted; it never replays them. This module does not publish answers.
"""

import copy
import json
import os
import sqlite3
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt

from .errors import ProcessingError
from .ingestion import canonical
from .plan_contracts import ROOT, SPEC, hash_object
from .plan_service import inventory, new_id

TERMINAL = {"completed", "failed", "cancelled", "interrupted"}
NOTICE = "证据执行记录；尚未完成事实发布门禁，没有发布分析答案或产品合格结论。"


class SourceResult(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    result_schema: str = Field(alias="schema")
    source: str
    items: list[dict]
    total_rows: StrictInt
    returned_rows: StrictInt
    complete: StrictBool
    snapshot_digest: str
    manifest_digest: str


class AttemptRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    attempt: StrictInt
    attempt_id: str
    started_at: str
    finished_at: str | None
    state: Literal["running", "completed", "failed"]
    error_code: str | None
    cleanup_confirmed: StrictBool | None


class CallRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    logical_call_id: str
    source: Literal["lots", "operations"]
    depends_on: list[str]
    state: Literal["not_attempted", "running", "completed", "failed"]
    attempts: list[AttemptRecord]
    error_code: str | None


class EvidenceRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    evidence_id: str
    request_id: str
    execution_id: str
    confirmed_plan_id: str
    scope_hash: str
    snapshot_digest: str
    manifest_digest: str
    contract_bundle_hash: str
    member_set_version: str
    logical_call_id: str
    attempt_id: str
    source: Literal["lots", "operations"]
    scope: dict
    executed_at: str
    completed_at: str
    result: SourceResult
    evidence_digest: str


class ExecutionRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    schema_version: Literal["evidence-execution-1"]
    execution_id: str
    request_id: str
    confirmed_plan_id: str
    scope_hash: str
    scope: dict
    snapshot_digest: str
    manifest_digest: str
    contract_bundle_hash: str
    member_set_version: str
    state: Literal["queued", "running", "completed", "failed", "cancelled", "interrupted"]
    created_at: str
    started_at: str | None
    finished_at: str | None
    execution_complete: StrictBool
    answer_published: Literal[False]
    notice: str
    policy: dict
    error_code: str | None
    evidence: list[EvidenceRecord]
    completed_scope: list[str]
    failed_scope: list[dict[str, str]]
    not_attempted_scope: list[str]
    calls: list[CallRecord]


class CallFailure(Exception):
    def __init__(self, code, cleanup=True):
        super().__init__(code)
        self.code, self.cleanup = code, cleanup


def timestamp():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def subprocess_call(command, payload, cancel, deadline, policy):
    """Trusted worker has no children. Kill and reap before returning a cutoff.

    stdin/stdout carry data, never command text. No shell, source stderr or
    arbitrary worker strings are returned to the browser.
    """
    if cancel.is_set():
        raise CallFailure("CANCELLED")
    if time.monotonic() >= deadline:
        raise CallFailure("TIMEOUT")
    if command != [sys.executable, "-m", "mdlab_app.evidence_worker"]:
        raise CallFailure("INVALID_WORKER_COMMAND")
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "src")
    proc = subprocess.Popen([sys.executable, "-m", "mdlab_app.evidence_worker"],
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                            shell=False, cwd=ROOT, env=env, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    data = canonical(payload).encode("utf-8")
    try:
        while True:
            reason = "CANCELLED" if cancel.is_set() else "TIMEOUT" if time.monotonic() >= deadline else None
            if reason:
                proc.kill()
                proc.communicate()
                raise CallFailure(reason, cleanup=proc.poll() is not None)
            try:
                out, _ = proc.communicate(data, timeout=min(policy["cancellation_poll_seconds"],
                                                          max(0.001, deadline - time.monotonic())))
                break
            except subprocess.TimeoutExpired:
                data = None
        if cancel.is_set():
            raise CallFailure("CANCELLED")
        if time.monotonic() >= deadline:
            raise CallFailure("TIMEOUT")
        if proc.returncode:
            raise CallFailure("SOURCE_BUSY" if proc.returncode == 75 else "SOURCE_ERROR")
        if len(out) > policy["max_response_bytes"]:
            raise CallFailure("RESPONSE_TOO_LARGE")
        try:
            return json.loads(out)
        except (ValueError, UnicodeError) as exc:
            raise CallFailure("INVALID_SOURCE_RESULT") from exc
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.communicate()


class ExecutionService:
    def __init__(self, plans):
        self.plans = plans
        self.active = {}
        self.volatile = {}
        # Server-only test seam. HTTP cannot supply commands, timeouts or adapters.
        self.command = [sys.executable, "-m", "mdlab_app.evidence_worker"]
        self.runner = subprocess_call
        with plans.lock, plans.transaction() as con:
            self._table(con)
            for row in con.execute("SELECT * FROM executions").fetchall():
                doc = self._decode(row)
                if doc["state"] not in TERMINAL:
                    self._finish(doc, "interrupted", "SERVER_RESTARTED", cleanup=None)
                    self._store(con, doc, row["owner"])

    @staticmethod
    def _table(con):
        con.execute("CREATE TABLE IF NOT EXISTS executions (id TEXT PRIMARY KEY, owner TEXT NOT NULL, "
                    "plan_id TEXT NOT NULL UNIQUE, document TEXT NOT NULL, digest TEXT NOT NULL)")

    @staticmethod
    def _decode(row):
        doc = json.loads(row["document"])
        if hash_object(doc) != row["digest"]:
            raise ProcessingError("PLAN_EXECUTION_INTEGRITY", "Execution ledger failed integrity validation")
        try:
            ExecutionRecord.model_validate(doc)
        except ValueError as exc:
            raise ProcessingError("PLAN_EXECUTION_INTEGRITY", "Execution ledger schema is invalid") from exc
        return doc

    def _store(self, con, doc, owner):
        doc["completed_scope"] = [c["source"] for c in doc["calls"] if c["state"] == "completed"]
        doc["failed_scope"] = [{"source": c["source"], "code": c["error_code"]}
                               for c in doc["calls"] if c["state"] == "failed"]
        doc["not_attempted_scope"] = [c["source"] for c in doc["calls"] if c["state"] == "not_attempted"]
        ExecutionRecord.model_validate(doc)
        con.execute("INSERT INTO executions VALUES (?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET "
                    "document=excluded.document,digest=excluded.digest",
                    (doc["execution_id"], owner, doc["confirmed_plan_id"], canonical(doc), hash_object(doc)))

    def _save(self, owner, doc):
        with self.plans.lock, self.plans.transaction() as con:
            self._store(con, doc, owner)

    @staticmethod
    def _finish(doc, state, code=None, cleanup=True):
        doc.update(state=state, finished_at=timestamp(), execution_complete=state == "completed", error_code=code)
        for call in doc["calls"]:
            if call["state"] == "running":
                call["state"] = "failed"
                call["error_code"] = code
                if call["attempts"] and call["attempts"][-1]["finished_at"] is None:
                    call["attempts"][-1].update(state="failed", finished_at=doc["finished_at"],
                                              error_code=code, cleanup_confirmed=cleanup)
        doc["completed_scope"] = [c["source"] for c in doc["calls"] if c["state"] == "completed"]
        doc["failed_scope"] = [{"source": c["source"], "code": c["error_code"]}
                               for c in doc["calls"] if c["state"] == "failed"]
        doc["not_attempted_scope"] = [c["source"] for c in doc["calls"] if c["state"] == "not_attempted"]

    def start(self, owner, plan_id, scope_hash):
        with self.plans.lock, self.plans.transaction() as con:
            plan = self.plans._plan(con, owner, plan_id)
            if plan["scope_hash"] != scope_hash:
                raise ProcessingError("PLAN_SCOPE_MISMATCH", "Execution must use the confirmed scope")
            self._table(con)
            existing = con.execute("SELECT * FROM executions WHERE plan_id=? AND owner=?", (plan_id, owner)).fetchone()
            if existing:
                stored = self._decode(existing)
                view = self._view(self.volatile.get(stored["execution_id"], stored), reused=True)
                view["state_persisted"] = stored["execution_id"] not in self.volatile
                return view
            card = plan["card"]
            doc = {"schema_version": "evidence-execution-1", "execution_id": new_id("execution-"),
                   "request_id": card["request_id"], "confirmed_plan_id": plan_id, "scope_hash": scope_hash,
                   "scope": card["scope"], "snapshot_digest": card["snapshot_digest"],
                   "manifest_digest": card["manifest_digest"], "contract_bundle_hash": card["contract_bundle_hash"],
                   "member_set_version": card["member_set"]["set_version"], "state": "queued",
                   "created_at": timestamp(), "started_at": None, "finished_at": None,
                   "execution_complete": False, "answer_published": False, "notice": NOTICE,
                   "policy": copy.deepcopy(SPEC["execution"]), "error_code": None, "evidence": [],
                   "completed_scope": [], "failed_scope": [], "not_attempted_scope": ["lots", "operations"],
                   "calls": [{"logical_call_id": new_id("call-"), "source": node["key"],
                              "depends_on": node["depends_on"], "state": "not_attempted", "attempts": [],
                              "error_code": None} for node in SPEC["execution"]["dag"]]}
            self._store(con, doc, owner)
        # Start only after the queued record commits successfully.
        with self.plans.lock:
            cancel = threading.Event()
            worker = threading.Thread(target=self._execute, args=(owner, copy.deepcopy(doc), cancel), daemon=True)
            self.active[doc["execution_id"]] = (cancel, worker)
            worker.start()
        return self._view(doc)

    def _g1(self, owner, doc):
        with self.plans.lock, self.plans.transaction() as con:
            plan = self.plans._plan(con, owner, doc["confirmed_plan_id"])
            if (plan["scope_hash"] != doc["scope_hash"] or plan["card"]["scope"] != doc["scope"]
                    or plan["card"]["contract_bundle_hash"] != doc["contract_bundle_hash"]):
                raise CallFailure("PLAN_SCOPE_MISMATCH")
            return plan

    def _validate(self, raw, doc, source):
        try:
            result = SourceResult.model_validate(raw).model_dump(by_alias=True)
        except ValueError as exc:
            raise CallFailure("INVALID_SOURCE_RESULT") from exc
        snapshot = self.plans._snapshot()
        lot, at = doc["scope"]["lot_id"], doc["scope"]["as_of"]
        expected = ([r for r in snapshot.tables["lots"] if r["lot_id"] == lot and r["created_at"] <= at]
                    if source == "lots" else inventory(snapshot, lot, at))
        if (result["schema"] != "source-result-1" or result["source"] != source
                or result["snapshot_digest"] != doc["snapshot_digest"]
                or result["manifest_digest"] != doc["manifest_digest"] or result["items"] != expected
                or result["total_rows"] != len(expected) or result["returned_rows"] != len(expected)
                or result["complete"] is not True):
            raise CallFailure("EVIDENCE_SCOPE_OR_COMPLETENESS_MISMATCH")
        return result

    def _execute(self, owner, doc, cancel):
        deadline = time.monotonic() + doc["policy"]["total_timeout_seconds"]
        try:
            self._g1(owner, doc)
            doc.update(state="running", started_at=timestamp())
            self._save(owner, doc)
            for call in doc["calls"]:
                self._g1(owner, doc)
                for number in range(1, doc["policy"]["max_attempts"] + 1):
                    if cancel.is_set():
                        raise CallFailure("CANCELLED")
                    if time.monotonic() >= deadline:
                        raise CallFailure("TIMEOUT")
                    attempt_deadline = min(deadline, time.monotonic() + doc["policy"]["attempt_timeout_seconds"])
                    attempt = {"attempt": number, "attempt_id": new_id("attempt-"), "started_at": timestamp(),
                               "finished_at": None, "state": "running", "error_code": None, "cleanup_confirmed": None}
                    call["state"] = "running"
                    call["attempts"].append(attempt)
                    self._save(owner, doc)
                    payload = {"source": call["source"], "scope": doc["scope"], "batch": str(self.plans.batch),
                               "schema": str(self.plans.schema) if self.plans.schema else None,
                               "snapshot_digest": doc["snapshot_digest"], "manifest_digest": doc["manifest_digest"]}
                    try:
                        raw = self.runner(self.command, payload, cancel, attempt_deadline, doc["policy"])
                        self._g1(owner, doc)
                        if cancel.is_set():
                            raise CallFailure("CANCELLED")
                        if time.monotonic() >= deadline:
                            raise CallFailure("TIMEOUT")
                        result = self._validate(raw, doc, call["source"])
                        if time.monotonic() >= attempt_deadline:
                            raise CallFailure("TIMEOUT")
                    except CallFailure as exc:
                        attempt.update(state="failed", finished_at=timestamp(), error_code=exc.code,
                                       cleanup_confirmed=exc.cleanup)
                        self._save(owner, doc)
                        if exc.code in doc["policy"]["retry_codes"] and number < doc["policy"]["max_attempts"]:
                            self._g1(owner, doc)
                            continue
                        raise
                    attempt.update(state="completed", finished_at=timestamp(), cleanup_confirmed=True)
                    call["state"] = "completed"
                    binding = {key: doc[key] for key in ("request_id", "execution_id", "confirmed_plan_id", "scope_hash",
                               "snapshot_digest", "manifest_digest", "contract_bundle_hash", "member_set_version")}
                    evidence = {**binding, "evidence_id": new_id("evidence-"), "logical_call_id": call["logical_call_id"],
                                "attempt_id": attempt["attempt_id"], "source": call["source"], "scope": doc["scope"],
                                "executed_at": attempt["started_at"], "completed_at": attempt["finished_at"], "result": result}
                    evidence["evidence_digest"] = hash_object(evidence)
                    doc["evidence"].append(evidence)
                    self._save(owner, doc)
                    break
            # Last G1 check and complete commit share the policy lock/transaction.
            with self.plans.lock, self.plans.transaction() as con:
                plan = self.plans._plan(con, owner, doc["confirmed_plan_id"])
                if plan["scope_hash"] != doc["scope_hash"]:
                    raise CallFailure("PLAN_SCOPE_MISMATCH")
                if cancel.is_set():
                    raise CallFailure("CANCELLED")
                if time.monotonic() >= deadline:
                    raise CallFailure("TIMEOUT")
                self._finish(doc, "completed")
                self._store(con, doc, owner)
        except (CallFailure, ProcessingError) as exc:
            self._finish(doc, "cancelled" if exc.code == "CANCELLED" else "failed", exc.code)
            self._safe_failure(owner, doc)
        except Exception:
            self._finish(doc, "failed", "EXECUTION_STORAGE_OR_INTERNAL_ERROR")
            self._safe_failure(owner, doc)
        finally:
            with self.plans.lock:
                self.active.pop(doc["execution_id"], None)

    def _safe_failure(self, owner, doc):
        try:
            self._save(owner, doc)
        except (sqlite3.Error, OSError):
            # A durable running record remains for restart recovery. Never serve
            # it as completed or pretend the failed terminal write succeeded.
            with self.plans.lock:
                self.volatile[doc["execution_id"]] = copy.deepcopy(doc)

    @staticmethod
    def _view(doc, *, reused=False):
        return {**copy.deepcopy(doc), "state_persisted": True, "reused": reused,
                "export_url": "/api/v1/query-plans/executions/" + doc["execution_id"] + "/export"}

    def read(self, owner, identity, *, cancel=False):
        with self.plans.lock, self.plans.transaction() as con:
            self.plans._permission(owner)
            row = con.execute("SELECT * FROM executions WHERE id=? AND owner=?", (identity, owner)).fetchone()
            if row is None:
                raise ProcessingError("PLAN_NOT_FOUND", "Execution not found in this session")
            doc = self._decode(row)
            if cancel and identity in self.active:
                self.active[identity][0].set()
            if identity in self.volatile:
                doc = copy.deepcopy(self.volatile[identity])
            view = self._view(doc)
            view["state_persisted"] = identity not in self.volatile
            view["cancel_requested"] = identity in self.active and self.active[identity][0].is_set()
            try:
                self.plans._plan(con, owner, doc["confirmed_plan_id"])
                view["access_state"] = "available"
            except ProcessingError as exc:
                view.update(access_state="blocked", access_error_code=exc.code, evidence=[], execution_complete=False)
            return view

    def export(self, owner, identity):
        view = self.read(owner, identity)
        return {"artifact_kind": "evidence_execution_ledger", "answer_published": False, "notice": NOTICE,
                "execution": view, "source_result_schema": SourceResult.model_json_schema(),
                "execution_record_schema": ExecutionRecord.model_json_schema()}
