"""Fixed persistence statements reject identifier changes and bind hostile text."""

import copy
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from mdlab_app.errors import ProcessingError
from mdlab_app.ingestion import load_snapshot
from mdlab_app.storage import _insert_rows, ingest


class StorageBoundaryTests(unittest.TestCase):
    def test_text_is_bound_without_becoming_sql(self):
        with closing(sqlite3.connect(":memory:")) as con:
            con.execute("CREATE TABLE products (product_id TEXT, product_name TEXT, product_revision TEXT)")
            value = "demo'); DROP TABLE products; --"
            _insert_rows(con, "products", [{"product_id": "P1", "product_name": value, "product_revision": "1"}],
                         ["product_id", "product_name", "product_revision"])
            self.assertEqual(con.execute("SELECT product_name FROM products").fetchall(), [(value,)])

    def test_unknown_table_column_and_order_fail_before_execute(self):
        for name, columns in (
            ("products; DROP TABLE products", ["product_id", "product_name", "product_revision"]),
            ("products", ["product_id", "product_name", "product_revision", "extra"]),
            ("products", ["product_name", "product_id", "product_revision"]),
        ):
            with self.subTest(name=name, columns=columns):
                con = Mock()
                with self.assertRaises(ProcessingError) as caught:
                    _insert_rows(con, name, [], columns)
                self.assertEqual(caught.exception.code, "INVALID_BATCH")
                con.executemany.assert_not_called()

    def test_unsupported_schema_rejected_before_database_creation(self):
        snapshot = load_snapshot(ROOT / "data/batches/demo-v1")
        for mutation in ("table", "column"):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as folder:
                changed = copy.deepcopy(snapshot)
                if mutation == "table":
                    changed.schema["tables"]["extra"] = changed.schema["tables"]["products"]
                else:
                    changed.schema["tables"]["products"]["fields"][0]["name"] = "other"
                target = Path(folder) / "nested" / "demo.sqlite3"
                with patch("mdlab_app.storage.load_snapshot", return_value=changed):
                    with self.assertRaises(ProcessingError) as caught:
                        ingest(ROOT / "data/batches/demo-v1", target)
                self.assertEqual(caught.exception.code, "INVALID_BATCH")
                self.assertFalse(target.parent.exists())
