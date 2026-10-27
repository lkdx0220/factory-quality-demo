"""JSON CLI for the local synthetic-data baseline."""

import argparse
import json
import sqlite3
import sys

from .errors import ProcessingError
from .storage import connection, ingest, stats


def parser():
    ap = argparse.ArgumentParser(description="Synthetic manufacturing data: deterministic local processing")
    subs = ap.add_subparsers(dest="command", required=True)
    p = subs.add_parser("ingest", help="Validate and atomically ingest one published snapshot")
    p.add_argument("--batch", required=True)
    p.add_argument("--db", required=True)
    p = subs.add_parser("stats")
    p.add_argument("--db", required=True)
    p = subs.add_parser("packaging", help="Evaluate and persist immutable simulated packaging decisions")
    p.add_argument("--db", required=True)
    group = p.add_mutually_exclusive_group(required=True)
    group.add_argument("--check-id")
    group.add_argument("--all", action="store_true")
    p = subs.add_parser("trace")
    p.add_argument("--db", required=True)
    p.add_argument("--as-of", required=True)
    group = p.add_mutually_exclusive_group(required=True)
    group.add_argument("--unit-id")
    group.add_argument("--sn")
    group.add_argument("--lot-id")
    p = subs.add_parser("neighbors")
    p.add_argument("--db", required=True)
    p.add_argument("--operation-id", required=True)
    p.add_argument("--as-of", required=True)
    p.add_argument("--before", type=int, default=3)
    p.add_argument("--after", type=int, default=3)
    p.add_argument("--same-product", action="store_true")
    p = subs.add_parser("concentration")
    p.add_argument("--db", required=True)
    p.add_argument("--equipment-id", required=True)
    p.add_argument("--from", dest="start", required=True)
    p.add_argument("--to", dest="end", required=True)
    p = subs.add_parser("compare-groups")
    p.add_argument("--db", required=True)
    p.add_argument("--as-of", required=True)
    p.add_argument("--group", action="append", required=True)
    return ap


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        if args.command == "ingest":
            result = ingest(args.batch, args.db)
        elif args.command == "stats":
            with connection(args.db) as con:
                result = stats(con)
        else:
            from .queries import compare_groups, concentration, neighbors, trace_lookup
            from .packaging import evaluate_and_record
            with connection(args.db, writable=args.command == "packaging") as con:
                if args.command == "packaging":
                    result = evaluate_and_record(con, args.check_id)
                elif args.command == "trace":
                    result = trace_lookup(con, args.as_of, unit_id=args.unit_id, sn=args.sn, lot_id=args.lot_id)
                elif args.command == "neighbors":
                    result = neighbors(con, args.operation_id, args.as_of, args.before, args.after, args.same_product)
                elif args.command == "compare-groups":
                    result = compare_groups(con, args.as_of, args.group)
                else:
                    result = concentration(con, args.equipment_id, args.start, args.end)
        print(json.dumps({"ok": True, "synthetic": True, "data": result}, ensure_ascii=False, indent=2))
        return 0
    except ProcessingError as exc:
        error = exc.as_dict()
    except sqlite3.Error as exc:
        error = {"code": "STORAGE_ERROR", "message": str(exc), "details": type(exc).__name__}
    except OSError as exc:
        error = {"code": "IO_ERROR", "message": str(exc), "details": type(exc).__name__}
    print(json.dumps({"ok": False, "synthetic": True, "error": error}, ensure_ascii=False, indent=2))
    return 2


if __name__ == "__main__":
    sys.exit(main())
