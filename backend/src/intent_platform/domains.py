"""Workspace-private immutable domain registry and finite projection compiler."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import Field
from sqlalchemy import select, text
from sqlalchemy.orm import Session
from rdflib import Graph, URIRef, Literal
from rdflib.namespace import RDF, RDFS
from .db import session
from .auth import authenticate, manager, audit
from .models import DomainPackage, DomainVersion, Document
from .domain_schemas import DomainModel, DomainDefinition, DomainCreate, DomainEdit, DomainRef
from .api_models import RevisionBody

router = APIRouter()
PREFIX = "/api/v2/management/workspaces/{ws}/domains"


class ResolverSummary(DomainModel):
    reference: str
    version: str
    entity_type: str


@router.get("/api/v2/management/workspaces/{ws}/resolvers", response_model=list[ResolverSummary])
def resolver_directory(ws: str, token=Depends(authenticate), db: Session = Depends(session)):
    import json
    from .config import settings
    from .resolvers import resolver_profile
    manager(db, token, ws)
    rows = []
    for reference in sorted(json.loads(settings().resolvers_json)):
        try:
            profile = resolver_profile(reference, ws)
        except (ValueError, KeyError, TypeError):
            continue
        rows.append({"reference": reference, "version": profile["version"], "entity_type": profile["entity_type"]})
    return rows


def unique(items, key):
    result = {item[key]: item for item in items}
    if len(result) != len(items):
        raise ValueError("Duplicate stable identifier")
    return result


def compile_domain(definition):
    d = DomainDefinition.model_validate(definition).model_dump()
    concepts = unique(d["concepts"], "id")
    relations = unique(d["relations"], "id")
    units = unique(d["units"], "id")
    closure = {}
    def ancestors(identifier, path):
        if identifier in path:
            raise ValueError("Concept inheritance cycle")
        if identifier not in concepts:
            raise ValueError("Unknown parent concept")
        if identifier in closure:
            return closure[identifier]
        current = concepts[identifier]
        if len(set(current["parents"])) != len(current["parents"]):
            raise ValueError("Duplicate parent concept")
        values = {identifier}
        for parent in current["parents"]:
            inherited = ancestors(parent, path | {identifier})
            if concepts[parent]["kind"] != current["kind"]:
                raise ValueError("Object/result concept kinds cannot be mixed through inheritance")
            values.update(inherited)
        closure[identifier] = sorted(values)
        return closure[identifier]
    for identifier in concepts:
        ancestors(identifier, set())
    for relation in relations.values():
        if relation["source_type"] not in concepts or relation["target_type"] not in concepts:
            raise ValueError("Relation endpoints must be registered concepts")
    conversions = {}
    for conversion in d["conversions"]:
        source, target = conversion["from_unit"], conversion["to_unit"]
        if source not in units or target not in units or units[source]["dimension"] != units[target]["dimension"]:
            raise ValueError("Unit conversion requires registered units of one dimension")
        key = source + "->" + target
        if source == target or key in conversions:
            raise ValueError("Duplicate or identity conversion is unsupported")
        conversions[key] = conversion["factor"]
    graph = Graph()
    for identifier, concept in concepts.items():
        graph.add((URIRef(identifier), RDF.type, RDFS.Class))
        graph.add((URIRef(identifier), RDFS.label, Literal(concept["label"])))
        for parent in concept["parents"]:
            graph.add((URIRef(identifier), RDFS.subClassOf, URIRef(parent)))
    for relation in relations.values():
        graph.add((URIRef(relation["id"]), RDFS.domain, URIRef(relation["source_type"])))
        graph.add((URIRef(relation["id"]), RDFS.range, URIRef(relation["target_type"])))
    return {"compiler_version": "domain-v1", "concepts": concepts, "closure": closure, "relations": relations,
            "units": units, "conversions": conversions, "rdf": graph.serialize(format="turtle")}


def domain_source(db, definition, workspace_id):
    source = definition["source"]
    if source.get("document_id"):
        from .document_versions import get_version
        document = get_version(db, workspace_id, source["document_id"], source.get("revision"))
        matching = [s for s in document.sections if source.get("section") in {s["id"], s["heading"]}]
        if len(matching) != 1:
            raise ValueError("Source document/revision/section unavailable in this workspace")


def project_domains(db, workspace_id, refs):
    projection = {"concepts": {}, "closure": {}, "relations": {}, "units": {}, "conversions": {}, "versions": []}
    seen = set()
    for raw in refs:
        ref = DomainRef.model_validate(raw)
        if ref.package_id in seen:
            raise ValueError("A projection pins exactly one version per domain package")
        seen.add(ref.package_id)
        version = db.scalar(select(DomainVersion).where(DomainVersion.workspace_id == workspace_id, DomainVersion.package_id == ref.package_id, DomainVersion.version == ref.version))
        if not version:
            raise ValueError("Published domain version is unavailable in this workspace")
        projection["versions"].append({"id": version.id, "package_id": version.package_id, "version": version.version, "definition": version.definition})
        for category in ("concepts", "closure", "relations", "units", "conversions"):
            for key, value in version.compiled[category].items():
                if key in projection[category] and projection[category][key] != value:
                    raise ValueError("Conflicting same-ID domain definitions; explicit reconciliation required")
                projection[category][key] = value
    if not seen:
        raise ValueError("Compiler v2 requires an explicit published domain version")
    return projection


class DomainValidation(DomainModel):
    valid: bool
    revision: int
    errors: list[str]
    checks: dict


class DomainDraftOut(DomainModel):
    id: str
    workspace_id: str
    key: str
    revision: int
    latest_version: int
    definition: DomainDefinition
    validation: DomainValidation | None


class DomainVersionOut(DomainModel):
    id: str
    package_id: str
    workspace_id: str
    version: int
    definition: DomainDefinition
    compiled: dict
    published_by: str


def view(package):
    return {field: getattr(package, field) for field in DomainDraftOut.model_fields}


def version_view(version):
    return {field: getattr(version, field) for field in DomainVersionOut.model_fields}


def get_package(db, ws, domain_id, lock=False):
    query = select(DomainPackage).where(DomainPackage.workspace_id == ws, DomainPackage.id == domain_id)
    if lock:
        query = query.with_for_update()
    package = db.scalar(query)
    if not package:
        raise HTTPException(404, "Domain unavailable")
    return package


@router.get(PREFIX, response_model=list[DomainDraftOut])
def list_domains(ws: str, token=Depends(authenticate), db: Session = Depends(session)):
    manager(db, token, ws)
    return [view(d) for d in db.scalars(select(DomainPackage).where(DomainPackage.workspace_id == ws).order_by(DomainPackage.key))]


@router.post(PREFIX, status_code=201, response_model=DomainDraftOut)
def create_domain(ws: str, body: DomainCreate, token=Depends(authenticate), db: Session = Depends(session)):
    manager(db, token, ws)
    db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:key))"), {"key": ws + ":domain:" + body.key})
    if db.scalar(select(DomainPackage).where(DomainPackage.workspace_id == ws, DomainPackage.key == body.key)):
        raise HTTPException(409, "Domain key already exists in this workspace")
    domain = DomainPackage(workspace_id=ws, key=body.key, definition=body.definition.model_dump())
    db.add(domain); db.flush(); audit(db, token.user_id, ws, "domain.create", domain.id)
    result = view(domain); db.commit(); return result


@router.patch(PREFIX + "/{domain_id}/draft", response_model=DomainDraftOut)
def edit_domain(ws: str, domain_id: str, body: DomainEdit, token=Depends(authenticate), db: Session = Depends(session)):
    manager(db, token, ws); domain = get_package(db, ws, domain_id, lock=True)
    if domain.revision != body.revision:
        raise HTTPException(409, "Domain draft revision changed")
    domain.definition = body.definition.model_dump(); domain.revision += 1; domain.validation = None; domain.validated_revision = None
    audit(db, token.user_id, ws, "domain.edit", domain.id, {"revision": domain.revision})
    result = view(domain); db.commit(); return result


@router.post(PREFIX + "/{domain_id}/validate", response_model=DomainValidation)
def validate_domain(ws: str, domain_id: str, token=Depends(authenticate), db: Session = Depends(session)):
    manager(db, token, ws); domain = get_package(db, ws, domain_id, lock=True)
    errors, checks = [], {}
    try:
        domain_source(db, domain.definition, ws)
        compiled = compile_domain(domain.definition)
        checks = {"finite_domain": True, "concept_count": len(compiled["concepts"]), "closure": True}
    except ValueError as exc:
        errors.append(str(exc))
    domain.validation = {"valid": not errors, "revision": domain.revision, "errors": errors, "checks": checks}
    domain.validated_revision = domain.revision if not errors else None
    audit(db, token.user_id, ws, "domain.validate", domain.id, {"valid": not errors})
    result = domain.validation; db.commit(); return result


@router.post(PREFIX + "/{domain_id}/publish", response_model=DomainVersionOut)
def publish_domain(ws: str, domain_id: str, body: RevisionBody, token=Depends(authenticate), db: Session = Depends(session)):
    manager(db, token, ws, "publisher"); domain = get_package(db, ws, domain_id, lock=True)
    if domain.revision != body.revision or domain.validated_revision != domain.revision:
        raise HTTPException(409, "Validate the current domain revision before publication")
    domain_source(db, domain.definition, ws)
    compiled = compile_domain(domain.definition); domain.latest_version += 1
    version = DomainVersion(workspace_id=ws, package_id=domain.id, version=domain.latest_version,
                            definition=domain.definition, compiled=compiled, published_by=token.user_id)
    db.add(version); db.flush(); audit(db, token.user_id, ws, "domain.publish", domain.id, {"version": version.version})
    result = version_view(version); db.commit(); return result


@router.get(PREFIX + "/{domain_id}/versions", response_model=list[DomainVersionOut])
def domain_versions(ws: str, domain_id: str, token=Depends(authenticate), db: Session = Depends(session)):
    manager(db, token, ws); get_package(db, ws, domain_id)
    return [version_view(v) for v in db.scalars(select(DomainVersion).where(DomainVersion.package_id == domain_id).order_by(DomainVersion.version.desc()))]
