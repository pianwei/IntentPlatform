"""The public-model exception is restricted to reviewed source-controlled fixtures."""
from .schemas import Definition
from .demo.catalog import function_packages, domain_definition, action_definitions
from .demo.skills import business_skill_templates


def assert_synthetic_snapshot(snapshot):
    packages, domain_factory, action_factory, skill_factory = function_packages, domain_definition, action_definitions, business_skill_templates
    from .demo.composites import composite_definition
    is_zhiyu = snapshot.get("synthetic_catalog") is not None
    if is_zhiyu:
        from .synthetic_runtime import catalog_metadata
        if snapshot["synthetic_catalog"] != catalog_metadata():
            raise ValueError("Unreviewed synthetic catalog")
        from .zhiyu import function_packages as packages, domain_definition as domain_factory, action_definitions as action_factory, business_skill_templates as skill_factory, composite_definition
        from .zhiyu.document_skills import business_skill_documents, document_composite_definition
    capabilities = snapshot.get("capabilities")
    if not isinstance(capabilities, list) or not capabilities:
        raise ValueError("Public model testing requires approved synthetic functions")
    seen = set()
    by_id = {c.get("capability_id"): c for c in capabilities}
    skills = {s.get("version_id"): s for s in snapshot.get("business_skills", [])}
    for c in capabilities:
        d = c.get("definition", {})
        if not isinstance(d, dict):
            raise ValueError("Public model testing requires approved synthetic definitions")
        key = d.get("connector", {}).get("workflow_id")
        refs = d.get("domain_refs", [])
        if len(refs) > 1:
            raise ValueError("Unreviewed synthetic domain reference")
        ref = refs[0] if refs else None
        if d.get("execution_kind", "direct") == "composite":
            comp = d.get("composite") or {}
            skill = skills.get(comp.get("skill_version_id"))
            deps = [by_id.get(item["function_id"]) for item in comp.get("dependencies", [])]
            if not skill or not deps or any(not dep or dep["definition"].get("execution_kind", "direct") != "direct" for dep in deps):
                raise ValueError("Unreviewed composite dependencies")
            try:
                factory = document_composite_definition if is_zhiyu and skill["definition"].get("mode") == "document" else composite_definition
                accepted = [Definition.model_validate(factory(skill["definition"], skill["version_id"], deps, ref)).model_dump()]
            except (KeyError, ValueError):
                raise ValueError("Unreviewed public composite") from None
        else:
            accepted = [Definition.model_validate(p["definition"]).model_dump() for upgraded in (False, True)
                        for p in packages(ref, three_layer=upgraded, **({} if is_zhiyu else {"include_extension": True})) if p["key"] == key]
            accepted += [{**value, "authoring_version": "function-v2"} for value in accepted]
        compared = Definition.model_validate(d).model_dump()
        # Server-approved result examples are not model input. Public cards never
        # receive them; no other author-owned text is exempted from this gate.
        compared["output_examples"] = []
        if key in seen or compared not in accepted:
            raise ValueError("Public model testing requires unchanged approved synthetic definitions")
        seen.add(key)
    for domain in snapshot.get("domain_packages", []):
        # DomainDefinition fills optional fields; compare normalized authority.
        from .domain_schemas import DomainDefinition
        if DomainDefinition.model_validate(domain["definition"]).model_dump() != DomainDefinition.model_validate(domain_factory()).model_dump():
            raise ValueError("Public model domain contains unreviewed content")
    allowed = {s["key"]: {k: v for k, v in s.items() if k != "version"} for s in skill_factory()}
    documents = {s["key"]: s for s in business_skill_documents()} if is_zhiyu else {}
    from .planning_api import SkillBody
    for s in snapshot.get("business_skills", []):
        d = s["definition"]
        expected = documents.get(d.get("key")) if d.get("mode") == "document" else allowed.get(d.get("key"))
        if expected is None or SkillBody.model_validate(d).model_dump() != SkillBody.model_validate(expected).model_dump():
            raise ValueError("Public model rule contains unreviewed content")
    actions = {a["key"]: a for a in action_factory()}
    from .planning_api import ActionBody
    for action in snapshot.get("action_definitions", []):
        d = action.get("definition", {})
        if d.get("key") not in actions or ActionBody.model_validate(d).model_dump() != ActionBody.model_validate(actions[d["key"]]).model_dump():
            raise ValueError("Public model action contains unreviewed content")
    projection = snapshot.get("agent_projection")
    if projection:
        for action in projection.get("actions", []):
            expected = actions.get(action.get("key"))
            if not expected or action.get("name") not in {expected["name"], expected["key"]} or action.get("effect") != expected["effect"]:
                raise ValueError("Public model derived action contains unreviewed content")
        from .routing_v3 import domain_projection
        expected_domain = domain_projection(capabilities)
        if projection.get("domain") != expected_domain:
            raise ValueError("Public model derived entity relationships contain unreviewed content")
        concepts = expected_domain.get("concepts", {})
        for entity in projection.get("entities", []):
            expected = concepts.get(entity.get("id"), {})
            if entity.get("label") != expected.get("label", str(entity.get("id", "")).split(":")[-1]):
                raise ValueError("Public model entity name contains unreviewed content")
