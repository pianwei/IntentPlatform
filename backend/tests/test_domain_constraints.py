"""Finite semantic tests use explicit synthetic concepts, never a business catalog."""
import copy
from datetime import datetime, timezone
import pytest
from intent_platform.schemas import Definition
from intent_platform.domains import compile_domain
from intent_platform.ontology import compile_definition
from intent_platform.semantic_v2 import semantic_pair, normalize_parameters, parameter_pair, condition_pair, truth


def domain_definition():
    return {"name": "Local synthetic domain", "description": "Compiler tests only", "source": {"reference": "test-fixture", "owner": "test-owner"},
            "concepts": [{"id": "test:Item", "label": "Item", "kind": "object", "parents": []},
                         {"id": "test:SpecialItem", "label": "Special", "kind": "object", "parents": ["test:Item"]},
                         {"id": "test:Owner", "label": "Owner", "kind": "object", "parents": []},
                         {"id": "test:Result", "label": "Result", "kind": "result", "parents": []}],
            "relations": [{"id": "test:belongsTo", "source_type": "test:Item", "target_type": "test:Owner"}],
            "units": [{"id": "m", "dimension": "length"}, {"id": "mm", "dimension": "length"}],
            "conversions": [{"from_unit": "mm", "to_unit": "m", "factor": 0.001}]}


def test_unit_identifier_and_conversion_overflow_are_rejected():
    domain = domain_definition(); domain["units"][0]["id"] = "m->mm"
    with pytest.raises(ValueError): compile_domain(domain)
    domain = domain_definition(); domain["conversions"][0]["factor"] = 1e308
    projection = compile_domain(domain)
    d = semantic_definition()
    d["input_schema"] = {"type": "object", "properties": {"amount": {"type": "number"}}, "required": ["amount"]}
    d["parameter_semantics"] = {"amount": {"kind": "quantity", "unit": "m", "allow_relative": False}}
    assert parameter_pair(d, {"amount": 1e308}, {"amount": "mm"}, projection, datetime.now(timezone.utc), "UTC") == (False, False)


def test_optional_business_binding_is_supported_without_weakening_required_inputs_or_trusted_facts():
    d = semantic_definition(); projection = compile_domain(domain_definition())
    d["input_schema"] = {"type": "object", "properties": {"id": {"type": "string"}}}
    d["argument_bindings"] = {"id": {"role": "target", "resolver_ref": "fixture@1"}}
    assert compile_definition(d, projection=projection)["compiler_version"] == "finite-json-v2"
    d["participant_roles"][0].update({"required": False, "min_count": 0})
    assert compile_definition(d, projection=projection)["compiler_version"] == "finite-json-v2"
    d["input_schema"]["required"] = ["id"]
    with pytest.raises(ValueError, match="Entity bindings"): compile_definition(d, projection=projection)
    d["participant_roles"][0].update({"required": True, "min_count": 1})
    d["trusted_preconditions"] = {"fact": "entity.unknown.authorized", "operator": "exists"}
    with pytest.raises(ValueError, match="registered bound"): compile_definition(d, projection=projection)


def semantic_definition():
    return Definition(name="Local semantic fixture", description="No real bank API", compiler_version="finite-json-v2",
                      object_type="test:Item", result_type="test:Result", action="READ",
                      participant_roles=[{"role": "target", "type": "test:Item", "required": True, "min_count": 1, "max_count": 1},
                                         {"role": "owner", "type": "test:Owner", "required": True, "min_count": 1, "max_count": 1}],
                      relations=[{"name": "test:belongsTo", "source": "target", "target": "owner"}]).model_dump()


def proposal():
    return {"capability_id": "local-fixture", "action": "READ", "object_type": "test:SpecialItem", "result_type": "test:Result", "negated": False,
            "participants": [{"role": "target", "type": "test:SpecialItem"}, {"role": "owner", "type": "test:Owner"}],
            "relations": [{"name": "test:belongsTo", "source": "target", "target": "owner"}], "arguments": {}, "parameter_units": {}}


def test_domain_closure_cycle_unknown_and_direction():
    domain = domain_definition(); compiled = compile_domain(domain)
    assert compiled["closure"]["test:SpecialItem"] == ["test:Item", "test:SpecialItem"]
    for altered in ("cycle", "unknown", "kind"):
        d = copy.deepcopy(domain)
        if altered == "cycle": d["concepts"][0]["parents"] = ["test:SpecialItem"]
        if altered == "unknown": d["concepts"][0]["parents"] = ["unknown:Object"]
        if altered == "kind": d["concepts"][0]["parents"] = ["test:Result"]
        with pytest.raises(ValueError): compile_domain(d)
    definition = semantic_definition()
    assert compile_definition(definition, projection=compiled)["compiler_version"] == "finite-json-v2"
    definition["relations"][0]["source"], definition["relations"][0]["target"] = "owner", "target"
    with pytest.raises(ValueError, match="direction"): compile_definition(definition, projection=compiled)


@pytest.mark.parametrize("change", ["valid", "role_type", "object", "result", "action", "negated", "direction", "missing", "cardinality", "unknown_role"])
def test_python_shacl_semantic_decisions(change):
    d, p, projection = semantic_definition(), proposal(), compile_domain(domain_definition())
    if change == "role_type": p["participants"][0]["type"] = "test:Owner"
    if change == "object": p["object_type"] = "unknown:Item"
    if change == "result": p["result_type"] = "test:Item"
    if change == "action": p["action"] = "DELETE"
    if change == "negated": p["negated"] = True
    if change == "direction": p["relations"][0] = {"name": "test:belongsTo", "source": "owner", "target": "target"}
    if change == "missing": p["participants"] = []
    if change == "cardinality": p["participants"].append(p["participants"][0])
    if change == "unknown_role": p["participants"].append({"role": "attacker", "type": "test:Item"})
    assert semantic_pair(d, p, projection) == ((True, True) if change == "valid" else (False, False))


def test_quantity_date_and_timezone_are_executable_constraints():
    d, projection = semantic_definition(), compile_domain(domain_definition())
    d["input_schema"] = {"type": "object", "properties": {"amount": {"type": "number", "minimum": 0}, "day": {"type": "string"}, "at": {"type": "string"}}, "required": ["amount", "day", "at"], "additionalProperties": False}
    d["parameter_semantics"] = {"amount": {"kind": "quantity", "unit": "m", "allow_relative": False},
                                "day": {"kind": "date", "unit": None, "allow_relative": True},
                                "at": {"kind": "datetime", "unit": None, "allow_relative": False}}
    compiled = compile_definition(d, projection=projection)
    assert compiled["parameter_conformance_cases"] >= 10
    clock = datetime(2026, 1, 1, 20, tzinfo=timezone.utc)
    values = {"amount": 1000, "day": "today", "at": "2026-01-02T04:00:00+08:00"}
    normalized, units, sources = normalize_parameters(d, values, {"amount": "mm"}, projection, clock, "Asia/Shanghai")
    assert normalized == {"amount": 1.0, "day": "2026-01-02", "at": "2026-01-01T20:00:00+00:00"}
    assert sources["day"]["reference_time"] == clock.isoformat() and units == {"amount": "m"}
    assert parameter_pair(d, values, {"amount": "mm"}, projection, clock, "Asia/Shanghai") == (True, True)
    for bad in ({**values, "day": "2026-02-30"}, {**values, "day": "next week"}, {**values, "at": "2026-01-02T04:00:00"}):
        assert parameter_pair(d, bad, {"amount": "m"}, projection, clock, "Asia/Shanghai") == (False, False)
    assert parameter_pair(d, values, {"amount": "unregistered"}, projection, clock, "Asia/Shanghai") == (False, False)


@pytest.mark.parametrize("a", [True, False, None])
@pytest.mark.parametrize("b", [True, False, None])
def test_three_valued_conditions_match_generated_shapes(a, b):
    leaf_a = {"fact": "identity.authenticated", "operator": "eq", "value": True}
    leaf_b = {"fact": "entity.target.authorized", "operator": "eq", "value": True}
    facts = {"identity.authenticated": a, "entity.target.authorized": b}
    for expression in ({"all": [leaf_a, leaf_b]}, {"any": [leaf_a, leaf_b]}, {"not": {"all": [leaf_a, leaf_b]}}, {"not": {"any": [leaf_a, leaf_b]}}):
        result = condition_pair(expression, facts)
        assert result[0] == result[1]
    assert truth(leaf_b, {}) is None


def test_compiler_rejects_unsupported_binding_cardinality_and_v1_downgrade():
    d = semantic_definition(); d["participant_roles"][0]["max_count"] = 2
    d["input_schema"] = {"type": "object", "properties": {"item_id": {"type": "string"}}, "required": ["item_id"], "additionalProperties": False}
    d["argument_bindings"] = {"item_id": {"role": "target", "resolver_ref": "resolver.item@1"}}
    with pytest.raises(ValueError, match="scalar"): compile_definition(d, projection=compile_domain(domain_definition()))
    d["compiler_version"] = "finite-json-v1"
    with pytest.raises(ValueError, match="explicit compiler"): compile_definition(d)
