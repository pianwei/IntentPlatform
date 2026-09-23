"""PostgreSQL-backed worker. Outbox is the durable events table, polled/streamed by API."""
import time
import logging
from contextlib import contextmanager
from datetime import timedelta
from sqlalchemy import select, or_, and_
from sqlalchemy.orm import Session
from .db import engine, set_identity, now, uid
from .config import settings
from .models import Membership, Run, Call, Conversation, AuthToken, Release, Function, Agent, RuntimeSession, ProcessingAttempt
from .auth import token_valid, authorize, audit
from .services import visible_candidates, emit, status_event, allowed_definition
from .ports import route_model, execute_connector, condition
from .ontology import input_errors, INPUT_VALIDATORS
from .schemas import Proposal, ProposalV2
from .context_binding import bind_arguments, history_context, revalidate_call_bindings, BindingDenied
from .semantic_v2 import semantic_errors, normalize_parameters, truth
from .resolvers import ResolverUnavailable, resolver_budget
from .policies import permitted, dependency_error, effective_channels
from .capacity import lock_workspace, claim_attempt

log = logging.getLogger(__name__)


def current_token(db, run):
    conv = db.get(Conversation, run.conversation_id)
    runtime = db.get(RuntimeSession, conv.runtime_session_id) if conv and conv.runtime_session_id else None
    token_id = runtime.current_token_id if runtime and runtime.current_token_id else conv.token_id if conv else None
    token = db.get(AuthToken, token_id) if token_id else None
    if runtime and runtime.revoked:
        return None
    if not conv or conv.status != "active" or not token_valid(db, token):
        return None
    member = db.get(Membership, (run.user_id, run.workspace_id))
    from .trust import member_authority, integration_allowed
    if not member_authority(member)[1]:
        return None
    agent = db.get(Agent, conv.agent_id)
    if not agent or not permitted(agent, run.user_id):
        return None
    release = db.get(Release, agent.current_release_id)
    if not release or not effective_channels(agent, release).get(runtime.channel if runtime else "chat"):
        return None
    if runtime is None and not integration_allowed(agent, db.get(Release, conv.release_id), token, "chat"):
        return None
    return token


def terminal_error(db, run, decision, message):
    run.status = "FAILED"; run.decision = decision; run.lease_until = None
    emit(db, run, "ERROR", {"code": decision, "message": message, "trace_id": run.id})
    status_event(db, run)


def claim_run(user_id):
    with Session(engine) as db:
        set_identity(db, user_id)
        spaces = list(db.scalars(select(Run.workspace_id).where(Run.user_id == user_id, or_(Run.status == "RECEIVED", and_(Run.status == "ROUTING", Run.lease_until < now()))).distinct()))
    for ws in sorted(spaces):
        work = _claim_run(user_id, ws)
        if work: return work
    return None


def _claim_run(user_id, ws):
    with Session(engine, expire_on_commit=False) as db, db.begin():
        set_identity(db, user_id)
        lock_workspace(db, ws)
        run = db.scalar(select(Run).where(Run.user_id == user_id, Run.workspace_id == ws, or_(Run.status == "RECEIVED", and_(Run.status == "ROUTING", Run.lease_until < now())))
                        .order_by(Run.created_at).with_for_update(skip_locked=True).limit(1))
        if not run:
            return None
        token = current_token(db, run)
        if not token:
            terminal_error(db, run, "FORBIDDEN", "Session or permission is no longer valid")
            return {"done": True}
        conv = db.get(Conversation, run.conversation_id)
        release = db.get(Release, conv.release_id)
        cfg = settings()
        if error := dependency_error(release.snapshot):
            terminal_error(db, run, "ERROR", error)
            return {"done": True}
        candidates = visible_candidates(db, release, token)
        if not candidates:
            terminal_error(db, run, "OUT_OF_SCOPE", "No authorized capabilities are available")
            return {"done": True}
        # Bounded historical authorization and entity resolution share this finite worker lease.
        resolver_budget = 600 if release.snapshot["prompt_policy_version"] in {"router-v2", "router-v3"} else 0
        lease = now() + timedelta(seconds=max(cfg.worker_lease_seconds, cfg.model_timeout + resolver_budget + 10))
        attempt = claim_attempt(db, run, "routing", lease)
        if not attempt: return None
        run.status = "ROUTING"; run.lease_until = lease
        status_event(db, run)
        return {"id": run.id, "user_id": user_id, "revision": run.revision, "text": run.text,
                "candidates": candidates, "proposal": run.proposal, "prompt_policy_version": release.snapshot["prompt_policy_version"],
                "release_id": release.id, "attempt_id": attempt.id}


def prepare_run(work):
    started = time.monotonic(); work["metrics"] = {}
    try:
        with resolver_budget():
            _prepare_run(work)
    finally:
        finish_attempt(work, time.monotonic() - started)


@contextmanager
def measured_stage(work, name, started=None):
    started = time.monotonic() if started is None else started
    try:
        yield
    finally:
        work.setdefault("metrics", {})[name] = (time.monotonic() - started) * 1000


def attempt_live(db, work):
    if not work.get("attempt_id"): return True  # compatibility for direct local adapter tests
    row = db.get(ProcessingAttempt, work["attempt_id"])
    return row is not None and row.status == "active" and row.lease_until > now()


def finish_attempt(work, seconds):
    if not work.get("attempt_id"): return
    with Session(engine) as db, db.begin():
        set_identity(db, work["user_id"])
        row = db.scalar(select(ProcessingAttempt).where(ProcessingAttempt.id == work["attempt_id"]).with_for_update())
        if not row or row.status != "active": return
        if row.lease_until <= now():
            row.status = "lease_expired"; row.error_stage = "lease"; return
        run = db.get(Run, row.run_id)
        row.status = "superseded" if run.revision != row.revision else "completed"
        row.finished_at = now()
        row.timings = {**row.timings, **work.get("metrics", {}), "total_ms": seconds * 1000 + row.timings.get("queue_ms", 0)}
        row.error_stage = work.get("error_stage")
        if not row.error_stage and run.status == "FAILED" and run.decision == "ERROR": row.error_stage = "validation"
        if row.error_stage: row.status = "failed"


def _prepare_run(work):
    if work.get("done"):
        return
    try:
        history = []
        context_stats = {}
        if not work["proposal"] and work.get("prompt_policy_version") in {"router-v2", "router-v3"}:
            stage = time.monotonic(); work["error_stage"] = "context"
            # Claim has committed: external authorization never holds its run row lock.
            with Session(engine) as context_db:
                set_identity(context_db, work["user_id"])
                context_run = context_db.get(Run, work["id"])
                if not current_token(context_db, context_run):
                    raise PermissionError("Historical context authorization changed")
                with resolver_budget(calls=40, seconds=20):
                    history = history_context(context_db, context_run, context_db.get(Release, work["release_id"]), context_stats)
            work["metrics"]["context_ms"] = (time.monotonic() - stage) * 1000
        parser = ProposalV2 if work.get("prompt_policy_version") in {"router-v2", "router-v3"} else Proposal
        stage = time.monotonic(); work["error_stage"] = "model"
        proposal = parser.model_validate(work["proposal"] or route_model(work["text"], work["candidates"], policy_version=work.get("prompt_policy_version", "router-v1"), context=history)).model_dump()
        if not work["proposal"]: work["metrics"]["model_ms"] = (time.monotonic() - stage) * 1000
        work["error_stage"] = None
        failure = None
    except Exception:
        if work.get("error_stage") in {"context", "model"}:
            work["metrics"][work["error_stage"] + "_ms"] = (time.monotonic() - stage) * 1000
        proposal = None; failure = "Model could not produce a reliable routing proposal"
        log.exception("routing failed for %s", work["id"])
    validation_started = time.monotonic()
    with Session(engine) as db, db.begin(), measured_stage(work, "validation_ms", validation_started):
        set_identity(db, work["user_id"])
        run = db.scalar(select(Run).where(Run.id == work["id"]).with_for_update())
        if not run or run.status != "ROUTING" or run.revision != work["revision"] or not attempt_live(db, work):
            return
        token = current_token(db, run)
        if not token:
            terminal_error(db, run, "FORBIDDEN", "Session or permission was revoked")
            return
        if failure:
            terminal_error(db, run, "ERROR", failure); return
        if context_stats:
            run.bindings = {**(run.bindings or {}), "context": context_stats}
        if proposal["decision"] == "OUT_OF_SCOPE":
            terminal_error(db, run, "OUT_OF_SCOPE", "No registered capability supports this request"); return
        release = db.get(Release, db.get(Conversation, run.conversation_id).release_id)
        candidates = {c["capability_id"]: c for c in visible_candidates(db, release, token)}
        run.lease_until = None
        if proposal.get("decision") == "AMBIGUOUS":
            allowed_options = [o for o in proposal.get("options", []) if o.get("id") in candidates]
            if len(allowed_options) < 2:
                terminal_error(db, run, "ERROR", "Ambiguity proposal did not contain authorized alternatives"); return
            run.status = "WAITING_INPUT"; run.decision = "AMBIGUOUS"
            run.question = {"id": uid("question"), "fields": [], "options": allowed_options, "kind": "function_choice"}
            run.proposal = proposal
            emit(db, run, "QUESTION", {"question_id": run.question["id"], "kind": "function_choice", "fields": [], "options": allowed_options, "message": "请选择要处理的功能"})
            status_event(db, run); return
        requests = proposal.get("requests", [])
        if not requests:
            terminal_error(db, run, "OUT_OF_SCOPE", "No registered capability supports this request"); return
        if len(requests) > 8:
            terminal_error(db, run, "ERROR", "Too many independent requests"); return
        validated = []
        evidence = []
        run.validation_evidence = None
        for index, request in enumerate(requests):
            capability = candidates.get(request.get("capability_id"))
            if not capability:
                terminal_error(db, run, "FORBIDDEN", "Requested capability is unavailable"); return
            definition = capability["definition"]
            if work.get("prompt_policy_version") in {"router-v2", "router-v3"} and (request["negated"] or (work["prompt_policy_version"] == "router-v3" and request["negated"] is not False) or (capability["compiled"]["compiler_version"] == "finite-json-v1" and any(request[key] != definition[key] for key in ("action", "object_type", "result_type")))):
                terminal_error(db, run, "OUT_OF_SCOPE", "Routing semantics do not authorize the selected action"); return
            if capability["compiled"]["compiler_version"] == "finite-json-v2":
                errors = semantic_errors(definition, request, capability["compiled"]["domain_projection"])
                if errors:
                    terminal_error(db, run, "OUT_OF_SCOPE", "Semantic action, object, role, relation or result constraints were not satisfied"); return
                try:
                    facts, question = bind_arguments(db, run, index, request, capability, release)
                except ResolverUnavailable as exc:
                    terminal_error(db, run, "ERROR", str(exc)); return
                except BindingDenied as exc:
                    terminal_error(db, run, "FORBIDDEN", str(exc)); return
                except (ValueError, PermissionError):
                    terminal_error(db, run, "FORBIDDEN", "Trusted entity or historical binding is unavailable"); return
                if question:
                    run.proposal = proposal
                    run.question = {"id": uid("question"), **question}
                    binding = run.bindings[question["binding_key"]]
                    run.bindings = {**run.bindings, "question:" + run.question["id"]: {"capability_id": capability["capability_id"], "resolver_ref": binding["resolver_ref"], "candidates": list(binding.get("options", {}).values())}}
                    run.status = "WAITING_INPUT"; run.decision = "AMBIGUOUS" if question["options"] else "NEEDS_ARGUMENTS"
                    emit(db, run, "QUESTION", {"question_id": run.question["id"], **{k: v for k, v in question.items() if k not in {"binding_key", "request_index"}}, "message": "请选择或描述当前有权访问的对象"})
                    status_event(db, run); return
                try:
                    args, units, normalization = normalize_parameters(definition, request["arguments"], request["parameter_units"], capability["compiled"]["domain_projection"], run.created_at, release.snapshot["context_policy"]["timezone"])
                    request["arguments"] = args; request["parameter_units"] = units
                    run.bindings = {**run.bindings, "parameters:" + str(index): normalization}
                except (ValueError, TypeError, OverflowError):
                    terminal_error(db, run, "ERROR", "Parameter units or date/time semantics are unsupported"); return
                if truth(definition["trusted_preconditions"], facts) is not True:
                    terminal_error(db, run, "FORBIDDEN", "Trusted preconditions are false or unknown"); return
            if len(requests) > 1 and definition["effect"] != "READ_ONLY":
                terminal_error(db, run, "OUT_OF_SCOPE", "Multiple-effect composition requires a registered composite capability"); return
            args = request.get("arguments", {})
            if not isinstance(args, dict):
                terminal_error(db, run, "ERROR", "Arguments must be an object"); return
            # Identity fields can only be provided by this server.
            for field, value in {"user_id": run.user_id, "workspace_id": run.workspace_id}.items():
                if field in definition["input_schema"].get("properties", {}):
                    args[field] = value
            errors = INPUT_VALIDATORS[capability["compiled"]["compiler_version"]](definition["input_schema"], args)
            if errors:
                run.proposal = {**proposal, "requests": requests}
                fields = [{"name": name, "type": spec.get("type", "string"), "required": name in definition["input_schema"].get("required", [])}
                          for name, spec in definition["input_schema"].get("properties", {}).items() if name not in {"user_id", "workspace_id"} and name not in definition.get("argument_bindings", {})]
                for field in fields:
                    unit = definition.get("parameter_semantics", {}).get(field["name"], {}).get("unit")
                    if unit:
                        field.update({"unit": unit, "label": field["name"] + " (" + unit + ")"})
                run.question = {"id": uid("question"), "fields": fields, "options": [], "request_index": index, "kind": "arguments"}
                run.status = "WAITING_INPUT"; run.decision = "NEEDS_ARGUMENTS"
                emit(db, run, "QUESTION", {"question_id": run.question["id"], "kind": "arguments", "fields": fields, "options": [], "message": "请补齐或修正参数", "errors": errors})
                status_event(db, run); return
            if any(condition(c, args) is not True for c in definition.get("preconditions", [])):
                terminal_error(db, run, "FORBIDDEN", "Preconditions are false or unknown"); return
            request["arguments"] = args
            validated.append((capability, args))
            is_v2 = capability["compiled"]["compiler_version"] == "finite-json-v2"
            evidence.append({"index": index, "capability_id": capability["capability_id"], "version": capability["version"],
                             "compiler_version": capability["compiled"]["compiler_version"], "arguments": dict(args),
                             "checks": {"semantic": True if is_v2 else None, "input_schema": True, "preconditions": True,
                                        "entity_binding": True if is_v2 and definition.get("argument_bindings") else None},
                             "normalization": run.bindings.get("parameters:" + str(index), {}),
                             "semantics": {key: request[key] for key in ("action", "object_type", "result_type", "participants", "relations", "parameter_units", "negated")} if is_v2 else None})
        run.proposal = {**proposal, "requests": requests}
        run.validation_evidence = {"revision": run.revision, "requests": evidence}
        needs_confirmation = any(c["definition"]["requires_confirmation"] for c, _ in validated)
        if needs_confirmation and not run.confirmed:
            run.status = "WAITING_CONFIRMATION"; run.decision = "NEEDS_CONFIRMATION"
            run.confirmation_expires_at = now() + timedelta(seconds=settings().confirmation_seconds)
            actions = [{"capability_id": c["capability_id"], "version": c["version"], "name": c["definition"]["name"], "arguments": args} for c, args in validated]
            c, args = validated[0]
            emit(db, run, "CONFIRMATION", {"capability_id": c["capability_id"], "name": c["definition"]["name"] if len(actions) == 1 else "确认以下全部操作",
                                           "arguments": args if len(actions) == 1 else {"actions": actions}, "actions": actions,
                                           "expires_at": run.confirmation_expires_at.isoformat(), "message": "确认后才执行此操作"})
            status_event(db, run); return
        if needs_confirmation and (not run.confirmation_expires_at or run.confirmation_expires_at <= now()):
            run.confirmed = False
            terminal_error(db, run, "ERROR", "Confirmation expired before dispatch"); return
        for index, (capability, args) in enumerate(validated):
            # Stable action slot survives revision changes. Semantic edits use new user runs.
            action_id = f"action_{index}"
            existing = db.scalar(select(Call).where(Call.run_id == run.id, Call.action_id == action_id))
            if existing:
                if existing.capability_id != capability["capability_id"] or existing.arguments != args:
                    terminal_error(db, run, "ERROR", "A persisted action cannot be silently replaced"); return
                continue
            db.add(Call(workspace_id=run.workspace_id, run_id=run.id, action_id=action_id, revision=run.revision,
                        capability_id=capability["capability_id"], version=capability["version"], definition=capability["definition"], arguments=args,
                        bindings={k: v for k, v in run.bindings.items() if k.startswith(str(index) + ":") or k == "parameters:" + str(index)}))
        run.status = "EXECUTING"; run.decision = "READY"
        status_event(db, run)
        audit(db, run.user_id, run.workspace_id, "run.prepared", run.id, {"revision": run.revision, "action_count": len(validated)})


def claim_call(user_id):
    with Session(engine) as db:
        set_identity(db, user_id)
        spaces = list(db.scalars(select(Run.workspace_id).join(Call, Call.run_id == Run.id).where(Run.user_id == user_id, or_(Call.status == "PENDING", and_(Call.status == "EXECUTING", Call.lease_until < now()))).distinct()))
    for ws in sorted(spaces):
        work = _claim_call(user_id, ws)
        if work: return work
    return None


def _claim_call(user_id, ws):
    with Session(engine, expire_on_commit=False) as db, db.begin():
        set_identity(db, user_id)
        lock_workspace(db, ws)
        eligible = select(Call.run_id).where(or_(Call.status == "PENDING", and_(Call.status == "EXECUTING", Call.lease_until < now())))
        run = db.scalar(select(Run).where(Run.user_id == user_id, Run.workspace_id == ws, Run.id.in_(eligible)).with_for_update(skip_locked=True).limit(1))
        if not run:
            return None
        call = db.scalar(select(Call).where(Call.run_id == run.id, or_(Call.status == "PENDING", and_(Call.status == "EXECUTING", Call.lease_until < now())))
                         .order_by(Call.action_id).with_for_update(skip_locked=True).limit(1))
        if not call:
            return None
        if call.status == "EXECUTING" and call.dispatched_at is not None:
            # An already-sent operation's outcome stays unknown even if its authorization
            # or confirmation expired during the crash. Revocation pauses reconciliation.
            call.status = "UNKNOWN"; call.lease_until = now() + timedelta(seconds=30)
            db.flush(); aggregate_status(db, run)
            emit(db, run, "ERROR", {"code": "UNKNOWN", "message": "Worker lease expired; reconcile external operation before retry", "trace_id": call.id})
            status_event(db, run)
            return {"done": True}
        token = current_token(db, run)
        f = db.get(Function, call.capability_id)
        if not token or not f or f.disabled or not permitted(f, user_id) or run.status != "EXECUTING" or call.revision != run.revision:
            call.status = "CANCELLED"
            terminal_error(db, run, "FORBIDDEN", "Execution eligibility changed before dispatch")
            return {"done": True}
        if call.definition["requires_confirmation"] and (not run.confirmed or not run.confirmation_expires_at or run.confirmation_expires_at <= now()):
            call.status = "CANCELLED"; terminal_error(db, run, "ERROR", "Confirmation expired before dispatch"); return {"done": True}
        if call.status == "EXECUTING":
            if call.dispatched_at is None:
                call.status = "PENDING"; call.lease_until = None
                return {"done": True}
        binding_budget = 20 if call.bindings else 0
        lease = now() + timedelta(seconds=max(settings().worker_lease_seconds, call.definition["connector"]["timeout_seconds"] + binding_budget + 10))
        attempt = claim_attempt(db, run, "execution", lease, call.id)
        if not attempt: return None
        call.status = "EXECUTING"; call.lease_until = lease
        return {"id": call.id, "run_id": run.id, "revision": run.revision, "user_id": user_id,
                "definition": call.definition, "arguments": call.arguments, "workspace_id": call.workspace_id, "attempt_id": attempt.id}


def aggregate_status(db, run):
    states = list(db.scalars(select(Call.status).where(Call.run_id == run.id)))
    if any(s in {"PENDING", "EXECUTING"} for s in states):
        run.status = "EXECUTING"
    elif any(s in {"UNKNOWN", "RECONCILING"} for s in states):
        run.status = "UNKNOWN"
    elif "FAILED" in states:
        run.status = "FAILED"
    elif states and all(s == "SUCCEEDED" for s in states):
        run.status = "SUCCEEDED"
    elif states:
        run.status = "CANCELLED"


def finish_call(work, result):
    with Session(engine) as db, db.begin():
        set_identity(db, work["user_id"])
        info = db.get(Call, work["id"])
        if not info:
            return
        run = db.scalar(select(Run).where(Run.id == info.run_id).with_for_update())
        call = db.scalar(select(Call).where(Call.id == work["id"]).with_for_update())
        if not call or call.status not in {"EXECUTING", "RECONCILING"} or not attempt_live(db, work):
            return
        call.status = result["status"]; call.operation_id = result.get("operation_id") or call.operation_id
        call.lease_until = now() + timedelta(seconds=30) if call.status == "UNKNOWN" else None
        d = call.definition; data = result.get("data", {})
        from .result_files import materialize_files
        data, files, file_error = materialize_files(db, run, call, data)
        call.result = {**result, "data": data, "files": files}
        # Results keep the original run/revision and never mutate a newer request's object.
        payload = {"call_id": call.id, "status": call.status, "component": d["presentation"]["component"], "title": d["presentation"]["title"],
                   "data": data, "provenance": {"capability_id": call.capability_id, "version": call.version}, "fixture_mode": result.get("fixture_mode", False)}
        if files:
            payload["files"] = files
        if file_error:
            emit(db, run, "ERROR", {"code": "FILE_REJECTED", "message": file_error, "trace_id": call.id})
        if isinstance(data, dict):
            payload["fields"] = [{"label": label, "value": data.get(field)} for label, field in d["presentation"].get("field_map", {}).items()] or [{"label": k, "value": v} for k, v in data.items()]
        emit(db, run, "RESULT", payload)
        db.flush()
        aggregate_status(db, run)
        status_event(db, run)
        audit(db, run.user_id, run.workspace_id, "call.finished", call.id, {"status": call.status, "run_id": run.id, "revision": call.revision})


def process_call(work):
    started = time.monotonic(); work["metrics"] = {}
    try:
        _process_call(work)
    finally:
        finish_attempt(work, time.monotonic() - started)


def _process_call(work):
    if work.get("done"):
        return
    # Recheck revocation after claim transaction, immediately before the outbound request.
    with Session(engine) as db, db.begin():
        set_identity(db, work["user_id"])
        run = db.scalar(select(Run).where(Run.id == work["run_id"]).with_for_update())
        call = db.scalar(select(Call).where(Call.id == work["id"]).with_for_update())
        f = db.get(Function, call.capability_id)
        if call.status != "EXECUTING" or not attempt_live(db, work):
            return
        if not dispatch_permitted(db, run, call, f):
            work["error_stage"] = "authorization"
            result = {"status": "CANCELLED", "data": {}, "operation_id": None}
        else:
            result = None
    if result is None:
        try:
            with measured_stage(work, "connector_ms"):
                result = execute_connector(work["definition"], work["arguments"], work["id"], work["user_id"], work["workspace_id"], before_send=lambda: mark_dispatch(work))
        except PermissionError:
            work["error_stage"] = "authorization"
            result = {"status": "FAILED", "data": {"message": "Business object/data authorization denied"}, "operation_id": None}
        except Exception:
            work["error_stage"] = "connector"
            log.exception("connector failed for %s", work["id"])
            result = {"status": "UNKNOWN" if work["definition"]["effect"] == "WRITE" else "FAILED", "data": {"message": "Connector failed; inspect trace using call ID"}, "operation_id": None}
    finish_call(work, result)


def dispatch_permitted(db, run, call, function):
    if not current_token(db, run) or not function or function.disabled or not permitted(function, run.user_id) or run.status != "EXECUTING" or run.revision != call.revision:
        return False
    if call.definition["requires_confirmation"] and (not run.confirmed or not run.confirmation_expires_at or run.confirmation_expires_at <= now()):
        return False
    release = db.get(Release, db.get(Conversation, run.conversation_id).release_id)
    return dependency_error(release.snapshot) is None


def mark_dispatch(work):
    # Called by the connector after its potentially slow object-authority request,
    # immediately before the business request. A stale confirmation never crosses this boundary.
    with Session(engine) as db, resolver_budget(calls=32, seconds=20):
        set_identity(db, work["user_id"])
        run = db.get(Run, work["run_id"]); call = db.get(Call, work["id"])
        if not current_token(db, run):
            raise PermissionError("Runtime authorization changed before entity revalidation")
        release = db.get(Release, db.get(Conversation, run.conversation_id).release_id)
        try:
            revalidate_call_bindings(db, run, call, release)
        except (ValueError, PermissionError) as exc:
            raise PermissionError("Trusted entity or historical authorization changed") from exc
    with Session(engine) as db, db.begin():
        set_identity(db, work["user_id"])
        run = db.scalar(select(Run).where(Run.id == work["run_id"]).with_for_update())
        call = db.scalar(select(Call).where(Call.id == work["id"]).with_for_update())
        if call.status != "EXECUTING" or not attempt_live(db, work) or not dispatch_permitted(db, run, call, db.get(Function, call.capability_id)):
            raise PermissionError("Execution or confirmation permission expired before send")
        call.dispatched_at = now()


def reconcile_one(user_id):
    with Session(engine) as db:
        set_identity(db, user_id)
        spaces = list(db.scalars(select(Run.workspace_id).join(Call, Call.run_id == Run.id).where(Run.user_id == user_id, Call.status.in_(["UNKNOWN", "RECONCILING"])).distinct()))
    return any(_reconcile_one(user_id, ws) for ws in sorted(spaces))


def _reconcile_one(user_id, ws):
    with Session(engine, expire_on_commit=False) as db, db.begin():
        set_identity(db, user_id)
        lock_workspace(db, ws)
        calls = db.scalars(select(Call).join(Run, Run.id == Call.run_id).where(Run.user_id == user_id, Run.workspace_id == ws, Call.status.in_(["UNKNOWN", "RECONCILING"]),
                          Call.definition["connector"]["status_endpoint"].as_string().is_not(None),
                          or_(Call.lease_until.is_(None), Call.lease_until < now())).order_by((Call.status == "RECONCILING").desc(), Call.lease_until.asc().nulls_first(), Call.id).with_for_update(of=Call, skip_locked=True).limit(20)).all()
        work = None
        for call in calls:
            connector = call.definition["connector"]
            lookup = call.operation_id or (call.id if connector.get("reconcile_by") == "idempotency_key" and connector.get("idempotency_header") else None)
            if not connector.get("status_endpoint") or not lookup:
                call.status = "UNKNOWN"; call.lease_until = now() + timedelta(seconds=30)
                continue
            run = db.get(Run, call.run_id)
            f = db.get(Function, call.capability_id)
            release = db.get(Release, db.get(Conversation, run.conversation_id).release_id)
            if not current_token(db, run) or not f or f.disabled or not permitted(f, user_id) or dependency_error(release.snapshot):
                call.status = "UNKNOWN"
                call.lease_until = now() + timedelta(seconds=30)
                continue
            lease = now() + timedelta(seconds=max(settings().worker_lease_seconds, call.definition["connector"]["timeout_seconds"] + 40))
            attempt = claim_attempt(db, run, "reconciliation", lease, call.id)
            if not attempt: continue
            call.status = "RECONCILING"; call.lease_until = lease
            work = {"id": call.id, "run_id": run.id, "user_id": user_id, "workspace_id": call.workspace_id, "definition": call.definition,
                    "arguments": call.arguments, "lookup": lookup, "attempt_id": attempt.id}
            break
    if not work:
        return False
    started = time.monotonic(); work["metrics"] = {}
    try:
        with measured_stage(work, "connector_ms"):
            result = execute_connector(work["definition"], work["arguments"], work["id"], user_id, work["workspace_id"], reconcile_operation=work["lookup"], before_send=lambda: authorize_reconciliation(work))
    except Exception:
        work["error_stage"] = "reconciliation"
        result = {"status": "UNKNOWN", "data": {"message": "Status query unavailable; manual reconciliation may be required"}}
    finish_call(work, result)
    finish_attempt(work, time.monotonic() - started)
    return True


def authorize_reconciliation(work):
    with Session(engine) as db, resolver_budget(calls=32, seconds=20):
        set_identity(db, work["user_id"])
        run = db.get(Run, work["run_id"]); call = db.get(Call, work["id"])
        if not current_token(db, run):
            raise PermissionError("Runtime authorization changed before reconciliation")
        release = db.get(Release, db.get(Conversation, run.conversation_id).release_id)
        revalidate_call_bindings(db, run, call, release)
    with Session(engine) as db:
        set_identity(db, work["user_id"])
        run = db.get(Run, work["run_id"]); call = db.get(Call, work["id"])
        f = db.get(Function, call.capability_id)
        release = db.get(Release, db.get(Conversation, run.conversation_id).release_id)
        if call.status != "RECONCILING" or not attempt_live(db, work) or not current_token(db, run) or not f or f.disabled or not permitted(f, run.user_id) or dependency_error(release.snapshot):
            raise PermissionError("Reconciliation paused after authorization change")


def tick():
    with Session(engine) as db:
        users = list(db.scalars(select(Membership.user_id).where(Membership.active.is_(True)).distinct()))
    did_work = False
    for user in users:
        work = claim_run(user)
        if work:
            did_work = True; prepare_run(work)
        work = claim_call(user)
        if work:
            did_work = True; process_call(work)
        if reconcile_one(user):
            did_work = True
    return did_work


def main():
    logging.basicConfig(level=logging.INFO)
    log.info("Worker ready: PostgreSQL; fixture_mode=%s", settings().fixture_mode)
    while True:
        try:
            if not tick():
                time.sleep(settings().worker_poll_seconds)
        except KeyboardInterrupt:
            return
        except Exception:
            log.exception("Worker iteration failed")
            time.sleep(2)


if __name__ == "__main__":
    main()
