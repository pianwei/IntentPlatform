"""Explicit development installation of the reviewed Zhiyu catalog."""
from fastapi import APIRouter, Body, Depends, HTTPException
from sqlalchemy import select, text
from sqlalchemy.orm import Session
from .auth import authenticate, manager, audit
from .db import session, set_identity
from .config import settings
from .models import (Agent, Function, FunctionVersion, DomainPackage, DomainVersion, ActionDefinition, ActionDefinitionVersion,
                     BusinessSkill, BusinessSkillVersion, SyntheticState, ModelProfile, Release)
from .schemas import Definition, ValidateBody
from .domain_schemas import DomainDefinition
from .domains import compile_domain
from .services import validate_function, publish_function, publish_agent, agent_view, compile_for_workspace
from .synthetic_runtime import CATALOG_ID, catalog_metadata

router = APIRouter()
from .api_models import TokenOut


@router.post("/api/v2/runtime/apps/{public_id}/demo-persona", response_model=TokenOut)
def switch_persona(public_id: str, demo_persona: str = Body(embed=True),
                   token=Depends(authenticate), db: Session = Depends(session)):
    import secrets
    from datetime import timedelta
    from .db import uid, now, set_identity
    from .models import RuntimeSession, AuthToken
    from .identity_models import AnonymousPrincipal
    from .local_identity import mint_runtime, secret_hash, locked_account, locked_key
    from .auth import token_valid
    from .runtime_sessions import application, token_view
    from .synthetic_runtime import validate_persona
    from .policies import permitted, effective_channels
    if token.scope != "runtime" or not token.runtime_session_id:
        raise HTTPException(403, "限定应用的运行身份必需")
    agent = application(db, public_id)
    from .capacity import lock_workspace
    lock_workspace(db, agent.workspace_id)
    # Authentication preceded the workspace lock. A concurrent credential
    # change may have committed while this request waited; do not promote an
    # old runtime bearer to the key's or password's newer credential version.
    token_id, runtime_id, agent_id = token.id, token.runtime_session_id, agent.id
    db.expire_all()
    token = db.get(AuthToken, token_id)
    if not token:
        raise HTTPException(401, "运行身份或授权已变更")
    key = account = None
    if token.auth_method == "api-key":
        key = locked_key(db, token.source_key_id)
    elif token.auth_method == "password":
        account = locked_account(db, token.user_id)
    elif token.auth_method != "anonymous":
        raise HTTPException(403, "模拟角色需要本地运行身份")
    old = db.scalar(select(RuntimeSession).where(RuntimeSession.id == runtime_id).with_for_update().execution_options(populate_existing=True))
    db.refresh(token)
    parent = db.get(AuthToken, token.parent_token_id) if token.parent_token_id else None
    if parent:
        db.refresh(parent)
    if not token_valid(db, token):
        raise HTTPException(401, "运行身份或授权已变更")
    agent = db.get(Agent, agent_id)
    if not old or old.revoked or old.agent_id != agent.id or old.user_id != token.user_id:
        raise HTTPException(403, "会话归属不匹配")
    if old.auth_method == "api-key" and old.demo_successor_id:
        raise HTTPException(409, "模拟角色已切换，请恢复当前会话")
    release = db.get(Release, agent.current_release_id)
    persona = validate_persona(demo_persona, release.snapshot)
    if not persona or not effective_channels(agent, release).get(old.channel):
        raise HTTPException(403, "模拟渠道不可用")
    user_id = token.user_id
    if old.auth_method == "anonymous":
        guest = AnonymousPrincipal(user_id=uid("guest"), workspace_id=agent.workspace_id,
            agent_id=agent.id, release_id=release.id, expires_at=now()+timedelta(seconds=settings().anonymous_seconds))
        db.add(guest); db.flush(); user_id = guest.user_id; set_identity(db, user_id)
    elif not permitted(agent, user_id):
        raise HTTPException(403, "应用授权已撤销")
    fresh, runtime = mint_runtime(db, user_id, agent, old.auth_method, key=key,
                                 visitor_id=old.visitor_id, origin=old.host_origin, parent=parent)
    runtime.demo_persona = persona
    if old.auth_method == "api-key":
        # Every trusted host locator in this lineage points directly to its
        # current session. Repeated switches cannot exhaust a traversal limit.
        ancestors = db.scalars(select(RuntimeSession).where(
            RuntimeSession.workspace_id == old.workspace_id,
            RuntimeSession.workspace_epoch == old.workspace_epoch,
            RuntimeSession.auth_method == "api-key", RuntimeSession.user_id == old.user_id,
            RuntimeSession.source_key_id == old.source_key_id, RuntimeSession.key_version == old.key_version,
            RuntimeSession.visitor_id == old.visitor_id, RuntimeSession.agent_id == old.agent_id,
            RuntimeSession.host_origin == old.host_origin,
            RuntimeSession.demo_successor_id == old.id).with_for_update()).all()
        for ancestor in ancestors:
            ancestor.demo_successor_id = runtime.id
        old.demo_successor_id = runtime.id
    recovery = None
    if old.auth_method == "anonymous":
        recovery = secrets.token_urlsafe(36); runtime.recovery_hash = secret_hash(recovery)
    response = token_view(fresh) | {"recovery_secret": recovery}
    db.commit(); return response


@router.post("/api/v2/management/workspaces/{ws}/synthetic/zhiyu/install")
def install(ws: str, model_profile_id: str | None = Body(default=None, embed=True),
            document_skills: bool = Body(default=False, embed=True),
            token=Depends(authenticate), db: Session = Depends(session)):
    if settings().env == "production": raise HTTPException(404, "Not found")
    manager(db, token, ws, "admin")
    from .zhiyu import (function_packages, domain_definition, action_definitions, business_skill_templates,
                        composite_definition, initial_state)
    from .agent_projection import versioned_capability
    db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:key))"), {"key": ws + ":" + CATALOG_ID})
    agent = db.scalar(select(Agent).where(Agent.workspace_id == ws, Agent.synthetic_catalog["id"].as_string() == CATALOG_ID,
                                        Agent.runtime_ui["authoring_test"].is_(None)))
    if agent and document_skills:
        return upgrade_documents(ws, model_profile_id, token, db)
    if model_profile_id:
        model = db.get(ModelProfile, model_profile_id)
        if not model or model.workspace_id != ws or model.disabled or not model.latest_version:
            raise HTTPException(409, "模型配置不可用")
    domain = db.scalar(select(DomainPackage).where(DomainPackage.workspace_id == ws, DomainPackage.key == CATALOG_ID))
    definition = DomainDefinition.model_validate(domain_definition()).model_dump()
    if domain is None:
        domain = DomainPackage(workspace_id=ws, key=CATALOG_ID, definition=definition)
        db.add(domain); db.flush()
    if domain.definition != definition: raise HTTPException(409, "已安装领域有修改，不能覆盖")
    if not domain.latest_version:
        compiled = compile_domain(definition); domain.latest_version = 1
        domain.validated_revision = domain.revision
        domain.validation = {"valid": True, "revision": domain.revision, "errors": []}
        db.add(DomainVersion(workspace_id=ws, package_id=domain.id, version=1, definition=definition,
                             compiled=compiled, published_by=token.user_id)); db.flush()
    ref = {"package_id": domain.id, "version": domain.latest_version}
    if not db.get(SyntheticState, (ws, CATALOG_ID)):
        db.add(SyntheticState(workspace_id=ws, catalog_id=CATALOG_ID, state=initial_state())); db.flush()
    for item in action_definitions():
        action = db.scalar(select(ActionDefinition).where(ActionDefinition.workspace_id == ws, ActionDefinition.key == item["key"]))
        if action is None:
            action = ActionDefinition(workspace_id=ws, key=item["key"], definition=item)
            db.add(action); db.flush()
        if action.definition != item: raise HTTPException(409, "动作定义冲突")
        if not action.latest_version:
            action.latest_version = 1
            db.add(ActionDefinitionVersion(workspace_id=ws, action_id=action.id, version=1, definition=item))

    def add_function(raw):
        normalized = Definition.model_validate(raw).model_dump()
        key = normalized["connector"]["workflow_id"]
        f = db.scalar(select(Function).where(Function.workspace_id == ws,
                      Function.source_state["synthetic_catalog"].as_string() == CATALOG_ID,
                      Function.definition["connector"]["workflow_id"].as_string() == key))
        if f is None:
            f = Function(workspace_id=ws, definition=normalized, source_state={"synthetic_catalog": CATALOG_ID, "catalog_version": 1})
            db.add(f); db.flush()
        if f.definition != normalized or f.disabled: raise HTTPException(409, "已安装功能有修改或已停用，不能覆盖")
        if not f.latest_version:
            if normalized.get("execution_kind") == "composite":
                from .agent_projection import composite_parts
                compile_for_workspace(db, normalized, ws)
                composite_parts(db, ws, normalized, token.user_id)
                f.validation = {"valid": True, "revision": f.revision, "errors": [], "checks": {"reviewed_legacy_fixture": True}, "probe": None}
                f.validated_revision = f.revision
                checked = f.validation
            else:
                checked = validate_function(db, f, token, ValidateBody())
            if not checked["valid"]: raise HTTPException(409, {"function": normalized["name"], "errors": checked["errors"]})
            publish_function(db, f, token, reviewed_fixture=True)
        return f

    functions = [add_function(p["definition"]) for p in function_packages(ref, three_layer=True)]
    by_key = {f.definition["connector"]["workflow_id"]: f for f in functions}
    composites = []
    for item in business_skill_templates():
        item = {k: v for k, v in item.items() if k != "version"}
        skill = db.scalar(select(BusinessSkill).where(BusinessSkill.workspace_id == ws, BusinessSkill.key == item["key"]))
        if skill is None:
            skill = BusinessSkill(workspace_id=ws, key=item["key"], definition=item)
            db.add(skill); db.flush()
        if skill.definition != item or skill.disabled: raise HTTPException(409, "已安装业务规则有修改或已停用")
        if not skill.latest_version:
            skill.latest_version = 1
            db.add(BusinessSkillVersion(workspace_id=ws, skill_id=skill.id, version=1, definition=item)); db.flush()
        version = db.scalar(select(BusinessSkillVersion).where(BusinessSkillVersion.skill_id == skill.id,
                            BusinessSkillVersion.version == skill.latest_version))
        keys = list(dict.fromkeys(s["capability_key"] for s in item["steps"]))
        deps = [versioned_capability(db, ws, by_key[k].id) for k in keys]
        composites.append(add_function(composite_definition(item, version.id, deps, ref)))
    if not db.get(SyntheticState, (ws, CATALOG_ID)):
        db.add(SyntheticState(workspace_id=ws, catalog_id=CATALOG_ID, state=initial_state()))
    ids = [f.id for f in [*functions, *composites]]
    changed = agent is None
    if agent is None:
        agent = Agent(workspace_id=ws, name="智驭业务助手", capability_ids=ids, planning_enabled=True,
                      routing_strategy="three-layer-v1", business_skill_ids=[], model_profile_id=model_profile_id,
                      synthetic_catalog=catalog_metadata(), allowed_origins=settings().cors_origins.split(","),
                      suggested_questions=["查一下我名下的代发高潜客户", "为星澜科技准备访前一页纸", "解释一下流动资金贷款"],
                      runtime_ui={"title": "智驭业务助手", "description": "客户、产品与日常业务，一处问答",
                                  "welcome_message": "今天想了解什么？", "input_placeholder": "查客户、问产品，或描述需要完成的任务"})
        db.add(agent); db.flush()
    elif agent.capability_ids != ids or (model_profile_id and agent.model_profile_id != model_profile_id):
        changed = True
        agent.capability_ids = ids
        if model_profile_id: agent.model_profile_id = model_profile_id
        agent.revision += 1
    published = db.get(Release, agent.current_release_id) if agent.current_release_id else None
    if changed or not published or published.snapshot.get("synthetic_catalog") != catalog_metadata() or published.snapshot.get("three_layer_prompt_revision") != "scope-v9":
        publish_agent(db, agent, token, agent.revision)
    audit(db, token.user_id, ws, "synthetic.install", agent.id, {"catalog_id": CATALOG_ID, "version": 1, "scenarios": 14})
    result = {"agent": agent_view(agent, db), "scenario_count": 14, "function_count": len(functions),
              "composite_count": len(composites), "chat_path": "/chat/" + agent.public_id, "embed_path": "/embed/" + agent.public_id}
    owner = token.user_id
    db.commit()
    if document_skills:
        set_identity(db, owner)
        return upgrade_documents(ws, model_profile_id, token, db)
    return result


@router.post("/api/v2/management/workspaces/{ws}/synthetic/zhiyu/document-skills")
def upgrade_documents(ws: str, model_profile_id: str | None = Body(default=None, embed=True),
                      token=Depends(authenticate), db: Session = Depends(session)):
    """Explicit, resumable upgrade; only new versions and a new release are made."""
    if settings().env == "production":
        raise HTTPException(404, "Not found")
    manager(db, token, ws, "admin")
    from .zhiyu.skills import business_skill_templates, composite_definition
    from .zhiyu.document_skills import business_skill_documents, document_composite_definition, TEST_INPUTS
    from .planning_api import SkillBody
    from .agent_projection import versioned_capability
    agent = db.scalar(select(Agent).where(Agent.workspace_id == ws, Agent.synthetic_catalog["id"].as_string() == CATALOG_ID,
                                       Agent.runtime_ui["authoring_test"].is_(None)).with_for_update())
    if not agent:
        raise HTTPException(409, "Install the reviewed Zhiyu direct catalog first")
    model_id = model_profile_id or agent.model_profile_id
    records = []
    templates = {s["key"]: s for s in business_skill_templates()}
    for document in business_skill_documents():
        manager(db, token, ws, "admin")
        skill = db.scalar(select(BusinessSkill).where(BusinessSkill.workspace_id == ws, BusinessSkill.key == document["key"]).with_for_update())
        if not skill or skill.disabled:
            raise HTTPException(409, "Reviewed business Skill is unavailable")
        old_normal = SkillBody.model_validate({k: v for k, v in templates[document["key"]].items() if k != "version"}).model_dump()
        doc_normal = SkillBody.model_validate(document).model_dump()
        current = SkillBody.model_validate({k: v for k, v in skill.definition.items() if k != "version"}).model_dump()
        if current not in [old_normal, doc_normal]:
            raise HTTPException(409, "Modified Skill cannot be overwritten by the catalog upgrade")
        if current != doc_normal:
            skill.definition = doc_normal; skill.revision += 1; skill.latest_version += 1
            db.add(BusinessSkillVersion(workspace_id=ws, skill_id=skill.id, version=skill.latest_version, definition=doc_normal)); db.flush()
        version = db.scalar(select(BusinessSkillVersion).where(BusinessSkillVersion.skill_id == skill.id, BusinessSkillVersion.version == skill.latest_version))
        key = document["key"].replace("zhiyu.skill.", "zhiyu.composite.")
        function = db.scalar(select(Function).where(Function.workspace_id == ws, Function.definition["connector"]["workflow_id"].as_string() == key).with_for_update())
        if not function or function.disabled:
            raise HTTPException(409, "Reviewed composite is unavailable")
        dependencies = [versioned_capability(db, ws, d["function_id"], d["version"], token.user_id) for d in function.definition["composite"]["dependencies"]]
        ref = function.definition["domain_refs"][0]
        expected = Definition.model_validate(document_composite_definition(document, version.id, dependencies, ref)).model_dump()
        # Preserve approved examples as management metadata, never as execution.
        expected["output_examples"] = function.definition.get("output_examples", [])
        if function.definition != expected:
            prior_version_id = function.definition["composite"]["skill_version_id"]
            prior_skill = db.get(BusinessSkillVersion, prior_version_id)
            old_expected = Definition.model_validate(composite_definition(templates[document["key"]], prior_version_id, dependencies, ref)).model_dump()
            if Definition.model_validate(function.definition).model_dump() != old_expected:
                raise HTTPException(409, "Modified composite cannot be overwritten by the catalog upgrade")
            function.definition = expected; function.revision += 1; function.validation = None; function.validated_revision = None; function.status = "draft"
        published = db.scalar(select(FunctionVersion).where(FunctionVersion.function_id == function.id,
                                                          FunctionVersion.version == function.latest_version))
        if not published or published.definition.get("composite", {}).get("mode") != "document":
            checked = validate_function(db, function, token, ValidateBody(revision=function.revision, probe=True,
                arguments=TEST_INPUTS[document["key"].rsplit(".", 1)[-1]], test_goal=document["name"], model_profile_id=model_id,
                allow_effects=function.definition["effect"] == "WRITE"))
            if not checked["valid"] or not (checked.get("execution_test") or {}).get("passed"):
                detail = {"function": document["name"], "function_id": function.id, "errors": checked["errors"], "execution_test": checked.get("execution_test")}
                db.commit()
                raise HTTPException(409, detail)
            publish_function(db, function, token)
        records.append({"name": document["name"], "function_id": function.id, "version": function.latest_version})
        owner = token.user_id
        db.commit()
        set_identity(db, owner)
    agent = db.get(Agent, agent.id)
    if model_id:
        agent.model_profile_id = model_id
    prior = db.get(Release, agent.current_release_id)
    if not prior or prior.snapshot.get("three_layer_prompt_revision") != "scope-v10" or any(
        next(c["version"] for c in prior.snapshot["capabilities"] if c["capability_id"] == row["function_id"]) != row["version"] for row in records):
        agent.revision += 1
        publish_agent(db, agent, token, agent.revision)
    audit(db, token.user_id, ws, "synthetic.document_upgrade", agent.id, {"catalog_id": CATALOG_ID, "composites": records})
    result = {"agent": agent_view(agent, db), "scenario_count": 14, "function_count": 26, "composite_count": 6,
              "composites": records, "chat_path": "/chat/" + agent.public_id, "embed_path": "/embed/" + agent.public_id}
    db.commit(); return result
