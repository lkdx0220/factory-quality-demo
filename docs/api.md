# Factory Quality Demo HTTP API

API version: v1. This is a local demonstration over fictional data, using FastAPI and Uvicorn.
The existing processing layer supplies all calculation results; the HTTP layer validates input and formats responses.
The optional DeepSeek draft endpoints and opt-in startup flags are documented in
[model planning](model-planning.md). They do not grant models direct query or publication authority.

## Start

From the portable demo directory, with Python 3.11 or newer:

~~~shell
python -m pip install -r requirements-api.txt
python scripts/serve_api.py
~~~

The server listens on http://127.0.0.1:8000. OpenAPI JSON is at /openapi.json.
Interactive API documentation is at /docs (Swagger UI loads its JavaScript/CSS from a CDN).
This documentation page is an API test tool, not the project's business UI.

Startup options --batch, --db, --schema and --port bind local configuration.
Defaults are data/batches/demo-v1, local/demo.sqlite3, schemas/csv-schema.json, and port8000.
No paths or SQL can be supplied through HTTP requests. Start does not automatically import or change the database.

PowerShell example:

~~~powershell
# Initialize the configured snapshot. Repeating this reuses the existing import.
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/api/v1/ingest -ContentType application/json -Body '{}'

Invoke-RestMethod http://127.0.0.1:8000/api/v1/stats
Invoke-RestMethod 'http://127.0.0.1:8000/api/v1/units?lot_id=LOT-0001&limit=3'
Invoke-RestMethod 'http://127.0.0.1:8000/api/v1/trace?unit_id=U-00001&as_of=2026-09-01T06:00:00Z'

# Preview performs no write. Evaluate persists one idempotent decision event.
Invoke-RestMethod http://127.0.0.1:8000/api/v1/checks/CHK-0020/preview
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/api/v1/checks/CHK-0020/evaluate -ContentType application/json -Body '{}'
~~~

## Routes

All business responses are JSON. Required time format is UTC YYYY-MM-DDTHH:MM:SSZ.
Every POST requires a JSON object with the listed fields; requests marked {} must contain an empty object.
Unknown fields and repeated query parameter names are rejected.

| Method | Route | Parameters / behavior |
|---|---|---|
| GET | /api/v1/health | Process health and whether the configured DB file exists; does not assert DB readiness |
| POST | /api/v1/ingest | Body {}; validate and import the startup-configured snapshot |
| GET | /api/v1/stats | Imported counts, provenance digests and persisted decision count |
| GET | /api/v1/lots | limit, offset |
| GET | /api/v1/units | Optional lot_id; limit, offset |
| GET | /api/v1/equipment | limit, offset |
| GET | /api/v1/operations | Optional lot_id, equipment_id, as_of; limit, offset. With as_of, only known completed operations |
| GET | /api/v1/checks | Optional unit_id; limit, offset |
| GET | /api/v1/checks/{check_id}/preview | Compute without saving; includes persisted=false |
| POST | /api/v1/checks/{check_id}/evaluate | Body {}; compute and save/reuse a decision |
| POST | /api/v1/packaging/evaluate-all | Body {}; compute and save/reuse every imported check atomically |
| GET | /api/v1/decisions | Optional check_id, decision; limit, offset; read saved result, policy version and evaluation timestamp |
| GET | /api/v1/trace | Required as_of and exactly one of unit_id/sn/lot_id |
| GET | /api/v1/operations/{operation_id}/neighbors | Required as_of; before/after default3 (0–100); same_product defaultfalse |
| GET | /api/v1/concentration | Required equipment_id, start, end; compare binding groups over [start,end) |
| POST | /api/v1/compare-groups | Body: as_of and groups (1–20 values such as EQUIPMENT:MACHINE-A1 or BINDING:BND-0003) |

Identifiers are nonempty strings of at most128 ASCII letters/digits or _ . : -.
List pages have limit1–100 (default50), offset0–100000 (default0).
Response data contains items, total, limit, offset, next_offset; next_offset=null means no next page.
List results represent the imported snapshot unless an as_of filter is explicitly provided.
Decision history lists when the calculation was executed; the saved request's check_at determines business-time visibility.

## Response semantics

Success:

~~~json
{"ok":true,"synthetic":true,"data":{"decision":"HOLD","reason_codes":["CONFIRMED_FAIL:TEST_B","EXPIRED_RESULT:TEST_B"]}}
~~~

This is a shortened example; actual decision responses also include identities, selected evidence and event metadata.
ALLOW, HOLD and REVIEW_BLOCKED all mean the calculation succeeded: HTTP200 / ok=true.
The latter two forbid proceeding under the simulation policy; they are not HTTP request failures.
Repeating an evaluation returns the same decision_id with decision_event.reused=true, without adding another event.
There is no update/delete/release endpoint.

Error:

~~~json
{"ok":false,"synthetic":true,"error":{"code":"AMBIGUOUS_SN","message":"SN maps to multiple units","details":{"sn":"SYN-00028","unit_ids":["U-00027","U-00028"]}}}
~~~

| Status | Meaning |
|---|---|
| 400 | Invalid local Host header |
| 403 | Cross-origin browser request rejected |
| 404 | Unknown/not-yet-visible business object or route |
| 405 | Method is not available |
| 409 | Ambiguous identity, invalid configured input, different snapshot or conflicting prior decision |
| 422 | Invalid/missing/unknown request fields |
| 503 | No imported DB, invalid DB, database/storage unavailable |
| 500 | Unexpected internal error |

Storage exceptions are returned without local filesystem paths or tracebacks.
The launcher only binds loopback. There is no user authentication or production deployment configuration;
this version is for local demonstration. Same-origin browser access is allowed; unrelated Origin/Host values are rejected.
There are no CORS permissions for a separate frontend origin; a later UI can be served under the same origin.

## Tests

~~~shell
python -m pip install -r requirements-dev.txt
python -m unittest discover -s tests -v
~~~

Tests use private temporary databases, real SQLite transactions and the fictional dataset.
The source snapshot is read-only; decision writes occur only in those databases.
The application does not read case answers during HTTP handling.
## Controlled query extension

The separate `/api/v1/query-plans` namespace supports explicit scope preflight,
stable member views, confirmation, evidence execution, fact validation and fixed-template
LOT inventory publication. Optional model planning produces drafts only. See
[query-plan-preflight.md](query-plan-preflight.md), [answer-publication.md](answer-publication.md)
and [model-planning.md](model-planning.md) for endpoints, assumptions and current limits.
