"""Explicit development installation of the reviewed Zhiyu catalog."""
from copy import deepcopy
from fastapi import APIRouter, Body, Depends, HTTPException
from sqlalchemy import select, text
from sqlalchemy.orm import Session
from .auth import authenticate, manager, audit
from .db import session, set_identity
from .config import settings
from .models import (Agent, Function, FunctionVersion, DomainPackage, DomainVersion, ActionDefinition, ActionDefinitionVersion,
                     BusinessSkill, BusinessSkillVersion, SyntheticState, ModelProfile, Release)
from .schemas import BusinessAuthorization, Definition, ValidateBody
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

    catalog_packages = function_packages(ref, three_layer=True)
    catalog_policies = {item["key"]: item["definition"].get("business_authorization") for item in catalog_packages}
    description_migrations = {
        "zhiyu.customer.search": (
            "【智驭模拟业务】按名称、编号、城市或业务标签查询当前角色管户；不是客户画像问答。",
            "【智驭模拟业务】按名称、编号、城市或业务标签查询模拟客户目录；不是客户画像问答，对象权限由本功能版本配置。",
        ),
        "zhiyu.segment.list": (
            "【智驭模拟业务】列出当前角色有获准客户的客群包，仅展示授权范围内人数。",
            "【智驭模拟业务】列出模拟目录中的营销客群及关联人数，对象权限由本功能版本配置。",
        ),
        "zhiyu.marketing.list": (
            "【智驭模拟业务】查询当前角色获准营销任务和进度。",
            "【智驭模拟业务】查询模拟目录中的营销任务和进度，对象权限由本功能版本配置。",
        ),
    }

    def add_function(raw):
        from copy import deepcopy

        def changed_paths(before, after, prefix=""):
            if isinstance(before, dict) and isinstance(after, dict):
                return [path for field in sorted(set(before) | set(after))
                        for path in changed_paths(before.get(field), after.get(field),
                                                  f"{prefix}.{field}" if prefix else field)]
            return [] if before == after else [prefix]

        normalized = Definition.model_validate(raw).model_dump()
        key = normalized["connector"]["workflow_id"]
        f = db.scalar(select(Function).where(Function.workspace_id == ws,
                      Function.source_state["synthetic_catalog"].as_string() == CATALOG_ID,
                      Function.definition["connector"]["workflow_id"].as_string() == key))
        if f is None:
            f = Function(workspace_id=ws, definition=normalized, source_state={"synthetic_catalog": CATALOG_ID, "catalog_version": 1})
            db.add(f); db.flush()
        if f.disabled: raise HTTPException(409, "已安装功能已停用，不能覆盖")
        if f.definition != normalized:
            old = Definition.model_validate(f.definition).model_dump()
            new = deepcopy(normalized)
            old_mode = old.pop("business_authorization", None)
            new_mode = new.pop("business_authorization", None)
            expected_mode = catalog_policies.get(key)
            old_auth = BusinessAuthorization.model_validate(old_mode).model_dump() if old_mode is not None else None
            new_auth = BusinessAuthorization.model_validate(new_mode).model_dump() if new_mode is not None else None
            expected_auth = BusinessAuthorization.model_validate(expected_mode).model_dump() if expected_mode is not None else None
            policy_migration = (
                (expected_auth is None and old_auth is None and new_auth is None)
                or (expected_auth is not None and new_auth == expected_auth
                    and (old_auth is None or old_auth == expected_auth
                         or (expected_auth["mode"] == "function" and old_auth == {"mode": "none", "user_id_header": None, "workspace_id_header": None})))
            )
            policy_changed = old_auth != expected_auth
            description_migration = (
                key in description_migrations
                and old.get("description") == description_migrations[key][0]
                and new.get("description") == description_migrations[key][1]
            )
            if description_migration:
                old["description"] = new["description"]
            output_schema_migration = False
            if key == "zhiyu.customer.search":
                expected_old_schema = deepcopy(new.get("output_schema"))
                try:
                    expected_old_schema["properties"]["customers"]["items"]["properties"]["tags"] = {
                        "type": "string", "minLength": 1, "maxLength": 200,
                    }
                    output_schema_migration = old.get("output_schema") == expected_old_schema
                except (KeyError, TypeError):
                    output_schema_migration = False
                if output_schema_migration:
                    old["output_schema"] = deepcopy(new["output_schema"])
            precondition_migration = False
            expected_old_preconditions = deepcopy(new.get("trusted_preconditions"))
            if isinstance(expected_old_preconditions, dict):
                for condition in expected_old_preconditions.get("all", []):
                    if isinstance(condition, dict) and isinstance(condition.get("fact"), str):
                        condition["fact"] = condition["fact"].replace(".bound", ".authorized")
                precondition_migration = old.get("trusted_preconditions") == expected_old_preconditions
                if precondition_migration:
                    old["trusted_preconditions"] = deepcopy(new["trusted_preconditions"])
            # Keep the installed editing contract when republishing this
            # source-catalog function; in particular, never downgrade a
            # function-v2 revision to the catalog template's legacy default.
            authoring_version_preserved = old.get("authoring_version") != new.get("authoring_version")
            if authoring_version_preserved:
                new["authoring_version"] = old.get("authoring_version")
                normalized["authoring_version"] = old.get("authoring_version")
            old_composite = old.get("composite")
            new_composite = new.get("composite")
            dependency_upgrade = old_composite is None and new_composite is None
            dependency_changed = False
            if isinstance(old_composite, dict) and isinstance(new_composite, dict):
                old_dependencies = old_composite.get("dependencies", [])
                new_dependencies = new_composite.get("dependencies", [])
                dependency_changed = old_dependencies != new_dependencies
                dependency_upgrade = ([item.get("function_id") for item in old_dependencies]
                                     == [item.get("function_id") for item in new_dependencies]
                                     and all(item.get("version", 0) <= new_item.get("version", 0)
                                             for item, new_item in zip(old_dependencies, new_dependencies)))
                if dependency_upgrade:
                    old_composite["dependencies"] = deepcopy(new_dependencies)
            presentation_preserved = old.get("presentation") != new.get("presentation")
            if presentation_preserved:
                new["presentation"] = deepcopy(old.get("presentation"))
                normalized["presentation"] = deepcopy(old.get("presentation"))
            catalog_update_required = (policy_changed or description_migration or output_schema_migration
                                       or precondition_migration or dependency_changed)
            # This narrowly permits the catalog's explicit authorization
            # migration, approved description corrections, and version
            # repinning of its own composite children.
            # Any edited business or connector field still stops installation.
            only_catalog_upgrade = (
                policy_migration
                and dependency_upgrade
                and old == new
                and f.latest_version > 0
                and f.status in {"draft", "validated", "published"}
            )
            if not only_catalog_upgrade:
                changed_fields = changed_paths(old, new)
                raise HTTPException(409, {"message": "已安装功能有修改或已停用，不能覆盖",
                                          "function_key": key, "changed_fields": changed_fields,
                                          "migration_checks": {
                                              "policy_migration": policy_migration,
                                              "dependency_upgrade": dependency_upgrade,
                                              "definition_unchanged_after_catalog_migrations": old == new,
                                              "revision_validated": f.validated_revision == f.revision,
                                              "eligible_status": f.status in {"published", "validated"},
                                          }})
            if catalog_update_required:
                f.definition = normalized
                f.revision += 1
                f.validation = {"valid": True, "revision": f.revision, "errors": [],
                                "checks": {"catalog_authorization_policy_upgrade": True}, "probe": None}
                f.validated_revision = f.revision
                f.status = "validated"
                publish_function(db, f, token, reviewed_fixture=True, reviewed_catalog_upgrade=True)
                audit(db, token.user_id, ws, "synthetic.function_authorization_upgrade", f.id,
                      {"version": f.latest_version, "mode": expected_auth.get("mode") if expected_auth else None,
                       "description_migrated": description_migration,
                       "output_schema_migrated": output_schema_migration,
                       "preconditions_migrated": precondition_migration,
                       "authoring_version_preserved": authoring_version_preserved,
                       "presentation_preserved": presentation_preserved,
                       "dependencies_repinned": dependency_changed})
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

    functions = [add_function(p["definition"]) for p in catalog_packages]
    by_key = {f.definition["connector"]["workflow_id"]: f for f in functions}
    composites = []
    for item in business_skill_templates():
        item = {k: v for k, v in item.items() if k != "version"}
        skill = db.scalar(select(BusinessSkill).where(BusinessSkill.workspace_id == ws, BusinessSkill.key == item["key"]))
        if skill and document_skills and skill.definition.get("mode") == "document":
            key = item["key"].replace("zhiyu.skill.", "zhiyu.composite.")
            existing_composite = db.scalar(select(Function).where(
                Function.workspace_id == ws,
                Function.definition["connector"]["workflow_id"].as_string() == key))
            if not existing_composite or existing_composite.disabled:
                raise HTTPException(409, "Reviewed document composite is unavailable")
            composites.append(existing_composite)
            continue
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
    if not document_skills and (changed or not published or published.snapshot.get("synthetic_catalog") != catalog_metadata() or published.snapshot.get("three_layer_prompt_revision") != "scope-v11"):
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
        old_dependencies = [versioned_capability(db, ws, d["function_id"], d["version"], token.user_id)
                            for d in function.definition["composite"]["dependencies"]]
        dependencies = [versioned_capability(db, ws, d["function_id"], None, token.user_id)
                        for d in function.definition["composite"]["dependencies"]]
        if any(new["version"] < old["version"] for old, new in zip(old_dependencies, dependencies)):
            raise HTTPException(409, "Composite dependency version cannot move backwards")
        ref = function.definition["domain_refs"][0]
        expected = Definition.model_validate(document_composite_definition(document, version.id, dependencies, ref)).model_dump()
        current_definition = Definition.model_validate(function.definition).model_dump()
        validation_before_catalog_change = function.validation or {}
        execution_test_before_catalog_change = validation_before_catalog_change.get("execution_test") or {}
        resume_diagnostics = {
            "definition_matches_catalog_before": current_definition == expected,
            "definition_delta_fields_before": sorted(field for field in set(current_definition) | set(expected)
                if current_definition.get(field) != expected.get(field)),
            "revision_before": function.revision,
            "latest_version_before": function.latest_version,
            "status_before": function.status,
            "validated_revision_matches_before": function.validated_revision == function.revision,
            "validation_present_before": bool(validation_before_catalog_change),
            "execution_test_present_before": bool(execution_test_before_catalog_change.get("run_id")),
            "execution_test_revision_matches_before": execution_test_before_catalog_change.get("revision") == function.revision,
            "execution_test_status_before": execution_test_before_catalog_change.get("status"),
        }
        # Preserve user-facing examples and editor metadata; these fields do
        # not alter the composite Skill execution plan.
        preserved_metadata = ("output_examples", "authoring_version")
        for field in preserved_metadata:
            expected[field] = deepcopy(current_definition[field])
        # A legacy composite had no explicit policy. Upgrade only that case to
        # the reviewed default; preserve any policy a maintainer already set.
        if current_definition.get("business_authorization") is not None:
            expected["business_authorization"] = deepcopy(current_definition["business_authorization"])
        # Compare the normalized authoring definition after preserved metadata
        # has been reapplied. Raw JSONB and the generated template may differ
        # in presentation while the effective definition is already current.
        if current_definition != expected:
            prior_version_id = function.definition["composite"]["skill_version_id"]
            if function.definition["composite"].get("mode", "template") == "document":
                old_expected = Definition.model_validate(document_composite_definition(
                    document, prior_version_id, old_dependencies, ref)).model_dump()
            else:
                old_expected = Definition.model_validate(composite_definition(
                    templates[document["key"]], prior_version_id, old_dependencies, ref)).model_dump()
            for field in preserved_metadata:
                old_expected[field] = deepcopy(current_definition[field])
            old_expected["business_authorization"] = deepcopy(current_definition.get("business_authorization"))
            # Once a composite is document-driven, its presentation is part of
            # the maintainer's published display contract. Re-deriving it on
            # each resumable catalog install can bump the function revision
            # after a confirmed write test and demand the same confirmation
            # again. Preserve it; output-contract validation still rejects
            # incompatible mappings before a version can publish.
            preserve_presentation = (
                current_definition.get("composite", {}).get("mode") == "document"
                or current_definition.get("presentation") != old_expected.get("presentation")
            )
            if preserve_presentation:
                expected["presentation"] = deepcopy(current_definition["presentation"])
                old_expected["presentation"] = deepcopy(current_definition["presentation"])
            # Re-check after applying the presentation-preservation rule. If
            # that was the only difference, this is an idempotent retry, not a
            # new function revision and not a reason to re-run a write test.
            if current_definition != expected:
                if current_definition != old_expected:
                    changed_fields = sorted(field for field in set(current_definition) | set(old_expected)
                                            if current_definition.get(field) != old_expected.get(field))
                    raise HTTPException(409, {"message": "Modified composite cannot be overwritten by the catalog upgrade",
                                              "changed_fields": changed_fields})
                function.definition = expected; function.revision += 1; function.validation = None; function.validated_revision = None; function.status = "draft"
                resume_diagnostics["definition_migrated_this_request"] = True
                resume_diagnostics["revision_after_migration"] = function.revision
        published = db.scalar(select(FunctionVersion).where(FunctionVersion.function_id == function.id,
                                                          FunctionVersion.version == function.latest_version))
        # Output examples belong to the editable authoring record; publish
        # intentionally strips them from immutable runtime snapshots. Ignore
        # only that field when deciding whether this composite still needs a
        # tested/published catalog upgrade, otherwise every retry re-runs a
        # write test whenever the maintainer has saved an example.
        published_definition = deepcopy(published.definition) if published else None
        if published_definition is not None:
            published_definition["output_examples"] = function.definition.get("output_examples", [])
        if (not published or published_definition != function.definition
                or published.definition.get("composite", {}).get("mode") != "document"
                or function.status != "published" or function.validated_revision != function.revision):
            # Confirmation is a separate request from the original test. Once
            # its run reaches SUCCEEDED, reuse that exact execution evidence on
            # the resumable install request instead of planning/sending again.
            prior_test = ((function.validation or {}).get("execution_test") or {})
            reused_test = False
            resume_diagnostics.update({"prior_test_present_after_migration": bool(prior_test.get("run_id")),
                "prior_test_revision_matches_after_migration": prior_test.get("revision") == function.revision,
                "prior_test_status_after_migration": prior_test.get("status")})
            if prior_test.get("revision") == function.revision and prior_test.get("run_id"):
                from .authoring_tests import test_result
                refreshed = test_result(db, function, token)
                resume_diagnostics.update({"prior_test_now_passes": bool(refreshed.get("passed")),
                    "prior_test_current_status": refreshed.get("status")})
                if refreshed.get("passed"):
                    static = validate_function(db, function, token,
                        ValidateBody(revision=function.revision, probe=False))
                    static_test = (static.get("execution_test") or {})
                    resume_diagnostics.update({"static_validation_passes": bool(static.get("valid")),
                        "static_error_count": len(static.get("errors") or []),
                        "static_test_run_matches": static_test.get("run_id") == prior_test.get("run_id"),
                        "static_test_revision_matches": static_test.get("revision") == function.revision})
                    if (static.get("valid") and static_test.get("revision") == function.revision
                            and static_test.get("run_id") == prior_test.get("run_id")):
                        function.validation = {**static, "execution_test": refreshed,
                            "probe": {"status": "SUCCEEDED", "data": refreshed.get("data", {})}}
                        function.validation["valid"] = True
                        function.validation["errors"] = []
                        function.validated_revision = function.revision
                        function.status = "validated"
                        reused_test = True
            if not reused_test:
                checked = validate_function(db, function, token, ValidateBody(revision=function.revision, probe=True,
                    arguments=TEST_INPUTS[document["key"].rsplit(".", 1)[-1]], test_goal=document["name"], model_profile_id=model_id,
                    allow_effects=function.definition["effect"] == "WRITE"))
                if not checked["valid"] or not (checked.get("execution_test") or {}).get("passed"):
                    detail = {"function": document["name"], "function_id": function.id, "errors": checked["errors"], "execution_test": checked.get("execution_test")}
                    detail["resume_diagnostics"] = resume_diagnostics
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
    if not prior or prior.snapshot.get("three_layer_prompt_revision") != "scope-v11" or any(
        next(c["version"] for c in prior.snapshot["capabilities"] if c["capability_id"] == row["function_id"]) != row["version"] for row in records):
        agent.revision += 1
        publish_agent(db, agent, token, agent.revision)
    audit(db, token.user_id, ws, "synthetic.document_upgrade", agent.id, {"catalog_id": CATALOG_ID, "composites": records})
    result = {"agent": agent_view(agent, db), "scenario_count": 14, "function_count": 26, "composite_count": 6,
              "composites": records, "chat_path": "/chat/" + agent.public_id, "embed_path": "/embed/" + agent.public_id}
    db.commit(); return result
