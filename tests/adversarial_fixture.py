"""Isolated fictional fixtures; never modify the shipped source batch."""

import csv
import hashlib
import json
import shutil
from pathlib import Path

from mdlab_app.ingestion import load_snapshot

ROOT = Path(__file__).resolve().parents[1]


def build_fixture(folder, count=30, *, injected_value=None, temporal=False):
    folder = Path(folder)
    batch = folder / "batch"
    shutil.copytree(ROOT / "data/batches/demo-v1", batch)
    path = batch / "business/operations.csv"
    with path.open(encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(stream)
        fields, rows = reader.fieldnames, list(reader)
    # Original identities remain for all test-result foreign keys; append untested operations.
    original = sum(r["lot_id"] == "LOT-0001" for r in rows)
    for index in range(count - original):
        rows.append({"operation_id": f"EXTRA-{index:04d}", "lot_id": "LOT-0001", "process_step": "TEST_A",
                     "equipment_id": "MACHINE-A1", "track_in_at": "2026-09-05T00:00:00Z",
                     "track_out_at": "2026-09-05T01:00:00Z", "recorded_at": "2026-09-05T01:00:01Z"})
    if temporal:
        rows[-1].update(track_in_at="2026-09-05T00:00:00Z", track_out_at="2026-09-10T00:00:00Z",
                        recorded_at="2026-09-05T00:00:01Z")
    if injected_value:
        equipment_path = batch / "business/equipment.csv"
        with equipment_path.open(encoding="utf-8", newline="") as stream:
            reader = csv.DictReader(stream)
            equipment_fields, equipment_rows = reader.fieldnames, list(reader)
        equipment_rows.append({"equipment_id": injected_value, "equipment_name": "Fictional test-only equipment"})
        with equipment_path.open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, equipment_fields, lineterminator="\n")
            writer.writeheader(); writer.writerows(equipment_rows)
        rows[-1]["equipment_id"] = injected_value
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fields, lineterminator="\n")
        writer.writeheader(); writer.writerows(rows)
    manifest_path = batch / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["batch_id"] = "isolated-adversarial-fixture"
    manifest["counts"]["operations"] = len(rows)
    if injected_value:
        manifest["counts"]["equipment"] += 1
    for name in manifest["files_sha256"]:
        manifest["files_sha256"][name] = hashlib.sha256((batch / name).read_bytes()).hexdigest()
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    snapshot = load_snapshot(batch)
    coverage = json.loads((ROOT / "contracts/demo-closed-world.json").read_text(encoding="utf-8"))
    coverage.update(data_digest=snapshot.data_digest, manifest_digest=snapshot.manifest_digest,
                    inventory={r["lot_id"]: sorted(op["operation_id"] for op in snapshot.tables["operations"]
                               if op["lot_id"] == r["lot_id"]) for r in snapshot.tables["lots"]})
    coverage_path = folder / "coverage.json"
    coverage_path.write_text(json.dumps(coverage, ensure_ascii=False, indent=2), encoding="utf-8")
    return batch, coverage_path
