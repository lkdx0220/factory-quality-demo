"""Frozen output policy without service imports."""

OUTPUT_CONTRACT = {
    "schema": "publication-1", "template": "lot-inventory-answer-1",
    "intent": "lot_operation_inventory", "model_text": False,
    "required_summary": ["lot_id", "as_of", "operation_count", "member_set_digest",
                         "membership_complete", "exclusion_count", "metric_applicability"],
    "member_fields": ["operation_id", "lot_id", "process_step", "equipment_id", "track_in_at",
                      "track_out_at", "recorded_at"],
    "preview_rows": 3,
    "read_policy": "revalidate_four_preconditions_and_evidence_facts_and_bound_accepted_receipt",
    "publication": True,
}
