"""One immutable business snapshot per SQLite database, with append-only decisions."""

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from .errors import ProcessingError
from .ingestion import canonical, digest, load_snapshot

DB_VERSION = 1

# Persistence supports the application's twelve-table layout, not arbitrary SQL
# identifiers from an alternate CSV contract. Values (including hostile text)
# are always passed separately. Layout changes require an explicit migration.
INSERT_STATEMENTS = {
    "products": "INSERT INTO products (product_id,product_name,product_revision) VALUES (?,?,?)",
    "equipment": "INSERT INTO equipment (equipment_id,equipment_name) VALUES (?,?)",
    "fixtures": "INSERT INTO fixtures (fixture_id,fixture_type) VALUES (?,?)",
    "lots": "INSERT INTO lots (lot_id,product_id,initial_quantity,created_at) VALUES (?,?,?,?)",
    "units": "INSERT INTO units (unit_id,lot_id,created_at) VALUES (?,?,?)",
    "fixture_bindings": "INSERT INTO fixture_bindings (binding_id,fixture_id,equipment_id,position_id,valid_from,valid_to,recorded_at) VALUES (?,?,?,?,?,?,?)",
    "operations": "INSERT INTO operations (operation_id,lot_id,process_step,equipment_id,track_in_at,track_out_at,recorded_at) VALUES (?,?,?,?,?,?,?)",
    "test_requirements": "INSERT INTO test_requirements (requirement_id,product_id,test_type,required,effective_from,effective_to,max_age_seconds,recorded_at) VALUES (?,?,?,?,?,?,?,?)",
    "test_results": "INSERT INTO test_results (test_record_id,test_id,version,unit_id,test_type,attempt_no,operation_id,equipment_id,binding_id,started_at,completed_at,outcome,recorded_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
    "sn_assignments": "INSERT INTO sn_assignments (assignment_id,unit_id,sn,assigned_at,recorded_at) VALUES (?,?,?,?,?)",
    "packaging_checks": "INSERT INTO packaging_checks (check_id,unit_id,requested_sn,check_at) VALUES (?,?,?,?)",
    "alarms": "INSERT INTO alarms (alarm_id,equipment_id,scope,operation_id,binding_id,unit_id,alarm_code,started_at,ended_at,recorded_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
}


def _insert_statement(name, columns):
    sql = INSERT_STATEMENTS.get(name)
    expected = sql.partition("(")[2].partition(")")[0].split(",") if sql else []
    if not sql or list(columns) != expected:
        raise ProcessingError("INVALID_BATCH", "Unsupported database table layout")
    return sql


def _validate_layout(schema):
    if set(schema["tables"]) != set(INSERT_STATEMENTS):
        raise ProcessingError("INVALID_BATCH", "Unsupported database table set")
    for name, definition in schema["tables"].items():
        _insert_statement(name, [field["name"] for field in definition["fields"]])


def quote(name):
    return '"'+name.replace('"', '""')+'"'


@contextmanager
def connection(path, writable=False):
    resolved = Path(path).resolve()
    if not resolved.is_file():
        raise ProcessingError("NOT_INGESTED", "Database does not exist", str(resolved))
    uri = resolved.as_uri() + ("?mode=rw" if writable else "?mode=ro")
    con = sqlite3.connect(uri, uri=True, timeout=5)
    try:
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys=ON")
        if con.execute("PRAGMA user_version").fetchone()[0] != DB_VERSION:
            raise ProcessingError("INVALID_DATABASE", "Unsupported or incomplete database")
        if con.execute("SELECT COUNT(*) FROM ingest_meta").fetchone()[0] != 1:
            raise ProcessingError("INVALID_DATABASE", "No committed input snapshot")
        yield con
    finally:
        con.close()


def _create_tables(con, schema):
    con.execute("CREATE TABLE ingest_meta (singleton INTEGER PRIMARY KEY CHECK(singleton=1), batch_id TEXT NOT NULL, data_digest TEXT NOT NULL, manifest_digest TEXT NOT NULL, imported_at TEXT NOT NULL)")
    for name, definition in schema["tables"].items():
        cols = []
        for field in definition["fields"]:
            kind = "INTEGER" if field["type"] in ("integer", "boolean") else "TEXT"
            cols.append(quote(field["name"])+" "+kind+(" NOT NULL" if not field["nullable"] else ""))
        cols.append("PRIMARY KEY ("+", ".join(map(quote, definition["primary_key"]))+")")
        for keys in definition["unique_keys"]:
            cols.append("UNIQUE ("+", ".join(map(quote, keys))+")")
        for fk in definition["foreign_keys"]:
            table, col = fk["references"].split(".")
            cols.append("FOREIGN KEY ("+quote(fk["column"])+") REFERENCES "+quote(table)+"("+quote(col)+")")
        con.execute("CREATE TABLE "+quote(name)+" ("+", ".join(cols)+")")
    con.execute("CREATE TABLE delivery_receipts (delivery_id TEXT PRIMARY KEY, received_at TEXT NOT NULL, test_record_id TEXT NOT NULL REFERENCES test_results(test_record_id), payload_sha256 TEXT NOT NULL, payload_json TEXT NOT NULL, disposition TEXT NOT NULL CHECK(disposition IN ('first','duplicate')))")
    con.execute("CREATE TABLE packaging_decisions (decision_id TEXT PRIMARY KEY, check_id TEXT NOT NULL UNIQUE REFERENCES packaging_checks(check_id), policy_version TEXT NOT NULL, decision TEXT NOT NULL, result_json TEXT NOT NULL, evaluated_at TEXT NOT NULL)")
    con.execute("CREATE INDEX results_visible ON test_results(unit_id,test_type,recorded_at)")
    con.execute("CREATE INDEX results_operation ON test_results(operation_id)")
    con.execute("CREATE INDEX sn_lookup ON sn_assignments(sn,recorded_at)")
    con.execute("CREATE INDEX operations_timeline ON operations(equipment_id,process_step,track_out_at,operation_id)")


def _insert_rows(con, name, rows, columns):
    sql = _insert_statement(name, columns)
    con.executemany(sql, [[row[k] for k in columns] for row in rows])


def ingest(batch, db, schema_path=None):
    snapshot = load_snapshot(batch, schema_path)
    _validate_layout(snapshot.schema)
    path = Path(db).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path, timeout=5)
    try:
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys=ON")
        con.execute("BEGIN IMMEDIATE")
        tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        version = con.execute("PRAGMA user_version").fetchone()[0]
        if tables:
            if version != DB_VERSION or "ingest_meta" not in tables:
                raise ProcessingError("INVALID_DATABASE", "Refusing to modify an unrelated database")
            meta = con.execute("SELECT * FROM ingest_meta WHERE singleton=1").fetchone()
            if not meta or meta["batch_id"] != snapshot.manifest["batch_id"] or meta["data_digest"] != snapshot.data_digest:
                raise ProcessingError("SNAPSHOT_CONFLICT", "Database is bound to different input; use a new database")
            con.commit()
            return {"batch_id": meta["batch_id"], "data_digest": meta["data_digest"], "reused": True, **_counts(con)}
        _create_tables(con, snapshot.schema)
        for name, definition in snapshot.schema["tables"].items():
            if name != "test_results":
                _insert_rows(con, name, snapshot.tables[name], [f["name"] for f in definition["fields"]])
        seen = set()
        columns = [f["name"] for f in snapshot.schema["tables"]["test_results"]["fields"]]
        for delivery in snapshot.deliveries:
            rid = delivery["test_record_id"]
            disposition = "duplicate" if rid in seen else "first"
            if rid not in seen:
                _insert_rows(con, "test_results", [delivery["payload"]], columns)
                seen.add(rid)
            payload_json = canonical(delivery["payload"])
            con.execute("INSERT INTO delivery_receipts VALUES (?,?,?,?,?,?)", (delivery["delivery_id"], delivery["received_at"], rid,
                        digest(payload_json.encode("utf-8")), payload_json, disposition))
        con.execute("INSERT INTO ingest_meta VALUES (1,?,?,?,?)", (snapshot.manifest["batch_id"], snapshot.data_digest,
                    snapshot.manifest_digest, datetime.now(timezone.utc).isoformat()))
        con.execute("PRAGMA user_version=1")
        con.commit()
        return {"batch_id": snapshot.manifest["batch_id"], "data_digest": snapshot.data_digest, "reused": False, **_counts(con)}
    except BaseException:
        con.rollback()
        raise
    finally:
        con.close()


def _counts(con):
    return {"test_record_versions": con.execute("SELECT COUNT(*) FROM test_results").fetchone()[0],
            "test_attempts": con.execute("SELECT COUNT(DISTINCT test_id) FROM test_results").fetchone()[0],
            "delivery_receipts": con.execute("SELECT COUNT(*) FROM delivery_receipts").fetchone()[0],
            "duplicate_deliveries": con.execute("SELECT COUNT(*) FROM delivery_receipts WHERE disposition='duplicate'").fetchone()[0],
            "packaging_decisions": con.execute("SELECT COUNT(*) FROM packaging_decisions").fetchone()[0]}


def stats(con):
    meta = dict(con.execute("SELECT * FROM ingest_meta WHERE singleton=1").fetchone())
    return {"source": meta, **_counts(con),
            "lots": con.execute("SELECT COUNT(*) FROM lots").fetchone()[0],
            "units": con.execute("SELECT COUNT(*) FROM units").fetchone()[0]}
