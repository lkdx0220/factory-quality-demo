"""Read-only LOT-operation preflight; this is not the answer publisher."""

import copy
import json
import secrets
import sqlite3
import threading
import time
from contextlib import contextmanager

from .errors import ProcessingError, utc_timestamp
from .ingestion import canonical, load_snapshot
from .plan_contracts import BundleRegistry, ROOT, SPEC, coverage_receipt, hash_object, read_declaration
from .storage import connection


def new_id(prefix):
    return prefix + secrets.token_hex(16)


def inventory(snapshot, lot_id, as_of):
    lots = [r for r in snapshot.tables["lots"] if r["lot_id"] == lot_id and r["created_at"] <= as_of]
    if not lots:
        raise ProcessingError("LOT_NOT_VISIBLE", "LOT does not exist at the requested time")
    rows = []
    for row in snapshot.tables["operations"]:
        if row["lot_id"] != lot_id or row["recorded_at"] > as_of or row["track_in_at"] > as_of:
            continue
        visible = dict(row)
        if visible["track_out_at"] and visible["track_out_at"] > as_of:
            visible["track_out_at"] = None
        rows.append(visible)
    return sorted(rows, key=lambda row: (row["track_in_at"], row["operation_id"]))


def snapshot_page(rows, offset, limit):
    """Default adapter reads an already validated immutable source snapshot."""
    page = rows[offset:offset + limit]
    return {"items": page, "total": len(rows), "next_offset": offset + len(page) if offset + len(page) < len(rows) else None,
            "set_version": hash_object(rows), "complete": True, "truncated": False}


def _discovery_page_error(page, rows, offset, total, version):
    """Validate one page's completeness and exact snapshot membership."""
    if total != page["total"] or page.get("set_version") != version:
        return "DISCOVERY_VERSION_DRIFT"
    if page.get("complete") is not True or page.get("truncated") is not False:
        return "DISCOVERY_TRUNCATED"
    items = page["items"]
    if not items and page.get("next_offset") is not None:
        return "EMPTY_PAGE_WITH_CURSOR"
    if len(items) > SPEC["page_size"] or items != rows[offset:offset + len(items)]:
        return "DISCOVERY_MEMBERSHIP_MISMATCH"
    return None


def _fetch_discovery_page(fetch_page, rows, call):
    try:
        page = fetch_page(copy.deepcopy(rows), call["offset"], SPEC["page_size"])
    except Exception:
        call["status"] = "failed"
        return None, "DISCOVERY_PAGE_FAILED"
    call["status"] = "returned"
    if not isinstance(page, dict) or not isinstance(page.get("items"), list):
        return None, "INVALID_DISCOVERY_PAGE"
    reported = page.get("total")
    if type(reported) is not int or reported < 0:
        return None, "UNKNOWN_TOTAL"
    return page, None


def resolve(rows, fetch_page):
    """Check pagination against independent complete snapshot inventory, not count alone."""
    collected, calls, errors, offset, seen = [], [], [], 0, set()
    total, version = None, hash_object(rows)
    for _ in range(SPEC["max_pages"]):
        if offset in seen:
            errors.append("CURSOR_LOOP")
            break
        seen.add(offset)
        call = {"offset": offset, "call_id": new_id("discovery-"), "status": "started"}
        calls.append(call)
        page, error = _fetch_discovery_page(fetch_page, rows, call)
        if error:
            errors.append(error)
            break
        if total is None:
            total = page["total"]
        error = _discovery_page_error(page, rows, offset, total, version)
        if error:
            errors.append(error)
            break
        items = page["items"]
        collected.extend(items)
        following = page.get("next_offset")
        if following is None:
            break
        if type(following) is not int or following != offset + len(items):
            errors.append("INVALID_CURSOR")
            break
        offset = following
    else:
        errors.append("DISCOVERY_BUDGET_EXHAUSTED")
    if collected != rows or total != len(rows):
        errors.append("DISCOVERY_NOT_EXHAUSTIVE")
    return collected, {"resolution_complete": not errors, "fetched_count": len(collected), "total_count": total,
                       "expected_snapshot_count": len(rows), "set_version": version, "calls": calls,
                       "cursor_exhausted": not errors, "truncated": "DISCOVERY_TRUNCATED" in errors,
                       "conflict_count": 0, "conflicts_complete": True, "identity_mode": "exact_lot_primary_key",
                       "failed_scope": errors, "not_attempted_scope": [] if not errors else
                       [{"from_offset": len(collected), "snapshot_total": len(rows)}]}


class PlanService:
    """One local service; durable CAS plus a shared lock for policy transitions.

    No HTTP fault flags. Tests inject a page adapter/clock or change server-only
    policy under this same lock. This demo is single-process and single-user;
    the random HttpOnly session token isolates browser sessions, not enterprise IAM.
    """

    def __init__(self, db, batch, schema=None, *, coverage_path=None, fetch_page=snapshot_page, clock=time.time, planning_profile=None):
        self.db, self.batch, self.schema = db, batch, schema
        self.path = db.with_name(db.name + ".plans.sqlite3")
        self.coverage_path = coverage_path or ROOT / "contracts/demo-closed-world.json"
        self.fetch_page, self.clock = fetch_page, clock
        self.lock = threading.RLock()
        self.bundles = BundleRegistry(self.coverage_path, planning_profile=planning_profile)
        self.revoked_sessions, self.revoked_coverage = set(), set()

    @contextmanager
    def transaction(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(self.path, timeout=5)
        con.row_factory = sqlite3.Row
        try:
            con.execute("BEGIN IMMEDIATE")
            con.execute("CREATE TABLE IF NOT EXISTS sessions (id TEXT PRIMARY KEY)")
            con.execute("CREATE TABLE IF NOT EXISTS revocations (kind TEXT NOT NULL, identity TEXT NOT NULL, PRIMARY KEY(kind,identity))")
            con.execute("CREATE TABLE IF NOT EXISTS drafts (id TEXT PRIMARY KEY, owner TEXT NOT NULL, revision INTEGER NOT NULL, card_digest TEXT NOT NULL, document TEXT NOT NULL)")
            con.execute("CREATE TABLE IF NOT EXISTS plans (id TEXT PRIMARY KEY, owner TEXT NOT NULL, document TEXT NOT NULL, digest TEXT NOT NULL)")
            targets = {"session": self.revoked_sessions, "coverage": self.revoked_coverage, "bundle": self.bundles.revoked}
            for row in con.execute("SELECT kind,identity FROM revocations"):
                targets[row["kind"]].add(row["identity"])
            yield con
            con.commit()
        except BaseException:
            con.rollback()
            raise
        finally:
            con.close()

    def session(self, token):
        with self.lock, self.transaction() as con:
            if token and con.execute("SELECT id FROM sessions WHERE id=?", (token,)).fetchone():
                return token
            owner = new_id("session-")
            con.execute("INSERT INTO sessions VALUES (?)", (owner,))
            return owner

    def revoke(self, kind, identity):
        """Server-only durable policy update; not an HTTP capability."""
        if kind not in ("session", "coverage", "bundle"):
            raise ValueError("Unsupported revocation kind")
        with self.lock, self.transaction() as con:
            con.execute("INSERT OR IGNORE INTO revocations VALUES (?,?)", (kind, identity))
            {"session": self.revoked_sessions, "coverage": self.revoked_coverage,
             "bundle": self.bundles.revoked}[kind].add(identity)

    def _snapshot(self):
        snapshot = load_snapshot(self.batch, self.schema)
        # Existing DB remains a prerequisite, but is NOT the query source. This
        # slice reads verified source files so changed SQLite rows cannot drift it.
        with connection(self.db) as con:
            meta = con.execute("SELECT * FROM ingest_meta WHERE singleton=1").fetchone()
            if meta["data_digest"] != snapshot.data_digest or meta["manifest_digest"] != snapshot.manifest_digest:
                raise ProcessingError("SNAPSHOT_CONFLICT", "Configured input and imported snapshot differ")
        return snapshot

    def _permission(self, owner):
        if owner in self.revoked_sessions:
            raise ProcessingError("PLAN_PERMISSION_REVOKED", "Session access was revoked")

    def _build(self, owner, original_input, lot_id, as_of, request_id, revision, planning_origin=None):
        self._permission(owner)
        utc_timestamp(as_of)
        bundle = self.bundles.default
        self.bundles.require(bundle)
        snapshot = self._snapshot()
        rows = inventory(snapshot, lot_id, as_of)
        members, receipt = resolve(rows, self.fetch_page)
        coverage = coverage_receipt(snapshot, read_declaration(self.coverage_path), lot_id, as_of, bundle)
        if coverage["declaration_digest"] in self.revoked_coverage:
            coverage.update(status="not_covered", gaps=["COVERAGE_REVOKED"])
        ready = receipt["resolution_complete"] and coverage["status"] == "covered"
        scope = {"lot_id": lot_id, "as_of": as_of, "selection": "all_visible_operations", "intent_key": SPEC["intent"],
                 "predicate": SPEC["visibility"], "order": SPEC["order"], "exclusions": [],
                 "source": "verified fictional source files; not live SQLite query results"}
        binding = {"request_id": request_id, "draft_revision": revision, "snapshot_digest": snapshot.data_digest,
                   "contract_bundle_hash": bundle, "scope": scope, "authorization": "local-session-full-demo"}
        receipt.update(binding)
        member_set = {"set_handle": new_id("set-"), "set_version": hash_object(members), "count": len(members),
                      "digest": hash_object(members), "members": members, "exclusions": [], "readable": True,
                      "snapshot_digest": snapshot.data_digest, "draft_revision": revision}
        now = self.clock()
        doc = {"request_id": request_id, "revision": revision, "original_input": original_input,
               "original_input_label": "用户输入（仅供核对，未经事实背书）", "scope": scope,
               "snapshot_digest": snapshot.data_digest, "manifest_digest": snapshot.manifest_digest,
               "contract_bundle_hash": bundle, "resolution_receipt": receipt, "coverage_receipt": coverage,
               "member_set": member_set, "created_at_epoch": now, "expires_at_epoch": now + SPEC["ttl_seconds"],
               "state": "awaiting_confirmation" if ready else "incomplete", "can_confirm": ready,
               "confirmed_plan_id": None, "answer_published": False, "notice": SPEC["templates"]["notice"]}
        if planning_origin is not None:
            doc["planning_origin"] = copy.deepcopy(planning_origin)
        doc["card_digest"] = hash_object(doc)
        return doc

    def _create(self, con, owner, original_input, lot_id, as_of, planning_origin=None):
        doc = self._build(owner, original_input, lot_id, as_of, new_id("request-"), 1, planning_origin)
        con.execute("INSERT INTO drafts VALUES (?,?,?,?,?)", (doc["request_id"], owner, 1, doc["card_digest"], canonical(doc)))
        return self._view(doc)

    def create(self, owner, original_input, lot_id, as_of):
        with self.lock, self.transaction() as con:
            return self._create(con, owner, original_input, lot_id, as_of)

    def _draft(self, con, owner, identity):
        self._permission(owner)
        row = con.execute("SELECT * FROM drafts WHERE id=? AND owner=?", (identity, owner)).fetchone()
        if row is None:
            raise ProcessingError("PLAN_NOT_FOUND", "Query plan not found in this session")
        doc = json.loads(row["document"])
        unsealed = {k: v for k, v in doc.items() if k != "card_digest"}
        if (hash_object(unsealed) != row["card_digest"] or doc["card_digest"] != row["card_digest"]
                or doc["revision"] != row["revision"]):
            raise ProcessingError("PLAN_INTEGRITY_ERROR", "Stored confirmation card failed integrity validation")
        return doc

    def revise(self, owner, identity, revision, card_digest, original_input, lot_id, as_of):
        with self.lock, self.transaction() as con:
            old = self._draft(con, owner, identity)
            self._cas(old, revision, card_digest)
            doc = self._build(owner, original_input, lot_id, as_of, identity, revision + 1)
            changed = con.execute("UPDATE drafts SET revision=?,card_digest=?,document=? WHERE id=? AND revision=? AND card_digest=?",
                                  (doc["revision"], doc["card_digest"], canonical(doc), identity, revision, card_digest)).rowcount
            if changed != 1:
                raise ProcessingError("STALE_CONFIRMATION_CARD", "Confirmation card changed")
            return self._view(doc)

    @staticmethod
    def _cas(doc, revision, card_digest):
        if type(revision) is not int or doc["revision"] != revision or doc["card_digest"] != card_digest:
            raise ProcessingError("STALE_CONFIRMATION_CARD", "Card changed; inspect the new scope before confirming")

    def _check(self, owner, doc, *, require_ready=False):
        self._permission(owner)
        self.bundles.require(doc["contract_bundle_hash"])
        if self.clock() >= doc["expires_at_epoch"]:
            raise ProcessingError("PLAN_EXPIRED", "Plan expired; create and inspect a new draft")
        snapshot = self._snapshot()
        if snapshot.data_digest != doc["snapshot_digest"] or snapshot.manifest_digest != doc["manifest_digest"]:
            raise ProcessingError("SNAPSHOT_CONFLICT", "Frozen snapshot is no longer available")
        coverage = coverage_receipt(snapshot, read_declaration(self.coverage_path), doc["scope"]["lot_id"],
                                    doc["scope"]["as_of"], doc["contract_bundle_hash"])
        if coverage["declaration_digest"] in self.revoked_coverage:
            coverage.update(status="not_covered", gaps=["COVERAGE_REVOKED"])
        if (coverage != doc["coverage_receipt"] or coverage["declaration_digest"] in self.revoked_coverage):
            raise ProcessingError("COVERAGE_CHANGED", "Coverage changed or was revoked; create a new draft")
        member_set = doc["member_set"]
        if member_set.get("readable") is not True or member_set["digest"] != hash_object(member_set["members"]):
            raise ProcessingError("MEMBERS_UNREADABLE", "Bound member list is unavailable or damaged")
        if doc["resolution_receipt"]["resolution_complete"]:
            expected = inventory(snapshot, doc["scope"]["lot_id"], doc["scope"]["as_of"])
            if (member_set["members"] != expected or member_set["set_version"] != hash_object(expected)
                    or member_set["count"] != len(expected)):
                raise ProcessingError("MEMBER_SCOPE_MISMATCH", "Member list does not match the requested LOT and time")
        if require_ready and (doc["can_confirm"] is not True or not doc["resolution_receipt"]["resolution_complete"]
                              or coverage["status"] != "covered"):
            raise ProcessingError("PLAN_NOT_CONFIRMABLE", "Discovery and coverage must complete before confirmation")

    @staticmethod
    def _view(doc):
        result = copy.deepcopy(doc)
        member_set = result["member_set"]
        member_set["preview"] = member_set.pop("members")[:3]
        base = "/api/v1/query-plans/requests/" + doc["request_id"]
        result["members_url"] = base + "/members?set_version=" + member_set["set_version"]
        result["export_url"] = base + "/export"
        return result

    def read(self, owner, identity):
        with self.lock, self.transaction() as con:
            doc = self._draft(con, owner, identity)
            self._check(owner, doc)
            return self._view(doc)

    def confirm(self, owner, identity, revision, card_digest):
        with self.lock, self.transaction() as con:
            doc = self._draft(con, owner, identity)
            self._cas(doc, revision, card_digest)
            self._check(owner, doc, require_ready=True)
            # A frozen plan is a separate append-only record. Confirmation does
            # not rewrite the card, so repeat clicks can safely reuse this record.
            existing = con.execute("SELECT id,document FROM plans WHERE owner=?", (owner,)).fetchall()
            for row in existing:
                saved = json.loads(row["document"])
                if saved["card"]["request_id"] == identity and saved["card"]["card_digest"] == card_digest:
                    return self._plan_view(saved, reused=True)
            scope_hash = hash_object({"scope": doc["scope"], "snapshot": doc["snapshot_digest"],
                                      "member_set": doc["member_set"], "bundle": doc["contract_bundle_hash"]})
            plan = {"confirmed_plan_id": new_id("plan-"), "scope_hash": scope_hash, "card": doc,
                    "confirmed_at_epoch": self.clock(), "stage": "preflight_confirmed", "answer_published": False}
            con.execute("INSERT INTO plans VALUES (?,?,?,?)", (plan["confirmed_plan_id"], owner, canonical(plan), hash_object(plan)))
            return self._plan_view(plan)

    def _plan(self, con, owner, identity):
        self._permission(owner)
        row = con.execute("SELECT * FROM plans WHERE id=? AND owner=?", (identity, owner)).fetchone()
        if row is None:
            raise ProcessingError("PLAN_NOT_FOUND", "Confirmed plan not found in this session")
        plan = json.loads(row["document"])
        if hash_object(plan) != row["digest"]:
            raise ProcessingError("PLAN_INTEGRITY_ERROR", "Stored plan failed integrity validation")
        self._check(owner, plan["card"], require_ready=True)
        return plan

    @staticmethod
    def _plan_view(plan, reused=False):
        result = {key: value for key, value in plan.items() if key != "card"}
        result.update(card=PlanService._view(plan["card"]), reused=reused)
        base = "/api/v1/query-plans/confirmed/" + plan["confirmed_plan_id"]
        result["members_url"] = base + "/members?set_version=" + plan["card"]["member_set"]["set_version"]
        result["export_url"] = base + "/export"
        return result

    def confirmed(self, owner, identity):
        """G1-ready preflight recheck only. No G2-G4 or answer publication."""
        with self.lock, self.transaction() as con:
            return self._plan_view(self._plan(con, owner, identity))

    def members(self, owner, identity, version, offset, limit, *, confirmed=False):
        with self.lock, self.transaction() as con:
            if confirmed:
                doc = self._plan(con, owner, identity)["card"]
            else:
                doc = self._draft(con, owner, identity)
                self._check(owner, doc)
            member_set = doc["member_set"]
            if version != member_set["set_version"]:
                raise ProcessingError("SET_VERSION_MISMATCH", "Member link belongs to a different set revision")
            rows = member_set["members"]
            return {"items": copy.deepcopy(rows[offset:offset + limit]), "total": len(rows), "offset": offset,
                    "limit": limit, "next_offset": offset + limit if offset + limit < len(rows) else None,
                    "set_handle": member_set["set_handle"], "set_version": version, "digest": member_set["digest"],
                    "snapshot_digest": doc["snapshot_digest"], "draft_revision": doc["revision"],
                    "request_id": doc["request_id"], "contract_bundle_hash": doc["contract_bundle_hash"],
                    "resolution_complete": doc["resolution_receipt"]["resolution_complete"], "exclusions": member_set["exclusions"],
                    "requested_scope_count": doc["resolution_receipt"]["expected_snapshot_count"],
                    "answer_published": False}

    def export(self, owner, identity, *, confirmed=False):
        with self.lock, self.transaction() as con:
            if confirmed:
                plan = self._plan(con, owner, identity)
                doc = plan["card"]
                view = self._plan_view(plan)
            else:
                doc = self._draft(con, owner, identity)
                self._check(owner, doc)
                view = self._view(doc)
            return {"artifact_kind": "query_plan_preflight", "answer_published": False, "notice": SPEC["templates"]["notice"],
                    "plan": view, "discovered_members": doc["member_set"]["members"],
                    "exclusions": doc["member_set"]["exclusions"],
                    "contract_bundle_manifest": self.bundles.require(doc["contract_bundle_hash"])}
