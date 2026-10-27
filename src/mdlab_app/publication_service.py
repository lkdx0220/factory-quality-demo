"""Immutable final publications; every output rechecks the current gates."""

import copy
import json
from datetime import datetime

from .errors import ProcessingError
from .evidence_execution import timestamp
from .fact_contracts import ValidationReceipt
from .fact_registry import binding
from .ingestion import canonical
from .plan_contracts import hash_object
from .publication_contracts import Publication, PublishInput
from .publication_templates import render


class PublicationService:
    def __init__(self, facts):
        self.facts, self.plans = facts, facts.plans
        with self.plans.lock, self.plans.transaction() as con:
            con.execute("CREATE TABLE IF NOT EXISTS publications (id TEXT PRIMARY KEY, owner TEXT NOT NULL, "
                        "fact_set_id TEXT NOT NULL, validation_id TEXT NOT NULL, document TEXT NOT NULL, digest TEXT NOT NULL, "
                        "UNIQUE(owner,fact_set_id,validation_id))")

    def _context(self, con, owner, fact_set_id, validation_id):
        # _stored rebuilds the facts from current source evidence and rechecks all four preconditions.
        doc = self.facts._stored(con, owner, fact_set_id)
        row = con.execute("SELECT * FROM fact_validations WHERE id=? AND owner=? AND fact_set_id=?",
                          (validation_id, owner, fact_set_id)).fetchone()
        if row is None:
            raise ProcessingError("PLAN_PUBLICATION_VALIDATION_REQUIRED", "An accepted bound validation is required")
        receipt = self.facts._decode(row, ValidationReceipt)
        if (binding(receipt) != binding(doc) or receipt["validation_id"] != validation_id
                or receipt["fact_set_id"] != fact_set_id or receipt["fact_set_digest"] != doc["fact_set_digest"]
                or receipt["evidence_set_digest"] != doc["evidence_set_digest"]
                or receipt["registry_version"] != doc["registry_version"] or receipt["g2_passed"] is not True
                or receipt["g3_passed"] is not True or receipt["accepted"] is not True or receipt["issues"]
                or receipt["missing_required_fact_ids"] or receipt["required_count"] != len(doc["required_fact_ids"])
                or receipt["submitted_count"] != len(doc["required_fact_ids"])
                or receipt["validated_fact_ids"] != doc["required_fact_ids"]):
            raise ProcessingError("PLAN_PUBLICATION_VALIDATION_REQUIRED", "Validation does not authorize this complete fact set")
        plan = self.plans._plan(con, owner, doc["confirmed_plan_id"])
        return doc, receipt, plan

    @staticmethod
    def _decode(row):
        try:
            doc = json.loads(row["document"])
            Publication.model_validate(doc)
            datetime.strptime(doc["published_at"], "%Y-%m-%dT%H:%M:%S.%fZ")
            core = {k: v for k, v in doc.items() if k not in ("publication_id", "publication_digest")}
            if (hash_object(doc) != row["digest"] or hash_object(core) != doc["publication_digest"]
                    or doc["publication_id"] != row["id"] or doc["publication_id"] != "pub-" + doc["publication_digest"]
                    or doc["fact_set_id"] != row["fact_set_id"] or doc["validation_id"] != row["validation_id"]):
                raise ValueError("Publication integrity mismatch")
            return doc
        except (KeyError, ValueError, TypeError) as exc:
            raise ProcessingError("PLAN_PUBLICATION_INTEGRITY", "Stored publication failed integrity validation") from exc

    def _current(self, con, owner, identity):
        self.plans._permission(owner)
        row = con.execute("SELECT * FROM publications WHERE id=? AND owner=?", (identity, owner)).fetchone()
        if row is None:
            raise ProcessingError("PLAN_NOT_FOUND", "Publication not found in this session")
        saved = self._decode(row)
        doc, receipt, plan = self._context(con, owner, saved["fact_set_id"], saved["validation_id"])
        if render(doc, receipt, plan, saved["published_at"]) != saved:
            raise ProcessingError("PLAN_PUBLICATION_OUTPUT_INVALID", "Publication differs from the exact controlled output")
        final_doc, final_receipt, final_plan = self._context(con, owner, saved["fact_set_id"], saved["validation_id"])
        if (final_doc, final_receipt, final_plan) != (doc, receipt, plan):
            raise ProcessingError("PLAN_PUBLICATION_OUTPUT_INVALID", "Publication eligibility changed during access")
        return saved, doc, receipt

    @staticmethod
    def _view(doc, reused=False):
        # Return full summary, only a bounded row preview; the bound detail/export endpoints have every row.
        view = copy.deepcopy(doc)
        rows = view.pop("operations")
        base = "/api/v1/query-plans/publications/" + doc["publication_id"]
        view.update(preview=rows[:3], preview_rows=len(rows[:3]), total_rows=len(rows), reused=reused,
                    details_url=base + "/members?publication_digest=" + doc["publication_digest"],
                    export_url=base + "/export")
        return view

    def publish(self, owner, fact_set_id, body):
        body = PublishInput.model_validate(body).model_dump()
        with self.plans.lock, self.plans.transaction() as con:
            doc, receipt, plan = self._context(con, owner, fact_set_id, body["validation_id"])
            if doc["fact_set_digest"] != body["fact_set_digest"]:
                raise ProcessingError("PLAN_PUBLICATION_VERSION", "Fact set version differs")
            existing = con.execute("SELECT * FROM publications WHERE owner=? AND fact_set_id=? AND validation_id=?",
                                   (owner, fact_set_id, body["validation_id"])).fetchone()
            if existing:
                saved, _, _ = self._current(con, owner, existing["id"])
                return self._view(saved, reused=True)
            result = render(doc, receipt, plan, timestamp())
            # Rendering may take time; recheck eligibility immediately before the durable write.
            final_doc, final_receipt, final_plan = self._context(con, owner, fact_set_id, body["validation_id"])
            if (final_doc, final_receipt, final_plan) != (doc, receipt, plan):
                raise ProcessingError("PLAN_PUBLICATION_OUTPUT_INVALID", "Publication context changed during rendering")
            self._save(con, owner, result)
            # Return only after context-manager commit; a commit failure raises instead of returning a publication.
            return self._view(result)

    @staticmethod
    def _save(con, owner, doc):
        con.execute("INSERT INTO publications VALUES (?,?,?,?,?,?)", (doc["publication_id"], owner, doc["fact_set_id"],
                    doc["validation_id"], canonical(doc), hash_object(doc)))

    def read(self, owner, identity):
        with self.plans.lock, self.plans.transaction() as con:
            return self._view(self._current(con, owner, identity)[0])

    def members(self, owner, identity, digest, offset, limit):
        with self.plans.lock, self.plans.transaction() as con:
            doc, _, _ = self._current(con, owner, identity)
            if digest != doc["publication_digest"]:
                raise ProcessingError("PLAN_PUBLICATION_VERSION", "Detail link belongs to a different publication")
            rows = doc["operations"]
            return {**binding(doc), "publication_id": identity, "publication_digest": digest,
                    "status": doc["status"], "answer_published": True, "source_notice": doc["source_notice"],
                    "order_notice": doc["order_notice"], "items": copy.deepcopy(rows[offset:offset + limit]),
                    "total": len(rows), "returned_rows": len(rows[offset:offset + limit]), "offset": offset,
                    "limit": limit, "next_offset": offset + limit if offset + limit < len(rows) else None}

    def export(self, owner, identity):
        with self.plans.lock, self.plans.transaction() as con:
            doc, facts, receipt = self._current(con, owner, identity)
            return {"artifact_kind": "verified_lot_inventory_export", "answer_published": True,
                    "publication": copy.deepcopy(doc), "view": self._view(doc), "fact_set": facts,
                    "validation_receipt": receipt, "publication_schema": Publication.model_json_schema()}
