"""One bounded, read-only evidence call. Invoked only by the local executor."""

import json
import sys
from pathlib import Path

from .ingestion import canonical, load_snapshot
from .plan_service import inventory


def main():
    # The parent sends UTF-8 bytes. Never use the Windows console/pipe locale.
    command = json.loads(sys.stdin.buffer.read().decode("utf-8"))
    snapshot = load_snapshot(Path(command["batch"]), Path(command["schema"]) if command["schema"] else None)
    if (snapshot.data_digest != command["snapshot_digest"]
            or snapshot.manifest_digest != command["manifest_digest"]):
        raise ValueError("Snapshot identity changed")
    lot, as_of = command["scope"]["lot_id"], command["scope"]["as_of"]
    if command["source"] == "lots":
        rows = [row for row in snapshot.tables["lots"] if row["lot_id"] == lot and row["created_at"] <= as_of]
    elif command["source"] == "operations":
        rows = inventory(snapshot, lot, as_of)
    else:
        raise ValueError("Unknown evidence source")
    result = canonical({"schema": "source-result-1", "source": command["source"], "items": rows,
                     "total_rows": len(rows), "returned_rows": len(rows), "complete": True,
                     "snapshot_digest": snapshot.data_digest, "manifest_digest": snapshot.manifest_digest})
    sys.stdout.buffer.write(result.encode("utf-8") + b"\n")


if __name__ == "__main__":
    main()
