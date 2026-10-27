"""Strict scalar fact/reference contracts for the single LOT inventory intent."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StrictStr, model_validator

Sha = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
FactId = Annotated[str, Field(pattern=r"^fact-[a-f0-9]{64}$")]
SetId = Annotated[str, Field(pattern=r"^facts-[a-f0-9]{64}$")]
Scalar = StrictStr | StrictInt | StrictBool | None


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Binding(StrictModel):
    request_id: str
    confirmed_plan_id: str
    execution_id: str
    scope_hash: Sha
    snapshot_digest: Sha
    manifest_digest: Sha
    contract_bundle_hash: Sha
    member_set_version: Sha


class FactReference(StrictModel):
    fact_id: FactId
    claim_type: Literal["lot_identity", "historical_cutoff", "operation_count", "member_set_digest",
                        "membership_complete", "exclusion_count", "metric_applicability", "operation_field"]
    entity_type: Literal["lot", "operation"]
    entity_id: str
    operation_id: str | None
    as_of: str
    metric_key: Literal["lot_id", "as_of", "operation_count", "member_set_digest", "membership_complete",
                        "exclusion_count", "metric_applicability", "operation.operation_id", "operation.lot_id",
                        "operation.process_step", "operation.equipment_id", "operation.track_in_at",
                        "operation.track_out_at", "operation.recorded_at"]
    metric_version: str
    rule_version: str
    unit: Literal["identifier", "utc_timestamp", "operation_record", "sha256", "boolean",
                  "exclusion_record", "reason_code", "text"]
    denominator_domain: Literal["all_visible_operation_records", "not_applicable"]
    value_type: Literal["string", "integer", "boolean", "null"]
    value: Scalar

    @model_validator(mode="after")
    def scalar_type(self):
        expected = {"string": str, "integer": int, "boolean": bool, "null": type(None)}[self.value_type]
        if type(self.value) is not expected:
            raise ValueError("Scalar type must match value_type without coercion")
        return self


class SourceLeaf(StrictModel):
    evidence_id: str
    source: Literal["lots", "operations"]
    location: Literal["record", "scope"]
    record_id: str
    field: Literal["lot_id", "as_of", "operation_id", "process_step", "equipment_id",
                   "track_in_at", "track_out_at", "recorded_at"]


class Fact(FactReference, Binding):
    fact_key: str
    registry_version: str
    evidence_ids: list[str]
    evidence_digests: list[Sha]
    source_leaf: SourceLeaf | None
    algorithm_id: Literal["count_unique_operations_v1", "canonical_members_digest_v1", "complete_membership_v1",
                          "bound_exclusions_count_v1", "inventory_applicability_v1"] | None
    input_fact_ids: list[FactId]


class Completeness(StrictModel):
    execution: Literal["complete"]
    business_evidence: Literal["not_applicable"]
    business_reason: Literal["NOT_APPLICABLE_OPERATION_INVENTORY"]
    presentation: Literal["full_registry_available_via_bound_members_and_export"]


class FactSet(Binding):
    schema_version: Literal["fact-set-1"]
    fact_set_id: SetId
    fact_set_digest: Sha
    registry_version: str
    evidence_set_digest: Sha
    lot_id: str
    as_of: str
    g2_passed: StrictBool
    required_evidence: list[str]
    required_groups: dict[str, list[FactId]]
    required_fact_ids: list[FactId]
    facts: list[Fact]
    completeness: Completeness
    answer_published: Literal[False]
    notice: str


class ClaimProposal(Binding):
    fact_set_id: SetId
    fact_set_digest: Sha
    claims: list[FactReference] = Field(max_length=2000)


class ValidationIssue(StrictModel):
    code: Literal["BINDING_MISMATCH", "UNKNOWN_FACT", "DUPLICATE_FACT", "SEMANTIC_MISMATCH", "MISSING_REQUIRED_FACTS"]
    index: StrictInt | None
    fields: list[str]


class ValidationReceipt(Binding):
    validation_id: str
    fact_set_id: SetId
    fact_set_digest: Sha
    evidence_set_digest: Sha
    registry_version: str
    created_at: str
    proposal_digest: Sha
    g2_passed: StrictBool
    g3_passed: StrictBool
    accepted: StrictBool
    required_count: StrictInt
    submitted_count: StrictInt
    validated_fact_ids: list[FactId]
    missing_required_fact_ids: list[FactId]
    issues: list[ValidationIssue]
    answer_published: Literal[False]
