"""Bounded nested input schemas with equivalent JSON and generated SHACL."""
from copy import deepcopy
from urllib.parse import quote
from rdflib import Graph, BNode, Literal, URIRef
from rdflib.namespace import RDF, XSD
from pyshacl import validate
from .ontology import EX, SH, value_shape, rdf_value, input_errors, sample_value


def enhanced(schema):
    def visit(node):
        return (node.get("type") == "object" and node is not schema or bool(node.get("format")) or
                any(visit(v) for v in node.get("properties", {}).values()) or bool(node.get("items") and visit(node["items"])))
    return visit(schema)


def shapes(schema):
    graph = Graph()

    def node(spec):
        shape = BNode()
        graph.add((shape, RDF.type, SH.NodeShape)); graph.add((shape, SH.closed, Literal(True)))
        kind = spec["type"]
        marker = BNode(); graph.add((shape, SH.property, marker)); graph.add((marker, SH.path, EX.kind))
        graph.add((marker, SH.hasValue, Literal(kind if kind in {"object", "array"} else "scalar")))
        if kind == "object":
            for key, child in spec.get("properties", {}).items():
                prop = BNode(); graph.add((shape, SH.property, prop)); graph.add((prop, SH.path, URIRef(EX + "field/" + quote(key, safe=""))))
                graph.add((prop, SH.maxCount, Literal(1))); graph.add((prop, SH.node, node(child)))
                if key in spec.get("required", []): graph.add((prop, SH.minCount, Literal(1)))
        elif kind == "array":
            prop = BNode(); graph.add((shape, SH.property, prop)); graph.add((prop, SH.path, EX.item))
            graph.add((prop, SH.minCount, Literal(spec.get("minItems", 0)))); graph.add((prop, SH.maxCount, Literal(spec["maxItems"])))
            graph.add((prop, SH.node, node(spec["items"])))
        else:
            prop = BNode(); graph.add((shape, SH.property, prop)); graph.add((prop, SH.path, RDF.value))
            graph.add((prop, SH.minCount, Literal(1))); graph.add((prop, SH.maxCount, Literal(1)))
            value = value_shape(graph, {k: v for k, v in spec.items() if k != "format"})
            graph.add((prop, SH.node, value))
            if spec.get("format"):
                pattern = r"^\d{4}-\d{2}-\d{2}$" if spec["format"] == "date" else r"^\d{4}-\d{2}-\d{2}T.*(?:Z|[+-]\d{2}:\d{2})$"
                graph.add((value, SH.pattern, Literal(pattern)))
        return shape
    root = node(schema); graph.add((root, SH.targetNode, EX.input))
    return graph


def facts(schema, values):
    graph = Graph()

    def node(value, spec, target):
        kind = "object" if isinstance(value, dict) else "array" if isinstance(value, list) else "scalar"
        graph.add((target, EX.kind, Literal(kind)))
        if kind == "object":
            for key, child in value.items():
                item = BNode(); graph.add((target, URIRef(EX + "field/" + quote(str(key), safe="")), item))
                node(child, spec.get("properties", {}).get(key, {}), item)
        elif kind == "array":
            for child in value:
                item = BNode(); graph.add((target, EX.item, item)); node(child, spec.get("items", {}), item)
        else:
            # Format conformance is additionally encoded as a closed RDF marker.
            # Invalid Gregorian dates receive a deliberately unapproved predicate.
            from jsonschema import FormatChecker
            if spec.get("format") and not FormatChecker().conforms(value, spec["format"]):
                graph.add((target, EX.invalidFormat, Literal(True)))
            graph.add((target, RDF.value, rdf_value(value)))
    node(values, schema, EX.input)
    return graph


def validate_pair(schema, values):
    py = not input_errors(schema, values)
    rdf, _, report = validate(facts(schema, values), shacl_graph=shapes(schema))
    return py, bool(rdf), str(report)


def compile_inputs(definition):
    from .output_contract import _shape
    schema = definition["input_schema"]
    _shape(schema)
    sample = {k: sample_value(v) for k, v in schema["properties"].items() if k in schema.get("required", [])}
    cases = [sample, {**sample, "_unknown": 1}]
    for key in schema.get("required", []):
        case = deepcopy(sample); case.pop(key); cases.append(case)
    for key in schema["properties"]:
        cases += [{**sample, key: None}, {**sample, key: {"invalid": True}}, {**sample, key: 1.0}]
    cases += [x["arguments"] for x in definition.get("positive_examples", []) + definition.get("negative_examples", []) if "arguments" in x]
    for value in cases:
        py, rdf, _ = validate_pair(schema, value)
        if py != rdf:
            raise ValueError("Python/RDF nested input constraints differ")
    return {"compiler_version": "finite-json-v1", "schema": schema, "rdf_shapes": shapes(schema).serialize(format="turtle"),
            "short_card": {k: definition[k] for k in ("name", "description", "object_type", "action", "result_type", "participant_roles", "relations", "input_schema")},
            "conformance_cases": len(cases), "consistent": True}
