"""All answer text is fixed, or a labelled scalar from the registered facts."""

from .errors import ProcessingError
from .fact_registry import binding
from .plan_contracts import hash_object
from .publication_contracts import Publication

TEMPLATE = {
    "version": "lot-inventory-answer-1",
    "title": "LOT 历史作业清单",
    "status_label": "答案证据校验通过（不表示产品合格）",
    "notice": "仅回答已确认 LOT 和历史时刻的全部可见作业；不判断质量，不提供实际放行或处置建议。",
    "source_notice": "源字段值均为合成数据库原文，以数据展示，不是系统指令或质量结论。",
    "time_notice": "历史截止采用 UTC 且含边界；记录和进站时刻均不晚于截止。发布时间不代替历史截止。",
    "order_notice": "完整作业按进站时间、作业 ID 排序；分页只改变展示，不改变全量范围。",
    "summary": [
        ["lot_id", "LOT 身份"], ["as_of", "历史截止（UTC，含边界）"],
        ["operation_count", "全部可见作业数（作业记录）"], ["member_set_digest", "完整成员摘要"],
        ["membership_complete", "成员范围完整"], ["exclusion_count", "显式排除数"],
        ["metric_applicability", "质量及测试指标适用性"],
    ],
    "columns": [["operation_id", "作业 ID"], ["lot_id", "LOT"], ["process_step", "关卡"],
                ["equipment_id", "设备"], ["track_in_at", "进站（UTC）"],
                ["track_out_at", "出站（UTC）"], ["recorded_at", "记录（UTC）"]],
    "not_applicable": "不适用：此意图仅列作业；应测、通过、失败、缺测及过期等指标不作计算。",
    "null": "本时刻未知（null）",
    "true": "是",
    "false": "否",
}


def display(fact, label):
    value = fact["value"]
    text = (TEMPLATE["not_applicable"] if fact["fact_key"] == "metric_applicability" else
            TEMPLATE["null"] if value is None else TEMPLATE["true" if value else "false"]
            if type(value) is bool else str(value))
    return {"key": fact["metric_key"], "label": label, "fact_id": fact["fact_id"], "value": value,
            "display_value": text, "unit": fact["unit"],
            "origin": "registered_derivation" if fact["algorithm_id"] else
                      "confirmed_scope" if fact["source_leaf"]["location"] == "scope" else "source_value"}


def render(doc, receipt, plan, published_at):
    facts = {f["fact_key"]: f for f in doc["facts"]}
    summary = [display(facts[key], label) for key, label in TEMPLATE["summary"]]
    identities = [f["operation_id"] for f in doc["facts"] if f["metric_key"] == "operation.operation_id"]
    rows = [{"operation_id": identity, "cells": [display(facts["operation:" + identity + ":" + key], label)
             for key, label in TEMPLATE["columns"]]} for identity in identities]
    rendered = [item["fact_id"] for item in summary] + [cell["fact_id"] for row in rows for cell in row["cells"]]
    if (rendered != doc["required_fact_ids"] or len(set(rendered)) != len(rendered)
            or facts["operation_count"]["value"] != len(rows) or facts["membership_complete"]["value"] is not True
            or facts["exclusion_count"]["value"] != 0):
        raise ProcessingError("PLAN_PUBLICATION_OUTPUT_INVALID", "Final output does not cover the mandatory facts")
    result = {**binding(doc), "schema_version": "publication-1", "artifact_kind": "lot_operation_inventory_answer",
              "status": "verified_answer", "answer_published": True, "template_version": TEMPLATE["version"],
              **{key: doc[key] for key in ("fact_set_id", "fact_set_digest", "evidence_set_digest")},
              "validation_id": receipt["validation_id"], "validation_digest": hash_object(receipt),
              "published_at": published_at, "expires_at_epoch": float(plan["card"]["expires_at_epoch"]),
              **{key: TEMPLATE[key] for key in ("title", "status_label", "notice", "source_notice", "time_notice", "order_notice")},
              "summary": summary, "operations": rows, "exclusions": [], "model_output_used": False,
              "gates": {key: True for key in ("discovery_complete", "members_reviewable", "snapshot_covered",
                                             "exact_bundle_available", "g1", "g2", "g3", "g4")},
              "completeness": {"execution": "complete", "business_evidence": "not_applicable",
                               "business_reason": "NOT_APPLICABLE_OPERATION_INVENTORY",
                               "presentation": "complete_summary_and_bound_full_details",
                               "total_operations": len(rows), "included_operations": len(rows),
                               "required_facts": len(rendered), "rendered_facts": len(rendered)},
              "required_fact_ids": doc["required_fact_ids"], "rendered_fact_ids": rendered}
    result["publication_digest"] = hash_object(result)
    result["publication_id"] = "pub-" + result["publication_digest"]
    Publication.model_validate(result)
    return result
