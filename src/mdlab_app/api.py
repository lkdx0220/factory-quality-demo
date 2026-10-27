"""Local HTTP adapter over the deterministic processing layer."""

import json
import sqlite3
from pathlib import Path
from typing import Annotated, Literal

from fastapi import FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from starlette.exceptions import HTTPException

from . import VERSION
from .errors import ProcessingError, utc_timestamp
from .packaging import evaluate_and_record, evaluate_check
from .queries import compare_groups, concentration, neighbors, trace_lookup
from .storage import connection, ingest, stats
from .plan_routes import install_plan_routes

Identifier = Annotated[str, Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.:-]+$")]


class InputModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    @field_validator("as_of", "start", "end", check_fields=False)
    @classmethod
    def canonical_time(cls, value):
        try:
            utc_timestamp(value)
        except ProcessingError as exc:
            raise ValueError("Use UTC YYYY-MM-DDTHH:MM:SSZ") from exc
        return value


class EmptyInput(InputModel):
    pass


class Page(InputModel):
    limit: int = Field(default=50, ge=1, le=100)
    offset: int = Field(default=0, ge=0, le=100000)


class UnitPage(Page):
    lot_id: Identifier | None = None


class OperationPage(Page):
    lot_id: Identifier | None = None
    equipment_id: Identifier | None = None
    as_of: str | None = None

    @field_validator("as_of")
    @classmethod
    def optional_time(cls, value):
        return value if value is None else cls.canonical_time(value)


class CheckPage(Page):
    unit_id: Identifier | None = None


class DecisionPage(Page):
    check_id: Identifier | None = None
    decision: Literal["ALLOW", "HOLD", "REVIEW_BLOCKED"] | None = None


class TraceInput(InputModel):
    as_of: str
    unit_id: Identifier | None = None
    sn: Identifier | None = None
    lot_id: Identifier | None = None

    @model_validator(mode="after")
    def one_identity(self):
        if sum(v is not None for v in (self.unit_id, self.sn, self.lot_id)) != 1:
            raise ValueError("Specify exactly one of unit_id, sn, lot_id")
        return self


class NeighborInput(InputModel):
    as_of: str
    before: int = Field(default=3, ge=0, le=100)
    after: int = Field(default=3, ge=0, le=100)
    same_product: bool = False


class ConcentrationInput(InputModel):
    equipment_id: Identifier
    start: str
    end: str

    @model_validator(mode="after")
    def ordered_window(self):
        if self.start >= self.end:
            raise ValueError("Window must be nonempty [start,end)")
        return self


class CompareInput(InputModel):
    as_of: str
    groups: list[Identifier] = Field(min_length=1, max_length=20)


class Success(BaseModel):
    ok: Literal[True] = True
    synthetic: Literal[True] = True
    data: dict


class ErrorDetail(BaseModel):
    code: str
    message: str
    details: object | None = None


class Failure(BaseModel):
    ok: Literal[False] = False
    synthetic: Literal[True] = True
    error: ErrorDetail


def success(data):
    return {"ok": True, "synthetic": True, "data": data}


def failure(status, code, message, details=None):
    return JSONResponse(status_code=status, content={"ok": False, "synthetic": True,
                        "error": {"code": code, "message": message, "details": details}})


def _page(con, count_sql, rows_sql, values, page):
    """Paginate fixed endpoint statements; filters are exclusively bound values."""
    total = con.execute(count_sql, values).fetchone()[0]
    rows = [dict(r) for r in con.execute(rows_sql, [*values, page.limit, page.offset])]
    next_offset = page.offset + len(rows) if page.offset + len(rows) < total else None
    return {"items": rows, "total": total, "limit": page.limit, "offset": page.offset, "next_offset": next_offset}


def create_app(db_path, batch_path, schema_path=None, *, model_config=None):
    """Bind paths at startup, never accept filesystem paths over HTTP."""
    db, batch = Path(db_path).resolve(), Path(batch_path).resolve()
    app = FastAPI(
        title="Factory Quality Demo API", version=VERSION,
        description="Local API over fictional manufacturing quality data. No equipment control or automatic release.",
        redoc_url=None,
        responses={status: {"model": Failure} for status in (400, 404, 409, 422, 503)},
    )
    # Install first so the local Host/Origin middleware below is outermost.
    install_plan_routes(app, db, batch, schema_path, model_config)

    @app.middleware("http")
    async def local_requests(request: Request, call_next):
        # CLI binds loopback. Host/Origin checks also prevent unrelated browser
        # pages from using this unauthenticated local demo as a write endpoint.
        authority = request.headers.get("host", "")
        host = authority.split(":")[0].lower()
        if host not in ("127.0.0.1", "localhost", "testserver"):
            return failure(400, "INVALID_HOST", "Use the local API address")
        origin = request.headers.get("origin")
        if origin and origin != "http://" + authority:
            return failure(403, "ORIGIN_NOT_ALLOWED", "Use the API from its own local origin")
        names = [k for k, _ in request.query_params.multi_items()]
        if len(names) != len(set(names)):
            return failure(422, "INVALID_ARGUMENT", "Duplicate query parameters are not allowed")
        # Routes without query models still reject unexpected parameters.
        allowed: dict[str, set[str]] = {
            "/api/v1/health": set(), "/api/v1/stats": set(), "/api/v1/ingest": set(),
            "/api/v1/compare-groups": set(), "/api/v1/packaging/evaluate-all": set(),
        }
        if request.url.path in allowed and names:
            return failure(422, "INVALID_ARGUMENT", "Unexpected query parameters")
        if request.url.path.startswith("/api/v1/checks/") and names:
            return failure(422, "INVALID_ARGUMENT", "Unexpected query parameters")
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request, exc):
        # Do not echo submitted values or entire request bodies in errors.
        details = [{"location": list(e["loc"]), "type": e["type"], "message": e["msg"]} for e in exc.errors()]
        return failure(422, "INVALID_ARGUMENT", "Request validation failed", details)

    @app.exception_handler(ProcessingError)
    async def business_error(request, exc):
        if exc.code.startswith("MODEL_"):
            status = 409 if exc.code in ("MODEL_SCOPE_MISMATCH", "MODEL_PROTOCOL_ERROR", "MODEL_CONFIGURATION_CHANGED") else 503
            return failure(status, exc.code, str(exc), exc.details)
        if exc.code in ("NOT_INGESTED", "INVALID_DATABASE"):
            return failure(503, exc.code, "Import the configured demo snapshot before querying")
        if exc.code in ("INVALID_BATCH", "CONFLICTING_RETRY"):
            return failure(409, exc.code, "Configured demo input failed integrity validation")
        if exc.code in ("SNAPSHOT_CONFLICT", "DECISION_CONFLICT", "AMBIGUOUS_SN"):
            return failure(409, exc.code, str(exc), exc.details)
        if exc.code.startswith(("CONTRACT_BUNDLE_", "PLAN_", "MEMBER", "SET_VERSION_", "COVERAGE_", "STALE_CONFIRMATION_")):
            status = 404 if exc.code == "PLAN_NOT_FOUND" else 403 if exc.code == "PLAN_PERMISSION_REVOKED" else 409
            return failure(status, exc.code, str(exc))
        status = 422 if exc.code == "INVALID_ARGUMENT" else 404
        return failure(status, exc.code, str(exc), exc.details)

    @app.exception_handler(sqlite3.Error)
    async def storage_error(request, exc):
        return failure(503, "STORAGE_ERROR", "Database is unavailable; retry after the local operation completes")

    @app.exception_handler(OSError)
    async def io_error(request, exc):
        return failure(503, "IO_ERROR", "Configured demo storage is unavailable")

    @app.exception_handler(HTTPException)
    async def http_error(request, exc):
        return failure(exc.status_code, "NOT_FOUND" if exc.status_code == 404 else "HTTP_ERROR", "Route not found" if exc.status_code == 404 else "HTTP request rejected")

    @app.exception_handler(Exception)
    async def unexpected_error(request, exc):
        return failure(500, "INTERNAL_ERROR", "An unexpected local processing error occurred")

    @app.get("/api/v1/health", response_model=Success, tags=["System"])
    def health():
        return success({"status": "running", "version": VERSION, "database_exists": db.is_file()})

    @app.post("/api/v1/ingest", response_model=Success, tags=["System"])
    def import_snapshot(body: EmptyInput):
        return success(ingest(batch, db, schema_path))

    @app.get("/api/v1/stats", response_model=Success, tags=["System"])
    def overview():
        with connection(db) as con:
            return success(stats(con))

    @app.get("/api/v1/lots", response_model=Success, tags=["Browse"])
    def lots(page: Annotated[Page, Query()]):
        with connection(db) as con:
            return success(_page(con, "SELECT COUNT(*) FROM lots",
                                 "SELECT * FROM lots ORDER BY lot_id LIMIT ? OFFSET ?", [], page))

    @app.get("/api/v1/units", response_model=Success, tags=["Browse"])
    def units(page: Annotated[UnitPage, Query()]):
        with connection(db) as con:
            return success(_page(con, "SELECT COUNT(*) FROM units WHERE (? IS NULL OR lot_id=?)",
                                 "SELECT * FROM units WHERE (? IS NULL OR lot_id=?) ORDER BY unit_id LIMIT ? OFFSET ?",
                                 [page.lot_id, page.lot_id], page))

    @app.get("/api/v1/equipment", response_model=Success, tags=["Browse"])
    def equipment(page: Annotated[Page, Query()]):
        with connection(db) as con:
            return success(_page(con, "SELECT COUNT(*) FROM equipment",
                                 "SELECT * FROM equipment ORDER BY equipment_id LIMIT ? OFFSET ?", [], page))

    @app.get("/api/v1/operations", response_model=Success, tags=["Browse"])
    def operations(page: Annotated[OperationPage, Query()]):
        with connection(db) as con:
            return success(_page(con,
                "SELECT COUNT(*) FROM operations WHERE (? IS NULL OR lot_id=?) AND (? IS NULL OR equipment_id=?) AND (? IS NULL OR (recorded_at<=? AND track_out_at<=?))",
                "SELECT * FROM operations WHERE (? IS NULL OR lot_id=?) AND (? IS NULL OR equipment_id=?) AND (? IS NULL OR (recorded_at<=? AND track_out_at<=?)) ORDER BY track_in_at,operation_id LIMIT ? OFFSET ?",
                [page.lot_id, page.lot_id, page.equipment_id, page.equipment_id, page.as_of, page.as_of, page.as_of], page))

    @app.get("/api/v1/checks", response_model=Success, tags=["Packaging"])
    def checks(page: Annotated[CheckPage, Query()]):
        with connection(db) as con:
            return success(_page(con, "SELECT COUNT(*) FROM packaging_checks WHERE (? IS NULL OR unit_id=?)",
                "SELECT * FROM packaging_checks WHERE (? IS NULL OR unit_id=?) ORDER BY check_at,check_id LIMIT ? OFFSET ?",
                [page.unit_id, page.unit_id], page))

    @app.get("/api/v1/checks/{check_id}/preview", response_model=Success, tags=["Packaging"])
    def preview(check_id: Identifier):
        with connection(db) as con:
            return success({"check_id": check_id, **evaluate_check(con, check_id), "persisted": False})

    @app.post("/api/v1/checks/{check_id}/evaluate", response_model=Success, tags=["Packaging"])
    def evaluate(check_id: Identifier, body: EmptyInput):
        with connection(db, writable=True) as con:
            return success(evaluate_and_record(con, check_id))

    @app.post("/api/v1/packaging/evaluate-all", response_model=Success, tags=["Packaging"])
    def evaluate_all(body: EmptyInput):
        with connection(db, writable=True) as con:
            return success(evaluate_and_record(con))

    @app.get("/api/v1/decisions", response_model=Success, tags=["Packaging"])
    def decisions(page: Annotated[DecisionPage, Query()]):
        with connection(db) as con:
            result = _page(con,
                "SELECT COUNT(*) FROM packaging_decisions WHERE (? IS NULL OR check_id=?) AND (? IS NULL OR decision=?)",
                "SELECT * FROM packaging_decisions WHERE (? IS NULL OR check_id=?) AND (? IS NULL OR decision=?) ORDER BY evaluated_at,decision_id LIMIT ? OFFSET ?",
                [page.check_id, page.check_id, page.decision, page.decision], page)
            for row in result["items"]:
                row["result"] = json.loads(row.pop("result_json"))
            return success(result)

    @app.get("/api/v1/trace", response_model=Success, tags=["Analysis"])
    def trace(params: Annotated[TraceInput, Query()]):
        with connection(db) as con:
            return success(trace_lookup(con, params.as_of, unit_id=params.unit_id, sn=params.sn, lot_id=params.lot_id))

    @app.get("/api/v1/operations/{operation_id}/neighbors", response_model=Success, tags=["Analysis"])
    def neighbor_operations(operation_id: Identifier, params: Annotated[NeighborInput, Query()]):
        with connection(db) as con:
            return success(neighbors(con, operation_id, params.as_of, params.before, params.after, params.same_product))

    @app.get("/api/v1/concentration", response_model=Success, tags=["Analysis"])
    def binding_concentration(params: Annotated[ConcentrationInput, Query()]):
        with connection(db) as con:
            return success(concentration(con, params.equipment_id, params.start, params.end))

    @app.post("/api/v1/compare-groups", response_model=Success, tags=["Analysis"])
    def group_comparison(body: CompareInput):
        with connection(db) as con:
            return success(compare_groups(con, body.as_of, body.groups))

    return app
