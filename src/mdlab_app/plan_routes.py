"""Strict HTTP boundary for the preflight slice, with server-issued sessions."""

from typing import Annotated, Literal
import sqlite3

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator

from .errors import ProcessingError, utc_timestamp
from .plan_service import PlanService
from .evidence_execution import ExecutionService
from .fact_contracts import ClaimProposal
from .fact_service import FactService
from .publication_contracts import PublishInput
from .publication_service import PublicationService
from .model_adapter import ModelConfig
from .model_contracts import AdoptInput, QuestionInput
from .model_planner import PlannerService

Id = Annotated[str, Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.:-]+$")]
Sha = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]


class DraftInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    intent_key: Literal["lot_operation_inventory"]
    original_input: str = Field(max_length=2000)
    lot_id: Id
    as_of: str

    @field_validator("as_of")
    @classmethod
    def valid_time(cls, value):
        try:
            utc_timestamp(value)
        except ProcessingError as exc:
            raise ValueError("Use UTC YYYY-MM-DDTHH:MM:SSZ") from exc
        return value


class ConfirmInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision: StrictInt = Field(ge=1)
    card_digest: Sha


class ReviseInput(DraftInput, ConfirmInput):
    pass


class ExecuteInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scope_hash: Sha


class EmptyInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class MemberPage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    set_version: Sha
    offset: int = Field(default=0, ge=0, le=100000)
    limit: int = Field(default=10, ge=1, le=100)


class FactPage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    fact_set_digest: Sha
    offset: int = Field(default=0, ge=0, le=100000)
    limit: int = Field(default=10, ge=1, le=100)


class PublicationPage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    publication_digest: Sha
    offset: int = Field(default=0, ge=0, le=100000)
    limit: int = Field(default=10, ge=1, le=100)


def install_plan_routes(app, db, batch, schema, model_config=None):
    config = model_config or ModelConfig()
    service = PlanService(db, batch, schema, planning_profile=config.profile())
    app.state.plan_service = service
    executions = ExecutionService(service)
    app.state.execution_service = executions
    facts = FactService(service, executions)
    app.state.fact_service = facts
    publications = PublicationService(facts)
    app.state.publication_service = publications
    planner = PlannerService(service, config)
    app.state.planner_service = planner
    router = APIRouter(prefix="/api/v1/query-plans", tags=["Controlled query planning"])

    @app.middleware("http")
    async def plan_session(request: Request, call_next):
        if not request.url.path.startswith("/api/v1/query-plans"):
            return await call_next(request)
        # Existing outer Host/Origin middleware runs first (registered later).
        names = set(request.query_params)
        if names and not request.url.path.endswith("/members"):
            return JSONResponse(status_code=422, content={"ok": False, "synthetic": True,
                                "error": {"code": "INVALID_ARGUMENT", "message": "Unexpected query parameters"}})
        token = request.cookies.get("demo_plan_session")
        try:
            owner = service.session(token)
        except (sqlite3.Error, OSError):
            return JSONResponse(status_code=503, content={"ok": False, "synthetic": True,
                                "error": {"code": "STORAGE_ERROR", "message": "Plan storage is unavailable"}})
        request.state.plan_owner = owner
        response = await call_next(request)
        if token != owner:
            response.set_cookie("demo_plan_session", owner, httponly=True, samesite="strict", path="/api/v1/query-plans")
        response.headers["Cache-Control"] = "no-store"
        return response

    def wrap(data):
        return {"ok": True, "synthetic": True, "data": data}

    @router.get("/nl/config")
    def planner_configuration():
        return wrap(planner.configuration())

    @router.post("/nl/proposals")
    def propose_question(request: Request, body: QuestionInput):
        return wrap(planner.propose(request.state.plan_owner, body.question))

    @router.get("/nl/proposals/{identity}")
    def read_proposal(request: Request, identity: Id):
        return wrap(planner.read(request.state.plan_owner, identity))

    @router.post("/nl/proposals/{identity}/preflight")
    def proposal_preflight(request: Request, identity: Id, body: AdoptInput):
        return wrap(planner.preflight(request.state.plan_owner, identity, body.proposal_digest))

    @router.post("/requests")
    def create(request: Request, body: DraftInput):
        return wrap(service.create(request.state.plan_owner, body.original_input, body.lot_id, body.as_of))

    @router.get("/requests/{identity}")
    def read(request: Request, identity: Id):
        return wrap(service.read(request.state.plan_owner, identity))

    @router.post("/requests/{identity}/revise")
    def revise(request: Request, identity: Id, body: ReviseInput):
        return wrap(service.revise(request.state.plan_owner, identity, body.revision, body.card_digest,
                                   body.original_input, body.lot_id, body.as_of))

    @router.post("/requests/{identity}/confirm")
    def confirm(request: Request, identity: Id, body: ConfirmInput):
        return wrap(service.confirm(request.state.plan_owner, identity, body.revision, body.card_digest))

    @router.get("/requests/{identity}/members")
    def members(request: Request, identity: Id, page: Annotated[MemberPage, Query()]):
        return wrap(service.members(request.state.plan_owner, identity, page.set_version, page.offset, page.limit))

    @router.get("/requests/{identity}/export")
    def export(request: Request, identity: Id):
        return JSONResponse(wrap(service.export(request.state.plan_owner, identity)),
                            headers={"Content-Disposition": 'attachment; filename="query-plan-preflight.json"'})

    @router.get("/confirmed/{identity}")
    def confirmed(request: Request, identity: Id):
        return wrap(service.confirmed(request.state.plan_owner, identity))

    @router.get("/confirmed/{identity}/members")
    def confirmed_members(request: Request, identity: Id, page: Annotated[MemberPage, Query()]):
        return wrap(service.members(request.state.plan_owner, identity, page.set_version, page.offset, page.limit, confirmed=True))

    @router.get("/confirmed/{identity}/export")
    def confirmed_export(request: Request, identity: Id):
        return JSONResponse(wrap(service.export(request.state.plan_owner, identity, confirmed=True)),
                            headers={"Content-Disposition": 'attachment; filename="confirmed-query-plan-preflight.json"'})

    @router.post("/confirmed/{identity}/execute")
    def execute(request: Request, identity: Id, body: ExecuteInput):
        return wrap(executions.start(request.state.plan_owner, identity, body.scope_hash))

    @router.get("/executions/{identity}")
    def execution(request: Request, identity: Id):
        return wrap(executions.read(request.state.plan_owner, identity))

    @router.post("/executions/{identity}/cancel")
    def cancel_execution(request: Request, identity: Id, body: EmptyInput):
        return wrap(executions.read(request.state.plan_owner, identity, cancel=True))

    @router.get("/executions/{identity}/export")
    def export_execution(request: Request, identity: Id):
        return JSONResponse(wrap(executions.export(request.state.plan_owner, identity)),
                            headers={"Content-Disposition": 'attachment; filename="evidence-execution-ledger.json"'})

    @router.post("/executions/{identity}/facts")
    def create_facts(request: Request, identity: Id, body: EmptyInput):
        return wrap(facts.create(request.state.plan_owner, identity))

    @router.get("/fact-sets/{identity}")
    def read_facts(request: Request, identity: Id):
        return wrap(facts.read(request.state.plan_owner, identity))

    @router.get("/fact-sets/{identity}/members")
    def fact_members(request: Request, identity: Id, page: Annotated[FactPage, Query()]):
        return wrap(facts.members(request.state.plan_owner, identity, page.fact_set_digest, page.offset, page.limit))

    @router.get("/fact-sets/{identity}/proposal")
    def fact_proposal(request: Request, identity: Id):
        return wrap(facts.proposal(request.state.plan_owner, identity))

    @router.post("/fact-sets/{identity}/validate")
    def validate_facts(request: Request, identity: Id, body: ClaimProposal):
        return wrap(facts.validate(request.state.plan_owner, identity, body.model_dump()))

    @router.get("/fact-sets/{identity}/export")
    def export_facts(request: Request, identity: Id):
        return JSONResponse(wrap(facts.export(request.state.plan_owner, identity)),
                            headers={"Content-Disposition": 'attachment; filename="fact-registry.json"'})

    @router.post("/fact-sets/{identity}/publish")
    def publish_answer(request: Request, identity: Id, body: PublishInput):
        return wrap(publications.publish(request.state.plan_owner, identity, body.model_dump()))

    @router.get("/publications/{identity}")
    def read_publication(request: Request, identity: Id):
        return wrap(publications.read(request.state.plan_owner, identity))

    @router.get("/publications/{identity}/members")
    def publication_members(request: Request, identity: Id, page: Annotated[PublicationPage, Query()]):
        return wrap(publications.members(request.state.plan_owner, identity, page.publication_digest, page.offset, page.limit))

    @router.get("/publications/{identity}/export")
    def export_publication(request: Request, identity: Id):
        return JSONResponse(wrap(publications.export(request.state.plan_owner, identity)),
                            headers={"Content-Disposition": 'attachment; filename="verified-lot-inventory.json"'})

    app.include_router(router)
