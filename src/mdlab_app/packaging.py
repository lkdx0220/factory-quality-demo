"""Explainable simulation policy with immutable, idempotent decision records."""

from datetime import datetime, timezone

from . import POLICY_VERSION
from .errors import ProcessingError, utc_timestamp
from .ingestion import canonical, digest
from .queries import test_types, unit_identity, visible_results


def _sn_identity(con, uid, sn, time):
    forward = list(con.execute("SELECT * FROM sn_assignments WHERE unit_id=? AND assigned_at<=? AND recorded_at<=? ORDER BY assignment_id", (uid, time, time)))
    reverse = list(con.execute("SELECT * FROM sn_assignments WHERE sn=? AND assigned_at<=? AND recorded_at<=?", (sn, time, time)))
    if not forward:
        sn_status = "MISSING"
    elif len({a["sn"] for a in forward}) != 1 or len({a["unit_id"] for a in reverse}) > 1:
        sn_status = "CONFLICT"
    elif forward[0]["sn"] != sn:
        sn_status = "MISMATCH"
    else:
        sn_status = "OK"
    return sn_status, forward


def _required_result(item, rule, available, time):
    """Select the latest attempt without falling back from INVALID or expiry."""
    kind = item["test_type"]
    attempts = sorted([r for r in available if r["test_type"] == kind], key=lambda r: (r["completed_at"], r["attempt_no"], r["test_id"]))
    if not attempts:
        item["status"] = "MISSING"
        return ["MISSING_RESULT:"+kind]
    selected = attempts[-1]
    age = int((utc_timestamp(time)-utc_timestamp(selected["completed_at"])).total_seconds())
    expired = rule["max_age_seconds"] is not None and age > rule["max_age_seconds"] and selected["outcome"] in ("PASS", "FAIL")
    item.update(status=selected["outcome"], expired=expired, test_id=selected["test_id"], test_record_id=selected["test_record_id"],
                evidence_test_record_ids=[selected["test_record_id"]], attempt_no=selected["attempt_no"],
                version=selected["version"], completed_at=selected["completed_at"], age_seconds=age)
    reasons = []
    if selected["outcome"] == "FAIL":
        reasons.append("CONFIRMED_FAIL:"+kind)
    if selected["outcome"] == "INVALID":
        reasons.append("INVALID_RESULT:"+kind)
    if expired:
        reasons.append("EXPIRED_RESULT:"+kind)
    return reasons


def _test_requirement(con, product_id, kind, time, available):
    rules = list(con.execute("SELECT * FROM test_requirements WHERE product_id=? AND test_type=? AND recorded_at<=? AND effective_from<=? AND (effective_to IS NULL OR ?<effective_to) ORDER BY requirement_id", (product_id, kind, time, time, time)))
    if len(rules) != 1:
        status = "RULE_INCOMPLETE" if not rules else "RULE_OVERLAP"
        return {"test_type": kind, "requirement_id": None, "required": None, "status": status}, [status+":"+kind]
    rule = rules[0]
    item = {"test_type": kind, "requirement_id": rule["requirement_id"], "required": bool(rule["required"]),
            "status": "NOT_REQUIRED", "expired": False, "test_id": None, "test_record_id": None,
            "evidence_test_record_ids": [], "attempt_no": None, "version": None, "completed_at": None,
            "age_seconds": None, "max_age_seconds": rule["max_age_seconds"]}
    reasons = _required_result(item, rule, available, time) if rule["required"] else []
    return item, reasons


def _decision(sn_status, tests, reasons):
    if sn_status != "OK" or any(t["status"] in ("RULE_INCOMPLETE", "RULE_OVERLAP") for t in tests):
        return "REVIEW_BLOCKED"
    if any(r.startswith("CONFIRMED_FAIL:") for r in reasons):
        return "HOLD"
    if any(r.startswith(("MISSING_RESULT:", "INVALID_RESULT:", "EXPIRED_RESULT:")) for r in reasons):
        return "REVIEW_BLOCKED"
    return "ALLOW"


def evaluate_check(con, check_id):
    check = con.execute("SELECT * FROM packaging_checks WHERE check_id=?", (check_id,)).fetchone()
    if not check:
        raise ProcessingError("CHECK_NOT_FOUND", "Unknown packaging check", check_id)
    time, uid, sn = check["check_at"], check["unit_id"], check["requested_sn"]
    unit = unit_identity(con, uid, time)
    sn_status, forward = _sn_identity(con, uid, sn, time)
    available = visible_results(con, time, uid)
    tests, reasons = [], []
    if sn_status != "OK":
        reasons.append({"MISSING": "SN_MISSING", "CONFLICT": "SN_CONFLICT", "MISMATCH": "SN_MISMATCH"}[sn_status])
    for kind in test_types(con):
        item, item_reasons = _test_requirement(con, unit["product_id"], kind, time, available)
        tests.append(item)
        reasons.extend(item_reasons)
    decision = _decision(sn_status, tests, reasons)
    return {"decision": decision, "reason_codes": reasons or ["ALL_REQUIRED_PASS"],
            "identity": {"unit_found": True, "lot_id": unit["lot_id"], "product_id": unit["product_id"], "sn_status": sn_status,
                         "assignment_ids": [a["assignment_id"] for a in forward], "visible_sns": [a["sn"] for a in forward]}, "tests": tests}


def _record(con, check_id, result):
    encoded = canonical(result)
    existing = con.execute("SELECT * FROM packaging_decisions WHERE check_id=?", (check_id,)).fetchone()
    if existing:
        if existing["policy_version"] != POLICY_VERSION or existing["result_json"] != encoded:
            raise ProcessingError("DECISION_CONFLICT", "Refusing to overwrite a prior decision", check_id)
        event_id, reused = existing["decision_id"], True
    else:
        meta = con.execute("SELECT * FROM ingest_meta WHERE singleton=1").fetchone()
        event_id = "pd-"+digest(canonical([meta["data_digest"], check_id, POLICY_VERSION]).encode("utf-8"))[:24]
        con.execute("INSERT INTO packaging_decisions VALUES (?,?,?,?,?,?)", (event_id, check_id, POLICY_VERSION, result["decision"], encoded, datetime.now(timezone.utc).isoformat()))
        reused = False
    return {"check_id": check_id, **result, "decision_event": {"decision_id": event_id, "policy_version": POLICY_VERSION, "reused": reused},
            "mes_write_performed": False}


def evaluate_and_record(con, check_id=None):
    """Append one/all decision events atomically; never edit prior hold history."""
    con.execute("BEGIN IMMEDIATE")
    try:
        ids = [check_id] if check_id is not None else [r[0] for r in con.execute("SELECT check_id FROM packaging_checks ORDER BY check_at,check_id")]
        results = [_record(con, cid, evaluate_check(con, cid)) for cid in ids]
        con.commit()
        return results[0] if check_id is not None else {"total": len(results), "decisions": results}
    except BaseException:
        con.rollback()
        raise
