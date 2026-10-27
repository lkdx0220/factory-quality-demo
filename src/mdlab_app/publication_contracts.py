"""Closed final-output contracts for the supported LOT inventory only."""

from typing import Literal

from pydantic import Field, StrictBool, StrictInt

from .fact_contracts import Binding, FactId, Scalar, SetId, Sha, StrictModel


class PublishInput(StrictModel):
    fact_set_digest: Sha
    validation_id: str = Field(min_length=1, max_length=128, pattern=r"^validation-[a-f0-9]{32}$")


class DisplayFact(StrictModel):
    key: str
    label: str
    fact_id: FactId
    value: Scalar
    display_value: str
    unit: str
    origin: Literal["registered_derivation", "source_value", "confirmed_scope"]


class DisplayOperation(StrictModel):
    operation_id: str
    cells: list[DisplayFact]


class PublicationCompleteness(StrictModel):
    execution: Literal["complete"]
    business_evidence: Literal["not_applicable"]
    business_reason: Literal["NOT_APPLICABLE_OPERATION_INVENTORY"]
    presentation: Literal["complete_summary_and_bound_full_details"]
    total_operations: StrictInt
    included_operations: StrictInt
    required_facts: StrictInt
    rendered_facts: StrictInt


class PublicationGates(StrictModel):
    discovery_complete: Literal[True]
    members_reviewable: Literal[True]
    snapshot_covered: Literal[True]
    exact_bundle_available: Literal[True]
    g1: Literal[True]
    g2: Literal[True]
    g3: Literal[True]
    g4: Literal[True]


class Publication(Binding):
    schema_version: Literal["publication-1"]
    publication_id: str = Field(pattern=r"^pub-[a-f0-9]{64}$")
    publication_digest: Sha
    artifact_kind: Literal["lot_operation_inventory_answer"]
    status: Literal["verified_answer"]
    answer_published: Literal[True]
    template_version: Literal["lot-inventory-answer-1"]
    fact_set_id: SetId
    fact_set_digest: Sha
    evidence_set_digest: Sha
    validation_id: str
    validation_digest: Sha
    published_at: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$")
    expires_at_epoch: float
    title: str
    status_label: str
    notice: str
    source_notice: str
    time_notice: str
    order_notice: str
    summary: list[DisplayFact]
    operations: list[DisplayOperation]
    exclusions: list[str]
    gates: PublicationGates
    completeness: PublicationCompleteness
    required_fact_ids: list[FactId]
    rendered_fact_ids: list[FactId]
    model_output_used: Literal[False]
