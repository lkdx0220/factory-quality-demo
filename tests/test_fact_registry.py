"""G2/G3 positive and adversarial checks against actual execution records."""

import copy
import json
import sqlite3
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from fastapi.testclient import TestClient
from mdlab_app.api import create_app
from mdlab_app.fact_contracts import FactSet
from mdlab_app.ingestion import canonical
from mdlab_app.plan_contracts import hash_object
from mdlab_app.storage import ingest

BATCH, SCHEMA = ROOT / "data/batches/demo-v1", ROOT / "schemas/csv-schema.json"
PREFIX = "/api/v1/query-plans"


class FactRegistryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "demo.sqlite3"
        ingest(BATCH, self.db, SCHEMA)
        self.app = create_app(self.db, BATCH, SCHEMA)
        self.client = TestClient(self.app, raise_server_exceptions=False)
        self.plans = self.app.state.plan_service

    def tearDown(self):
        for event, worker in list(self.app.state.execution_service.active.values()):
            event.set()
            worker.join(10)
            self.assertFalse(worker.is_alive())
        self.client.close()
        self.tmp.cleanup()

    def data(self, response):
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["data"]

    def execution(self, at="2026-09-07T00:00:00Z", lot="LOT-0001"):
        card = self.data(self.client.post(PREFIX + "/requests", json={"intent_key": "lot_operation_inventory",
            "original_input": "完整查询", "lot_id": lot, "as_of": at}))
        plan = self.data(self.client.post(PREFIX + "/requests/" + card["request_id"] + "/confirm",
                         json={"revision": card["revision"], "card_digest": card["card_digest"]}))
        started = self.data(self.client.post(PREFIX + "/confirmed/" + plan["confirmed_plan_id"] + "/execute",
                                            json={"scope_hash": plan["scope_hash"]}))
        for _ in range(300):
            current = self.data(self.client.get(PREFIX + "/executions/" + started["execution_id"]))
            if current["state"] in ("completed", "failed", "cancelled"):
                self.assertEqual(current["state"], "completed", current)
                return current
            time.sleep(0.02)
        self.fail("Execution not complete")

    def facts(self, execution=None):
        execution = execution or self.execution()
        return self.data(self.client.post(PREFIX + "/executions/" + execution["execution_id"] + "/facts", json={}))

    def path(self, facts, suffix=""):
        return PREFIX + "/fact-sets/" + facts["fact_set_id"] + suffix

    def proposal(self, facts):
        return self.data(self.client.get(self.path(facts, "/proposal")))

    def validate(self, facts, proposal):
        return self.data(self.client.post(self.path(facts, "/validate"), json=proposal))

    def rewrite_execution(self, identity, change):
        with self.plans.transaction() as con:
            row = con.execute("SELECT document FROM executions WHERE id=?", (identity,)).fetchone()
            doc = json.loads(row["document"])
            change(doc)
            con.execute("UPDATE executions SET document=?,digest=? WHERE id=?", (canonical(doc), hash_object(doc), identity))

    def test_complete_registry_stable_ids_gates_and_actual_export(self):
        before = self.db.read_bytes()
        execution = self.execution()
        facts = self.facts(execution)
        self.assertTrue(facts["g2_passed"])
        self.assertEqual(facts["fact_count"], 35)
        values = {f["fact_key"]: f["value"] for f in facts["summary_facts"]}
        self.assertEqual(values, {"lot_id": "LOT-0001", "as_of": "2026-09-07T00:00:00Z", "operation_count": 4,
            "member_set_digest": execution["member_set_version"], "membership_complete": True,
            "exclusion_count": 0, "metric_applicability": "NOT_APPLICABLE_OPERATION_INVENTORY"})
        self.assertEqual(facts["completeness"]["business_evidence"], "not_applicable")
        self.assertIsNone(facts["latest_validation"])
        reordered = self.proposal(facts)
        reordered["claims"].reverse()
        receipt = self.validate(facts, reordered)
        self.assertTrue(receipt["accepted"])
        self.assertTrue(receipt["g2_passed"] and receipt["g3_passed"])
        self.assertEqual(receipt["required_count"], receipt["submitted_count"])
        self.assertFalse(receipt["answer_published"])
        response = self.client.get(facts["export_url"])
        self.assertIn("attachment", response.headers["content-disposition"])
        artifact = self.data(response)
        doc = artifact["fact_set"]
        FactSet.model_validate(doc)
        for fact in doc["facts"]:
            self.assertEqual(fact["fact_id"], "fact-" + hash_object({k: v for k, v in fact.items() if k != "fact_id"}))
            self.assertEqual(fact["execution_id"], execution["execution_id"])
            if fact["source_leaf"]:
                self.assertNotIn("[", fact["source_leaf"]["record_id"])
        self.assertEqual(len({f["fact_id"] for f in doc["facts"]}), 35)
        self.assertEqual(artifact["view"]["latest_validation"], receipt)
        again = self.facts(execution)
        self.assertTrue(again["reused"])
        self.assertEqual(again["fact_set_id"], facts["fact_set_id"])
        self.assertEqual(before, self.db.read_bytes())

    def test_zero_operations_has_seven_complete_mandatory_facts(self):
        facts = self.facts(self.execution("2026-09-01T02:00:00Z"))
        self.assertEqual(facts["fact_count"], 7)
        summary = {f["fact_key"]: f for f in facts["summary_facts"]}
        self.assertEqual(summary["operation_count"]["value"], 0)
        self.assertEqual(summary["operation_count"]["input_fact_ids"], [])
        self.assertTrue(summary["membership_complete"]["value"])
        self.assertTrue(summary["operation_count"]["evidence_ids"])
        self.assertTrue(self.validate(facts, self.proposal(facts))["accepted"])

    def test_derived_count_members_and_full_source_chain_are_registered(self):
        facts = self.facts()
        doc = self.data(self.client.get(facts["export_url"]))["fact_set"]
        lookup = {f["fact_id"]: f for f in doc["facts"]}
        count = next(f for f in doc["facts"] if f["fact_key"] == "operation_count")
        self.assertEqual(count["algorithm_id"], "count_unique_operations_v1")
        self.assertEqual({lookup[i]["value"] for i in count["input_fact_ids"]}, {"OP-0001", "OP-0002", "OP-0007", "OP-0012"})
        digest = next(f for f in doc["facts"] if f["fact_key"] == "member_set_digest")
        self.assertEqual(len(digest["input_fact_ids"]), 28)
        self.assertEqual(len(doc["required_groups"]["complete_members"]), 30)

    def test_equal_values_wrong_entity_time_metric_unit_domain_or_rule_are_rejected(self):
        facts = self.facts()
        good = self.proposal(facts)
        index = next(i for i, f in enumerate(good["claims"]) if f["metric_key"] == "operation_count")
        mutations = {"entity_id": "LOT-0002", "operation_id": "OP-0001", "as_of": "2026-09-06T00:00:00Z",
                     "metric_key": "exclusion_count", "metric_version": "other", "rule_version": "other",
                     "unit": "identifier", "denominator_domain": "not_applicable", "claim_type": "exclusion_count"}
        for key, value in mutations.items():
            with self.subTest(key=key):
                candidate = copy.deepcopy(good)
                candidate["claims"][index][key] = value
                result = self.validate(facts, candidate)
                self.assertFalse(result["accepted"])
                self.assertIn("SEMANTIC_MISMATCH", [i["code"] for i in result["issues"]])

    def test_same_equipment_value_cannot_replace_operation_identity(self):
        facts = self.facts()
        candidate = self.proposal(facts)
        a = next(f for f in candidate["claims"] if f["metric_key"] == "operation.equipment_id" and f["operation_id"] == "OP-0007")
        b = next(f for f in candidate["claims"] if f["metric_key"] == "operation.equipment_id" and f["operation_id"] == "OP-0012")
        self.assertEqual(a["value"], b["value"])
        a["fact_id"] = b["fact_id"]
        self.assertFalse(self.validate(facts, candidate)["accepted"])

    def test_every_required_fact_is_mandatory_not_only_summary(self):
        facts = self.facts()
        good = self.proposal(facts)
        for index in range(len(good["claims"])):
            with self.subTest(index=index):
                candidate = copy.deepcopy(good)
                missing = candidate["claims"].pop(index)["fact_id"]
                result = self.validate(facts, candidate)
                self.assertFalse(result["g3_passed"])
                self.assertEqual(result["missing_required_fact_ids"], [missing])
        self.assertTrue(self.validate(facts, good)["accepted"])

    def test_duplicates_unknown_fact_and_changed_value_cannot_fill_missing(self):
        facts = self.facts()
        good = self.proposal(facts)
        for kind in ("duplicate", "unknown", "wrong_value", "empty"):
            with self.subTest(kind=kind):
                candidate = copy.deepcopy(good)
                if kind == "duplicate": candidate["claims"][1] = copy.deepcopy(candidate["claims"][0])
                if kind == "unknown": candidate["claims"][0]["fact_id"] = "fact-" + "0" * 64
                if kind == "wrong_value": candidate["claims"][2]["value"] = 40
                if kind == "empty": candidate["claims"] = []
                self.assertFalse(self.validate(facts, candidate)["accepted"])

    def test_cross_request_facts_and_bindings_are_not_reusable(self):
        first, second = self.facts(), self.facts()
        good, foreign = self.proposal(first), self.proposal(second)
        self.assertEqual(good["claims"][2]["value"], foreign["claims"][2]["value"])
        self.assertNotEqual(good["claims"][2]["fact_id"], foreign["claims"][2]["fact_id"])
        self.assertFalse(self.validate(first, foreign)["accepted"])
        candidate = copy.deepcopy(good)
        candidate["claims"][2] = foreign["claims"][2]
        self.assertFalse(self.validate(first, candidate)["accepted"])
        for key in ("scope_hash", "snapshot_digest", "manifest_digest", "contract_bundle_hash", "member_set_version", "fact_set_digest"):
            candidate = copy.deepcopy(good)
            candidate[key] = "0" * 64
            self.assertIn("BINDING_MISMATCH", [i["code"] for i in self.validate(first, candidate)["issues"]])

    def test_arrays_objects_expressions_free_text_and_evidence_objects_rejected(self):
        facts = self.facts()
        good = self.proposal(facts)
        for key, value in (("summary", "ALL_GOOD_INJECTION"), ("warnings", ["ALL_GOOD_INJECTION"]),
                           ("evidence", {}), ("status", "verified_answer")):
            candidate = copy.deepcopy(good)
            candidate[key] = value
            self.assertEqual(self.client.post(self.path(facts, "/validate"), json=candidate).status_code, 422)
        for key, value in (("value", []), ("value", {}), ("value", True),
                           ("fact_path", "$.items[*]"), ("evidence", {}), ("claim_type", "quality_pass")):
            candidate = copy.deepcopy(good)
            candidate["claims"][2][key] = value
            self.assertEqual(self.client.post(self.path(facts, "/validate"), json=candidate).status_code, 422)
        for value in ("4", 4.0, None):
            candidate = copy.deepcopy(good)
            candidate["claims"][2]["value"] = value
            self.assertEqual(self.client.post(self.path(facts, "/validate"), json=candidate).status_code, 422)

    def test_rejected_candidate_text_is_not_echoed_or_exported(self):
        facts = self.facts()
        candidate = self.proposal(facts)
        candidate["claims"][0]["value"] = "UNTRUSTED_MODEL_TEXT_<script>"
        receipt = self.validate(facts, candidate)
        self.assertFalse(receipt["accepted"])
        self.assertNotIn("UNTRUSTED_MODEL_TEXT", json.dumps(receipt))
        artifact = self.client.get(facts["export_url"])
        self.assertNotIn("UNTRUSTED_MODEL_TEXT", artifact.text)
        self.assertFalse(artifact.json()["data"]["view"]["latest_validation"]["accepted"])

    def test_all_fact_pages_are_bound_and_nonoverlapping(self):
        facts = self.facts()
        ids, offset = [], 0
        while True:
            page = self.data(self.client.get(facts["members_url"] + f"&offset={offset}&limit=3"))
            self.assertEqual(page["fact_set_digest"], facts["fact_set_digest"])
            self.assertEqual(page["total"], 35)
            ids.extend(f["fact_id"] for f in page["items"])
            if page["next_offset"] is None: break
            offset = page["next_offset"]
        self.assertEqual(ids, facts["required_fact_ids"])
        self.assertEqual(len(set(ids)), 35)
        wrong = facts["members_url"].replace("fact_set_digest=" + facts["fact_set_digest"], "fact_set_digest=" + "0" * 64)
        self.assertEqual(self.client.get(wrong).status_code, 409)
        self.assertEqual(self.client.get(facts["members_url"] + "&unexpected=1").status_code, 422)

    def test_foreign_session_cannot_read_validate_members_export_or_create(self):
        execution = self.execution()
        facts = self.facts(execution)
        proposal = self.proposal(facts)
        other = TestClient(self.app, raise_server_exceptions=False)
        for response in (other.get(self.path(facts)), other.get(facts["members_url"]), other.get(facts["export_url"]),
                         other.get(self.path(facts, "/proposal")), other.post(self.path(facts, "/validate"), json=proposal),
                         other.post(PREFIX + "/executions/" + execution["execution_id"] + "/facts", json={})):
            self.assertEqual(response.status_code, 404, response.text)
        other.close()

    def test_semantically_bad_rehashed_execution_cannot_generate_facts(self):
        execution = self.execution()
        def truncate(doc):
            result = doc["evidence"][1]["result"]
            result["items"] = result["items"][:1]
            result["total_rows"] = result["returned_rows"] = 1
            ev = doc["evidence"][1]
            ev["evidence_digest"] = hash_object({k: v for k, v in ev.items() if k != "evidence_digest"})
        self.rewrite_execution(execution["execution_id"], truncate)
        response = self.client.post(PREFIX + "/executions/" + execution["execution_id"] + "/facts", json={})
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()["error"]["code"], "PLAN_FACT_EVIDENCE_INVALID")

    def test_missing_call_evidence_wrong_binding_and_unconfirmed_cleanup_fail_g2(self):
        execution = self.execution()
        with self.plans.transaction() as con:
            original = json.loads(con.execute("SELECT document FROM executions WHERE id=?", (execution["execution_id"],)).fetchone()[0])
        for kind in ("missing_evidence", "extra_call", "wrong_request", "bad_cleanup", "wrong_attempt"):
            with self.subTest(kind=kind):
                doc = copy.deepcopy(original)
                if kind == "missing_evidence": doc["evidence"].pop()
                if kind == "extra_call": doc["calls"].append(copy.deepcopy(doc["calls"][0]))
                if kind == "wrong_request": doc["evidence"][1]["request_id"] = "another-request"
                if kind == "bad_cleanup": doc["calls"][1]["attempts"][0]["cleanup_confirmed"] = False
                if kind == "wrong_attempt": doc["evidence"][1]["attempt_id"] = "other-attempt"
                self.rewrite_execution(execution["execution_id"], lambda target: (target.clear(), target.update(doc)))
                response = self.client.post(PREFIX + "/executions/" + execution["execution_id"] + "/facts", json={})
                self.assertEqual(response.status_code, 409)

    def test_changed_evidence_invalidates_all_fact_outputs(self):
        execution = self.execution()
        facts = self.facts(execution)
        proposal = self.proposal(facts)
        self.rewrite_execution(execution["execution_id"], lambda doc: doc["evidence"].pop())
        for response in (self.client.get(self.path(facts)), self.client.get(facts["members_url"]),
                         self.client.get(facts["export_url"]), self.client.get(self.path(facts, "/proposal")),
                         self.client.post(self.path(facts, "/validate"), json=proposal)):
            self.assertEqual(response.status_code, 409)
            self.assertNotIn('"facts":', response.text)

    def test_pending_failed_or_volatile_execution_cannot_claim_g2(self):
        execution = self.execution()
        for state in ("queued", "running", "failed", "cancelled", "interrupted"):
            self.rewrite_execution(execution["execution_id"], lambda doc: doc.update(state=state, execution_complete=False))
            response = self.client.post(PREFIX + "/executions/" + execution["execution_id"] + "/facts", json={})
            self.assertEqual(response.status_code, 409)
            self.assertEqual(response.json()["error"]["code"], "PLAN_FACT_EXECUTION_NOT_READY")
        self.rewrite_execution(execution["execution_id"], lambda doc: doc.update(state="completed", execution_complete=True))
        self.app.state.execution_service.volatile[execution["execution_id"]] = {"state": "failed"}
        response = self.client.post(PREFIX + "/executions/" + execution["execution_id"] + "/facts", json={})
        self.assertEqual(response.status_code, 409)

    def test_fact_tamper_even_with_rehashed_storage_is_not_accepted(self):
        facts = self.facts()
        with self.plans.transaction() as con:
            row = con.execute("SELECT document FROM fact_sets WHERE id=?", (facts["fact_set_id"],)).fetchone()
            doc = json.loads(row[0])
            doc["facts"][2]["value"] = 40
            con.execute("UPDATE fact_sets SET document=?,digest=? WHERE id=?", (canonical(doc), hash_object(doc), facts["fact_set_id"]))
        response = self.client.get(facts["export_url"])
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"]["code"], "PLAN_FACT_EVIDENCE_CHANGED")

    def test_permission_coverage_expiry_and_bundle_are_rechecked(self):
        facts = self.facts()
        proposal = self.proposal(facts)
        original = self.plans.clock
        self.plans.clock = lambda: original() + 1801
        self.assertEqual(self.client.get(facts["export_url"]).status_code, 409)
        self.plans.clock = original
        self.plans.revoke("coverage", self.plans.confirmed(self.client.cookies.get("demo_plan_session"), facts["confirmed_plan_id"])["card"]["coverage_receipt"]["declaration_digest"])
        self.assertEqual(self.client.post(self.path(facts, "/validate"), json=proposal).status_code, 409)
        self.assertEqual(self.client.get(facts["members_url"]).status_code, 409)
        self.plans.revoke("session", self.client.cookies.get("demo_plan_session"))
        self.assertEqual(self.client.get(facts["export_url"]).status_code, 403)

    def test_bundle_revocation_blocks_even_a_previously_accepted_fact_set(self):
        facts = self.facts()
        self.assertTrue(self.validate(facts, self.proposal(facts))["accepted"])
        self.plans.revoke("bundle", facts["contract_bundle_hash"])
        self.assertEqual(self.client.get(facts["export_url"]).status_code, 409)

    def test_restart_preserves_fact_and_receipt_identity(self):
        facts = self.facts()
        receipt = self.validate(facts, self.proposal(facts))
        restarted = TestClient(create_app(self.db, BATCH, SCHEMA), raise_server_exceptions=False)
        restarted.cookies.update(self.client.cookies)
        view = self.data(restarted.get(self.path(facts)))
        self.assertEqual(view["fact_set_digest"], facts["fact_set_digest"])
        self.assertEqual(view["latest_validation"], receipt)
        restarted.close()

    def test_receipt_storage_failure_does_not_return_accepted(self):
        facts = self.facts()
        candidate = self.proposal(facts)
        service = self.app.state.fact_service
        real = service._save_receipt
        attempted = []
        def failure(con, owner, identity, receipt):
            real(con, owner, identity, receipt)
            attempted.append(receipt["validation_id"])
            raise sqlite3.OperationalError("PRIVATE_FAILURE_PATH")
        with patch.object(service, "_save_receipt", failure):
            response = self.client.post(self.path(facts, "/validate"), json=candidate)
        self.assertEqual(response.status_code, 503)
        self.assertEqual(len(attempted), 1)
        self.assertNotIn("PRIVATE_FAILURE_PATH", response.text)
        self.assertIsNone(self.data(self.client.get(self.path(facts)))["latest_validation"])

    def test_historical_boundary_is_fact_semantics_not_execution_time(self):
        before = self.facts(self.execution("2026-09-01T05:09:59Z"))
        at = self.facts(self.execution("2026-09-01T05:10:00Z"))
        self.assertEqual(before["summary_facts"][2]["value"], 0)
        self.assertEqual(at["summary_facts"][2]["value"], 1)
        self.assertEqual(at["summary_facts"][1]["value"], "2026-09-01T05:10:00Z")
        candidate = self.proposal(at)
        candidate["claims"][1]["value"] = "2026-10-27T00:00:00Z"
        self.assertFalse(self.validate(at, candidate)["accepted"])


if __name__ == "__main__":
    unittest.main()
