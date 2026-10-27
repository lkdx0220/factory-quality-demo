"""Durable G2/G3 fact sets and reference validation. No answer publication."""

import copy
import json

from .errors import ProcessingError
from .evidence_execution import timestamp
from .fact_contracts import Binding, ClaimProposal, Fact, FactSet, ValidationReceipt
from .fact_registry import binding, build_registry, reference, verify_evidence
from .ingestion import canonical
from .plan_contracts import hash_object
from .plan_service import new_id


class FactService:
    def __init__(self, plans, executions):
        self.plans, self.executions = plans, executions
        with plans.lock, plans.transaction() as con:
            con.execute("CREATE TABLE IF NOT EXISTS fact_sets (id TEXT PRIMARY KEY, owner TEXT NOT NULL, "
                        "execution_id TEXT NOT NULL UNIQUE, document TEXT NOT NULL, digest TEXT NOT NULL)")
            con.execute("CREATE TABLE IF NOT EXISTS fact_validations (id TEXT PRIMARY KEY, owner TEXT NOT NULL, "
                        "fact_set_id TEXT NOT NULL, document TEXT NOT NULL, digest TEXT NOT NULL)")

    @staticmethod
    def _decode(row, model):
        try:
            doc = json.loads(row["document"])
            if hash_object(doc) != row["digest"]:
                raise ValueError("Digest mismatch")
            model.model_validate(doc)
        except (ValueError, TypeError) as exc:
            raise ProcessingError("PLAN_FACT_INTEGRITY", "Stored fact record failed integrity validation") from exc
        return doc

    def _registry(self, con, owner, execution_id):
        self.plans._permission(owner)
        row = con.execute("SELECT * FROM executions WHERE id=? AND owner=?", (execution_id, owner)).fetchone()
        if row is None:
            raise ProcessingError("PLAN_NOT_FOUND", "Execution not found in this session")
        execution = self.executions._decode(row)
        if execution["execution_id"] != execution_id or execution["confirmed_plan_id"] != row["plan_id"]:
            raise ProcessingError("PLAN_FACT_INTEGRITY", "Execution row identity does not match its record")
        plan = self.plans._plan(con, owner, execution["confirmed_plan_id"])
        if execution_id in self.executions.volatile or execution["state"] != "completed":
            raise ProcessingError("PLAN_FACT_EXECUTION_NOT_READY", "A durably completed execution is required")
        evidence = verify_evidence(self.executions, plan, execution)
        return build_registry(plan, execution, evidence)

    def _stored(self, con, owner, identity):
        self.plans._permission(owner)
        row = con.execute("SELECT * FROM fact_sets WHERE id=? AND owner=?", (identity, owner)).fetchone()
        if row is None:
            raise ProcessingError("PLAN_NOT_FOUND", "Fact set not found in this session")
        doc = self._decode(row, FactSet)
        if doc["fact_set_id"] != identity or doc["execution_id"] != row["execution_id"]:
            raise ProcessingError("PLAN_FACT_INTEGRITY", "Fact row identity does not match its record")
        current = self._registry(con, owner, doc["execution_id"])
        if current != doc:
            raise ProcessingError("PLAN_FACT_EVIDENCE_CHANGED", "Stored facts no longer match the exact execution evidence")
        return doc

    def create(self, owner, execution_id):
        with self.plans.lock, self.plans.transaction() as con:
            doc = self._registry(con, owner, execution_id)
            row = con.execute("SELECT * FROM fact_sets WHERE execution_id=? AND owner=?", (execution_id, owner)).fetchone()
            if row:
                if row["id"] != doc["fact_set_id"] or self._decode(row, FactSet) != doc:
                    raise ProcessingError("PLAN_FACT_EVIDENCE_CHANGED", "Existing facts cannot be silently replaced")
            else:
                con.execute("INSERT INTO fact_sets VALUES (?,?,?,?,?)",
                            (doc["fact_set_id"], owner, execution_id, canonical(doc), hash_object(doc)))
            return self._view(con, owner, doc, reused=row is not None)

    def _latest(self, con, owner, doc):
        row = con.execute("SELECT * FROM fact_validations WHERE owner=? AND fact_set_id=? ORDER BY rowid DESC LIMIT 1",
                          (owner, doc["fact_set_id"])).fetchone()
        if row is None:
            return None
        receipt = self._decode(row, ValidationReceipt)
        required = doc["required_fact_ids"]
        if (binding(receipt) != binding(doc) or receipt["fact_set_id"] != doc["fact_set_id"]
                or receipt["validation_id"] != row["id"] or receipt["fact_set_digest"] != doc["fact_set_digest"]
                or receipt["evidence_set_digest"] != doc["evidence_set_digest"]
                or receipt["registry_version"] != doc["registry_version"]
                or receipt["required_count"] != len(required) or receipt["g2_passed"] is not True
                or receipt["accepted"] != receipt["g3_passed"]
                or (receipt["accepted"] and (receipt["issues"] or receipt["missing_required_fact_ids"]
                                            or receipt["validated_fact_ids"] != required
                                            or receipt["submitted_count"] != len(required)))):
            raise ProcessingError("PLAN_FACT_INTEGRITY", "Stored validation receipt is inconsistent")
        return receipt

    def _view(self, con, owner, doc, *, reused=False):
        view = {key: copy.deepcopy(value) for key, value in doc.items() if key != "facts"}
        base = "/api/v1/query-plans/fact-sets/" + doc["fact_set_id"]
        view.update(summary_facts=[copy.deepcopy(f) for f in doc["facts"] if f["claim_type"] != "operation_field"],
                    fact_count=len(doc["facts"]), required_count=len(doc["required_fact_ids"]),
                    members_url=base + "/members?fact_set_digest=" + doc["fact_set_digest"],
                    export_url=base + "/export", latest_validation=self._latest(con, owner, doc), reused=reused)
        return view

    def read(self, owner, identity):
        with self.plans.lock, self.plans.transaction() as con:
            return self._view(con, owner, self._stored(con, owner, identity))

    def members(self, owner, identity, digest, offset, limit):
        with self.plans.lock, self.plans.transaction() as con:
            doc = self._stored(con, owner, identity)
            if digest != doc["fact_set_digest"]:
                raise ProcessingError("PLAN_FACT_SET_VERSION", "Member link belongs to a different fact set")
            return {**binding(doc), "fact_set_id": identity, "fact_set_digest": digest,
                    "items": copy.deepcopy(doc["facts"][offset:offset + limit]), "total": len(doc["facts"]),
                    "returned_rows": len(doc["facts"][offset:offset + limit]), "offset": offset, "limit": limit,
                    "next_offset": offset + limit if offset + limit < len(doc["facts"]) else None,
                    "answer_published": False}

    def proposal(self, owner, identity):
        with self.plans.lock, self.plans.transaction() as con:
            doc = self._stored(con, owner, identity)
            proposal = {**binding(doc), "fact_set_id": identity, "fact_set_digest": doc["fact_set_digest"],
                        "claims": [reference(f) for f in doc["facts"]]}
            ClaimProposal.model_validate(proposal)
            return proposal

    def validate(self, owner, identity, proposal):
        # Also validate server-side callers; HTTP has the same strict model.
        candidate = ClaimProposal.model_validate(proposal).model_dump()
        with self.plans.lock, self.plans.transaction() as con:
            doc = self._stored(con, owner, identity)
            expected = {**binding(doc), "fact_set_id": identity, "fact_set_digest": doc["fact_set_digest"]}
            mismatch = [key for key, value in expected.items() if candidate[key] != value]
            issues, seen, valid = [], set(), set()
            if mismatch:
                issues.append({"code": "BINDING_MISMATCH", "index": None, "fields": mismatch})
            lookup = {fact["fact_id"]: fact for fact in doc["facts"]}
            for index, claim in enumerate(candidate["claims"]):
                identity_ref = claim["fact_id"]
                if identity_ref in seen:
                    issues.append({"code": "DUPLICATE_FACT", "index": index, "fields": []})
                seen.add(identity_ref)
                fact = lookup.get(identity_ref)
                if fact is None:
                    issues.append({"code": "UNKNOWN_FACT", "index": index, "fields": []})
                    continue
                expected_ref = reference(fact)
                fields = [key for key in expected_ref if canonical(claim[key]) != canonical(expected_ref[key])]
                if fields:
                    issues.append({"code": "SEMANTIC_MISMATCH", "index": index, "fields": fields})
                else:
                    valid.add(identity_ref)
            missing = [identity for identity in doc["required_fact_ids"] if identity not in valid]
            if missing:
                issues.append({"code": "MISSING_REQUIRED_FACTS", "index": None, "fields": []})
            receipt = {**binding(doc), "validation_id": new_id("validation-"), "fact_set_id": identity,
                       "fact_set_digest": doc["fact_set_digest"], "evidence_set_digest": doc["evidence_set_digest"],
                       "registry_version": doc["registry_version"], "created_at": timestamp(),
                       "proposal_digest": hash_object(candidate), "g2_passed": True, "g3_passed": not issues,
                       "accepted": not issues, "required_count": len(doc["required_fact_ids"]),
                       "submitted_count": len(candidate["claims"]),
                       "validated_fact_ids": [i for i in doc["required_fact_ids"] if i in valid],
                       "missing_required_fact_ids": missing, "issues": issues, "answer_published": False}
            ValidationReceipt.model_validate(receipt)
            # No raw candidate values or model text enter stored/output receipts.
            self._save_receipt(con, owner, identity, receipt)
            return receipt

    @staticmethod
    def _save_receipt(con, owner, identity, receipt):
        con.execute("INSERT INTO fact_validations VALUES (?,?,?,?,?)",
                    (receipt["validation_id"], owner, identity, canonical(receipt), hash_object(receipt)))

    def export(self, owner, identity):
        with self.plans.lock, self.plans.transaction() as con:
            doc = self._stored(con, owner, identity)
            return {"artifact_kind": "validated_fact_registry", "answer_published": False, "notice": doc["notice"],
                    "fact_set": doc, "view": self._view(con, owner, doc),
                    "fact_schema": Fact.model_json_schema(), "fact_set_schema": FactSet.model_json_schema(),
                    "claim_proposal_schema": ClaimProposal.model_json_schema(),
                    "validation_receipt_schema": ValidationReceipt.model_json_schema()}
