"""Execution HTTP, actual subprocess cutoffs and persistent ledger controls."""

import copy
import json
import sqlite3
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from fastapi.testclient import TestClient
from mdlab_app.api import create_app
from mdlab_app.evidence_execution import CallFailure, ExecutionService, subprocess_call
from mdlab_app.ingestion import canonical
from mdlab_app.plan_contracts import hash_object
from mdlab_app.plan_service import PlanService
from mdlab_app.storage import ingest

PREFIX = "/api/v1/query-plans"
BATCH, SCHEMA = ROOT / "data/batches/demo-v1", ROOT / "schemas/csv-schema.json"


class EvidenceExecutionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "demo.sqlite3"
        ingest(BATCH, self.db, SCHEMA)
        self.app = create_app(self.db, BATCH, SCHEMA)
        self.client = TestClient(self.app, raise_server_exceptions=False)
        self.plans, self.engine = self.app.state.plan_service, self.app.state.execution_service

    def tearDown(self):
        for event, worker in list(self.engine.active.values()):
            event.set()
            worker.join(10)
            self.assertFalse(worker.is_alive())
        self.client.close()
        self.tmp.cleanup()

    def plan(self, at="2026-09-07T00:00:00Z"):
        response = self.client.post(PREFIX + "/requests", json={"intent_key": "lot_operation_inventory",
            "original_input": "列出全部作业", "lot_id": "LOT-0001", "as_of": at})
        self.assertEqual(response.status_code, 200, response.text)
        card = response.json()["data"]
        result = self.client.post(PREFIX + "/requests/" + card["request_id"] + "/confirm",
                                  json={"revision": card["revision"], "card_digest": card["card_digest"]})
        self.assertEqual(result.status_code, 200, result.text)
        return result.json()["data"]

    def start(self, plan=None):
        plan = plan or self.plan()
        response = self.client.post(PREFIX + "/confirmed/" + plan["confirmed_plan_id"] + "/execute",
                                    json={"scope_hash": plan["scope_hash"]})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["data"]

    def terminal(self, execution):
        end = time.monotonic() + 12
        while time.monotonic() < end:
            response = self.client.get(PREFIX + "/executions/" + execution["execution_id"])
            self.assertEqual(response.status_code, 200, response.text)
            result = response.json()["data"]
            if result["state"] in ("completed", "failed", "cancelled", "interrupted"):
                return result
            time.sleep(0.02)
        self.fail("Execution did not terminate")

    def test_happy_actual_workers_binding_export_and_replay(self):
        before = self.db.read_bytes()
        plan = self.plan()
        done = self.terminal(self.start(plan))
        self.assertEqual(done["state"], "completed", done)
        self.assertTrue(done["execution_complete"])
        self.assertFalse(done["answer_published"])
        self.assertEqual(done["completed_scope"], ["lots", "operations"])
        self.assertEqual(done["not_attempted_scope"], [])
        self.assertEqual(done["failed_scope"], [])
        self.assertEqual(len(done["evidence"]), 2)
        self.assertEqual([r["operation_id"] for r in done["evidence"][1]["result"]["items"]],
                         ["OP-0001", "OP-0002", "OP-0007", "OP-0012"])
        for evidence, call in zip(done["evidence"], done["calls"]):
            self.assertEqual(evidence["confirmed_plan_id"], plan["confirmed_plan_id"])
            self.assertEqual(evidence["scope_hash"], plan["scope_hash"])
            self.assertEqual(evidence["logical_call_id"], call["logical_call_id"])
            self.assertEqual(evidence["evidence_digest"], hash_object({k: v for k, v in evidence.items() if k != "evidence_digest"}))
            attempt = call["attempts"][0]
            self.assertLessEqual(attempt["started_at"], attempt["finished_at"])
            self.assertTrue(attempt["cleanup_confirmed"])
        artifact = self.client.get(done["export_url"])
        self.assertIn("attachment", artifact.headers["content-disposition"])
        self.assertEqual(artifact.json()["data"]["execution"]["evidence"], done["evidence"])
        repeated = self.start(plan)
        self.assertEqual(repeated["execution_id"], done["execution_id"])
        self.assertTrue(repeated["reused"])
        self.assertEqual(before, self.db.read_bytes())

    def test_empty_operations_is_complete_not_source_failure(self):
        done = self.terminal(self.start(self.plan("2026-09-01T02:00:00Z")))
        self.assertEqual(done["state"], "completed")
        self.assertEqual(done["evidence"][1]["result"]["total_rows"], 0)
        self.assertEqual(done["evidence"][1]["result"]["items"], [])

    def test_http_cannot_replace_scope_policy_source_or_supply_null(self):
        plan = self.plan()
        url = PREFIX + "/confirmed/" + plan["confirmed_plan_id"] + "/execute"
        for key, value in (("lot_id", "LOT-0002"), ("as_of", "2026-09-01T00:00:00Z"),
                           ("source", "lots"), ("timeout", 999), ("evidence", [])):
            response = self.client.post(url, json={"scope_hash": plan["scope_hash"], key: value})
            self.assertEqual(response.status_code, 422)
        for body in ({}, {"scope_hash": None}, {"scope_hash": "0" * 64}):
            response = self.client.post(url, json=body)
            self.assertIn(response.status_code, (409, 422))
        other = TestClient(self.app, raise_server_exceptions=False)
        self.assertEqual(other.post(url, json={"scope_hash": plan["scope_hash"]}).status_code, 404)
        other.close()
        with self.plans.transaction() as con:
            self.assertEqual(con.execute("SELECT count(*) FROM executions").fetchone()[0], 0)

    def test_retry_retains_logical_binding_and_has_finite_budget(self):
        payloads = []
        def retry(command, payload, cancel, deadline, policy):
            payloads.append(copy.deepcopy(payload))
            if len(payloads) == 1:
                raise CallFailure("SOURCE_BUSY")
            return subprocess_call(command, payload, cancel, deadline, policy)
        self.engine.runner = retry
        done = self.terminal(self.start())
        self.assertEqual(done["state"], "completed")
        self.assertEqual(payloads[0], payloads[1])
        attempts = done["calls"][0]["attempts"]
        self.assertEqual([a["attempt"] for a in attempts], [1, 2])
        self.assertNotEqual(attempts[0]["attempt_id"], attempts[1]["attempt_id"])

    def test_retry_exhaustion_stops_dependent_source(self):
        import subprocess
        real_popen = subprocess.Popen
        def busy(*args, **kwargs):
            return real_popen([sys.executable, "-c", "import sys; sys.exit(75)"], **kwargs)
        with patch("mdlab_app.evidence_execution.subprocess.Popen", busy):
            done = self.terminal(self.start())
        self.assertEqual(done["state"], "failed")
        self.assertEqual(len(done["calls"][0]["attempts"]), 2)
        self.assertEqual(done["not_attempted_scope"], ["operations"])
        self.assertEqual(done["failed_scope"], [{"source": "lots", "code": "SOURCE_BUSY"}])
        self.assertFalse(done["execution_complete"])

    def test_partial_source_failure_retains_completed_evidence(self):
        def partial(command, payload, cancel, deadline, policy):
            if payload["source"] == "operations":
                raise CallFailure("SOURCE_ERROR")
            return subprocess_call(command, payload, cancel, deadline, policy)
        self.engine.runner = partial
        done = self.terminal(self.start())
        self.assertEqual(done["completed_scope"], ["lots"])
        self.assertEqual(done["failed_scope"], [{"source": "operations", "code": "SOURCE_ERROR"}])
        self.assertEqual(done["not_attempted_scope"], [])
        self.assertEqual(len(done["evidence"]), 1)
        self.assertFalse(done["execution_complete"])

    def test_actual_worker_deadline_kills_and_reaps(self):
        import subprocess
        real_popen, children = subprocess.Popen, []
        def capture(*args, **kwargs):
            child = real_popen([sys.executable, "-c", "import time; time.sleep(60)"], **kwargs)
            children.append(child)
            return child
        with patch("mdlab_app.evidence_execution.subprocess.Popen", capture):
            started = time.monotonic()
            done = self.terminal(self.start())
        self.assertGreater(time.monotonic() - started, 4.9)
        self.assertLess(time.monotonic() - started, 9)
        self.assertEqual(done["error_code"], "TIMEOUT")
        self.assertEqual(len(children), 1)
        self.assertIsNotNone(children[0].poll())
        self.assertTrue(done["calls"][0]["attempts"][0]["cleanup_confirmed"])

    def test_cancel_actual_worker_and_foreign_session_cannot_cancel(self):
        import subprocess
        real_popen, ready, children = subprocess.Popen, threading.Event(), []
        def capture(*args, **kwargs):
            child = real_popen([sys.executable, "-c", "import time; time.sleep(60)"], **kwargs)
            children.append(child)
            ready.set()
            return child
        with patch("mdlab_app.evidence_execution.subprocess.Popen", capture):
            execution = self.start()
            self.assertTrue(ready.wait(3))
            url = PREFIX + "/executions/" + execution["execution_id"] + "/cancel"
            other = TestClient(self.app, raise_server_exceptions=False)
            self.assertEqual(other.post(url, json={}).status_code, 404)
            other.close()
            self.assertEqual(self.client.post(url, json={"scope": "override"}).status_code, 422)
            self.assertEqual(self.client.post(url, json={}).status_code, 200)
            done = self.terminal(execution)
        self.assertEqual(done["state"], "cancelled")
        self.assertIsNotNone(children[0].poll())
        self.assertEqual(done["not_attempted_scope"], ["operations"])

    def test_alternate_worker_commands_are_rejected_before_spawn(self):
        for command in ([sys.executable, "-c", "raise SystemExit(0)"], "python", []):
            with self.subTest(command=command), patch("mdlab_app.evidence_execution.subprocess.Popen") as spawn:
                with self.assertRaises(CallFailure) as caught:
                    subprocess_call(command, {}, threading.Event(), time.monotonic() + 5,
                                    {"cancellation_poll_seconds": 0.1})
                self.assertEqual(caught.exception.code, "INVALID_WORKER_COMMAND")
                spawn.assert_not_called()

    def test_invalid_missing_and_semantically_wrong_results_fail_closed(self):
        plan = self.plan()
        def wrong(command, payload, cancel, deadline, policy):
            raw = subprocess_call(command, payload, cancel, deadline, policy)
            raw["items"][0]["lot_id"] = "LOT-0002"
            return raw
        self.engine.runner = wrong
        done = self.terminal(self.start(plan))
        self.assertEqual(done["error_code"], "EVIDENCE_SCOPE_OR_COMPLETENESS_MISMATCH")
        self.assertEqual(done["evidence"], [])
        self.assertEqual(len(done["calls"][0]["attempts"]), 1)

    def test_malformed_schema_is_not_treated_as_empty_success(self):
        self.engine.runner = lambda *args: {"items": [], "total_rows": False}
        done = self.terminal(self.start())
        self.assertEqual(done["error_code"], "INVALID_SOURCE_RESULT")
        self.assertFalse(done["execution_complete"])

    def test_truncated_result_even_with_plausible_counts_is_rejected(self):
        def truncated(command, payload, cancel, deadline, policy):
            raw = subprocess_call(command, payload, cancel, deadline, policy)
            if payload["source"] == "operations":
                raw["items"] = raw["items"][:1]
                raw["total_rows"] = raw["returned_rows"] = 1
            return raw
        self.engine.runner = truncated
        done = self.terminal(self.start())
        self.assertEqual(done["completed_scope"], ["lots"])
        self.assertEqual(done["error_code"], "EVIDENCE_SCOPE_OR_COMPLETENESS_MISMATCH")

    def test_total_budget_includes_retries_across_sources(self):
        ticks, seen = [100.0], {}
        def advance(command, payload, cancel, deadline, policy):
            source = payload["source"]
            seen[source] = seen.get(source, 0) + 1
            # A deterministic virtual-clock boundary test, not a wall-time run.
            result = None
            if seen[source] == 2:
                result = subprocess_call(command, payload, cancel, deadline, policy)
            ticks[0] += 4
            if result is None:
                raise CallFailure("SOURCE_BUSY")
            return result
        self.engine.runner = advance
        with patch("mdlab_app.evidence_execution.time", SimpleNamespace(monotonic=lambda: ticks[0])):
            done = self.terminal(self.start())
        self.assertEqual(done["error_code"], "TIMEOUT")
        self.assertFalse(done["execution_complete"])
        self.assertEqual(done["completed_scope"], ["lots"])

    def test_contract_revocation_during_call_blocks_evidence_commit(self):
        def revoked(command, payload, cancel, deadline, policy):
            raw = subprocess_call(command, payload, cancel, deadline, policy)
            self.plans.revoke("bundle", self.plans.bundles.default)
            return raw
        self.engine.runner = revoked
        done = self.terminal(self.start())
        self.assertEqual(done["error_code"], "CONTRACT_BUNDLE_REVOKED")
        self.assertEqual(done["access_state"], "blocked")
        self.assertEqual(done["evidence"], [])

    def test_policy_change_during_call_prevents_completion_and_hides_evidence(self):
        ready, release = threading.Event(), threading.Event()
        def held(command, payload, cancel, deadline, policy):
            result = subprocess_call(command, payload, cancel, deadline, policy)
            if payload["source"] == "operations":
                ready.set()
                self.assertTrue(release.wait(3))
            return result
        self.engine.runner = held
        execution = self.start()
        self.assertTrue(ready.wait(4))
        receipt = self.plan_receipt(execution)
        self.plans.revoke("coverage", receipt["declaration_digest"])
        release.set()
        done = self.terminal(execution)
        self.assertEqual(done["state"], "failed")
        self.assertEqual(done["error_code"], "COVERAGE_CHANGED")
        self.assertEqual(done["access_state"], "blocked")
        self.assertEqual(done["evidence"], [])
        exported = self.client.get(done["export_url"]).json()["data"]
        self.assertEqual(exported["execution"]["evidence"], [])

    def plan_receipt(self, execution):
        owner = self.client.cookies.get("demo_plan_session")
        with self.plans.transaction() as con:
            return self.plans._plan(con, owner, execution["confirmed_plan_id"])["card"]["coverage_receipt"]

    def test_revoked_session_blocks_read_export_and_cancel(self):
        done = self.terminal(self.start())
        self.plans.revoke("session", self.client.cookies.get("demo_plan_session"))
        path = PREFIX + "/executions/" + done["execution_id"]
        for response in (self.client.get(path), self.client.get(path + "/export"), self.client.post(path + "/cancel", json={})):
            self.assertEqual(response.status_code, 403)

    def test_restart_marks_pending_ledger_interrupted_without_replay(self):
        done = self.terminal(self.start())
        owner = self.client.cookies.get("demo_plan_session")
        # Emulate the committed running record left by a dead process. This is
        # recovery testing, not a claim of killing an actual API host.
        with self.plans.transaction() as con:
            row = con.execute("SELECT * FROM executions WHERE id=?", (done["execution_id"],)).fetchone()
            doc = self.engine._decode(row)
            doc.update(state="running", finished_at=None, execution_complete=False)
            doc["calls"][1].update(state="running")
            doc["calls"][1]["attempts"][-1].update(state="running", finished_at=None)
            self.engine._store(con, doc, owner)
        restarted = ExecutionService(PlanService(self.db, BATCH, SCHEMA))
        view = restarted.read(owner, done["execution_id"])
        self.assertEqual(view["state"], "interrupted")
        self.assertEqual(view["error_code"], "SERVER_RESTARTED")
        self.assertIsNone(view["calls"][1]["attempts"][-1]["cleanup_confirmed"])
        self.assertEqual(restarted.active, {})

    def test_terminal_storage_failure_never_reports_persisted_complete(self):
        real_store = self.engine._store
        def fail(con, doc, owner):
            if doc["state"] in ("completed", "failed"):
                raise sqlite3.OperationalError("private path must not escape")
            return real_store(con, doc, owner)
        with patch.object(self.engine, "_store", fail):
            done = self.terminal(self.start())
            self.assertFalse(done["state_persisted"])
            self.assertEqual(done["state"], "failed")
            self.assertFalse(done["execution_complete"])
            self.assertNotIn("private path", json.dumps(done))
        restarted = ExecutionService(PlanService(self.db, BATCH, SCHEMA))
        self.assertEqual(restarted.read(self.client.cookies.get("demo_plan_session"), done["execution_id"])["state"], "interrupted")

    def test_stored_ledger_tamper_blocks_read_and_export(self):
        done = self.terminal(self.start())
        with self.plans.transaction() as con:
            con.execute("UPDATE executions SET document=? WHERE id=?", (canonical({"state": "completed"}), done["execution_id"]))
        for suffix in ("", "/export"):
            response = self.client.get(PREFIX + "/executions/" + done["execution_id"] + suffix)
            self.assertEqual(response.status_code, 409)
            self.assertEqual(response.json()["error"]["code"], "PLAN_EXECUTION_INTEGRITY")


if __name__ == "__main__":
    unittest.main()
