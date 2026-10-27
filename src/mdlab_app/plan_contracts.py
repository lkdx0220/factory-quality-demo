"""Content-addressed preflight contracts. No model text or executable plugins."""

import copy
import importlib.metadata
import json
import platform
from pathlib import Path

from .errors import ProcessingError, utc_timestamp
from .ingestion import canonical, digest
from .publication_templates_config import OUTPUT_CONTRACT
from .model_contracts import PLANNING_CONTRACT

ROOT = Path(__file__).resolve().parents[2]
NOTICE = "查询计划预检；未发布分析答案或产品合格结论。"
SPEC = {
    "protocol": "plan-preflight-1",
    "intent": "lot_operation_inventory",
    "scope": "one exact LOT ID; all operations known and started at as_of",
    "identity": "exact primary key; SN, subsets and quality verdicts unsupported",
    "evidence_dag": ["lots", "operations"],
    "required_claims": ["lot_id", "as_of", "operation_count", "complete_members", "exclusions"],
    "metric": "operation records, not units or test results",
    "visibility": "recorded_at <= as_of AND track_in_at <= as_of; hide future track_out",
    "order": ["track_in_at", "operation_id"],
    "coverage": "each required source, exact inventory, retained history, inclusive recorded watermark",
    "page_size": 10,
    "max_pages": 1000,
    "ttl_seconds": 1800,
    "execution": {
        "schema": "evidence-execution-1",
        "dag": [{"key": "lots", "depends_on": []}, {"key": "operations", "depends_on": ["lots"]}],
        "attempt_timeout_seconds": 5,
        "total_timeout_seconds": 15,
        "max_attempts": 2,
        "retry_codes": ["SOURCE_BUSY"],
        "max_response_bytes": 1048576,
        "cancellation_poll_seconds": 0.05,
        "publication": False,
    },
    "facts": {
        "registry_version": "lot-inventory-facts-1",
        "metric_version": "lot-inventory-1",
        "rule_version": "lot-visibility-1",
        "denominator_domain": "all_visible_operation_records",
        "summary_keys": ["lot_id", "as_of", "operation_count", "member_set_digest",
                         "membership_complete", "exclusion_count", "metric_applicability"],
        "member_fields": ["operation_id", "lot_id", "process_step", "equipment_id",
                          "track_in_at", "track_out_at", "recorded_at"],
        "algorithms": ["count_unique_operations_v1", "canonical_members_digest_v1",
                       "complete_membership_v1", "bound_exclusions_count_v1", "inventory_applicability_v1"],
        "max_facts": 2000,
        "publication": False,
    },
    "publication": OUTPUT_CONTRACT,
    "planning": PLANNING_CONTRACT,
    "output_schema": "preflight-1; no model fields, no published answer",
    "templates": {"notice": NOTICE, "ready": "范围预检完成，等待确认", "blocked": "范围预检未完成"},
}


def hash_object(value):
    return digest(canonical(value).encode("utf-8"))


class BundleRegistry:
    """Only this loaded engine is supported. Changed code requires a new process.

    Multiple release identities may use the same verified engine. Old engines
    are deliberately unavailable rather than being silently mapped to new code.
    All management is server-side under PlanService.lock, never an HTTP option.
    """

    def __init__(self, coverage_path, *, planning_profile=None):
        self.planning_profile = copy.deepcopy(planning_profile)
        self.paths = {p.relative_to(ROOT).as_posix(): p for p in (ROOT / "src/mdlab_app").glob("*.py")}
        self.paths["schemas/csv-schema.json"] = ROOT / "schemas/csv-schema.json"
        self.paths["requirements-api.txt"] = ROOT / "requirements-api.txt"
        for folder in ("frontend/src", "frontend/dist"):
            for path in (ROOT / folder).rglob("*"):
                if path.is_file():
                    self.paths[path.relative_to(ROOT).as_posix()] = path
        for name in ("frontend/package.json", "frontend/package-lock.json"):
            self.paths[name] = ROOT / name
        self.paths["coverage-declaration"] = Path(coverage_path)
        self.loaded = self._files()
        self.dependencies = self._dependencies()
        self.artifacts = {}
        self.revoked = set()
        self.default = self.register("preflight-1")

    def _files(self):
        return {name: digest(path.read_bytes()) if path.is_file() else None for name, path in self.paths.items()}

    @staticmethod
    def _dependencies():
        return {"python": platform.python_version(), **{name: importlib.metadata.version(name)
                for name in ("fastapi", "pydantic", "starlette")}}

    def register(self, release):
        artifact = {"release": release, "contract": copy.deepcopy(SPEC), "files": self.loaded.copy(),
                    "dependencies": self.dependencies.copy(), "planning_profile": copy.deepcopy(self.planning_profile)}
        identity = hash_object(artifact)
        self.artifacts[identity] = artifact
        return identity

    def require(self, identity):
        if identity in self.revoked:
            raise ProcessingError("CONTRACT_BUNDLE_REVOKED", "Contract bundle was revoked")
        artifact = self.artifacts.get(identity)
        if artifact is None:
            raise ProcessingError("CONTRACT_BUNDLE_UNAVAILABLE", "Exact contract bundle is unavailable; create a new draft")
        if (hash_object(artifact) != identity or artifact["files"] != self._files()
                or artifact["dependencies"] != self._dependencies() or artifact["contract"] != SPEC
                or artifact["planning_profile"] != self.planning_profile):
            raise ProcessingError("CONTRACT_BUNDLE_MISMATCH", "Loaded implementation does not match the frozen bundle")
        return copy.deepcopy(artifact)


def coverage_receipt(snapshot, declaration, lot_id, as_of, bundle):
    """A source declaration, never MAX(timestamp) or import completion time."""
    result = {"status": "unknown", "snapshot_digest": snapshot.data_digest, "contract_bundle_hash": bundle,
              "declaration_digest": hash_object(declaration), "scope": [lot_id], "as_of": as_of,
              "source": "closed fictional scenario only", "sources": {}, "gaps": []}
    if not isinstance(declaration, dict) or declaration.get("kind") != "closed_fictional_world":
        result["gaps"] = ["NO_TRUSTED_DECLARATION"]
        return result
    if (declaration.get("data_digest") != snapshot.data_digest
            or declaration.get("manifest_digest") != snapshot.manifest_digest):
        result["gaps"] = ["DECLARATION_SNAPSHOT_MISMATCH"]
        return result
    inventory = {row["lot_id"]: sorted(op["operation_id"] for op in snapshot.tables["operations"]
                 if op["lot_id"] == row["lot_id"]) for row in snapshot.tables["lots"]}
    if declaration.get("inventory") != inventory:
        result.update(status="not_covered", gaps=["SCOPE_INVENTORY_MISMATCH"])
        return result
    result.update(producer=declaration.get("producer"), semantics=declaration.get("semantics"),
                  version=declaration.get("version"))
    known_gap, unknown = [], []
    sources = declaration.get("sources")
    for name in SPEC["evidence_dag"]:
        source = sources.get(name) if isinstance(sources, dict) else None
        if not isinstance(source, dict):
            unknown.append(name + ":MISSING_SOURCE")
            continue
        try:
            start, end = source["history_from"], source["recorded_through"]
            utc_timestamp(start)
            utc_timestamp(end)
            if start > end or type(source.get("history_retained")) is not bool:
                raise ValueError()
        except (KeyError, ValueError, ProcessingError):
            unknown.append(name + ":INVALID_WATERMARK")
            continue
        result["sources"][name] = copy.deepcopy(source)
        if not source["history_retained"] or not start <= as_of <= end:
            known_gap.append(name + ":TIME_OR_HISTORY_GAP")
    result["gaps"] = known_gap + unknown
    result["status"] = "not_covered" if known_gap else "unknown" if unknown else "covered"
    return result


def read_declaration(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
