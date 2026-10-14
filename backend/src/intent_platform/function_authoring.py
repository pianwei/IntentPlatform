"""Current authoring defaults; published definitions are never normalized in place."""
from copy import deepcopy
from sqlalchemy import select, text
from .models import DomainPackage, DomainVersion, ActionDefinition, ActionDefinitionVersion, Function, FunctionVersion, SyntheticState
from .domains import compile_domain
from .domain_schemas import DomainDefinition

DEFAULT_KEY = "function_defaults"


def options(db, ws):
    entities, results, relations, refs = [], [], [], []
    active_refs = {r["package_id"] for f in db.scalars(select(Function).where(Function.workspace_id == ws, Function.disabled.is_(False), Function.latest_version > 0)) for r in f.definition.get("domain_refs", [])}
    packages = list(db.scalars(select(DomainPackage).where(DomainPackage.workspace_id == ws, DomainPackage.latest_version > 0).order_by(DomainPackage.key)))
    packages.sort(key=lambda package: (package.id not in active_refs, package.key == DEFAULT_KEY, package.key))
    for package in packages:
        source = package.definition.get("source", {}).get("reference", "")
        if active_refs and package.id not in active_refs and package.key != DEFAULT_KEY and (package.key.startswith("demo") or str(source).startswith("demo")):
            continue
        version = db.scalar(select(DomainVersion).where(DomainVersion.package_id == package.id, DomainVersion.version == package.latest_version))
        if not version:
            continue
        ref = {"package_id": package.id, "version": version.version}
        refs.append(ref)
        for item in version.definition["concepts"]:
            row = {"id": item["id"], "label": item["label"], "domain_ref": ref, "source_name": version.definition["name"]}
            (entities if item["kind"] == "object" else results).append(row)
        labels = {c["id"]: c["label"] for c in version.definition["concepts"]}
        relations += [{**r, "domain_ref": ref, "source_name": version.definition["name"],
                       "label": labels.get(r["source_type"], "对象") + " → " + labels.get(r["target_type"], "对象")}
                      for r in version.definition.get("relations", [])]
    actions = []
    for action in db.scalars(select(ActionDefinition).where(ActionDefinition.workspace_id == ws, ActionDefinition.latest_version > 0).order_by(ActionDefinition.key)):
        version = db.scalar(select(ActionDefinitionVersion).where(ActionDefinitionVersion.action_id == action.id, ActionDefinitionVersion.version == action.latest_version))
        if version:
            actions.append({k: version.definition[k] for k in ("key", "name", "effect")})
    default = refs[0] if refs else None
    default_result = results[0]["id"] if results else "business:Detail"
    import json
    from .config import settings
    from .resolvers import resolver_profile
    resolvers = []
    references = set(json.loads(settings().resolvers_json))
    # Local synthetic bindings already approved in published definitions use
    # built-in trusted profiles rather than outbound resolver configuration.
    # Offer those same references only where the catalogue actually exists.
    from .synthetic_runtime import CATALOG_ID
    if settings().env != "production" and db.get(SyntheticState, (ws, CATALOG_ID)) is not None:
        published = db.scalars(select(FunctionVersion).join(Function, Function.id == FunctionVersion.function_id).where(
            Function.workspace_id == ws, Function.disabled.is_(False), FunctionVersion.version == Function.latest_version))
        for version in published:
            definition = version.definition
            bindings = list(definition.get("argument_bindings", {}).values()) + definition.get("result_bindings", [])
            references.update(binding["resolver_ref"] for binding in bindings
                              if binding.get("resolver_ref", "").startswith("zhiyu.entity."))
    for reference in sorted(references):
        try:
            profile = resolver_profile(reference, ws)
            resolvers.append({"reference": reference, "version": profile["version"], "entity_type": profile["entity_type"]})
        except (ValueError, KeyError, TypeError):
            pass
    from .business_catalog import items, view
    directory = items(db, ws)
    managed = {(item.kind, item.key): item for item in directory}
    def available(rows, kind):
        filtered = []
        for row in rows:
            item = managed.get((kind, row.get("id", row.get("key"))))
            if item and not item.definition.get("enabled", True): continue
            if item:
                row.update({"catalog_id": item.id, "origin": item.origin, "adopted_version": item.adopted_version,
                            "enabled": True, "catalog_version": item.latest_version})
                if kind == "object": row["label"] = item.definition["name"]
                else: row["name"] = item.definition["name"]
            filtered.append(row)
        return filtered
    return {"default_domain_ref": default, "domain_refs": refs, "default_result_type": default_result,
            "compiler_version": "finite-json-v2", "http_connector_version": "2", "entities": entities,
            "result_types": results, "actions": available(actions, "action"), "relations": relations, "resolvers": resolvers,
            "entities": available(entities, "object"), "catalog": [view(item) for item in directory]}


def bootstrap(db, ws, user_id):
    db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:key))"), {"key": ws + ":function-defaults"})
    definition = DomainDefinition.model_validate({"name": "基础业务对象", "description": "功能创建的默认对象与结果类型；对象授权仍需可信绑定。",
        "source": {"reference": "builtin:function-defaults-v1", "owner": "平台基础目录"},
        "concepts": [{"id": "business:" + key, "label": label, "kind": "object"} for key, label in
                     [("Customer", "客户"), ("Account", "账户"), ("Product", "产品"), ("Task", "任务"),
                      ("Document", "文档"), ("Department", "部门"), ("Employee", "员工")]] +
                    [{"id": "business:Detail", "label": "业务结果", "kind": "result"}],
        "relations": [{"id": "business:owns", "source_type": "business:Customer", "target_type": "business:Account"}]}).model_dump()
    package = db.scalar(select(DomainPackage).where(DomainPackage.workspace_id == ws, DomainPackage.key == DEFAULT_KEY))
    if package is None:
        package = DomainPackage(workspace_id=ws, key=DEFAULT_KEY, definition=definition, latest_version=1)
        db.add(package); db.flush()
        db.add(DomainVersion(workspace_id=ws, package_id=package.id, version=1, definition=definition,
                             compiled=compile_domain(definition), published_by=user_id))
    for key, name, effect in [("READ", "查询", "READ_ONLY"), ("LIST", "列表筛选", "READ_ONLY"),
                              ("COMPARE", "比较", "READ_ONLY"), ("SUMMARIZE", "汇总", "READ_ONLY"),
                              ("GENERATE", "生成草稿", "READ_ONLY"), ("CREATE", "创建", "WRITE")]:
        action = db.scalar(select(ActionDefinition).where(ActionDefinition.workspace_id == ws, ActionDefinition.key == key))
        if action is None:
            value = {"key": key, "name": name, "description": "默认业务动作：" + name, "effect": effect}
            action = ActionDefinition(workspace_id=ws, key=key, definition=value, latest_version=1)
            db.add(action); db.flush()
            db.add(ActionDefinitionVersion(workspace_id=ws, action_id=action.id, version=1, definition=value))
    db.flush()
    return options(db, ws)


def normalize_draft(db, ws, raw):
    d = deepcopy(raw)
    from .config import settings
    if settings().identity_mode == "local" or settings().env == "production":
        d["authoring_version"] = "function-v2"
    if d.get("authoring_version") != "function-v2":
        return d
    d["compiler_version"] = "finite-json-v2"
    # Business-object authorization belongs to the function contract. Keep
    # old published definitions (which have no field) on their pinned legacy
    # behavior, but make every new function's choice explicit.
    d.setdefault("business_authorization", {"mode": "function"})
    if d["connector"]["kind"] == "http":
        d["connector"]["version"] = "2"
    # Resolve author-selected concepts to current published packages. Existing
    # immutable snapshots keep their original refs, including old versions.
    catalogue = options(db, ws)
    for item in catalogue["catalog"]:
        selected_key = d["object_type"] if item["kind"] == "object" else d["action"]
        if item["key"] == selected_key and not item["enabled"]:
            raise ValueError("Selected business catalogue item is disabled; choose an enabled item for a new draft")
    needed = {d["object_type"], d["result_type"]} | {r["type"] for r in d.get("participant_roles", [])}
    selected = {r["package_id"]: r for r in d.get("domain_refs", [])}
    for row in [*catalogue["entities"], *catalogue["result_types"]]:
        if row["id"] in needed:
            selected[row["domain_ref"]["package_id"]] = row["domain_ref"]
    for row in catalogue["relations"]:
        if row["id"] in {r["name"] for r in d.get("relations", [])}:
            selected[row["domain_ref"]["package_id"]] = row["domain_ref"]
    d["domain_refs"] = list(selected.values())
    return d
