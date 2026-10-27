"""Time-aware, parameterized SQL queries over the immutable input snapshot."""

from collections import defaultdict

from .errors import ProcessingError, utc_timestamp

TEST_TYPES = ("TEST_A", "TEST_B", "TEST_C")


def test_types(con):
    """Use generic stage names for public snapshots; preserve legacy fixtures."""
    generic = ("TEST_A", "TEST_B", "TEST_C")
    present = {r[0] for r in con.execute("SELECT DISTINCT test_type FROM test_requirements")}
    return generic if present & set(generic) else TEST_TYPES


def visible_results(con, as_of, unit_id=None, operation_id=None):
    utc_timestamp(as_of)
    # Rank the visible correction first; only then check completion. Filtering by
    # operation before ranking could resurrect an earlier correction's old link.
    sql = """WITH ranked AS (
        SELECT *, ROW_NUMBER() OVER(PARTITION BY test_id ORDER BY version DESC) AS version_rank
        FROM test_results WHERE recorded_at<=?
    ) SELECT * FROM ranked WHERE version_rank=1 AND completed_at<=?"""
    params = [as_of, as_of]
    if unit_id is not None:
        sql += " AND unit_id=?"; params.append(unit_id)
    if operation_id is not None:
        sql += " AND operation_id=?"; params.append(operation_id)
    rows = []
    for row in con.execute(sql, params):
        value = dict(row); value.pop("version_rank")
        rows.append(value)
    order = {name: index for index, name in enumerate(test_types(con))}
    return sorted(rows, key=lambda r: (order[r["test_type"]], r["completed_at"], r["test_id"]))


def unit_identity(con, unit_id, as_of):
    row = con.execute("""SELECT u.*,l.product_id,l.created_at AS lot_created_at
                         FROM units u JOIN lots l ON l.lot_id=u.lot_id WHERE u.unit_id=?""", (unit_id,)).fetchone()
    if not row:
        raise ProcessingError("UNIT_NOT_FOUND", "Unknown unit", unit_id)
    if row["created_at"] > as_of or row["lot_created_at"] > as_of:
        raise ProcessingError("UNIT_NOT_VISIBLE", "Unit does not yet exist at as_of", unit_id)
    return dict(row)


def _trace_operations(con, lot_id, as_of):
    operations = []
    for row in con.execute("SELECT * FROM operations WHERE lot_id=? AND recorded_at<=? AND track_in_at<=? ORDER BY track_in_at,operation_id", (lot_id, as_of, as_of)):
        complete = bool(row["track_out_at"] and row["track_out_at"] <= as_of)
        operations.append({"operation_id": row["operation_id"], "process_step": row["process_step"], "equipment_id": row["equipment_id"],
                           "track_in_at": row["track_in_at"], "track_out_at": row["track_out_at"] if complete else None,
                           "status": "completed" if complete else "in_progress"})
    return operations


def _trace_tests(con, rows, as_of):
    tests = []
    for row in rows:
        keys = ("test_type", "attempt_no", "test_id", "version", "test_record_id", "operation_id", "equipment_id", "binding_id", "started_at", "completed_at", "outcome", "recorded_at")
        item = {k: row[k] for k in keys}
        if row["binding_id"]:
            binding = con.execute("SELECT fixture_id,equipment_id,position_id,valid_from,valid_to FROM fixture_bindings WHERE binding_id=? AND recorded_at<=?", (row["binding_id"], as_of)).fetchone()
            item["binding"] = dict(binding) if binding else None
        tests.append(item)
    return tests


def _alarm_linked(alarm, unit_id, binding_ids, op_ids):
    return ((alarm["scope"] == "UNIT" and alarm["unit_id"] == unit_id)
            or (alarm["scope"] == "BINDING" and alarm["binding_id"] in binding_ids)
            or (alarm["scope"] == "OPERATION" and alarm["operation_id"] in op_ids))


def _trace_alarms(con, unit_id, rows, operations, as_of):
    binding_ids = {r["binding_id"] for r in rows if r["binding_id"]}
    equipment_ids = {r["equipment_id"] for r in rows}
    op_ids = {o["operation_id"] for o in operations}
    linked, context = [], []
    for alarm in con.execute("SELECT * FROM alarms WHERE recorded_at<=? AND started_at<=? ORDER BY alarm_id", (as_of, as_of)):
        is_linked = _alarm_linked(alarm, unit_id, binding_ids, op_ids)
        common = {k: alarm[k] for k in ("alarm_id", "scope", "alarm_code", "started_at", "ended_at", "recorded_at")}
        # There is no separate end-update stream; do not present a future end as
        # already observed in a historical view.
        if common["ended_at"] and common["ended_at"] > as_of:
            common["ended_at"] = None
        if is_linked:
            linked.append(common)
        elif alarm["scope"] == "EQUIPMENT" and alarm["equipment_id"] in equipment_ids:
            end = common["ended_at"] or as_of
            overlaps = any(r["equipment_id"] == alarm["equipment_id"] and alarm["started_at"] < r["completed_at"] and r["started_at"] < end for r in rows)
            context.append(common | {"equipment_id": alarm["equipment_id"], "context_only": True, "overlaps_unit_activity": overlaps})
    return linked, context


def trace_unit(con, unit_id, as_of):
    utc_timestamp(as_of)
    unit = unit_identity(con, unit_id, as_of)
    operations = _trace_operations(con, unit["lot_id"], as_of)
    assignments = [dict(r) for r in con.execute("SELECT sn,assignment_id,assigned_at,recorded_at FROM sn_assignments WHERE unit_id=? AND assigned_at<=? AND recorded_at<=? ORDER BY assignment_id", (unit_id, as_of, as_of))]
    rows = visible_results(con, as_of, unit_id)
    tests = _trace_tests(con, rows, as_of)
    linked, context = _trace_alarms(con, unit_id, rows, operations, as_of)
    delivery_counts = {r["test_record_id"]: con.execute("SELECT COUNT(*) FROM delivery_receipts WHERE test_record_id=? AND received_at<=?", (r["test_record_id"], as_of)).fetchone()[0] for r in rows}
    return {"unit_id": unit_id, "as_of": as_of, "lot_id": unit["lot_id"], "product_id": unit["product_id"],
            "operations": operations, "operation_ids": [o["operation_id"] for o in operations], "visible_sn": assignments,
            "distinct_test_count": len(tests), "tests": tests, "unit_linked_alarms": linked,
            "equipment_context_alarms": context, "delivery_counts": delivery_counts}


def trace_lookup(con, as_of, unit_id=None, sn=None, lot_id=None):
    utc_timestamp(as_of)
    if sum(x is not None for x in (unit_id, sn, lot_id)) != 1:
        raise ProcessingError("INVALID_ARGUMENT", "Specify exactly one of unit_id, sn, lot_id")
    if sn is not None:
        candidates = [r[0] for r in con.execute("SELECT DISTINCT unit_id FROM sn_assignments WHERE sn=? AND assigned_at<=? AND recorded_at<=? ORDER BY unit_id", (sn, as_of, as_of))]
        if not candidates:
            raise ProcessingError("SN_NOT_VISIBLE", "No visible SN assignment", sn)
        if len(candidates) != 1:
            raise ProcessingError("AMBIGUOUS_SN", "SN maps to multiple units", {"sn": sn, "unit_ids": candidates})
        return trace_unit(con, candidates[0], as_of)
    if unit_id is not None:
        return trace_unit(con, unit_id, as_of)
    lot = con.execute("SELECT * FROM lots WHERE lot_id=? AND created_at<=?", (lot_id, as_of)).fetchone()
    if not lot:
        raise ProcessingError("LOT_NOT_VISIBLE", "Unknown or not-yet-created LOT", lot_id)
    uids = [r[0] for r in con.execute("SELECT unit_id FROM units WHERE lot_id=? AND created_at<=? ORDER BY unit_id", (lot_id, as_of))]
    return {"lot_id": lot_id, "as_of": as_of, "unit_count": len(uids), "units": [trace_unit(con, uid, as_of) for uid in uids]}


def operation_stats(con, operation, as_of):
    units = [r[0] for r in con.execute("SELECT unit_id FROM units WHERE lot_id=?", (operation["lot_id"],))]
    rows = visible_results(con, as_of, operation_id=operation["operation_id"])
    grouped = defaultdict(list)
    for row in rows:
        grouped[row["unit_id"]].append(row)
    counts = dict(denominator=len(units), first_pass_count=0, final_pass_count=0, fail_count=0, invalid_count=0, missing_count=0)
    for uid in units:
        attempts = sorted(grouped[uid], key=lambda r: (r["completed_at"], r["attempt_no"]))
        if not attempts:
            counts["missing_count"] += 1
        else:
            counts["first_pass_count"] += attempts[0]["outcome"] == "PASS"
            counts[{"PASS": "final_pass_count", "FAIL": "fail_count", "INVALID": "invalid_count"}[attempts[-1]["outcome"]]] += 1
    denominator = counts["denominator"]
    counts.update(first_pass_yield=counts["first_pass_count"]/denominator if denominator else None,
                  final_yield=counts["final_pass_count"]/denominator if denominator else None,
                  tested_count=denominator-counts["missing_count"],
                  coverage=(denominator-counts["missing_count"])/denominator if denominator else None)
    return counts


def neighbors(con, operation_id, as_of, n_before=3, n_after=3, same_product=False):
    utc_timestamp(as_of)
    if type(n_before) is not int or type(n_after) is not int or n_before < 0 or n_after < 0:
        raise ProcessingError("INVALID_ARGUMENT", "Neighbor counts must be nonnegative integers")
    anchor = con.execute("SELECT o.*,l.product_id FROM operations o JOIN lots l ON l.lot_id=o.lot_id WHERE operation_id=?", (operation_id,)).fetchone()
    if not anchor:
        raise ProcessingError("OPERATION_NOT_FOUND", "Unknown anchor operation", operation_id)
    if anchor["recorded_at"] > as_of or not anchor["track_out_at"] or anchor["track_out_at"] > as_of:
        raise ProcessingError("ANCHOR_NOT_VISIBLE", "Anchor must be known and completed at as_of", operation_id)
    rows = [dict(r) for r in con.execute("""SELECT o.*,l.product_id FROM operations o JOIN lots l ON l.lot_id=o.lot_id
        WHERE equipment_id=? AND process_step=? AND recorded_at<=? AND track_out_at<=?
        ORDER BY track_out_at,operation_id""", (anchor["equipment_id"], anchor["process_step"], as_of, as_of))]
    if same_product:
        rows = [r for r in rows if r["product_id"] == anchor["product_id"]]
    index = next(i for i, r in enumerate(rows) if r["operation_id"] == operation_id)
    def item(row):
        return {k: row[k] for k in ("operation_id", "lot_id", "product_id", "track_out_at")} | {"test_type": row["process_step"]} | operation_stats(con, row, as_of)
    before = [item(r) for r in rows[max(0, index-n_before):index]]
    after = [item(r) for r in rows[index+1:index+1+n_after]]
    return {"anchor_operation_id": operation_id, "equipment_id": anchor["equipment_id"], "process_step": anchor["process_step"],
            "as_of": as_of, "n_before": n_before, "n_after": n_after, "same_product_filter": same_product,
            "anchor_stats": item(dict(anchor)), "before": before, "after": after, "before_count": len(before), "after_count": len(after),
            "before_insufficient": len(before) < n_before, "after_insufficient": len(after) < n_after}


def _rate(rows):
    fails = sum(r["outcome"] == "FAIL" for r in rows)
    return {"attempts": len(rows), "fail_attempts": fails, "fail_rate": fails/len(rows) if rows else None}


def concentration(con, equipment_id, start, end):
    utc_timestamp(start); utc_timestamp(end)
    if start >= end:
        raise ProcessingError("INVALID_ARGUMENT", "Window must be nonempty [from,to)")
    if not con.execute("SELECT 1 FROM equipment WHERE equipment_id=?", (equipment_id,)).fetchone():
        raise ProcessingError("EQUIPMENT_NOT_FOUND", "Unknown equipment", equipment_id)
    rows = [r for r in visible_results(con, end) if r["test_type"] == test_types(con)[0] and r["equipment_id"] == equipment_id and start <= r["started_at"] < end]
    grouped = defaultdict(list)
    for r in rows:
        grouped[r["binding_id"]].append(r)
    groups = []
    for binding_id in sorted(grouped, key=lambda k: k or ""):
        binding = con.execute("SELECT * FROM fixture_bindings WHERE binding_id=?", (binding_id,)).fetchone()
        groups.append({"binding_id": binding_id, "fixture_id": binding["fixture_id"] if binding else None,
                       "position_id": binding["position_id"] if binding else None, **_rate(grouped[binding_id])})
    return {"analysis": "binding_window", "equipment_id": equipment_id, "window": {"from": start, "to": end}, "groups": groups,
            "interpretation": "Synthetic investigation signal; not a root-cause conclusion"}


def compare_groups(con, as_of, group_ids):
    utc_timestamp(as_of)
    rows = [r for r in visible_results(con, as_of) if r["test_type"] == test_types(con)[0]]
    groups = []
    for group_id in group_ids:
        kind, separator, identity = group_id.partition(":")
        if not separator or kind not in ("EQUIPMENT", "BINDING") or not identity:
            raise ProcessingError("INVALID_ARGUMENT", "Group must be EQUIPMENT:id or BINDING:id", group_id)
        field = "equipment_id" if kind == "EQUIPMENT" else "binding_id"
        groups.append({"group_id": group_id, **_rate([r for r in rows if r[field] == identity])})
    return {"analysis": "count_vs_rate", "as_of": as_of, "groups": groups}
