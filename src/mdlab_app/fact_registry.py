"""G2 semantic evidence checks and deterministic scalar/derived fact registry."""

from datetime import datetime

from .errors import ProcessingError
from .evidence_execution import CallFailure
from .fact_contracts import Binding, Fact, FactReference, FactSet
from .plan_contracts import SPEC, hash_object

NOTICE = "事实及引用校验阶段；尚未通过最终发布门，没有发布分析答案或产品合格结论。"


def rejected():
    raise ProcessingError("PLAN_FACT_EVIDENCE_INVALID", "Required evidence failed identity, scope or ledger validation")


def binding(doc):
    return {key: doc[key] for key in Binding.model_fields}


def reference(fact):
    return {key: fact[key] for key in FactReference.model_fields}


def _verify_execution_envelope(plan, doc):
    """Bind the completed DAG and its ordered sources to the confirmed scope."""
    card = plan["card"]
    expected_binding = {"request_id": card["request_id"], "confirmed_plan_id": plan["confirmed_plan_id"],
                        "execution_id": doc["execution_id"], "scope_hash": plan["scope_hash"],
                        **{key: card[key] for key in ("snapshot_digest", "manifest_digest", "contract_bundle_hash")},
                        "member_set_version": card["member_set"]["set_version"]}
    sources = [node["key"] for node in SPEC["execution"]["dag"]]
    if (binding(doc) != expected_binding or doc["scope"] != card["scope"] or doc["policy"] != SPEC["execution"]
            or doc["state"] != "completed" or doc["execution_complete"] is not True
            or doc["error_code"] is not None or doc["answer_published"] is not False
            or doc["completed_scope"] != sources or doc["failed_scope"] or doc["not_attempted_scope"]
            or [c["source"] for c in doc["calls"]] != sources
            or [e["source"] for e in doc["evidence"]] != sources):
        rejected()
    return expected_binding


def _verify_call(call, node, call_ids):
    if (call["logical_call_id"] in call_ids or call["state"] != "completed" or call["error_code"] is not None
            or call["depends_on"] != node["depends_on"]
            or not 1 <= len(call["attempts"]) <= SPEC["execution"]["max_attempts"]):
        rejected()
    call_ids.add(call["logical_call_id"])


def _verify_attempts(call, attempt_ids, previous, finish):
    """Retries must form one chronological, bounded, fully reaped sequence."""
    for number, attempt in enumerate(call["attempts"], 1):
        begin = datetime.fromisoformat(attempt["started_at"].replace("Z", "+00:00"))
        end = datetime.fromisoformat(attempt["finished_at"].replace("Z", "+00:00"))
        final = number == len(call["attempts"])
        if (attempt["attempt_id"] in attempt_ids or attempt["attempt"] != number
                or not previous <= begin <= end <= finish or attempt["cleanup_confirmed"] is not True
                or attempt["state"] != ("completed" if final else "failed")
                or (attempt["error_code"] is not None if final
                    else attempt["error_code"] not in SPEC["execution"]["retry_codes"])):
            rejected()
        attempt_ids.add(attempt["attempt_id"])
        previous = end
    return previous


def _verify_evidence_link(evidence, call, expected_binding, scope, evidence_ids):
    last = call["attempts"][-1]
    if (evidence["evidence_id"] in evidence_ids or binding(evidence) != expected_binding
            or evidence["scope"] != scope or evidence["logical_call_id"] != call["logical_call_id"]
            or evidence["attempt_id"] != last["attempt_id"] or evidence["executed_at"] != last["started_at"]
            or evidence["completed_at"] != last["finished_at"]
            or hash_object({k: v for k, v in evidence.items() if k != "evidence_digest"}) != evidence["evidence_digest"]):
        rejected()
    evidence_ids.add(evidence["evidence_id"])


def verify_evidence(engine, plan, doc):
    """Do not trust completed flags, hashes, source counts or old fact records alone."""
    expected_binding = _verify_execution_envelope(plan, doc)
    calls, attempts, evidence_ids = set(), set(), set()
    try:
        start = datetime.fromisoformat(doc["started_at"].replace("Z", "+00:00"))
        finish = datetime.fromisoformat(doc["finished_at"].replace("Z", "+00:00"))
        previous = start
        for node, call, evidence in zip(SPEC["execution"]["dag"], doc["calls"], doc["evidence"]):
            _verify_call(call, node, calls)
            previous = _verify_attempts(call, attempts, previous, finish)
            _verify_evidence_link(evidence, call, expected_binding, plan["card"]["scope"], evidence_ids)
            engine._validate(evidence["result"], doc, call["source"])
    except (ValueError, TypeError, AttributeError, KeyError, CallFailure) as exc:
        raise ProcessingError("PLAN_FACT_EVIDENCE_INVALID", "Required evidence ledger is invalid") from exc
    return {e["source"]: e for e in doc["evidence"]}


def build_registry(plan, execution, evidence):
    contract = SPEC["facts"]
    card, scope = plan["card"], plan["card"]["scope"]
    context = binding(execution)
    all_evidence = [evidence[name] for name in SPEC["evidence_dag"]]
    facts, by_key = [], {}

    def add(key, claim_type, value, unit, *, operation_id=None, field=None, source=None,
            algorithm=None, inputs=(), domain="not_applicable"):
        origin = [evidence[source]] if source else all_evidence
        kind = "null" if value is None else "boolean" if type(value) is bool else "integer" if type(value) is int else "string"
        leaf = None
        if source:
            leaf = {"evidence_id": evidence[source]["evidence_id"], "source": source,
                    "location": "scope" if field == "as_of" else "record",
                    "record_id": operation_id or scope["lot_id"], "field": field}
        fact = {**context, "fact_key": key, "registry_version": contract["registry_version"],
                "claim_type": claim_type, "entity_type": "operation" if operation_id else "lot",
                "entity_id": operation_id or scope["lot_id"], "operation_id": operation_id, "as_of": scope["as_of"],
                "metric_key": "operation." + field if operation_id else key,
                "metric_version": contract["metric_version"], "rule_version": contract["rule_version"],
                "unit": unit, "denominator_domain": domain, "value_type": kind, "value": value,
                "evidence_ids": [e["evidence_id"] for e in origin],
                "evidence_digests": [e["evidence_digest"] for e in origin], "source_leaf": leaf,
                "algorithm_id": algorithm, "input_fact_ids": list(inputs)}
        fact["fact_id"] = "fact-" + hash_object(fact)
        Fact.model_validate(fact)
        facts.append(fact)
        by_key[key] = fact["fact_id"]
        return fact["fact_id"]

    lot = evidence["lots"]["result"]["items"][0]
    add("lot_id", "lot_identity", lot["lot_id"], "identifier", field="lot_id", source="lots")
    add("as_of", "historical_cutoff", scope["as_of"], "utc_timestamp", field="as_of", source="operations")
    operation_ids, member_ids = [], []
    rows = evidence["operations"]["result"]["items"]
    for row in rows:
        for field in contract["member_fields"]:
            unit = "utc_timestamp" if field.endswith("_at") else "text" if field == "process_step" else "identifier"
            identity = add("operation:" + row["operation_id"] + ":" + field, "operation_field", row[field], unit,
                           operation_id=row["operation_id"], field=field, source="operations",
                           domain=contract["denominator_domain"])
            member_ids.append(identity)
            if field == "operation_id":
                operation_ids.append(identity)
    count = add("operation_count", "operation_count", len({r["operation_id"] for r in rows}), "operation_record",
                algorithm="count_unique_operations_v1", inputs=operation_ids, domain=contract["denominator_domain"])
    digest = add("member_set_digest", "member_set_digest", hash_object(rows), "sha256",
                 algorithm="canonical_members_digest_v1", inputs=member_ids, domain=contract["denominator_domain"])
    included = add("membership_complete", "membership_complete", rows == card["member_set"]["members"], "boolean",
                   algorithm="complete_membership_v1", inputs=[count, digest], domain=contract["denominator_domain"])
    excluded = add("exclusion_count", "exclusion_count", len(scope["exclusions"]), "exclusion_record",
                   algorithm="bound_exclusions_count_v1", inputs=[by_key["lot_id"]])
    applicability = add("metric_applicability", "metric_applicability", "NOT_APPLICABLE_OPERATION_INVENTORY", "reason_code",
                        algorithm="inventory_applicability_v1", inputs=[by_key["lot_id"], by_key["as_of"]])
    if len(facts) > contract["max_facts"]:
        raise ProcessingError("PLAN_FACT_LIMIT", "Fact registry exceeds the frozen capacity; no complete registry was saved")
    summary = [next(f for f in facts if f["fact_key"] == key) for key in contract["summary_keys"]]
    facts = summary + [f for f in facts if f["claim_type"] == "operation_field"]
    groups = {"lot_id": [by_key["lot_id"]], "as_of": [by_key["as_of"]], "operation_count": [count],
              "complete_members": [digest, included] + member_ids, "exclusions": [excluded],
              "metric_applicability": [applicability]}
    doc = {**context, "schema_version": "fact-set-1", "registry_version": contract["registry_version"],
           "evidence_set_digest": hash_object([{key: e[key] for key in ("source", "evidence_id", "evidence_digest",
                                              "logical_call_id", "attempt_id")} for e in all_evidence]),
           "lot_id": scope["lot_id"], "as_of": scope["as_of"], "g2_passed": True,
           "required_evidence": SPEC["evidence_dag"], "required_groups": groups,
           "required_fact_ids": [f["fact_id"] for f in facts], "facts": facts,
           "completeness": {"execution": "complete", "business_evidence": "not_applicable",
                            "business_reason": "NOT_APPLICABLE_OPERATION_INVENTORY",
                            "presentation": "full_registry_available_via_bound_members_and_export"},
           "answer_published": False, "notice": NOTICE}
    doc["fact_set_digest"] = hash_object(doc)
    doc["fact_set_id"] = "facts-" + doc["fact_set_digest"]
    FactSet.model_validate(doc)
    return doc
