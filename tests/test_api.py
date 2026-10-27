"""HTTP contract tests, using only the generic fictional demonstration fixture."""

import json
import shutil
import sqlite3
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from mdlab_app.api import create_app  # noqa: E402
from mdlab_app.storage import connection, ingest  # noqa: E402


def fixture():
    direct = ROOT / "data/batches/demo-v1"
    if direct.is_dir():
        return direct
    return next(iter(sorted(ROOT.glob("exports/*/data/batches/demo-v1"))))


class ApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.master = tempfile.TemporaryDirectory(prefix="quality-api-master-")
        cls.prototype = Path(cls.master.name) / "prototype.sqlite3"
        cls.batch = fixture()
        cls.schema = cls.batch.parents[2] / "schemas/csv-schema.json"
        ingest(cls.batch, cls.prototype, cls.schema)

    @classmethod
    def tearDownClass(cls):
        cls.master.cleanup()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="quality-api-case-")
        self.addCleanup(self.temp.cleanup)
        self.work = Path(self.temp.name)
        self.db = self.work / "demo.sqlite3"
        shutil.copyfile(self.prototype, self.db)
        self.app = create_app(self.db, self.batch, self.schema)
        self.client = TestClient(self.app, raise_server_exceptions=False)
        self.addCleanup(self.client.close)

    def get_data(self, path, params=None):
        response = self.client.get(path, params=params)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()["ok"])
        self.assertTrue(response.json()["synthetic"])
        return response.json()["data"]

    def test_health_openapi_and_documentation(self):
        self.assertEqual(self.get_data("/api/v1/health")["status"], "running")
        spec = self.client.get("/openapi.json").json()
        self.assertIn("/api/v1/checks/{check_id}/evaluate", spec["paths"])
        self.assertIn("422", spec["paths"]["/api/v1/trace"]["get"]["responses"])
        self.assertEqual(self.client.get("/docs").status_code, 200)

    def test_missing_database_has_structured_error_and_no_file_creation(self):
        missing = self.work / "absent.sqlite3"
        with TestClient(create_app(missing, self.batch, self.schema)) as client:
            self.assertFalse(client.get("/api/v1/health").json()["data"]["database_exists"])
            r = client.get("/api/v1/stats")
            self.assertEqual(r.status_code, 503)
            self.assertEqual(r.json()["error"]["code"], "NOT_INGESTED")
            self.assertNotIn(str(missing), r.text)
        self.assertFalse(missing.exists())

    def test_fixed_ingest_replays_without_adding_rows(self):
        before = self.db.read_bytes()
        for _ in range(2):
            r = self.client.post("/api/v1/ingest", json={})
            self.assertEqual(r.status_code, 200, r.text)
            self.assertTrue(r.json()["data"]["reused"])
            self.assertEqual(r.json()["data"]["delivery_receipts"], 113)
        self.assertEqual(self.db.read_bytes(), before)
        r = self.client.post("/api/v1/ingest", json={"batch": "/any/path"})
        self.assertEqual(r.status_code, 422)

    def test_missing_database_can_be_initialized_by_fixed_ingest(self):
        db = self.work / "new.sqlite3"
        with TestClient(create_app(db, self.batch, self.schema)) as client:
            r = client.post("/api/v1/ingest", json={})
            self.assertEqual(r.status_code, 200, r.text)
            self.assertFalse(r.json()["data"]["reused"])
            self.assertEqual(client.get("/api/v1/stats").json()["data"]["units"], 40)

    def test_browse_pagination_and_generic_vocabulary(self):
        first = self.get_data("/api/v1/units", {"lot_id": "LOT-0001", "limit": 3})
        second = self.get_data("/api/v1/units", {"lot_id": "LOT-0001", "limit": 3, "offset": first["next_offset"]})
        self.assertEqual(first["total"], 10)
        self.assertFalse({r["unit_id"] for r in first["items"]} & {r["unit_id"] for r in second["items"]})
        self.assertIsNone(self.get_data("/api/v1/units", {"offset": 1000})["next_offset"])
        self.assertEqual(self.get_data("/api/v1/lots")["total"], 4)
        equipment = self.get_data("/api/v1/equipment")["items"]
        self.assertTrue(all(row["equipment_id"].startswith("MACHINE-") for row in equipment))
        self.assertEqual(self.get_data("/api/v1/checks", {"unit_id": "U-00034"})["items"][0]["check_id"], "CHK-0020")

    def test_historical_trace_and_operation_discovery(self):
        time = "2026-09-01T06:00:00Z"
        result = self.get_data("/api/v1/trace", {"unit_id": "U-00001", "as_of": time})
        self.assertEqual(result["operation_ids"], ["OP-0001"])
        self.assertEqual(result["tests"][0]["test_type"], "TEST_A")
        ops = self.get_data("/api/v1/operations", {"lot_id": "LOT-0001", "as_of": time})
        self.assertEqual([r["operation_id"] for r in ops["items"]], ["OP-0001"])
        self.assertEqual(self.get_data("/api/v1/trace", {"lot_id": "LOT-0001", "as_of": "2026-09-07T00:00:00Z"})["unit_count"], 10)
        self.assertEqual(self.get_data("/api/v1/trace", {"sn": "SYN-00001", "as_of": "2026-09-07T00:00:00Z"})["unit_id"], "U-00001")

    def test_operation_filters_and_pagination_match_independent_row_selection(self):
        with connection(self.db) as con:
            all_rows = [dict(row) for row in con.execute("SELECT * FROM operations ORDER BY track_in_at,operation_id")]
        for lot in (None, "LOT-0001", "UNKNOWN"):
            for equipment in (None, "MACHINE-A1"):
                for at in (None, "2026-09-01T06:00:00Z", "2026-09-07T00:00:00Z"):
                    query = {k: v for k, v in (("lot_id", lot), ("equipment_id", equipment), ("as_of", at)) if v is not None}
                    expected = [r for r in all_rows if (lot is None or r["lot_id"] == lot)
                                and (equipment is None or r["equipment_id"] == equipment)
                                and (at is None or (r["recorded_at"] <= at and r["track_out_at"] is not None and r["track_out_at"] <= at))]
                    with self.subTest(query=query):
                        result = self.get_data("/api/v1/operations", {**query, "limit": 1, "offset": 1})
                        self.assertEqual(result["total"], len(expected))
                        self.assertEqual(result["items"], expected[1:2])
                        self.assertEqual(result["next_offset"], 2 if len(expected) > 2 else None)

    def test_preview_is_read_only_and_quality_failure_is_http_success(self):
        before = self.db.read_bytes()
        result = self.get_data("/api/v1/checks/CHK-0020/preview")
        self.assertEqual(result["decision"], "HOLD")
        self.assertEqual(set(result["reason_codes"]), {"CONFIRMED_FAIL:TEST_B", "EXPIRED_RESULT:TEST_B"})
        self.assertFalse(result["persisted"])
        self.assertEqual(self.db.read_bytes(), before)
        r = self.client.post("/api/v1/checks/CHK-0020/evaluate", json={})
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()["ok"])
        self.assertEqual(r.json()["data"]["decision"], "HOLD")

    def test_decision_history_and_idempotent_replay(self):
        first = self.client.post("/api/v1/packaging/evaluate-all", json={}).json()["data"]
        second = self.client.post("/api/v1/packaging/evaluate-all", json={}).json()["data"]
        self.assertEqual(first["total"], 22)
        self.assertTrue(all(d["decision_event"]["reused"] for d in second["decisions"]))
        self.assertEqual(self.get_data("/api/v1/stats")["packaging_decisions"], 22)
        holds = self.get_data("/api/v1/decisions", {"decision": "HOLD", "limit": 2})
        self.assertEqual(holds["total"], 4)
        self.assertEqual(holds["next_offset"], 2)
        self.assertIn("result", holds["items"][0])
        single = self.get_data("/api/v1/decisions", {"check_id": "CHK-0020"})
        self.assertEqual(single["total"], 1)

    def test_two_concurrent_requests_create_one_decision(self):
        def evaluate():
            with TestClient(self.app) as client:
                r = client.post("/api/v1/checks/CHK-0020/evaluate", json={})
                return r.status_code, r.json()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: evaluate(), range(2)))
        self.assertEqual([r[0] for r in results], [200, 200])
        ids = {r[1]["data"]["decision_event"]["decision_id"] for r in results}
        self.assertEqual(len(ids), 1)
        self.assertEqual(self.get_data("/api/v1/stats")["packaging_decisions"], 1)

    def test_neighbors_scoped_to_operation_and_analysis(self):
        n = self.get_data("/api/v1/operations/OP-0003/neighbors",
                          {"as_of": "2026-09-02T00:00:00Z", "before": 2, "after": 2})
        self.assertEqual((n["anchor_stats"]["final_pass_count"], n["anchor_stats"]["fail_count"]), (6, 4))
        self.assertEqual(n["after"][0]["missing_count"], 6)
        c = self.get_data("/api/v1/concentration", {"equipment_id": "MACHINE-A2", "start": "2026-09-02T00:00:00Z", "end": "2026-09-03T00:00:00Z"})
        self.assertEqual([(g["attempts"], g["fail_attempts"]) for g in c["groups"]], [(5, 3), (5, 0)])
        r = self.client.post("/api/v1/compare-groups", json={"as_of": "2026-09-07T00:00:00Z",
                             "groups": ["EQUIPMENT:MACHINE-A1", "BINDING:BND-0003"]})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual([g["fail_attempts"] for g in r.json()["data"]["groups"]], [4, 3])

    def test_conflict_and_not_found_are_distinct(self):
        r = self.client.get("/api/v1/trace", params={"sn": "SYN-00028", "as_of": "2026-09-07T00:00:00Z"})
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.json()["error"]["details"]["unit_ids"], ["U-00027", "U-00028"])
        r = self.client.get("/api/v1/checks/unknown/preview")
        self.assertEqual(r.status_code, 404)
        self.assertEqual(r.json()["error"]["code"], "CHECK_NOT_FOUND")
        r = self.client.get("/api/v1/operations/OP-0003/neighbors", params={"as_of": "2026-09-01T13:00:00Z"})
        self.assertEqual(r.status_code, 404)
        self.assertEqual(r.json()["error"]["code"], "ANCHOR_NOT_VISIBLE")

    def test_unknown_null_duplicate_and_out_of_range_query_fields_rejected(self):
        invalid = [
            ("/api/v1/lots", {"unexpected": 1}),
            ("/api/v1/lots", {"limit": 101}),
            ("/api/v1/lots", {"offset": -1}),
            ("/api/v1/health", {"unexpected": 1}),
            ("/api/v1/checks/CHK-0020/preview", {"unexpected": 1}),
            ("/api/v1/trace", {"unit_id": "U-00001"}),
            ("/api/v1/trace", {"unit_id": "U-00001", "sn": "SYN-00001", "as_of": "2026-09-07T00:00:00Z"}),
            ("/api/v1/trace", {"unit_id": "U-00001", "as_of": "null"}),
            ("/api/v1/operations/OP-0003/neighbors", {"as_of": "2026-09-07T00:00:00Z", "before": -1}),
        ]
        for path, query in invalid:
            with self.subTest(path=path, query=query):
                r = self.client.get(path, params=query)
                self.assertEqual(r.status_code, 422, r.text)
                self.assertEqual(r.json()["error"]["code"], "INVALID_ARGUMENT")
        self.assertEqual(self.client.get("/api/v1/lots?limit=1&limit=2").status_code, 422)

    def test_unknown_body_fields_and_malformed_json_rejected(self):
        for body in (None, [], {"unexpected": 1}):
            r = self.client.post("/api/v1/checks/CHK-0020/evaluate", content=json.dumps(body), headers={"Content-Type": "application/json"})
            self.assertEqual(r.status_code, 422)
        r = self.client.post("/api/v1/ingest", content="{", headers={"Content-Type": "application/json"})
        self.assertEqual(r.status_code, 422)
        self.assertEqual(self.get_data("/api/v1/stats")["packaging_decisions"], 0)

    def test_cross_origin_write_and_invalid_host_are_blocked(self):
        r = self.client.post("/api/v1/checks/CHK-0020/evaluate", json={}, headers={"Origin": "http://unrelated.invalid"})
        self.assertEqual(r.status_code, 403)
        self.assertEqual(self.client.get("/api/v1/health", headers={"Host": "unrelated.invalid"}).status_code, 400)
        self.assertEqual(self.get_data("/api/v1/stats")["packaging_decisions"], 0)
        r = self.client.post("/api/v1/checks/CHK-0020/evaluate", json={}, headers={"Origin": "http://testserver"})
        self.assertEqual(r.status_code, 200)

    def test_storage_errors_do_not_expose_local_paths(self):
        with patch("mdlab_app.api.stats", side_effect=sqlite3.OperationalError("private/location/database")):
            r = self.client.get("/api/v1/stats")
        self.assertEqual(r.status_code, 503)
        self.assertEqual(r.json()["error"]["code"], "STORAGE_ERROR")
        self.assertNotIn("private/location", r.text)
        self.assertNotIn("Traceback", r.text)
        with patch("mdlab_app.api.ingest", side_effect=OSError(str(self.batch))):
            r = self.client.post("/api/v1/ingest", json={})
        self.assertEqual(r.status_code, 503)
        self.assertNotIn(str(self.batch), r.text)

    def test_non_api_routes_and_methods_return_json_errors(self):
        self.assertEqual(self.client.get("/api/v1/missing").json()["error"]["code"], "NOT_FOUND")
        r = self.client.delete("/api/v1/lots")
        self.assertEqual(r.status_code, 405)
        self.assertFalse(r.json()["ok"])
        self.assertEqual(self.get_data("/api/v1/stats")["packaging_decisions"], 0)


if __name__ == "__main__":
    unittest.main()
