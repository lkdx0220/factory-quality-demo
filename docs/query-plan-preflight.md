# Query plan preflight

This document describes the deterministic preflight boundary. The optional
[model draft stage](model-planning.md) precedes it and cannot confirm or execute a plan.

This feature is a first implementation slice, not a natural-language assistant.
It supports one explicit intent: list **all operation records visible for one
exact LOT ID at an explicit UTC instant**. It does not infer an intent from the
saved user text, accept SQL, resolve SNs, select subsets, calculate overall LOT
quality, or publish an answer through the preflight endpoints. A separate [publication layer](answer-publication.md)
can issue the fixed LOT inventory answer after execution and fact validation. Existing analysis pages remain separate
deterministic queries; they are not covered by a new answer-publication claim.

## Local workflow

1. Import the configured synthetic batch using the existing initialization flow.
2. Open “查询计划预检”, enter the LOT and historical instant, then generate a card.
3. Inspect the source, scope, per-source coverage, discovery status and members.
4. Open the bound member list before clicking “确认这张范围卡”.
5. Optionally read/download the same server-generated preflight artifact.

Confirmation saves an immutable scope plan; it does **not** mean the product
passed, the analysis was completed, or an answer was published. Every artifact
sets `answer_published: false`. Unknown coverage and failed discovery can be
exported as incomplete diagnostic preflight artifacts, without becoming answers.
The member list distinguishes the requested candidate count from acquired rows.

## Controls in this slice

- The server expands the LOT against a validated immutable file snapshot. It
  exhausts stable pages, checks totals, cursor progression, membership and exact
  content against a separate full-snapshot inventory. Equal counts alone fail.
- Source files are validated on each use. The imported SQLite snapshot identity
  must match, but operation rows for this feature are read from the validated
  source files, **not live SQLite results**. Local decision writes are unrelated.
- Member views and downloads use the same immutable set/version and scope.
  Revision changes invalidate old links; confirmed plans retain their old sets.
- Confirmation checks revision and the complete card digest inside an immediate
  SQLite transaction. Snapshot, discovery, membership, coverage, bundle and
  current session policy are checked under the same service lock before saving.
- The content-addressed bundle includes the actual Python modules, schema,
  dependency versions, relevant frontend source/build files, configuration and
  fixed contract/templates. It is rechecked for views, confirmation and exports.
  Changing the deployment cannot silently rebind a saved plan to the newest code.
- Old identities supported by the **same verified engine** can remain available
  when the default release changes. This is not an arbitrary old-code loader.
  Missing, changed or revoked bundles stop the request; a new card is required.
- Session/coverage/bundle revocation takes precedence and can be persisted by the
  server-only `PlanService.revoke()` operation. There is no public policy or fault
  injection endpoint.

## Coverage is fictional and deliberately narrow

`contracts/demo-closed-world.json` explicitly enumerates all 14 operation IDs in
the controlled fictional scenario, pins the dataset/manifest digests and declares
retained history for `lots` and `operations` from `2026-09-01T00:00:00Z` through
`2026-09-11T23:59:59Z`, inclusive. This time range is fixed independently of any
request. These are scenario-authoring assumptions, not evidence of complete
real-world data collection or a real ingestion watermark. The declaration
supports only the operation-inventory intent, not quality or test coverage.

Visibility requires both `recorded_at <= as_of` and `track_in_at <= as_of`.
Future departure times are hidden. Records not yet visible are not candidates;
they are not described as known historical operations. LOT identity is a unique
primary key. No result status is used to filter operation members.

All required sources must cover the request and retain the necessary history.
Missing declarations return `unknown`; missing inventory/time/history returns a
gap. `MAX(recorded_at)`, import time, file mtime and absence of newer rows do not
create coverage. User confirmation cannot override these checks. Plans expire
after 30 minutes, including member/export reads.

## HTTP endpoints

All paths below are under `/api/v1/query-plans` and use the existing success/error
envelope. JSON bodies reject unknown fields. Writes reject query parameters;
member routes accept only `set_version`, `limit` (1–100) and `offset` (0–100000).
All responses are non-cacheable.

| Method/path | Body or purpose |
| --- | --- |
| POST `/requests` | `{intent_key:"lot_operation_inventory", original_input, lot_id, as_of}` |
| GET `/requests/{id}` | Recheck and read the current card |
| POST `/requests/{id}/revise` | Draft fields plus `revision` and `card_digest`; CAS creates the next revision |
| POST `/requests/{id}/confirm` | Only integer `revision` and exact `card_digest` |
| GET `/requests/{id}/members` | Bound discovered rows; partial rows are explicitly incomplete |
| GET `/requests/{id}/export` | Preflight JSON attachment, including acquired rows and bundle manifest |
| GET `/confirmed/{id}` | Recheck a saved immutable scope plan |
| GET `/confirmed/{id}/members` | Saved set with current policy and dependency checks |
| GET `/confirmed/{id}/export` | Saved plan attachment; no analysis answer |

Use the server-issued URLs; changing `set_version` fails. The random HttpOnly,
SameSite=Strict cookie is scoped to this route namespace and checked against the
local session registry. Request IDs/hashes alone cannot access another session.
This is a loopback, single-user demo, **not enterprise authentication**. Host and
Origin checks still apply. User text is labeled as unverified input and escaped
by React; it is never a conclusion, instruction or executable HTML.

Control state is stored beside the configured DB as `<database>.plans.sqlite3`.
The server uses one process; do not add workers or hot reload without revisiting
the control store and bundle/policy lifecycle. A deployment or frontend rebuild
requires restarting the API and inspecting a new card when its bundle changes.

## Validation and remaining work

`tests/test_query_plans.py` exercises the supported scope with positive cases,
partial/failed pages, 30-member pagination, changed members, concurrent revision,
history boundaries, missing/revoked coverage, bundle changes, restart and session
isolation. Fault adapters are Python test injections, not HTTP options.

The confirmed LOT slice now has a formal evidence execution ledger/DAG; see
[execution contract](evidence-execution.md). The required fact registry and scalar
claim checks are described in [fact contract](fact-registry.md), and the independent fixed answer gates
in [publication contract](answer-publication.md). Still pending: broader supported intents,
full adversarial acceptance matrix and actual model planning evaluations. The
preflight view/export does not stand in for final answer/UI/export validation.
