"""Preflight controls: positive results and failures at HTTP/output boundaries.

T23-T26 subsets for exact LOT inventory. No claim of SN discovery, full answer
publication, a real source watermark, or arbitrary old-engine compatibility.
"""

import copy
import json
import shutil
import sqlite3
import sys
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from fastapi.testclient import TestClient

from mdlab_app.api import create_app
from mdlab_app.errors import ProcessingError
from mdlab_app.ingestion import canonical, load_snapshot
from mdlab_app.plan_contracts import SPEC, coverage_receipt, hash_object
from mdlab_app.plan_service import PlanService, resolve, snapshot_page
from mdlab_app.storage import ingest

BATCH = ROOT / "data/batches/demo-v1"
SCHEMA = ROOT / "schemas/csv-schema.json"
PREFIX = "/api/v1/query-plans"
BODY = {"intent_key": "lot_operation_inventory", "lot_id": "LOT-0001", "as_of": "2026-09-07T00:00:00Z",
        "original_input": "列出这个 LOT 的全部作业"}


class QueryPlanTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.db = self.root / "demo.sqlite3"
        ingest(BATCH, self.db, SCHEMA)
        self.app = create_app(self.db, BATCH, SCHEMA)
        self.service = self.app.state.plan_service
        self.client = TestClient(self.app, raise_server_exceptions=False)

    def tearDown(self):
        self.client.close()
        self.tmp.cleanup()

    def create(self, **kwargs):
        response = self.client.post(PREFIX + "/requests", json={**BODY, **kwargs})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["data"]

    def confirm(self, card):
        return self.client.post(PREFIX + "/requests/" + card["request_id"] + "/confirm",
                                json={"revision": card["revision"], "card_digest": card["card_digest"]})

    def expect_error(self, response, code, status=409):
        self.assertEqual(response.status_code, status, response.text)
        self.assertEqual(response.json()["error"]["code"], code)
        self.assertNotIn("traceback", response.text.lower())

    def test_normal_complete_confirmation_members_export_and_idempotence(self):
        before = self.db.read_bytes()
        card = self.create()
        self.assertTrue(card["can_confirm"])
        self.assertFalse(card["answer_published"])
        page = self.client.get(card["members_url"]).json()["data"]
        self.assertEqual([r["operation_id"] for r in page["items"]], ["OP-0001", "OP-0002", "OP-0007", "OP-0012"])
        self.assertEqual(page["set_version"], card["member_set"]["digest"])
        first = self.confirm(card)
        self.assertEqual(first.status_code, 200, first.text)
        plan = first.json()["data"]
        self.assertEqual(plan["stage"], "preflight_confirmed")
        again = self.confirm(card).json()["data"]
        self.assertEqual(again["confirmed_plan_id"], plan["confirmed_plan_id"])
        self.assertTrue(again["reused"])
        export = self.client.get(plan["export_url"])
        self.assertIn("attachment", export.headers["content-disposition"])
        artifact = export.json()["data"]
        self.assertEqual(artifact["discovered_members"], page["items"])
        self.assertFalse(artifact["answer_published"])
        self.assertEqual(hash_object(artifact["contract_bundle_manifest"]), card["contract_bundle_hash"])
        self.assertEqual(self.db.read_bytes(), before, "Preflight must not write business database")

    def test_empty_complete_is_confirmable_not_unknown(self):
        card = self.create(as_of="2026-09-01T02:00:00Z")
        self.assertTrue(card["can_confirm"])
        self.assertEqual(card["member_set"]["count"], 0)
        self.assertEqual(self.confirm(card).status_code, 200)

    def test_historical_visibility_and_inclusive_boundary(self):
        early = self.create(as_of="2026-09-01T05:09:59Z")
        at = self.create(as_of="2026-09-01T05:10:00Z")
        self.assertEqual(early["member_set"]["count"], 0)
        self.assertEqual(at["member_set"]["count"], 1)
        self.assertTrue(self.create(as_of="2026-09-11T23:59:59Z")["can_confirm"])
        beyond = self.create(as_of="2026-09-12T00:00:00Z")
        self.assertFalse(beyond["can_confirm"])
        self.assertEqual(beyond["coverage_receipt"]["status"], "not_covered")
        self.expect_error(self.confirm(beyond), "PLAN_NOT_CONFIRMABLE")
        exported = self.client.get(beyond["export_url"]).json()["data"]
        self.assertEqual(exported["plan"]["state"], "incomplete")
        self.assertFalse(exported["answer_published"])

    def test_first_page_not_a_complete_set_at_http_boundary(self):
        def truncated(rows, offset, limit):
            page = snapshot_page(rows, offset, limit)
            page.update(items=rows[:1], next_offset=None)
            return page
        self.service.fetch_page = truncated
        card = self.create()
        self.assertFalse(card["can_confirm"])
        self.assertEqual(card["resolution_receipt"]["fetched_count"], 1)
        self.expect_error(self.confirm(card), "PLAN_NOT_CONFIRMABLE")
        artifact = self.client.get(card["export_url"]).json()["data"]
        self.assertEqual(artifact["plan"]["state"], "incomplete")
        self.assertFalse(artifact["answer_published"])
        self.assertNotIn("complete_discovered_members", artifact)
        self.assertEqual(len(artifact["discovered_members"]), 1)
        members = self.client.get(card["members_url"]).json()["data"]
        self.assertEqual(members["total"], 1)
        self.assertEqual(members["requested_scope_count"], 4)
        self.assertFalse(members["resolution_complete"])

    def test_thirty_members_partial_pages_and_faults(self):
        rows = [{"operation_id": f"OP-{i:04d}", "lot_id": "LOT-0001"} for i in range(30)]
        complete, receipt = resolve(rows, snapshot_page)
        self.assertTrue(receipt["resolution_complete"])
        self.assertEqual(complete, rows)
        self.assertEqual(len(receipt["calls"]), 3)
        for fault in ("second_page_failure", "total_drift", "same_count_changed_members", "loop", "truncated", "unknown_total", "changed_version", "skipped_offset"):
            with self.subTest(fault=fault):
                def broken(source, offset, limit):
                    page = snapshot_page(source, offset, limit)
                    if offset == 10:
                        if fault == "second_page_failure":
                            raise OSError("injected page failure")
                        if fault == "total_drift": page["total"] += 1
                        if fault == "same_count_changed_members": page["items"][0]["operation_id"] = "OTHER"
                        if fault == "loop": page["next_offset"] = 0
                        if fault == "truncated": page["truncated"] = True
                        if fault == "unknown_total": page["total"] = None
                        if fault == "changed_version": page["set_version"] = "0" * 64
                        if fault == "skipped_offset": page["next_offset"] = 21
                    return page
                _, failed = resolve(copy.deepcopy(rows), broken)
                self.assertFalse(failed["resolution_complete"])
                self.assertTrue(failed["failed_scope"])

    def test_wrong_lot_member_rejected_even_with_same_count(self):
        def wrong(rows, offset, limit):
            result = snapshot_page(rows, offset, limit)
            result["items"][0]["lot_id"] = "LOT-0002"
            return result
        self.service.fetch_page = wrong
        card = self.create()
        self.assertFalse(card["can_confirm"])
        self.expect_error(self.confirm(card), "PLAN_NOT_CONFIRMABLE")

    def test_strict_schemas_reject_model_fields_null_and_coercion(self):
        for extra in ({"summary": "全部合格"}, {"sn": "SN-1"}, {"subset": ["OP-0001"]}, {"lot_id": None},
                      {"intent_key": "overall_quality"}, {"as_of": "now"}):
            with self.subTest(extra=extra):
                self.expect_error(self.client.post(PREFIX + "/requests", json={**BODY, **extra}), "INVALID_ARGUMENT", 422)
        card = self.create()
        for revision in (True, "1", 1.0, None):
            self.expect_error(self.client.post(PREFIX + "/requests/" + card["request_id"] + "/confirm",
                              json={"revision": revision, "card_digest": card["card_digest"]}), "INVALID_ARGUMENT", 422)

    def test_foreign_session_and_forged_scope_hash_have_no_authority(self):
        card = self.create()
        with TestClient(self.app, raise_server_exceptions=False) as stranger:
            self.expect_error(stranger.get(card["members_url"]), "PLAN_NOT_FOUND", 404)
            self.expect_error(stranger.get(card["export_url"]), "PLAN_NOT_FOUND", 404)
        self.expect_error(self.client.post(PREFIX + "/requests/" + card["request_id"] + "/confirm",
                          json={"revision": 1, "card_digest": "0" * 64}), "STALE_CONFIRMATION_CARD")

    def test_revise_rejects_old_card_and_old_member_link(self):
        old = self.create()
        url = PREFIX + "/requests/" + old["request_id"] + "/revise"
        result = self.client.post(url, json={**BODY, "lot_id": "LOT-0002", "revision": old["revision"], "card_digest": old["card_digest"]})
        self.assertEqual(result.status_code, 200, result.text)
        new = result.json()["data"]
        self.assertEqual(new["revision"], 2)
        self.expect_error(self.confirm(old), "STALE_CONFIRMATION_CARD")
        self.expect_error(self.client.get(old["members_url"]), "SET_VERSION_MISMATCH")
        plan = self.confirm(new).json()["data"]
        self.assertEqual(plan["card"]["scope"]["lot_id"], "LOT-0002")

    def test_confirm_then_revise_preserves_old_immutable_plan(self):
        old = self.create()
        plan = self.confirm(old).json()["data"]
        self.client.post(PREFIX + "/requests/" + old["request_id"] + "/revise",
                         json={**BODY, "lot_id": "LOT-0002", "revision": old["revision"], "card_digest": old["card_digest"]})
        artifact = self.client.get(plan["export_url"]).json()["data"]
        self.assertEqual(artifact["plan"]["card"]["scope"]["lot_id"], "LOT-0001")
        self.assertTrue(all(r["lot_id"] == "LOT-0001" for r in artifact["discovered_members"]))

    def test_concurrent_revision_and_confirmation_are_linearizable(self):
        owner = self.service.session(None)
        old = self.service.create(owner, "request", BODY["lot_id"], BODY["as_of"])
        barrier = threading.Barrier(2)
        def confirm():
            barrier.wait()
            try:
                return self.service.confirm(owner, old["request_id"], 1, old["card_digest"])
            except ProcessingError as exc:
                return exc.code
        def revise():
            barrier.wait()
            return self.service.revise(owner, old["request_id"], 1, old["card_digest"], "new", "LOT-0002", BODY["as_of"])
        with ThreadPoolExecutor(2) as pool:
            a, b = pool.submit(confirm), pool.submit(revise)
            confirmation, revision = a.result(), b.result()
        self.assertEqual(revision["revision"], 2)
        if isinstance(confirmation, str):
            self.assertEqual(confirmation, "STALE_CONFIRMATION_CARD")
        else:
            self.assertEqual(confirmation["card"]["scope"]["lot_id"], "LOT-0001")
            exported = self.service.export(owner, confirmation["confirmed_plan_id"], confirmed=True)
            self.assertTrue(all(r["lot_id"] == "LOT-0001" for r in exported["discovered_members"]))

    def test_wrong_set_version_and_unknown_query_fields_rejected(self):
        card = self.create()
        self.expect_error(self.client.get(card["members_url"].replace(card["member_set"]["set_version"], "0" * 64)), "SET_VERSION_MISMATCH")
        for url in (card["export_url"] + "?extra=1", card["members_url"] + "&extra=1", card["members_url"] + "&limit=1&limit=2"):
            self.expect_error(self.client.get(url), "INVALID_ARGUMENT", 422)

    def test_unreadable_member_store_blocks_confirmation_and_export(self):
        card = self.create()
        # Simulated trusted-store fault with checksum maintained, to exercise
        # the semantic readability guard rather than only the integrity check.
        with self.service.lock, self.service.transaction() as con:
            row = con.execute("SELECT document FROM drafts WHERE id=?", (card["request_id"],)).fetchone()
            doc = json.loads(row["document"])
            doc["member_set"]["readable"] = False
            doc.pop("card_digest")
            doc["card_digest"] = hash_object(doc)
            con.execute("UPDATE drafts SET document=?,card_digest=? WHERE id=?", (canonical(doc), doc["card_digest"], card["request_id"]))
        self.expect_error(self.confirm(doc), "MEMBERS_UNREADABLE")
        self.expect_error(self.client.get(card["export_url"]), "MEMBERS_UNREADABLE")

    def test_missing_coverage_never_uses_latest_timestamp(self):
        missing = self.root / "no-declaration.json"
        service = PlanService(self.db, BATCH, SCHEMA, coverage_path=missing)
        owner = service.session(None)
        card = service.create(owner, "question", "LOT-0001", BODY["as_of"])
        self.assertEqual(card["coverage_receipt"]["status"], "unknown")
        self.assertFalse(card["can_confirm"])
        with self.assertRaisesRegex(ProcessingError, "Discovery and coverage"):
            service.confirm(owner, card["request_id"], 1, card["card_digest"])

    def test_source_partition_scope_and_history_gaps(self):
        snapshot = load_snapshot(BATCH, SCHEMA)
        original = json.loads((ROOT / "contracts/demo-closed-world.json").read_text())
        for fault, expected in (("missing_source", "unknown"), ("old_watermark", "not_covered"),
                                ("lost_history", "not_covered"), ("missing_lot", "not_covered"),
                                ("wrong_snapshot", "unknown"), ("bad_watermark", "unknown")):
            with self.subTest(fault=fault):
                d = copy.deepcopy(original)
                if fault == "missing_source": del d["sources"]["operations"]
                if fault == "old_watermark": d["sources"]["operations"]["recorded_through"] = "2026-09-06T12:00:00Z"
                if fault == "lost_history": d["sources"]["lots"]["history_retained"] = False
                if fault == "missing_lot": del d["inventory"]["LOT-0001"]
                if fault == "wrong_snapshot": d["data_digest"] = "0" * 64
                if fault == "bad_watermark": d["sources"]["operations"]["recorded_through"] = "tomorrow"
                receipt = coverage_receipt(snapshot, d, "LOT-0001", BODY["as_of"], "bundle")
                self.assertEqual(receipt["status"], expected)
                self.assertTrue(receipt["gaps"])

    def test_coverage_revoked_after_confirmation_blocks_every_output(self):
        card = self.create()
        plan = self.confirm(card).json()["data"]
        with self.service.lock:
            self.service.revoked_coverage.add(card["coverage_receipt"]["declaration_digest"])
        for url in (plan["export_url"], plan["members_url"], PREFIX + "/confirmed/" + plan["confirmed_plan_id"]):
            self.expect_error(self.client.get(url), "COVERAGE_CHANGED")

    def test_expired_card_cannot_be_viewed_confirmed_or_exported(self):
        card = self.create()
        self.service.clock = lambda: card["expires_at_epoch"]
        self.expect_error(self.confirm(card), "PLAN_EXPIRED")
        self.expect_error(self.client.get(card["members_url"]), "PLAN_EXPIRED")
        self.expect_error(self.client.get(card["export_url"]), "PLAN_EXPIRED")

    def test_default_bundle_upgrade_keeps_available_old_bundle_exact(self):
        card = self.create()
        plan = self.confirm(card).json()["data"]
        with self.service.lock:
            self.service.bundles.default = self.service.bundles.register("release-2-same-engine")
        new = self.create()
        self.assertNotEqual(card["contract_bundle_hash"], new["contract_bundle_hash"])
        old = self.client.get(plan["export_url"]).json()["data"]
        self.assertEqual(old["plan"]["card"]["contract_bundle_hash"], card["contract_bundle_hash"])
        self.assertEqual(hash_object(old["contract_bundle_manifest"]), card["contract_bundle_hash"])

    def test_unavailable_and_revoked_bundle_stop_confirm_and_export(self):
        for fault in ("unavailable", "revoked"):
            with self.subTest(fault=fault):
                card = self.create()
                plan = self.confirm(card).json()["data"]
                identity = card["contract_bundle_hash"]
                artifact = self.service.bundles.artifacts[identity]
                with self.service.lock:
                    if fault == "unavailable": del self.service.bundles.artifacts[identity]
                    else: self.service.bundles.revoked.add(identity)
                code = "CONTRACT_BUNDLE_UNAVAILABLE" if fault == "unavailable" else "CONTRACT_BUNDLE_REVOKED"
                self.expect_error(self.confirm(card), code)
                self.expect_error(self.client.get(plan["members_url"]), code)
                self.expect_error(self.client.get(plan["export_url"]), code)
                with self.service.lock:
                    self.service.bundles.artifacts[identity] = artifact
                    self.service.bundles.revoked.discard(identity)

    def test_each_contract_semantic_change_is_detected(self):
        card = self.create()
        identity = card["contract_bundle_hash"]
        original = copy.deepcopy(self.service.bundles.artifacts[identity])
        for name in ("intent", "evidence_dag", "required_claims", "metric", "visibility", "output_schema", "templates", "coverage", "order"):
            with self.subTest(field=name):
                self.service.bundles.artifacts[identity]["contract"][name] = "tampered"
                self.expect_error(self.confirm(card), "CONTRACT_BUNDLE_MISMATCH")
                self.expect_error(self.client.get(card["export_url"]), "CONTRACT_BUNDLE_MISMATCH")
                self.service.bundles.artifacts[identity] = copy.deepcopy(original)

    def test_actual_runtime_file_change_is_detected_without_changing_product(self):
        card = self.create()
        registry = self.service.bundles
        name = "src/mdlab_app/plan_service.py"
        original = registry.paths[name]
        fake = self.root / "runtime-copy.py"
        shutil.copyfile(original, fake)
        registry.paths[name] = fake
        self.assertEqual(self.confirm(card).status_code, 200)
        fake.write_bytes(fake.read_bytes() + b"\n# simulated deployment replacement\n")
        self.expect_error(self.confirm(card), "CONTRACT_BUNDLE_MISMATCH")
        self.expect_error(self.client.get(card["members_url"]), "CONTRACT_BUNDLE_MISMATCH")

    def test_permission_revocation_overrides_frozen_bundle(self):
        card = self.create()
        plan = self.confirm(card).json()["data"]
        owner = self.client.cookies.get("demo_plan_session")
        with self.service.lock:
            self.service.revoked_sessions.add(owner)
        for url in (plan["members_url"], plan["export_url"]):
            self.expect_error(self.client.get(url), "PLAN_PERMISSION_REVOKED", 403)

    def test_server_revocations_survive_restart(self):
        for kind, code in (("session", "PLAN_PERMISSION_REVOKED"), ("coverage", "COVERAGE_CHANGED"),
                           ("bundle", "CONTRACT_BUNDLE_REVOKED")):
            with self.subTest(kind=kind):
                # Each policy has its own isolated control store.
                separate = self.root / (kind + ".sqlite3")
                ingest(BATCH, separate, SCHEMA)
                service = PlanService(separate, BATCH, SCHEMA)
                owner = service.session(None)
                card = service.create(owner, "question", BODY["lot_id"], BODY["as_of"])
                plan = service.confirm(owner, card["request_id"], 1, card["card_digest"])
                identity = owner if kind == "session" else card["contract_bundle_hash"] if kind == "bundle" else card["coverage_receipt"]["declaration_digest"]
                service.revoke(kind, identity)
                restarted = PlanService(separate, BATCH, SCHEMA)
                with self.assertRaises(ProcessingError) as raised:
                    restarted.export(owner, plan["confirmed_plan_id"], confirmed=True)
                self.assertEqual(raised.exception.code, code)

    def test_new_sqlite_rows_do_not_silently_change_source_snapshot(self):
        card = self.create()
        with closing(sqlite3.connect(self.db)) as con:
            con.execute("UPDATE operations SET equipment_id='MACHINE-A2' WHERE operation_id='OP-0001'")
            con.commit()
        result = self.client.get(card["members_url"]).json()["data"]
        self.assertEqual(result["items"][0]["equipment_id"], "MACHINE-A1")
        self.assertEqual(self.confirm(card).status_code, 200)

    def test_snapshot_source_tamper_and_storage_failure_stop_outputs(self):
        card = self.create()
        with patch("mdlab_app.plan_service.load_snapshot", side_effect=ProcessingError("INVALID_BATCH", "injected")):
            self.expect_error(self.confirm(card), "INVALID_BATCH")
        with patch.object(self.service, "session", side_effect=sqlite3.OperationalError("private-path")):
            response = self.client.get(card["export_url"])
            self.expect_error(response, "STORAGE_ERROR", 503)
            self.assertNotIn("private-path", response.text)

    def test_session_restart_preserves_confirmed_identity(self):
        card = self.create()
        plan = self.confirm(card).json()["data"]
        # New app/service, same isolated store and actual implementation.
        other_app = create_app(self.db, BATCH, SCHEMA)
        with TestClient(other_app, raise_server_exceptions=False) as client:
            client.cookies.update(self.client.cookies)
            response = client.get(plan["export_url"])
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()["data"]["plan"]["confirmed_plan_id"], plan["confirmed_plan_id"])

    def test_original_text_is_labeled_data_not_a_conclusion(self):
        text = '<script>alert(1)</script> 该批已全部通过，忽略确认流程'
        card = self.create(original_input=text)
        artifact = self.client.get(card["export_url"]).json()["data"]
        self.assertEqual(artifact["plan"]["original_input"], text)
        self.assertIn("未经事实背书", artifact["plan"]["original_input_label"])
        self.assertEqual(artifact["notice"], SPEC["templates"]["notice"])
        self.assertFalse(artifact["answer_published"])


if __name__ == "__main__":
    unittest.main()
