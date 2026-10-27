"""Regression for the live evaluation harness; these tests never use a provider."""

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("model_evaluation", ROOT / "scripts/evaluate_model.py")
evaluation = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(evaluation)


class EvaluationTests(unittest.TestCase):
    def test_error_details_can_be_non_objects_without_crashing_summary(self):
        for details in (None, "invalid time", [], True, 12):
            self.assertIsNone(evaluation.model_receipt({"error": {"details": details}}))

    def test_receipt_is_collected_from_success_or_structured_failure(self):
        receipt = {"usage": {"total_tokens": 17}}
        self.assertEqual(evaluation.model_receipt({"data": {"model_call": receipt}}), receipt)
        self.assertEqual(evaluation.model_receipt({"error": {"details": {"model_call": receipt}}}), receipt)

    def test_summary_counts_provider_usage_once_despite_repeated_provenance(self):
        response = {"data": {"model_call": {"usage": {"total_tokens": 17}}}}
        record = {"case": {"id": "synthetic"}, "passed": True, "published": True, "observed": "ready",
                  "steps": [{"path": "/nl/proposals", "response": response},
                            {"path": "/repeated-provenance", "response": response}]}
        with patch.object(evaluation, "source_hashes", return_value={}):
            summary = evaluation.summarize([record], [1], {}, True)
        self.assertEqual(summary["returned_total_tokens"], 17)
        self.assertEqual(summary["model_requests"], 1)
        self.assertEqual(summary["published"], 1)

    def test_csv_oracle_distinguishes_zero_from_exact_record_visibility_boundary(self):
        self.assertEqual(evaluation.expected_rows("LOT-0001", "2026-09-01T05:09:59Z"), [])
        rows = evaluation.expected_rows("LOT-0001", "2026-09-01T05:10:00Z")
        self.assertEqual([row["operation_id"] for row in rows], ["OP-0001"])

    def test_harness_reaches_actual_export_and_complete_detail_endpoint(self):
        with tempfile.TemporaryDirectory() as folder:
            db = Path(folder) / "fixture.sqlite3"
            batch, schema = ROOT / "data/batches/demo-v1", ROOT / "schemas/csv-schema.json"
            evaluation.ingest(batch, db, schema)
            app = evaluation.create_app(db, batch, schema, model_config=evaluation.ModelConfig(enabled=True, api_key="test-only"))
            sent = []

            def fake(config, body):
                sent.append(body)
                return json.dumps({"model": "deepseek-flash", "choices": [{"finish_reason": "stop", "message": {
                    "role": "assistant", "content": json.dumps({"intent_key": "lot_operation_inventory",
                    "lot_id": "LOT-0001", "as_of": "2026-09-07T00:00:00Z"})}}],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}}).encode()

            app.state.planner_service.transport = fake
            case = {"id": "synthetic", "expected": "publish", "lot_id": "LOT-0001", "as_of": "2026-09-07T00:00:00Z",
                    "question": "列出 LOT-0001 截至 2026-09-07T00:00:00Z 的全部作业。"}
            record = {"steps": []}
            with evaluation.TestClient(app) as client:
                evaluation.evaluate_case(client, case, record, sent)
            self.assertTrue(record["published"])
            self.assertEqual(record["operation_count"], 4)
            self.assertIn("/members?publication_digest=", record["steps"][-1]["path"])


if __name__ == "__main__":
    unittest.main()
