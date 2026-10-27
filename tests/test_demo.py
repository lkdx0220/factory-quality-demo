"""Portable demo tests over fictional records."""
import sys
import tempfile
import unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"src"))
from mdlab_app.storage import ingest,connection,stats
from mdlab_app.packaging import evaluate_and_record
from mdlab_app.queries import trace_unit,trace_lookup,neighbors
from mdlab_app.errors import ProcessingError

class DemoTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix="quality-demo-")
        self.addCleanup(self.temp.cleanup)
        self.db=Path(self.temp.name)/"demo.sqlite3"
        self.result=ingest(ROOT/"data/batches/demo-v1",self.db)

    def test_ingestion_and_replay(self):
        self.assertEqual((self.result["delivery_receipts"],self.result["test_record_versions"],self.result["duplicate_deliveries"]),(113,107,6))
        self.assertTrue(ingest(ROOT/"data/batches/demo-v1",self.db)["reused"])

    def test_failed_expired_result_retains_both_facts(self):
        with connection(self.db,writable=True) as con:
            result=evaluate_and_record(con,"CHK-0020")
            self.assertEqual(result["decision"],"HOLD")
            self.assertEqual(set(result["reason_codes"]),{"CONFIRMED_FAIL:TEST_B","EXPIRED_RESULT:TEST_B"})
            self.assertTrue(evaluate_and_record(con,"CHK-0020")["decision_event"]["reused"])
            self.assertEqual(stats(con)["packaging_decisions"],1)

    def test_historical_visibility(self):
        with connection(self.db) as con:
            trace=trace_unit(con,"U-00001","2026-09-01T06:00:00Z")
            self.assertEqual(trace["operation_ids"],["OP-0001"])
            self.assertTrue(all(r["test_type"]=="TEST_A" for r in trace["tests"]))

    def test_identifier_conflict(self):
        with connection(self.db) as con:
            with self.assertRaises(ProcessingError) as caught:
                trace_lookup(con,"2026-09-07T00:00:00Z",sn="SYN-00028")
            self.assertEqual(caught.exception.code,"AMBIGUOUS_SN")

    def test_operation_scoped_counts(self):
        with connection(self.db) as con:
            result=neighbors(con,"OP-0003","2026-09-02T00:00:00Z",2,2)
            self.assertEqual(result["equipment_id"],"MACHINE-A1")
            self.assertEqual((result["anchor_stats"]["final_pass_count"],result["anchor_stats"]["fail_count"]),(6,4))
            self.assertEqual((result["after"][0]["final_pass_count"],result["after"][0]["missing_count"]),(4,6))

if __name__=="__main__":
    unittest.main()
