"""Versioned trusted resolver configuration, separate from model and function inputs."""
import json
import time
from contextlib import contextmanager
from contextvars import ContextVar
import httpx
from functools import wraps
from pydantic import BaseModel, ConfigDict, Field
from .config import settings
from .ports import connector_checks, credential, request_json


class ResolverUnavailable(ValueError):
    pass


_budget = ContextVar("resolver_budget", default=None)


@contextmanager
def resolver_budget(calls=64, seconds=90):
    parent = _budget.get()
    token = _budget.set({"remaining": calls, "deadline": time.monotonic() + seconds, "parent": parent,
                         "verified_questions": parent["verified_questions"] if parent else set()})
    try:
        yield
    finally:
        _budget.reset(token)


def bounded_resolution(calls=40, seconds=20):
    def decorate(function):
        @wraps(function)
        def wrapped(*args, **kwargs):
            with resolver_budget(calls, seconds):
                return function(*args, **kwargs)
        return wrapped
    return decorate


def question_cache():
    current = _budget.get()
    return current["verified_questions"] if current else set()


class ResolverConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    version: str
    workspace_ids: list[str]
    entity_type: str
    endpoint: str
    credential_ref: str | None = None
    timeout_seconds: float = Field(default=5, gt=0, le=10)


class Candidate(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    id: str = Field(min_length=1, max_length=256)
    label: str = Field(min_length=1, max_length=200)
    type: str
    authorized: bool


def connector_for(profile):
    return {"kind": "http", "version": "1", "endpoint": profile["endpoint"], "method": "POST",
            "credential_ref": profile["credential_ref"], "timeout_seconds": profile["timeout_seconds"], "status_endpoint": None}


def resolver_profile(reference, workspace_id):
    if reference.startswith("zhiyu.entity.") and reference.endswith("@1") and settings().env != "production":
        from .zhiyu import domain_definition
        entity_type = "zhiyu:" + reference[len("zhiyu.entity."):-2]
        if any(item["id"] == entity_type for item in domain_definition()["concepts"]):
            return {"kind": "zhiyu", "version": "1", "workspace_ids": [workspace_id], "entity_type": entity_type}
    if reference.startswith("demo.entity.") and reference.endswith("@1") and settings().env != "production":
        entity = reference[len("demo.entity."):-2]
        if entity in {"Customer", "Account", "Transaction", "Loan", "Product", "Holding", "Ticket"}:
            return {"kind": "demo", "version": "1", "workspace_ids": [workspace_id], "entity_type": "demo:" + entity}
    raw = json.loads(settings().resolvers_json).get(reference)
    if not raw:
        raise ValueError("Trusted resolver reference is not configured")
    profile = ResolverConfig.model_validate(raw).model_dump()
    if reference.rsplit("@", 1)[-1] != profile["version"] or workspace_id not in profile["workspace_ids"]:
        raise ValueError("Resolver version/workspace grant is unavailable")
    try:
        connector_checks({"connector": connector_for(profile)}, workspace_id)
    except OSError as exc:
        raise ResolverUnavailable("Trusted resolver network target is unavailable") from exc
    return profile


def profiles_for_definition(definition, workspace_id):
    roles = {r["role"]: r["type"] for r in definition["participant_roles"]}
    references = [(b["resolver_ref"], roles[b["role"]]) for b in definition["argument_bindings"].values()]
    references += [(b["resolver_ref"], b["entity_type"]) for b in definition["result_bindings"]]
    profiles = {}
    for reference, entity_type in references:
        profile = resolver_profile(reference, workspace_id)
        if profile["entity_type"] != entity_type:
            raise ValueError("Resolver's registered entity type must match the binding role")
        profiles[reference] = profile
    return profiles


def check_pinned_profiles(profiles, workspace_id):
    try:
        return all(resolver_profile(ref, workspace_id) == profile for ref, profile in profiles.items())
    except (ValueError, TypeError, KeyError):
        return False


def resolve(reference, pinned, workspace_id, user_id, query, selected_id=None, *, persona_id=None,
            authorization_mode=None):
    # Missing mode is an immutable legacy publication and retains the old
    # object ACL. Explicit function/none modes leave business authorization to
    # the selected function, never to this platform resolver.
    if authorization_mode not in {None, "function", "none"}:
        raise ValueError("Pinned function authorization mode is unavailable")
    current = resolver_profile(reference, workspace_id)
    if current != pinned:
        raise ValueError("Pinned resolver configuration is unavailable")
    if current.get("kind") == "demo":
        return _resolve_demo(current["entity_type"], workspace_id, user_id, query, selected_id,
                             authorization_mode=authorization_mode)
    if current.get("kind") == "zhiyu":
        from sqlalchemy.orm import Session
        from .db import engine, set_identity
        from .models import SyntheticState
        from .synthetic_runtime import CATALOG_ID
        from .zhiyu import resolve as resolve_synthetic
        with Session(engine) as db:
            set_identity(db, user_id)
            state = db.get(SyntheticState, (workspace_id, CATALOG_ID))
            if not state or persona_id is None:
                raise ResolverUnavailable("Synthetic session or catalog is unavailable")
            return resolve_synthetic(current["entity_type"], state.state, persona_id, query, selected_id,
                                     authorization_mode=authorization_mode)
    connector = connector_for(current)
    secret = credential(connector["credential_ref"], workspace_id, connector)
    budget = _budget.get()
    timeout = current["timeout_seconds"]
    current_budget = budget
    while current_budget is not None:
        remaining_time = current_budget["deadline"] - time.monotonic()
        if current_budget["remaining"] <= 0 or remaining_time <= 0:
            raise ResolverUnavailable("Finite resolver authorization budget exhausted")
        current_budget["remaining"] -= 1
        timeout = min(timeout, remaining_time)
        current_budget = current_budget["parent"]
    try:
        raw = request_json("POST", current["endpoint"], payload={"user_id": user_id, "workspace_id": workspace_id,
                           "entity_type": current["entity_type"], "query": query, **({"selected_id": selected_id} if selected_id else {})},
                           headers={"Authorization": "Bearer " + secret} if secret else {}, timeout=timeout)
    except (httpx.HTTPError, OSError, ValueError) as exc:
        raise ResolverUnavailable("Trusted resolver transport is unavailable") from exc
    if not isinstance(raw, dict) or set(raw) != {"candidates"} or not isinstance(raw["candidates"], list) or len(raw["candidates"]) > 20:
        raise ResolverUnavailable("Resolver response does not match the finite protocol")
    candidates, seen, labels = [], set(), set()
    for item in raw["candidates"]:
        try:
            candidate = Candidate.model_validate(item)
        except ValueError as exc:
            raise ResolverUnavailable("Resolver candidate does not match the finite protocol") from exc
        if authorization_mode is None and not candidate.authorized:
            continue
        if candidate.label in labels:
            raise ResolverUnavailable("Resolver must provide distinguishable candidate labels")
        if candidate.type != current["entity_type"] or candidate.id in seen:
            raise ResolverUnavailable("Resolver returned conflicting or incompatible entities")
        seen.add(candidate.id); labels.add(candidate.label); candidates.append(candidate.model_dump())
    if selected_id:
        candidates = [x for x in candidates if x["id"] == selected_id]
    return candidates


def _resolve_demo(entity_type, workspace_id, user_id, query, selected_id, *, authorization_mode=None):
    from sqlalchemy.orm import Session
    from .db import engine, set_identity
    from .models import DemoState
    from .demo.engine import authorize_operation, authorized_customers
    collections = {"Customer": ("customers", "customer_id"), "Account": ("accounts", "account_id"),
                   "Transaction": ("transactions", "transaction_id"), "Loan": ("loans", "loan_id"),
                   "Product": ("products", "product_id"), "Holding": ("holdings", "holding_id"), "Ticket": ("tickets", "ticket_id")}
    with Session(engine) as db:
        set_identity(db, user_id)
        record = db.get(DemoState, workspace_id)
        if not record: return []
        state = record.state
        collection, key = collections[entity_type.split(":")[-1]]
        source = state[collection]
        rows = source.values() if isinstance(source, dict) else source
        result = []
        for row in rows:
            identifier = row[key]
            if selected_id and identifier != selected_id: continue
            label = " · ".join(str(row[x]) for x in ("name", "label", "city", key) if row.get(x))
            if not selected_id and str(query).lower() not in label.lower(): continue
            if authorization_mode is None:
                try:
                    authorize_operation("demo.entity", {key: identifier}, state, user_id)
                    if row.get("customer_id") and row["customer_id"] not in authorized_customers(user_id, state): continue
                except PermissionError:
                    continue
            result.append({"id": identifier, "label": label, "type": entity_type, "authorized": True})
        return result[:20]
