"""Explicit, metered live-model evaluation on synthetic cases and a private DB.

Requires --live-model and a new output directory. Does not use the running demo.
Provider JSON is retained locally for review; no request headers or API keys are saved.
"""

import argparse
import csv
import hashlib
import json
import platform
import subprocess
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from fastapi.testclient import TestClient

from mdlab_app.api import create_app
from mdlab_app.model_adapter import ModelConfig, deepseek_request
from mdlab_app.model_contracts import PLANNING_CONTRACT, SYSTEM_PROMPT
from mdlab_app.plan_contracts import hash_object
from mdlab_app.storage import ingest

PREFIX = "/api/v1/query-plans"


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def source_hashes():
    return {p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for folder in ("src", "scripts", "tests", "evaluation", "data", "schemas", "contracts", "frontend/src", "frontend/dist")
            for p in sorted((ROOT / folder).rglob("*"))
            if p.is_file() and "__pycache__" not in p.parts}


def expected_rows(lot, cutoff):
    """Independent CSV oracle: no calls to the product inventory or renderer."""
    with (ROOT / "data/batches/demo-v1/business/operations.csv").open(encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    rows = [row for row in rows if row["lot_id"] == lot
            and row["recorded_at"] <= cutoff and row["track_in_at"] <= cutoff]
    for row in rows:
        if not row["track_out_at"] or row["track_out_at"] > cutoff:
            row["track_out_at"] = None
    return sorted(rows, key=lambda row: (row["track_in_at"], row["operation_id"]))


def model_receipt(envelope):
    data = envelope.get("data")
    if isinstance(data, dict) and isinstance(data.get("model_call"), dict):
        return data["model_call"]
    error = envelope.get("error")
    details = error.get("details") if isinstance(error, dict) else None
    return details.get("model_call") if isinstance(details, dict) else None


def summarize(results, sent, baseline, business_unchanged, *, recovered=False):
    receipts = [model_receipt(step["response"]) for row in results for step in row["steps"]
                if step["path"] == "/nl/proposals"]
    current = source_hashes()
    changed = sorted(name for name in set(current) | set(baseline) if current.get(name) != baseline.get(name))
    runtime_prefixes = ("src/", "data/", "schemas/", "contracts/", "frontend/src/", "frontend/dist/")
    return {"case_count": len(results), "passed": sum(row["passed"] for row in results),
            "failed_ids": [row["case"]["id"] for row in results if not row["passed"]],
            "model_requests": len(sent), "published": sum(row["published"] for row in results),
            "observed": dict(Counter(row.get("observed", "harness_failure") for row in results)),
            "returned_total_tokens": sum((call.get("usage") or {}).get("total_tokens", 0) for call in receipts if call),
            "business_db_unchanged": business_unchanged,
            "source_unchanged": not changed, "changed_source_files": changed,
            "runtime_sources_unchanged": not any(name.startswith(runtime_prefixes) for name in changed),
            "recovered_without_model_calls": recovered,
            "ended_at": datetime.now(timezone.utc).isoformat()}


def evaluate_case(client, case, record, sent):
    def request(method, path, body=None):
        response = client.request(method, PREFIX + path, **({"json": body} if body is not None else {}))
        payload = response.json()
        record["steps"].append({"method": method, "path": path, "http": response.status_code, "response": payload})
        return response, payload

    def data(method, path, body=None):
        response, payload = request(method, path, body)
        if response.status_code != 200 or not payload.get("ok"):
            raise AssertionError("Unexpected API failure: " + payload.get("error", {}).get("code", str(response.status_code)))
        return payload["data"]

    before_calls = len(sent)
    response, payload = request("POST", "/nl/proposals", {"question": case["question"]})
    record["model_requests"] = len(sent) - before_calls
    if case.get("never_model") and record["model_requests"]:
        raise AssertionError("A deterministic rejection unexpectedly contacted the provider")
    proposal = payload.get("data")
    record["observed"] = proposal["status"] if proposal else payload["error"]["code"]
    if case["expected"] == "safe_inventory_or_reject" and record["observed"] != "ready":
        assert record["observed"] in ("needs_clarification", "unsupported", "MODEL_PROTOCOL_ERROR", "MODEL_SCOPE_MISMATCH")
        record["injection_rejected"] = True
        return
    if case["expected"] == "no_ready":
        if proposal and proposal["status"] == "ready":
            # Inspect reachability without granting confirmation to a misread question.
            record["unexpected_preflight"] = data("POST", "/nl/proposals/" + proposal["proposal_id"] + "/preflight",
                                                   {"proposal_digest": proposal["proposal_digest"]})
            raise AssertionError("SEMANTIC_WRONG_READY: unsupported intent became an adoptable inventory")
        allowed = ("needs_clarification", "unsupported", "INVALID_ARGUMENT", "MODEL_PROTOCOL_ERROR", "MODEL_SCOPE_MISMATCH")
        if record["observed"] not in allowed:
            raise AssertionError("Infrastructure failure is not a successful semantic rejection")
        if case.get("status") and record["observed"] != case["status"]:
            raise AssertionError("Unexpected qualified terminal state")
        if proposal:
            assert proposal["answer_published"] is False and proposal["normalized_scope"] is None
            if case.get("reason"):
                assert case["reason"] in proposal["reason_codes"]
            denied, _ = request("POST", "/nl/proposals/" + proposal["proposal_id"] + "/preflight",
                                {"proposal_digest": proposal["proposal_digest"]})
            assert denied.status_code == 409
        return
    if not proposal or proposal["status"] != "ready":
        raise AssertionError("Supported question did not produce a ready draft")
    assert proposal["answer_published"] is False
    scope = proposal["normalized_scope"]
    assert scope == {"intent_key": "lot_operation_inventory", "lot_id": case["lot_id"], "as_of": case["as_of"]}
    adopted_response, adopted_payload = request("POST", "/nl/proposals/" + proposal["proposal_id"] + "/preflight",
                                                {"proposal_digest": proposal["proposal_digest"]})
    if case["expected"] == "preflight_blocked" and adopted_response.status_code == 404:
        assert adopted_payload["error"]["code"] == "LOT_NOT_VISIBLE"
        return
    assert adopted_response.status_code == 200 and adopted_payload["ok"]
    card = adopted_payload["data"]
    if case["expected"] == "preflight_blocked":
        assert card["can_confirm"] is False
        denied, _ = request("POST", "/requests/" + card["request_id"] + "/confirm",
                            {"revision": card["revision"], "card_digest": card["card_digest"]})
        assert denied.status_code == 409
        return
    assert card["can_confirm"] is True
    # A trusted test actor explicitly simulates the user's review and confirmation.
    members = data("GET", "/requests/" + card["request_id"] + "/members?set_version=" + card["member_set"]["set_version"] + "&limit=100")
    oracle = expected_rows(case["lot_id"], case["as_of"])
    record["oracle"] = oracle
    assert members["items"] == oracle
    confirmed = data("POST", "/requests/" + card["request_id"] + "/confirm",
                     {"revision": card["revision"], "card_digest": card["card_digest"]})
    execution = data("POST", "/confirmed/" + confirmed["confirmed_plan_id"] + "/execute", {"scope_hash": confirmed["scope_hash"]})
    deadline = time.monotonic() + 20
    while True:
        execution = data("GET", "/executions/" + execution["execution_id"])
        if execution["state"] not in ("queued", "running"):
            break
        if time.monotonic() >= deadline:
            raise AssertionError("Evidence execution exceeded evaluation wait budget")
        time.sleep(0.05)
    assert execution["state"] == "completed"
    facts = data("POST", "/executions/" + execution["execution_id"] + "/facts", {})
    base = "/fact-sets/" + facts["fact_set_id"]
    candidate = data("GET", base + "/proposal")
    receipt = data("POST", base + "/validate", candidate)
    assert receipt["accepted"] is True
    view = data("POST", base + "/publish", {"fact_set_digest": facts["fact_set_digest"], "validation_id": receipt["validation_id"]})
    artifact = data("GET", view["export_url"].removeprefix(PREFIX))
    publication = artifact["publication"]
    actual = [{cell["key"].removeprefix("operation."): cell["value"] for cell in row["cells"]}
              for row in publication["operations"]]
    assert actual == oracle
    assert publication["answer_published"] is True and publication["model_output_used"] is False
    if case.get("forbidden_text"):
        assert case["forbidden_text"] not in json.dumps(publication, ensure_ascii=False)
    assert all(publication["gates"].values())
    assert publication["required_fact_ids"] == publication["rendered_fact_ids"]
    assert len(publication["required_fact_ids"]) == 7 + 7 * len(oracle)
    assert hash_object(artifact["validation_receipt"]) == publication["validation_digest"]
    assert publication["publication_digest"] == hash_object({k: v for k, v in publication.items()
                                                             if k not in ("publication_digest", "publication_id")})
    page = data("GET", view["details_url"].removeprefix(PREFIX) + "&limit=100")
    assert page["items"] == publication["operations"]
    record["published"] = True
    record["operation_count"] = len(oracle)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live-model", action="store_true", help="Explicitly authorize metered provider calls")
    parser.add_argument("--summarize-existing", action="store_true", help="Rebuild a missing summary from saved complete records; no provider call")
    parser.add_argument("--output", type=Path, required=True, help="New private results directory; never overwrite")
    parser.add_argument("--cases", type=Path, default=ROOT / "evaluation/nl-query-cases.json")
    args = parser.parse_args()
    if args.summarize_existing:
        if args.live_model:
            parser.error("Summary recovery and live evaluation are mutually exclusive")
        if (args.output / "summary.json").exists():
            parser.error("Refusing to overwrite an existing summary")
        frozen = json.loads((args.output / "cases-frozen.json").read_text(encoding="utf-8"))
        metadata = json.loads((args.output / "metadata.json").read_text(encoding="utf-8"))
        assert hash_object(frozen) == metadata["cases_digest"]
        records = [json.loads((args.output / "cases" / (case["id"] + ".json")).read_text(encoding="utf-8")) for case in frozen]
        assert [record["case"] for record in records] == frozen
        sent = [json.loads(path.read_text(encoding="utf-8")) for path in sorted((args.output / "provider").glob("*-request.json"))]
        # An old run without a saved before-hash cannot prove byte identity.
        import sqlite3
        with sqlite3.connect("file:" + (args.output / "evaluation.sqlite3").resolve().as_posix() + "?mode=ro", uri=True) as con:
            unchanged = con.execute("SELECT COUNT(*) FROM packaging_decisions").fetchone()[0] == 0
        before_path = args.output / "business-before.json"
        byte_identity = (hashlib.sha256((args.output / "evaluation.sqlite3").read_bytes()).hexdigest()
                         == json.loads(before_path.read_text(encoding="utf-8"))["sha256"]) if before_path.exists() else None
        summary = summarize(records, sent, metadata["source_hashes"], byte_identity, recovered=True)
        summary["business_decisions_empty"] = unchanged
        summary["business_db_check"] = "zero decisions; original byte hash was not saved before reporting failure"
        write(args.output / "summary.json", summary)
        print(json.dumps(summary, ensure_ascii=True, indent=2))
        return 0 if summary["passed"] == len(records) and unchanged and summary["runtime_sources_unchanged"] else 1
    if not args.live_model:
        parser.error("--live-model is required; live evaluation incurs provider charges")
    config = ModelConfig.from_environment()
    cases = json.loads(args.cases.read_text(encoding="utf-8"))
    assert len({case["id"] for case in cases}) == len(cases)
    args.output.mkdir(parents=True, exist_ok=False)
    (args.output / "provider").mkdir()
    (args.output / "cases").mkdir()
    write(args.output / "cases-frozen.json", cases)
    baseline = source_hashes()
    write(args.output / "metadata.json", {
        "started_at": datetime.now(timezone.utc).isoformat(), "python": platform.python_version(),
        "platform": platform.system(), "git_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "profile": config.profile(), "prompt": SYSTEM_PROMPT, "contract": PLANNING_CONTRACT,
        "cases_digest": hash_object(cases), "source_hashes": baseline,
        "test_actor": "explicit simulated human confirmation after independent CSV range comparison"})
    db = args.output / "evaluation.sqlite3"
    ingest(ROOT / "data/batches/demo-v1", db, ROOT / "schemas/csv-schema.json")
    business_before = hashlib.sha256(db.read_bytes()).hexdigest()
    write(args.output / "business-before.json", {"sha256": business_before})
    app = create_app(db, ROOT / "data/batches/demo-v1", ROOT / "schemas/csv-schema.json", model_config=config)
    sent, results = [], []
    active_case = None

    def record_transport(local_config, body):
        if len(sent) >= 40:
            raise RuntimeError("Live evaluation call budget exhausted")
        item = {"index": len(sent) + 1, "case_id": active_case, "body": body}
        sent.append(item)
        try:
            raw = deepseek_request(local_config, body)
            item["response_sha256"] = hashlib.sha256(raw).hexdigest()
            (args.output / "provider" / f"{item['index']:03d}-response.json").write_bytes(raw)
            return raw
        finally:
            write(args.output / "provider" / f"{item['index']:03d}-request.json", item)

    app.state.planner_service.transport = record_transport
    with TestClient(app, raise_server_exceptions=False) as client:
        for case in cases:
            active_case = case["id"]
            record = {"case": case, "steps": [], "published": False}
            started = time.monotonic()
            try:
                evaluate_case(client, case, record, sent)
                record["passed"] = True
            except Exception as exc:
                record["passed"] = False
                record["failure"] = {"type": type(exc).__name__, "message": str(exc)}
            record["elapsed_seconds"] = time.monotonic() - started
            results.append(record)
            write(args.output / "cases" / (case["id"] + ".json"), record)
            print(json.dumps({"id": case["id"], "passed": record["passed"], "observed": record.get("observed"),
                              "published": record["published"], "failure": record.get("failure")}, ensure_ascii=True), flush=True)
    summary = summarize(results, sent, baseline, hashlib.sha256(db.read_bytes()).hexdigest() == business_before)
    write(args.output / "summary.json", summary)
    print(json.dumps(summary, ensure_ascii=True, indent=2))
    return 0 if summary["passed"] == len(results) and summary["business_db_unchanged"] and summary["source_unchanged"] else 1


if __name__ == "__main__":
    sys.exit(main())
