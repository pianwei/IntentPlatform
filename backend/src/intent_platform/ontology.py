"""Finite JSON constraint compiler. Generated RDF is never a second source of truth."""
import copy
from urllib.parse import quote
from jsonschema import Draft202012Validator
from rdflib import Graph, Namespace, BNode, Literal, URIRef
from rdflib.namespace import RDF, XSD
from rdflib.collection import Collection
from pyshacl import validate
from .schemas import Definition

EX = Namespace("urn:intent:")
SH = Namespace("http://www.w3.org/ns/shacl#")
TYPES = {"string": XSD.string, "integer": XSD.integer, "number": XSD.double, "boolean": XSD.boolean}
ROOT_KEYS = {"type", "properties", "required", "additionalProperties", "title", "description"}
VALUE_KEYS = {"type", "enum", "minimum", "maximum", "minLength", "maxLength", "items", "minItems", "maxItems", "title", "description"}


def check_schema(schema):
    Draft202012Validator.check_schema(schema)
    if schema.get("type") != "object" or set(schema) - ROOT_KEYS:
        raise ValueError("Only the documented finite object schema subset is supported")
    if schema.get("additionalProperties", False) is not False:
        raise ValueError("additionalProperties must be false")
    props = schema.get("properties", {})
    if set(schema.get("required", [])) - set(props):
        raise ValueError("Required field is undefined")
    for name, spec in props.items():
        if not name or set(spec) - VALUE_KEYS:
            raise ValueError(f"Unsupported schema keyword for {name}")
        kinds = spec.get("type")
        kinds = kinds if isinstance(kinds, list) else [kinds]
        if any(k not in {*TYPES, "null", "array"} for k in kinds):
            raise ValueError(f"Unsupported field type: {name}")
        if len(kinds) > 1 and (len(kinds) != 2 or "null" not in kinds or "array" in kinds):
            raise ValueError("Only explicit scalar/null unions are supported")
        if "array" in kinds:
            item = spec.get("items", {})
            if item.get("type") not in TYPES or set(item) - (VALUE_KEYS - {"items", "minItems", "maxItems"}):
                raise ValueError("Array items must be supported scalar constraints")


def rdf_value(value):
    if value is None:
        return EX.null
    if isinstance(value, bool):
        return Literal(value, datatype=XSD.boolean)
    if isinstance(value, int):
        return Literal(value, datatype=XSD.integer)
    if isinstance(value, float):
        if value.is_integer():
            return Literal(int(value), datatype=XSD.integer)
        return Literal(value, datatype=XSD.double)
    if isinstance(value, str):
        return Literal(value, datatype=XSD.string)
    return Literal("UNSUPPORTED", datatype=EX.unsupported)


def list_value(g, values):
    head = BNode()
    Collection(g, head, values)
    return head


def value_shape(g, spec):
    shape = BNode()
    kinds = spec.get("type")
    if isinstance(kinds, list):
        choices = [value_shape(g, {**spec, "type": kind}) for kind in kinds]
        g.add((shape, SH["or"], list_value(g, choices)))
        return shape
    if kinds == "null":
        g.add((shape, SH["in"], list_value(g, [EX.null])))
        return shape
    if kinds == "number":
        choices = []
        for datatype in [XSD.integer, XSD.double]:
            ch = BNode(); g.add((ch, SH.datatype, datatype)); choices.append(ch)
        g.add((shape, SH["or"], list_value(g, choices)))
    else:
        g.add((shape, SH.datatype, TYPES[kinds]))
    for key, predicate in [("minimum", SH.minInclusive), ("maximum", SH.maxInclusive), ("minLength", SH.minLength), ("maxLength", SH.maxLength)]:
        if key in spec:
            g.add((shape, predicate, Literal(spec[key])))
    if "enum" in spec:
        g.add((shape, SH["in"], list_value(g, [rdf_value(v) for v in spec["enum"]])))
    return shape


def shapes(schema):
    g = Graph(); root = EX.InputShape
    g.add((root, RDF.type, SH.NodeShape)); g.add((root, SH.targetNode, EX.input))
    g.add((root, SH.closed, Literal(True)))
    for name, spec in schema.get("properties", {}).items():
        prop = BNode(); g.add((root, SH.property, prop)); g.add((prop, SH.path, URIRef(EX + "field/" + quote(name, safe=""))))
        g.add((prop, SH.maxCount, Literal(1)))
        if name in schema.get("required", []):
            g.add((prop, SH.minCount, Literal(1)))
        container = BNode(); g.add((prop, SH.node, container))
        itemprop = BNode(); g.add((container, SH.property, itemprop)); g.add((itemprop, SH.path, EX.item))
        array = spec.get("type") == "array"
        g.add((itemprop, SH.minCount, Literal(spec.get("minItems", 0) if array else 1)))
        if not array or "maxItems" in spec:
            g.add((itemprop, SH.maxCount, Literal(spec["maxItems"] if array else 1)))
        itemshape = BNode(); g.add((itemprop, SH.node, itemshape))
        scalar = BNode(); g.add((itemshape, SH.property, scalar)); g.add((scalar, SH.path, RDF.value))
        g.add((scalar, SH.minCount, Literal(1))); g.add((scalar, SH.maxCount, Literal(1)))
        g.add((scalar, SH.node, value_shape(g, spec["items"] if array else spec)))
        # Preserve whether a JSON field was scalar or array even when cardinality is one.
        marker = BNode(); g.add((container, SH.property, marker)); g.add((marker, SH.path, EX.kind))
        g.add((marker, SH.hasValue, Literal("array" if array else "scalar")))
    return g


def facts(values):
    g = Graph()
    for key, value in values.items():
        container = BNode(); g.add((EX.input, URIRef(EX + "field/" + quote(key, safe="")), container))
        array = isinstance(value, list)
        g.add((container, EX.kind, Literal("array" if array else "scalar")))
        for v in value if array else [value]:
            item = BNode(); g.add((container, EX.item, item)); g.add((item, RDF.value, rdf_value(v)))
    return g


def input_errors(schema, values):
    canonical = {**schema, "additionalProperties": False}
    return [f"{'.'.join(map(str, e.path)) or 'input'}: {e.message}" for e in Draft202012Validator(canonical).iter_errors(values)]


def validate_pair(schema, values):
    python_ok = not input_errors(schema, values)
    rdf_ok, _, report = validate(facts(values), shacl_graph=shapes(schema))
    return python_ok, bool(rdf_ok), str(report)


def sample_value(spec):
    if "enum" in spec:
        return next((v for v in spec["enum"] if v is not None), None)
    kind = spec.get("type")
    if isinstance(kind, list):
        kind = next(k for k in kind if k != "null")
    if kind == "array":
        return [sample_value(spec["items"]) for _ in range(spec.get("minItems", 0))]
    if kind == "string":
        return "x" * max(1, spec.get("minLength", 1)) if spec.get("maxLength", 1) else ""
    if kind in {"integer", "number"}:
        return spec.get("minimum", min(0, spec.get("maximum", 0)))
    if kind == "boolean":
        return True
    return None


def _compile_definition_v1(definition):
    d = Definition.model_validate(definition).model_dump()
    check_schema(d["input_schema"])
    if not all(":" in d[k] for k in ["object_type", "result_type"]):
        raise ValueError("Concept IDs must use a namespace prefix")
    for relation in d["relations"]:
        if not {"name", "source", "target"} <= relation.keys():
            raise ValueError("Relation direction requires name/source/target")
    schema = d["input_schema"]
    sample = {k: sample_value(v) for k, v in schema.get("properties", {}).items() if k in schema.get("required", [])}
    cases = [sample, {**sample, "_unknown": 1}]
    for name in schema.get("required", []):
        c = copy.deepcopy(sample); c.pop(name); cases.append(c)
    for name in schema.get("properties", {}):
        cases.extend([{**sample, name: None}, {**sample, name: {"invalid": True}}, {**sample, name: 1.0}])
    cases += [x["arguments"] for x in d["positive_examples"] + d["negative_examples"] if "arguments" in x]
    for case in cases:
        a, b, _ = validate_pair(schema, case)
        if a != b:
            raise ValueError("Python/RDF constraint inconsistency; publishing blocked")
    return {
        "compiler_version": "finite-json-v1", "schema": schema,
        "short_card": {k: d[k] for k in ["name", "description", "object_type", "action", "result_type", "participant_roles", "relations", "input_schema"]},
        "rdf_shapes": shapes(schema).serialize(format="turtle"),
        "conformance_cases": len(cases), "consistent": True,
    }


COMPILERS = {"finite-json-v1": _compile_definition_v1}
INPUT_VALIDATORS = {"finite-json-v1": input_errors}


def compile_definition(definition, version=None, projection=None):
    version = version or definition.get("compiler_version", "finite-json-v1")
    if version not in COMPILERS:
        raise ValueError("Pinned ontology compiler unavailable")
    if version == "finite-json-v1":
        if any(definition.get(k) for k in ("domain_refs", "argument_bindings", "parameter_semantics", "trusted_preconditions", "result_bindings")):
            raise ValueError("Domain/binding constraints require explicit compiler v2; v1 will not ignore them")
        return COMPILERS[version](definition)
    return COMPILERS[version](definition, projection)


def _compile_v2_registered(definition, projection=None):
    from .semantic_v2 import compile_v2
    return compile_v2(definition, projection)


COMPILERS["finite-json-v2"] = _compile_v2_registered
INPUT_VALIDATORS["finite-json-v2"] = input_errors
