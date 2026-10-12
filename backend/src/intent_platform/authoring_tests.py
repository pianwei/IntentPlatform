"""Private draft execution, using the production plan, binding and call ledger."""
from copy import deepcopy
from datetime import timedelta
import json
from sqlalchemy import select
from .db import now, uid, engine, set_identity
from .models import (Agent, Release, Function, FunctionVersion, Conversation, Run, ExecutionPlan,
                     PlanStep, Call, RuntimeSession, ModelProfile, AuthToken)


def is_test_agent(agent):
    return bool(agent and (agent.runtime_ui or {}).get("authoring_test"))


def current_execution_evidence(db, function, *, expected_failure=False):
    """Check private ledger facts for publication, without exposing author data.

    A publisher does not own the author's run and cannot read it through RLS.
    Resolve its identity only from matching immutable, server-created snapshots;
    the isolated read session never changes the caller's database identity.
    """
    from sqlalchemy.orm import Session
    from fastapi import HTTPException
    from .models import Membership, Event
    from .trust import member_authority
    from .worker import current_token
    from .services import authorize_run_capabilities
    from .output_contract import project_output
    from .policies import dependency_error
    key = "failure_execution_test" if expected_failure else "execution_test"
    evidence = (function.validation or {}).get(key) or {}
    if evidence.get("revision") != function.revision or not evidence.get("run_id"):
        return False
    snapshots = db.execute(select(Release.id, Release.snapshot["authoring_test"]).where(
        Release.workspace_id == function.workspace_id,
        Release.snapshot["authoring_test"]["function_id"].as_string() == function.id,
        Release.snapshot["authoring_test"]["revision"].as_integer() == function.revision))
    for release_id, marker in snapshots:
        if not isinstance(marker, dict) or bool(marker.get("expected_failure")) != expected_failure:
            continue
        owner = marker.get("owner")
        if not isinstance(owner, str) or not owner:
            continue
        with Session(engine) as ledger:
            set_identity(ledger, owner)
            roles, active = member_authority(ledger.get(Membership, (owner, function.workspace_id)))
            if not active or not {"admin", "maintainer"}.intersection(roles):
                continue
            run = ledger.get(Run, evidence["run_id"])
            conversation = ledger.get(Conversation, run.conversation_id) if run else None
            release = ledger.get(Release, release_id)
            if (not run or not conversation or not release or run.workspace_id != function.workspace_id or
                    run.user_id != owner or conversation.user_id != owner or conversation.workspace_id != function.workspace_id or
                    conversation.release_id != release_id or (conversation.business_context or {}).get("authoring_test") != marker):
                continue
            if run.status != ("FAILED" if expected_failure else "SUCCEEDED"):
                continue
            try:
                if not current_token(ledger, run) or dependency_error(release.snapshot, ledger):
                    continue
                authorize_run_capabilities(ledger, run)
                calls = list(ledger.scalars(select(Call).where(Call.run_id == run.id)))
                pinned = {cap["capability_id"]: cap["version"] for cap in release.snapshot["capabilities"]}
                if not calls or any(call.workspace_id != function.workspace_id or call.dispatched_at is None or
                        call.version != pinned.get(call.capability_id) or (call.result or {}).get("result_unavailable")
                        for call in calls):
                    continue
                if expected_failure:
                    if all(call.status == "FAILED" and (call.result or {}).get("business_failure") is True for call in calls):
                        return True
                elif all(call.status == "SUCCEEDED" for call in calls):
                    summary = ledger.scalar(select(Event).where(Event.run_id == run.id,
                        Event.event_type == "SUMMARY").order_by(Event.sequence.desc()))
                    if summary and summary.workspace_id == function.workspace_id:
                        project_output(function.definition, summary.payload.get("data"))
                        return True
            except (HTTPException, ValueError, PermissionError):
                continue
    return False


def finish_test(db, run):
    from .worker import current_token
    from .planning import _path
    from .output_contract import project_output
    from .services import emit, status_event
    conv = db.get(Conversation, run.conversation_id)
    release = db.get(Release, conv.release_id)
    if not release.snapshot.get("authoring_test"):
        raise ValueError("Not an authoring test")
    plan = db.scalar(select(ExecutionPlan).where(ExecutionPlan.run_id == run.id))
    run = db.scalar(select(Run).where(Run.id == run.id).with_for_update())
    plan = db.scalar(select(ExecutionPlan).where(ExecutionPlan.run_id == run.id).with_for_update())
    if run.status == "SUCCEEDED":
        function = db.get(Function, release.snapshot["authoring_test"]["function_id"])
        return ((function.validation or {}).get("execution_test") or {}).get("data", {})
    steps = {s.step_key: s for s in db.scalars(select(PlanStep).where(PlanStep.plan_id == plan.id))}
    from .services import authorize_run_capabilities
    authorize_run_capabilities(db, run)
    if not current_token(db, run) or not steps or any(s.status not in {"SUCCEEDED", "SKIPPED"} for s in steps.values()):
        raise ValueError("Execution test did not establish successful authorized completion")
    calls = list(db.scalars(select(Call).where(Call.run_id == run.id)))
    if not calls or any(c.status != "SUCCEEDED" or (c.result or {}).get("result_unavailable") for c in calls):
        raise ValueError("Execution test has incomplete or unavailable business results")
    fid = release.snapshot["authoring_test"]["function_id"]
    definition = next(c["definition"] for c in release.snapshot["capabilities"] if c["capability_id"] == fid)
    if plan.snapshot.get("composite_function_id"):
        data = {field: ((steps[ref["$step"]].result or {}).get("data") if ref["path"] == "data" else
                        _path(steps[ref["$step"]].result, ref["path"]))
                for field, ref in plan.snapshot["final_output_mapping"].items()}
    else:
        data = (next(iter(steps.values())).result or {}).get("data")
    projected, _ = project_output(definition, data)
    plan.status = run.status = "SUCCEEDED"; plan.finished_at = run.finished_at = now()
    # Only the approved projection is recorded; the private raw state never
    # appears in events or validation responses.
    emit(db, run, "SUMMARY", {"text": "执行测试完成", "data": projected, "final": True})
    status_event(db, run)
    function = db.scalar(select(Function).where(Function.id == fid).with_for_update())
    if function and function.revision == release.snapshot["authoring_test"]["revision"] and not release.snapshot["authoring_test"].get("expected_failure"):
        tested = {"passed": True, "revision": function.revision, "status": "SUCCEEDED", "run_id": run.id, "data": projected}
        failure = (function.validation or {}).get("failure_execution_test")
        ready = not (function.source_state or {}).get("require_failure_probe") or bool(
            failure and failure.get("revision") == function.revision and failure.get("passed"))
        function.validation = {"valid": ready, "revision": function.revision,
                               "errors": [] if ready else ["还需完成失败场景测试"], "checks": {"actual_execution": True},
                               "probe": {"status": "SUCCEEDED", "data": projected}, "execution_test": tested,
                               "failure_execution_test": failure}
        function.validated_revision = function.revision if ready else None; function.status = "validated" if ready else "draft"
    return projected


def prepare_test(work):
    from sqlalchemy.orm import Session
    from .worker import current_token, attempt_live, terminal_error
    from .planning import create_plan, _skill_spec, advance
    from .document_planning import plan_document
    snapshot = work["snapshot"]
    fid = snapshot["authoring_test"]["function_id"]
    with Session(engine) as db, db.begin():
        set_identity(db, work["user_id"])
        run = db.get(Run, work["id"])
        if not current_token(db, run) or not attempt_live(db, work):
            terminal_error(db, run, "FORBIDDEN", "Execution test authority changed"); return
        existing = db.scalar(select(ExecutionPlan).where(ExecutionPlan.run_id == run.id))
        if existing:
            if existing.status == "SUMMARIZING":
                try:
                    finish_test(db, run)
                except (ValueError, PermissionError) as exc:
                    terminal_error(db, run, "ERROR", str(exc)[:180])
            else:
                advance(db, run)
            return
        arguments = deepcopy((run.proposal or {}).get("authoring_arguments", {}))
    target = next(c for c in snapshot["capabilities"] if c["capability_id"] == fid)
    layers = {"goal": {"goal": work["text"], "entities": [{"type": target["definition"]["object_type"], "mention": None}],
                      "actions": [target["definition"]["action"]]}, "candidates": [fid]}
    skill_id = None
    try:
        if target["definition"].get("execution_kind") == "composite":
            rule = snapshot["business_skills"][0]; skill_id = rule["version_id"]
            comp = snapshot["agent_projection"]["composites"][fid]
            dependencies = [c for c in snapshot["capabilities"] if c["capability_id"] in comp["dependency_ids"]]
            if rule["definition"].get("mode") == "document":
                raw = plan_document(work, rule["definition"], dependencies, target["definition"], arguments, layers["goal"], work["text"])
                if "_clarification" in raw:
                    raise ValueError(raw["_clarification"])
            else:
                raw = _skill_spec(rule["definition"], arguments, {c["definition"]["connector"].get("workflow_id"): c for c in dependencies})
        else:
            units = {field: semantic["unit"] for field, semantic in target["definition"].get("parameter_semantics", {}).items()
                     if semantic.get("kind") == "quantity" and field in arguments}
            raw = {"inputs": {}, "steps": [{"step_id": "test", "capability_id": fid, "arguments": arguments, "depends_on": [], "parameter_units": units}]}
        with Session(engine) as db, db.begin():
            set_identity(db, work["user_id"])
            run = db.get(Run, work["id"])
            if not current_token(db, run) or not attempt_live(db, work):
                terminal_error(db, run, "FORBIDDEN", "Test authority changed during planning"); return
            if not db.scalar(select(ExecutionPlan.id).where(ExecutionPlan.run_id == run.id)):
                create_plan(db, run, db.get(Release, work["release_id"]), raw, "skill" if skill_id else "model", skill_id, snapshot["capabilities"], layers)
    except Exception as exc:
        from .three_layer import failure_detail
        detail = failure_detail(exc, "plan")
        with Session(engine) as db, db.begin():
            set_identity(db, work["user_id"])
            run = db.get(Run, work["id"])
            from .planning import trace
            trace(db, run, "planning", "test_rejected", detail)
            terminal_error(db, run, detail["reason_code"], "执行测试规划未完成：" + type(exc).__name__)


def execution_test(db, function, token, body, compiled, *, expected_failure=False):
    from .config import settings
    from fastapi import HTTPException
    from sqlalchemy.orm import Session
    from .auth import manager, issue_token
    from .services import publish_agent
    from .planning import create_plan, _skill_spec, advance
    from .worker import _claim_call, process_call, current_token, _claim_run, prepare_run
    from .policies import permitted
    revision, fid, ws, owner = function.revision, function.id, function.workspace_id, token.user_id
    from .ontology import input_errors
    from .planning_schemas import ExecutionPlanSpec
    from pydantic import ValidationError
    try:
        ExecutionPlanSpec.model_validate({"inputs": body.arguments, "steps": [{"step_id": "test", "capability_id": fid, "arguments": {}, "depends_on": []}]})
    except ValidationError:
        raise HTTPException(422, "Test input exceeds the finite input size, type or depth limits") from None
    if input_errors(function.definition["input_schema"], body.arguments):
        raise HTTPException(422, "Test input does not satisfy the declared input fields and types")
    if not permitted(function, owner):
        raise HTTPException(403, "Function test authorization unavailable")
    test_spec = {"arguments": body.arguments, "goal": body.test_goal, "model_profile_id": body.model_profile_id,
                 "allow_effects": body.allow_effects, "expected_failure": expected_failure}
    evidence_key = "failure_execution_test" if expected_failure else "execution_test"
    previous = (function.validation or {}).get(evidence_key) or {}
    if previous.get("revision") == revision and previous.get("run_id"):
        old_run = db.get(Run, previous["run_id"])
        conv = db.get(Conversation, old_run.conversation_id) if old_run else None
        marker = ((conv.business_context or {}).get("authoring_test") or {}) if conv else {}
        pending = old_run and old_run.status in {"RECEIVED", "ROUTING", "EXECUTING", "UNKNOWN", "WAITING_CONFIRMATION"}
        same_spec = marker.get("test_spec") == test_spec
        if old_run and (pending or same_spec or function.definition["effect"] == "WRITE"):
            # Recover the same ledger, never turn an unresolved write into a new
            # probe. A changed write test needs a new saved draft revision.
            if not same_spec:
                raise HTTPException(409, "An existing test uses different inputs; finish it or save a new draft revision")
            return test_result(db, function, token, evidence_key=evidence_key)
    if function.definition["effect"] == "WRITE":
        unresolved = db.scalar(select(Run.id).join(Conversation, Conversation.id == Run.conversation_id).join(Release, Release.id == Conversation.release_id).where(
            Run.workspace_id == ws, Release.snapshot["authoring_test"]["function_id"].as_string() == fid,
            Run.status.in_(["UNKNOWN", "EXECUTING", "ROUTING", "RECEIVED", "WAITING_CONFIRMATION"])).limit(1))
        if unresolved:
            raise HTTPException(409, "An earlier execution test is still pending or UNKNOWN; reconcile its ledger first")
    if function.definition["effect"] == "WRITE" and not body.allow_effects:
        raise HTTPException(409, "Execution of a write test requires explicit allow_effects")
    if function.definition["effect"] == "WRITE" and function.definition["connector"]["kind"] != "fixture" and settings().env == "production":
        raise HTTPException(409, "Business writes may only be tested in an authorized test environment")
    negative = -revision
    existing = db.scalar(select(FunctionVersion).where(FunctionVersion.function_id == fid, FunctionVersion.version == negative))
    frozen = deepcopy(compiled.get("semantic_definition", function.definition)); frozen["output_examples"] = []
    if existing is None:
        db.add(FunctionVersion(workspace_id=ws, function_id=fid, version=negative, definition=frozen, compiled=compiled)); db.flush()
    elif {**existing.definition, "output_examples": []} != frozen:
        raise HTTPException(409, "The current revision test snapshot differs; save a new draft revision")
    marker = {"function_id": fid, "revision": revision, "owner": owner, "allow_effects": body.allow_effects,
              "test_spec": deepcopy(test_spec), "expected_failure": expected_failure}
    model_id = body.model_profile_id
    if not model_id and function.definition.get("execution_kind") == "composite":
        model_id = db.scalar(select(Agent.model_profile_id).where(Agent.workspace_id == ws,
            Agent.model_profile_id.is_not(None), Agent.runtime_ui["authoring_test"].is_(None)).limit(1))
        if not model_id:
            model_id = db.scalar(select(ModelProfile.id).where(ModelProfile.workspace_id == ws,
                ModelProfile.disabled.is_(False), ModelProfile.latest_version > 0).order_by(ModelProfile.id).limit(1))
    agent = Agent(workspace_id=ws, name="执行测试 " + uid("test"), capability_ids=[fid],
        access_mode="allowlist", allowed_users=[owner], planning_enabled=True, routing_strategy="three-layer-v1",
        channels={"chat": True, "embed": False}, live_channels={"chat": True, "embed": False},
        model_profile_id=model_id, runtime_ui={"authoring_test": marker})
    if function.definition["connector"].get("version") == "3":
        from .synthetic_runtime import catalog_metadata
        agent.synthetic_catalog = catalog_metadata()
    db.add(agent); db.flush()
    capability = {"capability_id": fid, "version": negative, "definition": frozen, "compiled": compiled}
    publish_agent(db, agent, token, authoring_capability=capability)
    release = db.get(Release, agent.current_release_id)
    if token.source == "local":
        from .local_identity import mint_runtime
        runtime_token, runtime = mint_runtime(db, owner, agent, "password", parent=token)
    else:
        from .workspace_lifecycle import require_workspace
        from .trust import token_integration
        runtime = RuntimeSession(workspace_id=ws, workspace_epoch=require_workspace(db, ws), agent_id=agent.id,
            user_id=owner, channel="chat", integration_id=token_integration(token), identity_policy=release.snapshot["identity_policy"])
        if agent.synthetic_catalog:
            runtime.demo_persona = "manager_a"
        db.add(runtime); db.flush()
        runtime_token = issue_token(db, owner, scope="runtime", agent_id=agent.id, source=token.source,
            parent=token.id, runtime_session_id=runtime.id, delegation=token.delegation)
        runtime.current_token_id = runtime_token.id
    conv = Conversation(workspace_id=ws, agent_id=agent.id, release_id=release.id, user_id=owner,
        token_id=runtime_token.id, runtime_session_id=runtime.id, title="功能执行测试", business_context={"authoring_test": marker})
    db.add(conv); db.flush()
    # Fixtures receive a separate state namespace while retaining the real user
    # and server-bound object authority. No mock identity is minted.
    from .models import SyntheticState, DemoState
    state = db.get(SyntheticState, (ws, "zhiyu-v1"))
    old = db.get(DemoState, ws)
    conv.business_context = {**conv.business_context, "authoring_synthetic_state": deepcopy(state.state) if state else None,
                             "authoring_demo_state": deepcopy(old.state) if old else None}
    question = body.test_goal or function.definition["description"]
    text = question + "\n测试输入：" + json.dumps(body.arguments, ensure_ascii=False)
    run = Run(workspace_id=ws, conversation_id=conv.id, user_id=owner, client_message_id=uid("test_message"),
        text=text, status="RECEIVED", proposal={"authoring_arguments": deepcopy(body.arguments)})
    db.add(run); db.flush()
    run_id, release_id = run.id, release.id
    snapshot = deepcopy(release.snapshot)
    function.validation = {**(function.validation or {}), "valid": False, "revision": revision, "errors": [], "checks": {}, "probe": None,
                           evidence_key: {"passed": False, "revision": revision, "status": "RUNNING", "run_id": run_id}}
    db.commit()  # durable registration; release locks before network/model work
    set_identity(db, owner)
    work = {"id": run_id, "release_id": release_id, "user_id": owner, "workspace_id": ws,
            "text": text, "revision": 1, "snapshot": snapshot, "candidates": snapshot["capabilities"]}
    error = None
    try:
        for _ in range(45):
            route_work = _claim_run(owner, ws, run_id)
            if route_work and not route_work.get("done"):
                prepare_run(route_work)
            with Session(engine) as test_db, test_db.begin():
                set_identity(test_db, owner); current = test_db.get(Run, run_id)
                plan = test_db.scalar(select(ExecutionPlan).where(ExecutionPlan.run_id == run_id))
                if plan and plan.status == "SUMMARIZING":
                    finish_test(test_db, current)
                    break
                if current.status in {"SUCCEEDED", "FAILED", "CANCELLED", "UNKNOWN", "WAITING_INPUT", "WAITING_CONFIRMATION"}:
                    break
            call_work = _claim_call(owner, ws, run_id)
            if not call_work:
                break
            process_call(call_work)
    except Exception as exc:
        error = "执行测试未完成：" + type(exc).__name__
        with Session(engine) as test_db, test_db.begin():
            set_identity(test_db, owner); current = test_db.get(Run, run_id)
            if current.status not in {"UNKNOWN", "SUCCEEDED"}:
                current.status = "FAILED"; current.finished_at = now()
            for call in test_db.scalars(select(Call).where(Call.run_id == run_id, Call.status == "PENDING")):
                call.status = "CANCELLED"
    db.expire_all()
    set_identity(db, owner)
    function = db.scalar(select(Function).where(Function.id == fid).with_for_update())
    manager(db, token, ws)
    if function.revision != revision:
        raise HTTPException(409, "Draft changed during execution; test evidence belongs to the previous revision")
    run = db.get(Run, run_id)
    data = None
    if run.status == "SUCCEEDED":
        from .models import Event
        event = db.scalar(select(Event).where(Event.run_id == run_id, Event.event_type == "SUMMARY").order_by(Event.sequence.desc()))
        data = event.payload.get("data") if event else None
    passed = run.status == "SUCCEEDED" and isinstance(data, dict) and current_token(db, run) is not None
    result = test_result(db, function, token, evidence_key=evidence_key)
    if error: result["error"] = error
    return result


def test_result(db, function, token, *, evidence_key="execution_test"):
    from fastapi import HTTPException
    from .auth import manager
    from .worker import current_token
    from .services import authorize_run_capabilities
    from .models import Event
    manager(db, token, function.workspace_id)
    from .policies import permitted
    if not permitted(function, token.user_id):
        raise HTTPException(403, "Function test authorization unavailable")
    value = (function.validation or {}).get(evidence_key) or {}
    run = db.get(Run, value.get("run_id")) if value.get("run_id") else None
    if not run or run.workspace_id != function.workspace_id or run.user_id != token.user_id:
        raise HTTPException(404, "Execution test unavailable to this author")
    authorize_run_capabilities(db, run)
    if current_token(db, run) is None:
        raise HTTPException(403, "Execution test authority changed")
    expected_failure = evidence_key == "failure_execution_test"
    calls = list(db.scalars(select(Call).where(Call.run_id == run.id))) if expected_failure else []
    passed = value.get("revision") == function.revision and (run.status == "FAILED" and bool(calls) and
              all(call.status == "FAILED" and call.dispatched_at is not None and (call.result or {}).get("business_failure") is True
                  and not (call.result or {}).get("result_unavailable") for call in calls)
              if expected_failure else run.status == "SUCCEEDED")
    event = db.scalar(select(Event).where(Event.run_id == run.id, Event.event_type == "SUMMARY").order_by(Event.sequence.desc())) if passed and not expected_failure else None
    confirmation = None
    if run.status == "WAITING_CONFIRMATION":
        preview = db.scalar(select(Event).where(Event.run_id == run.id, Event.event_type == "CONFIRMATION").order_by(Event.sequence.desc()))
        if preview:
            confirmation = {"run_id": run.id, "revision": run.revision, "name": preview.payload["name"],
                            "arguments": preview.payload["arguments"], "expires_at": preview.payload["expires_at"]}
    success = bool(passed and (event or expected_failure))
    return {"passed": success, "valid": success, "revision": value.get("revision"), "status": run.status, "run_id": run.id,
            **({"confirmation": confirmation} if confirmation else {}),
            **({"data": event.payload["data"]} if event else {} if success else {"error": "运行中" if run.status in {"RECEIVED", "ROUTING", "EXECUTING", "WAITING_CONFIRMATION"} else "执行测试未完成，失败或未知结果不能发布"})}


def confirm_test(db, function, token, body):
    """Confirm the displayed, unchanged test plan; never pre-approve model output."""
    from fastapi import HTTPException
    from .conversation_logs import record_interaction
    from .services import status_event
    from .auth import audit
    evidence_key = next((key for key in ("execution_test", "failure_execution_test") if (function.validation or {}).get(key, {}).get("run_id") == body.run_id), "execution_test")
    test_result(db, function, token, evidence_key=evidence_key)
    value = (function.validation or {}).get(evidence_key) or {}
    run = db.scalar(select(Run).where(Run.id == value.get("run_id")).with_for_update())
    if (not run or run.id != body.run_id or run.revision != body.revision or
            value.get("revision") != function.revision or run.status != "WAITING_CONFIRMATION" or
            not run.confirmation_expires_at or run.confirmation_expires_at <= now()):
        raise HTTPException(409, "Execution-test confirmation is stale or expired")
    conv = db.get(Conversation, run.conversation_id)
    if not (conv.business_context or {}).get("authoring_test", {}).get("allow_effects"):
        raise HTTPException(409, "The test environment has not been approved for a write")
    record_interaction(db, run, "confirmation", {"revision": body.revision, "confirmed": body.confirmed})
    run.confirmed = body.confirmed
    run.status = "RECEIVED" if body.confirmed else "CANCELLED"
    if body.confirmed: run.queued_at = now()
    else: run.finished_at = now()
    audit(db, token.user_id, run.workspace_id, "function.test.confirm" if body.confirmed else "function.test.reject", function.id,
          {"revision": function.revision, "run_id": run.id})
    status_event(db, run)
    owner = token.user_id
    db.commit()
    set_identity(db, owner)
    return test_result(db, function, token, evidence_key=evidence_key)
