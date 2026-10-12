"""Finite v2 semantic constraints; Python decisions and generated SHACL share definitions."""
import copy
import math
import re
from datetime import datetime, date, timedelta, timezone
from zoneinfo import ZoneInfo
from rdflib import Graph, BNode, URIRef, Literal, Namespace
from rdflib.namespace import RDF, XSD
from rdflib.collection import Collection
from pyshacl import validate
from .domain_schemas import ParticipantRole, RoleRelation, ArgumentBinding, ParameterSemantic, ResultBinding
from .ontology import _compile_definition_v1, input_errors, validate_pair, sample_value, SH

V = Namespace("urn:intent:semantic:")
ROOT = V.request


def subtype(actual, expected, projection):
    return expected in projection["closure"].get(actual, [])


def check_expression(expression, depth=0, budget=None):
    budget = [0] if budget is None else budget
    budget[0] += 1
    if budget[0] > 64:
        raise ValueError("Trusted condition exceeds the finite node budget")
    if depth > 6 or not isinstance(expression, dict):
        raise ValueError("Unsupported trusted condition structure/depth")
    if set(expression) in ({"all"}, {"any"}):
        children = expression[next(iter(expression))]
        if not isinstance(children, list) or not 1 <= len(children) <= 8:
            raise ValueError("Condition groups require 1 to 8 expressions")
        for child in children:
            check_expression(child, depth + 1, budget)
    elif set(expression) == {"not"}:
        check_expression(expression["not"], depth + 1, budget)
    elif set(expression) <= {"fact", "operator", "value"} and {"fact", "operator"} <= set(expression):
        if not re.fullmatch(r"identity\.authenticated|(?:entity|history)\.[a-z][a-z0-9_]{0,63}\.authorized", expression["fact"]):
            raise ValueError("Trusted conditions may only use registered server facts")
        if expression["operator"] not in {"eq", "ne", "exists"} or (expression["operator"] != "exists" and type(expression.get("value")) is not bool):
            raise ValueError("Trusted conditions compare explicit booleans")
    else:
        raise ValueError("Unsupported trusted condition")


def truth(expression, facts):
    if expression is None:
        return True
    if "not" in expression:
        value = truth(expression["not"], facts)
        return None if value is None else not value
    for key in ("all", "any"):
        if key in expression:
            values = [truth(x, facts) for x in expression[key]]
            if key == "all":
                return False if False in values else None if None in values else True
            return True if True in values else None if None in values else False
    value = facts.get(expression["fact"])
    if value is None:
        return None
    if expression["operator"] == "exists":
        return True
    matched = value is expression["value"]
    return matched if expression["operator"] == "eq" else not matched


def property_shape(graph, shape, path, **constraints):
    prop = BNode(); graph.add((shape, SH.property, prop)); graph.add((prop, SH.path, path))
    for key, value in constraints.items():
        graph.add((prop, SH[key], value if isinstance(value, (URIRef, BNode, Literal)) else Literal(value)))
    return prop


def and_or(graph, shape, kind, children):
    head = BNode(); Collection(graph, head, children); graph.add((shape, SH[kind], head))


def condition_shape(graph, expression, want=True):
    shape = BNode()
    if "not" in expression:
        return condition_shape(graph, expression["not"], not want)
    for key in ("all", "any"):
        if key in expression:
            combine = "and" if (key == "all") == want else "or"
            and_or(graph, shape, combine, [condition_shape(graph, x, want) for x in expression[key]])
            return shape
    path = V["fact:" + expression["fact"]]
    property_shape(graph, shape, path, minCount=1, maxCount=1, datatype=XSD.boolean)
    if expression["operator"] == "exists":
        if not want:
            # Known existence cannot be FALSE; UNKNOWN must not satisfy NOT(exists).
            property_shape(graph, shape, path, maxCount=0)
    else:
        expected = expression["value"] if (expression["operator"] == "eq") == want else not expression["value"]
        property_shape(graph, shape, path, hasValue=Literal(expected))
    return shape


def condition_pair(expression, facts):
    graph, shapes = Graph(), Graph()
    shape = condition_shape(shapes, expression); shapes.add((shape, SH.targetNode, ROOT))
    for key, value in facts.items():
        if value is not None:
            graph.add((ROOT, V["fact:" + key], Literal(value)))
    rdf, _, _ = validate(graph, shacl_graph=shapes)
    return truth(expression, facts) is True, bool(rdf)


def semantic_errors(definition, request, projection):
    errors = []
    if request.get("action") != definition["action"] or request.get("result_type") != definition["result_type"] or not subtype(request.get("object_type"), definition["object_type"], projection):
        errors.append("Action, object or result type does not match capability semantics")
    if request.get("negated") is not False:
        errors.append("Negated or unspecified execution intent")
    roles = {r["role"]: r for r in definition["participant_roles"]}
    participants = request.get("participants", [])
    for participant in participants:
        role = roles.get(participant["role"])
        if not role or not subtype(participant["type"], role["type"], projection):
            errors.append("Unknown role or incompatible participant type")
    for name, role in roles.items():
        count = sum(p["role"] == name for p in participants)
        if not role["min_count"] <= count <= role["max_count"]:
            errors.append("Participant cardinality violated")
    expected = {(r["name"], r["source"], r["target"]) for r in definition["relations"]}
    actual = [(r["name"], r["source"], r["target"]) for r in request.get("relations", [])]
    if len(set(actual)) != len(actual) or set(actual) != expected:
        errors.append("Relation direction or relation set does not match the capability")
    return errors


def semantic_shapes(definition, projection):
    graph = Graph(); shape = V.SemanticShape
    graph.add((shape, SH.targetNode, ROOT)); graph.add((shape, SH.closed, Literal(True)))
    property_shape(graph, shape, V.action, minCount=1, maxCount=1, hasValue=Literal(definition["action"]))
    prop = property_shape(graph, shape, V.object_type, minCount=1, maxCount=1)
    permitted = [URIRef(t) for t in projection["concepts"] if subtype(t, definition["object_type"], projection)]
    head = BNode(); Collection(graph, head, permitted); graph.add((prop, SH["in"], head))
    property_shape(graph, shape, V.result_type, minCount=1, maxCount=1, hasValue=URIRef(definition["result_type"]))
    property_shape(graph, shape, V.negated, minCount=1, maxCount=1, hasValue=Literal(False))
    for role in definition["participant_roles"]:
        prop = property_shape(graph, shape, V["role:" + role["role"]], minCount=role["min_count"], maxCount=role["max_count"])
        graph.add((prop, SH["class"], URIRef(role["type"])))
    expected = ["|".join((r["name"], r["source"], r["target"])) for r in definition["relations"]]
    prop = property_shape(graph, shape, V.relations, minCount=len(expected), maxCount=len(expected))
    if expected:
        head = BNode(); Collection(graph, head, [Literal(x) for x in expected]); graph.add((prop, SH["in"], head))
        for relation in expected:
            graph.add((prop, SH.hasValue, Literal(relation)))
    return graph


def semantic_pair(definition, request, projection):
    facts = Graph()
    for field in ("action", "object_type", "result_type", "negated"):
        if field in request:
            value = URIRef(request[field]) if field in {"object_type", "result_type"} else Literal(request[field])
            facts.add((ROOT, V[field], value))
    for participant in request.get("participants", []):
        node = BNode(); facts.add((ROOT, V["role:" + participant["role"]], node))
        for concept in projection["closure"].get(participant["type"], []):
            facts.add((node, RDF.type, URIRef(concept)))
    for relation in request.get("relations", []):
        facts.add((ROOT, V.relations, Literal("|".join((relation["name"], relation["source"], relation["target"])))))
    rdf, _, _ = validate(facts, shacl_graph=semantic_shapes(definition, projection))
    # RDF is a set: reject duplicate relation assertions at the authoritative JSON boundary.
    relations = request.get("relations", [])
    unique_relations = len({(x["name"], x["source"], x["target"]) for x in relations}) == len(relations)
    return not semantic_errors(definition, request, projection), bool(rdf) and unique_relations


def normalize_parameters(definition, arguments, units, projection, reference_time, timezone_name):
    result, normalized_units, provenance = copy.deepcopy(arguments), dict(units), {}
    if set(units) - set(definition["parameter_semantics"]):
        raise ValueError("Unit supplied for an undeclared semantic parameter")
    for name, spec in definition["parameter_semantics"].items():
        if name not in arguments:
            continue
        value = arguments[name]
        if spec["kind"] == "quantity":
            source = units.get(name)
            target = spec["unit"]
            factor = 1 if source == target else projection["conversions"].get(str(source) + "->" + target)
            if factor is None or type(value) not in (int, float) or not math.isfinite(value):
                raise ValueError("Quantity needs a declared unit and supported finite conversion")
            converted = value * factor
            if not math.isfinite(converted):
                raise ValueError("Normalized quantity must remain finite")
            result[name] = converted; normalized_units[name] = target
            provenance[name] = {"kind": "quantity", "source_unit": source, "unit": target, "factor": factor}
        elif spec["kind"] == "date":
            if not isinstance(value, str):
                raise ValueError("Date requires an explicit string")
            if value in {"today", "yesterday"} and spec["allow_relative"]:
                local = reference_time.astimezone(ZoneInfo(timezone_name)).date()
                result[name] = (local - timedelta(days=int(value == "yesterday"))).isoformat()
            elif date.fromisoformat(value).isoformat() != value:
                raise ValueError("Use canonical YYYY-MM-DD dates")
            provenance[name] = {"kind": "date", "input": value, "reference_time": reference_time.isoformat(), "timezone": timezone_name}
        else:
            if not isinstance(value, str) or "T" not in value:
                raise ValueError("Datetime requires a timestamp with explicit offset")
            parsed = datetime.fromisoformat(value)
            if parsed.tzinfo is None or parsed.utcoffset() is None:
                raise ValueError("Datetime requires a timestamp with explicit offset")
            result[name] = parsed.astimezone(timezone.utc).isoformat()
            provenance[name] = {"kind": "datetime", "input": value}
    return result, normalized_units, provenance


def parameter_pair(definition, arguments, units, projection, reference_time, timezone_name):
    try:
        normalized, _, _ = normalize_parameters(definition, arguments, units, projection, reference_time, timezone_name)
        python_ok = not input_errors(definition["input_schema"], normalized)
    except (ValueError, TypeError, OverflowError):
        normalized = arguments
        python_ok = False
    # Normalization is generated from one unit/time policy, while constraints have independent SHACL shapes.
    rdf_ok = validate_pair(definition["input_schema"], normalized)[1]
    graph, shapes = Graph(), Graph(); root_shape = V.Parameters
    shapes.add((root_shape, SH.targetNode, ROOT))
    for name, spec in definition["parameter_semantics"].items():
        if name not in arguments:
            continue
        value = normalized[name]
        path = V["parameter:" + name]
        if spec["kind"] == "quantity":
            allowed = [spec["unit"], *[x.split("->")[0] for x in projection["conversions"] if x.endswith("->" + spec["unit"])]]
            graph.add((ROOT, path, Literal(units.get(name, ""))))
            prop = property_shape(shapes, root_shape, path, minCount=1)
            head = BNode(); Collection(shapes, head, [Literal(x) for x in allowed]); shapes.add((prop, SH["in"], head))
            if type(value) not in (int, float) or not math.isfinite(value):
                rdf_ok = False
            factor = 1 if units.get(name) == spec["unit"] else projection["conversions"].get(str(units.get(name)) + "->" + spec["unit"])
            try:
                if factor is None or not math.isfinite(arguments[name] * factor):
                    rdf_ok = False
            except (ValueError, TypeError, OverflowError):
                rdf_ok = False
        else:
            datatype = XSD.date if spec["kind"] == "date" else XSD.dateTime
            graph.add((ROOT, path, Literal(value, datatype=datatype, normalize=False)))
            prop = property_shape(shapes, root_shape, path, datatype=datatype, minCount=1)
            if spec["kind"] == "date":
                shapes.add((prop, SH.pattern, Literal(r"^\d{4}-\d{2}-\d{2}$")))
            else:
                shapes.add((prop, SH.pattern, Literal(r"T.*(?:Z|[+-]\d{2}:\d{2})$")))
    rdf, _, _ = validate(graph, shacl_graph=shapes)
    return python_ok, bool(rdf_ok and rdf) and not (set(units) - set(definition["parameter_semantics"]))


def compile_v2(definition, projection=None):
    if not projection:
        raise ValueError("Published domain projection required")
    from .schemas import Definition
    d = Definition.model_validate(definition).model_dump()
    d["participant_roles"] = [ParticipantRole.model_validate(x).model_dump() for x in d["participant_roles"]]
    d["relations"] = [RoleRelation.model_validate(x).model_dump() for x in d["relations"]]
    d["argument_bindings"] = {k: ArgumentBinding.model_validate(v).model_dump() for k, v in d["argument_bindings"].items()}
    d["parameter_semantics"] = {k: ParameterSemantic.model_validate(v).model_dump() for k, v in d["parameter_semantics"].items()}
    d["result_bindings"] = [ResultBinding.model_validate(x).model_dump() for x in d["result_bindings"]]
    concepts = projection["concepts"]
    if concepts.get(d["object_type"], {}).get("kind") != "object" or concepts.get(d["result_type"], {}).get("kind") != "result":
        raise ValueError("Function object/result must reference published concepts of the appropriate kind")
    if not re.fullmatch(r"[A-Z][A-Z0-9_]{0,63}", d["action"]):
        raise ValueError("Register an explicit action identifier")
    roles = {r["role"]: r for r in d["participant_roles"]}
    if len(roles) != len(d["participant_roles"]) or any(concepts.get(r["type"], {}).get("kind") != "object" for r in roles.values()):
        raise ValueError("Duplicate role or unknown participant concept")
    if len({(r["name"], r["source"], r["target"]) for r in d["relations"]}) != len(d["relations"]):
        raise ValueError("Duplicate relation")
    for relation in d["relations"]:
        registered = projection["relations"].get(relation["name"])
        if not registered or relation["source"] not in roles or relation["target"] not in roles or not subtype(roles[relation["source"]]["type"], registered["source_type"], projection) or not subtype(roles[relation["target"]]["type"], registered["target_type"], projection):
            raise ValueError("Unknown relation or incompatible declared role direction")
    properties = d["input_schema"].get("properties", {})
    required_parameters = set(d["input_schema"].get("required", []))
    for name, binding in d["argument_bindings"].items():
        role = roles.get(binding["role"])
        valid_cardinality = role and role["max_count"] == 1 and (role["min_count"] == 1 or
            role["min_count"] == 0 and name not in required_parameters and role["required"] is False)
        if not valid_cardinality or properties.get(name, {}).get("type") != "string":
            raise ValueError("Entity bindings require one scalar string role; optional roles may bind only optional parameters")
    if len({b["role"] for b in d["argument_bindings"].values()}) != len(d["argument_bindings"]):
        raise ValueError("The finite binding contract supports one business-ID parameter per participant role")
    for name, spec in d["parameter_semantics"].items():
        kind = properties.get(name, {}).get("type")
        if spec["kind"] == "quantity":
            if kind not in {"integer", "number"} or spec["unit"] not in projection["units"] or spec["allow_relative"]:
                raise ValueError("Quantity semantics require a registered unit and numeric parameter")
        elif kind != "string" or spec["unit"] is not None or (spec["kind"] == "datetime" and spec["allow_relative"]):
            raise ValueError("Date/time semantics require a string and supported relative-date policy")
    for binding in d["result_bindings"]:
        if concepts.get(binding["entity_type"], {}).get("kind") != "object":
            raise ValueError("Result entity type is not a registered object")
        for field in ("collection_path", "id_field", "label_field"):
            if not re.fullmatch(r"[a-zA-Z_][a-zA-Z0-9_]*(?:\.[a-zA-Z_][a-zA-Z0-9_]*)*", binding[field]):
                raise ValueError("Result binding uses an unsupported field path")
    if len({x["name"] for x in d["result_bindings"]}) != len(d["result_bindings"]):
        raise ValueError("Result binding names must be distinct")
    if d["trusted_preconditions"] is not None:
        check_expression(d["trusted_preconditions"])
    if d.get("authoring_version") == "function-v2":
        from .finite_inputs import compile_inputs
        result = compile_inputs(d)
    else:
        result = _compile_definition_v1(d)
    proposal = {"action": d["action"], "object_type": d["object_type"], "result_type": d["result_type"], "negated": False,
                "participants": [{"role": r["role"], "type": r["type"]} for r in roles.values() for _ in range(r["min_count"])], "relations": d["relations"]}
    cases = [proposal, {**proposal, "negated": True}, {**proposal, "action": "UNREGISTERED_ACTION"}, {**proposal, "object_type": "unknown:Object"}, {**proposal, "participants": []}]
    for role in roles.values():
        cases.append({**proposal, "participants": [p for p in proposal["participants"] if p["role"] != role["role"]] + [{"role": role["role"], "type": role["type"]}] * (role["max_count"] + 1)})
    for relation in d["relations"]:
        cases.append({**proposal, "relations": [{**relation, "source": relation["target"], "target": relation["source"]}]})
    for case in cases:
        a, b = semantic_pair(d, case, projection)
        if a != b:
            raise ValueError("Python/SHACL semantic conformance differs")
    reference_time = datetime(2026, 1, 1, 20, tzinfo=timezone.utc)
    sample = {k: sample_value(v) for k, v in properties.items()}
    sample_units = {}
    for name, spec in d["parameter_semantics"].items():
        if spec["kind"] == "quantity":
            sample_units[name] = spec["unit"]
        elif spec["kind"] == "date":
            sample[name] = "2026-01-02"
        else:
            sample[name] = "2026-01-02T04:00:00+08:00"
    parameter_cases = [(sample, sample_units)]
    for name, spec in d["parameter_semantics"].items():
        if spec["kind"] == "quantity":
            parameter_cases.append((sample, {**sample_units, name: "unregistered-unit"}))
            for key in projection["conversions"]:
                if key.endswith("->" + spec["unit"]):
                    parameter_cases.append((sample, {**sample_units, name: key.split("->")[0]}))
        else:
            for value in ("2026-02-30", "today", "yesterday", "next week", "2026-01-02T04:00:00", None):
                parameter_cases.append(({**sample, name: value}, sample_units))
    for values, units in parameter_cases:
        a, b = parameter_pair(d, values, units, projection, reference_time, "Asia/Shanghai")
        if a != b:
            raise ValueError("Python/SHACL unit/date conformance differs")
    condition_cases = 0
    if d["trusted_preconditions"]:
        def names(e):
            if "fact" in e:
                return {e["fact"]}
            return set().union(*(names(x) for key in ("all", "any") for x in e.get(key, [])), names(e["not"]) if "not" in e else set())
        facts = names(d["trusted_preconditions"])
        bound_roles = {b["role"] for b in d["argument_bindings"].values()}
        if any(fact != "identity.authenticated" and fact.split(".")[1] not in bound_roles for fact in facts):
            raise ValueError("Trusted entity/history facts require a registered bound participant role")
        samples = [{}, {k: True for k in facts}, {k: False for k in facts}]
        samples.extend({**{k: True for k in facts}, key: value} for key in facts for value in (False, None))
        for facts_sample in samples:
            a, b = condition_pair(d["trusted_preconditions"], facts_sample)
            if a != b:
                raise ValueError("Python/SHACL three-valued condition conformance differs")
        condition_cases = len(samples)
    result.update({"compiler_version": "finite-json-v2", "domain_projection": projection, "semantic_definition": d,
                   "semantic_shapes": semantic_shapes(d, projection).serialize(format="turtle"), "semantic_conformance_cases": len(cases),
                   "parameter_conformance_cases": len(parameter_cases), "condition_conformance_cases": condition_cases})
    result["short_card"].update({"compiler_version": "finite-json-v2", "argument_bindings": d["argument_bindings"], "parameter_semantics": d["parameter_semantics"], "result_bindings": d["result_bindings"]})
    return result
