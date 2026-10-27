"""Published business input only. No imports from generator or evaluation."""

import csv
import hashlib
import io
import json
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from .errors import ProcessingError, utc_timestamp

PROJECT = Path(__file__).resolve().parents[2]


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def require(condition, message, details=None, code="INVALID_BATCH"):
    if not condition:
        raise ProcessingError(code, message, details)


@dataclass
class Snapshot:
    manifest: dict
    schema: dict
    tables: dict
    deliveries: list
    data_digest: str
    manifest_digest: str


def _field_value(field, value, csv_input=False):
    name, kind = field["name"], field["type"]
    if csv_input:
        if value == "":
            value = None
        elif kind == "integer":
            try:
                value = int(value)
            except (ValueError, TypeError) as exc:
                raise ProcessingError("INVALID_BATCH", "Invalid integer", name) from exc
        elif kind == "boolean":
            require(value in ("true", "false"), "Invalid boolean", name)
            value = value == "true"
    if value is None:
        require(field["nullable"], "Required field is null", name)
        return None
    expected = int if kind == "integer" else bool if kind == "boolean" else str
    require(type(value) is expected, "Wrong field type", name)
    if kind == "timestamp_utc":
        utc_timestamp(value)
    if "minimum" in field:
        require(value >= field["minimum"], "Value below minimum", name)
    if "enum" in field:
        require(value in field["enum"], "Unknown enum value", name)
    return value


def _result_links(row, units, ops, binds):
    uid, oid = row["unit_id"], row["operation_id"]
    require(uid in units and oid in ops, "Unknown result unit or operation", row["test_record_id"])
    op = ops[oid]
    require(units[uid]["lot_id"] == op["lot_id"] and row["equipment_id"] == op["equipment_id"]
            and row["test_type"] == op["process_step"], "Result/operation identity mismatch", row["test_record_id"])
    require(op["track_in_at"] <= row["started_at"] <= row["completed_at"] <= row["recorded_at"]
            and (not op["track_out_at"] or row["completed_at"] <= op["track_out_at"]), "Invalid result timeline", row["test_record_id"])
    if row["binding_id"]:
        binding = binds.get(row["binding_id"])
        require(binding is not None, "Unknown binding", row["binding_id"])
        require(binding["equipment_id"] == row["equipment_id"] and binding["valid_from"] <= row["started_at"]
                and (not binding["valid_to"] or row["completed_at"] < binding["valid_to"])
                and binding["recorded_at"] <= row["recorded_at"], "Binding does not cover result", row["test_record_id"])


def _test_history(tables, units, ops, binds):
    by_test = defaultdict(list)
    attempt_keys = {}
    for row in tables["test_results"]:
        _result_links(row, units, ops, binds)
        key = (row["unit_id"], row["test_type"], row["attempt_no"])
        require(key not in attempt_keys or attempt_keys[key] == row["test_id"], "Ambiguous test attempt", key)
        attempt_keys[key] = row["test_id"]
        by_test[row["test_id"]].append(row)
    for tid, rows in by_test.items():
        rows.sort(key=lambda r: r["version"])
        require([r["version"] for r in rows] == list(range(1, len(rows)+1)), "Non-contiguous versions", tid)
        require(len({(r["unit_id"], r["test_type"], r["attempt_no"]) for r in rows}) == 1,
                "Correction changes test identity", tid)
        require(all(a["recorded_at"] < b["recorded_at"] for a, b in zip(rows, rows[1:])), "Correction time is not increasing", tid)


def _binding_intervals(bindings):
    for index, left in enumerate(bindings):
        require(not left["valid_to"] or left["valid_from"] < left["valid_to"], "Invalid binding interval", left["binding_id"])
        for right in bindings[index+1:]:
            shared = (left["fixture_id"] == right["fixture_id"] or
                      (left["equipment_id"], left["position_id"]) == (right["equipment_id"], right["position_id"]))
            overlap = left["valid_from"] < (right["valid_to"] or "9999") and right["valid_from"] < (left["valid_to"] or "9999")
            require(not (shared and overlap), "Conflicting fixture binding intervals", [left["binding_id"], right["binding_id"]])


def _relations(tables):
    units = {r["unit_id"]: r for r in tables["units"]}
    lots = {r["lot_id"]: r for r in tables["lots"]}
    ops = {r["operation_id"]: r for r in tables["operations"]}
    binds = {r["binding_id"]: r for r in tables["fixture_bindings"]}
    for lot in lots.values():
        require(sum(u["lot_id"] == lot["lot_id"] for u in units.values()) == lot["initial_quantity"],
                "LOT quantity does not match units", lot["lot_id"])
    for op in ops.values():
        require(not op["track_out_at"] or op["track_in_at"] <= op["track_out_at"], "Invalid operation interval", op["operation_id"])
    _test_history(tables, units, ops, binds)
    _binding_intervals(list(binds.values()))


def _published_files(root, schema):
    """Verify publication metadata before trusting any business rows."""
    manifest_raw = (root / "manifest.json").read_bytes()
    manifest = json.loads(manifest_raw.decode("utf-8"))
    require(isinstance(manifest, dict), "Manifest must be an object")
    require(manifest.get("status") == "complete", "Manifest is not complete")
    require(isinstance(manifest.get("batch_id"), str) and manifest["batch_id"], "Missing batch ID")
    expected = {"business/"+t["file"] for t in schema["tables"].values()} | {"raw/test_result_deliveries.jsonl"}
    counts = manifest.get("counts")
    require(type(counts) is dict and set(counts) == set(schema["tables"]) | {"deliveries"}, "Incomplete counts metadata")
    require(all(type(v) is int and v >= 0 for v in counts.values()), "Counts must be nonnegative integers")
    hashes = manifest.get("files_sha256")
    require(type(hashes) is dict and set(hashes) == expected, "Incomplete file digest registry")
    actual = {p.relative_to(root).as_posix() for sub in ("business", "raw") for p in (root / sub).iterdir()}
    require(actual == expected, "Business/raw file set differs", {"missing": sorted(expected-actual), "extra": sorted(actual-expected)})
    files = {rel: (root / rel).read_bytes() for rel in sorted(expected)}
    require(all(digest(raw) == hashes[rel] for rel, raw in files.items()), "File digest mismatch")
    return manifest_raw, manifest, files


def _csv_table(name, definition, raw, count):
    require(not raw.startswith(b"\xef\xbb\xbf") and b"\r" not in raw, "CSV must be UTF-8/LF without BOM", name)
    reader = csv.DictReader(io.StringIO(raw.decode("utf-8"), newline=""))
    fields = definition["fields"]
    require(reader.fieldnames == [f["name"] for f in fields], "CSV columns differ", name)
    rows = []
    for row in reader:
        require(set(row) == set(reader.fieldnames) and None not in row.values(), "Malformed CSV row", name)
        rows.append({f["name"]: _field_value(f, row[f["name"]], True) for f in fields})
    require(len(rows) == count, "Table count mismatch", name)
    for keys in [definition["primary_key"], *definition["unique_keys"]]:
        identities = [tuple(r[k] for k in keys) for r in rows]
        require(len(identities) == len(set(identities)), "Duplicate key", {"table": name, "keys": keys})
    return rows


def _delivery_stream(raw, result_fields, results, count):
    deliveries = []
    canonical_results = {r["test_record_id"]: r for r in results}
    delivered = {}; delivery_ids = set(); first_at = {}
    for line in raw.decode("utf-8").splitlines():
        require(bool(line.strip()), "Empty delivery line")
        item = json.loads(line)
        require(type(item) is dict and set(item) == {"delivery_id", "received_at", "test_record_id", "payload"}, "Malformed delivery")
        require(type(item["delivery_id"]) is str and item["delivery_id"] and item["delivery_id"] not in delivery_ids, "Duplicate/invalid delivery ID")
        delivery_ids.add(item["delivery_id"])
        utc_timestamp(item["received_at"])
        payload = item["payload"]
        require(type(payload) is dict and set(payload) == {f["name"] for f in result_fields}, "Malformed delivery payload")
        payload = {f["name"]: _field_value(f, payload[f["name"]]) for f in result_fields}
        rid = item["test_record_id"]
        require(type(rid) is str and rid == payload["test_record_id"], "Delivery record ID differs")
        require(rid not in delivered or delivered[rid] == payload, "Retry payload conflicts with original", rid, "CONFLICTING_RETRY")
        require(canonical_results.get(rid) == payload, "Delivery payload differs from business record", rid)
        delivered[rid] = payload
        first_at[rid] = min(first_at.get(rid, item["received_at"]), item["received_at"])
        deliveries.append(item | {"payload": payload})
    require(len(deliveries) == count and set(delivered) == set(canonical_results), "Delivery count/coverage mismatch")
    require(all(first_at[rid] == row["recorded_at"] for rid, row in canonical_results.items()), "First delivery differs from recorded_at")
    return deliveries


def load_snapshot(batch, schema_path=None):
    """Read each payload once, verify those exact bytes, then parse the snapshot."""
    root = Path(batch).resolve()
    require(root.is_dir(), "Batch directory does not exist", str(root))
    require(not (root / ".incomplete").exists(), "Batch is not published")
    schema_raw = Path(schema_path or PROJECT / "schemas/csv-schema.json").read_bytes()
    schema = json.loads(schema_raw)
    try:
        manifest_raw, manifest, files = _published_files(root, schema)
        tables = {name: _csv_table(name, definition, files["business/"+definition["file"]], manifest["counts"][name])
                  for name, definition in schema["tables"].items()}
        deliveries = _delivery_stream(files["raw/test_result_deliveries.jsonl"], schema["tables"]["test_results"]["fields"],
                                      tables["test_results"], manifest["counts"]["deliveries"])
        _relations(tables)
        identity = {"batch_id": manifest["batch_id"], "files": manifest["files_sha256"], "schema_sha256": digest(schema_raw)}
        return Snapshot(manifest, schema, tables, sorted(deliveries, key=lambda d: (d["received_at"], d["delivery_id"])),
                        digest(canonical(identity).encode("utf-8")), digest(manifest_raw))
    except ProcessingError:
        raise
    except (OSError, UnicodeError, ValueError, TypeError, KeyError, csv.Error) as exc:
        raise ProcessingError("INVALID_BATCH", "Cannot read a complete business snapshot", str(exc)) from exc
