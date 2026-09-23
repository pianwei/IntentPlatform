"""A deliberately finite, allowlisted business-result contract.

Only approved fields are projected into durable calls and events. This is not a
general JSON Schema interpreter; unsupported shapes must be reviewed manually.
"""
import json
import math
import re


MAX_OUTPUT_BYTES = 256_000
MAX_PROPERTIES = 40
MAX_ITEMS = 100
MAX_STRING = 16_000
FIELD_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_.-]{0,63}\Z")
ROOT_KEYS = {"type", "properties", "required", "additionalProperties", "title", "description"}
VALUE_KEYS = {"type", "maxLength", "minLength", "minimum", "maximum", "enum", "title", "description", "items", "maxItems", "minItems", "properties", "required", "additionalProperties"}


class OutputContractError(ValueError):
    """Do not include raw values or connector diagnostics in this exception."""


def _shape(spec, depth=0):
    if not isinstance(spec, dict) or set(spec) - VALUE_KEYS:
        raise ValueError("Output schema has unsupported keywords")
    kind = spec.get("type")
    if kind == "object":
        if depth > 2 or spec.get("additionalProperties") is not False:
            raise ValueError("Output objects must be bounded and closed")
        props = spec.get("properties")
        required = spec.get("required", [])
        if (not isinstance(props, dict) or len(props) > MAX_PROPERTIES or not isinstance(required, list)
                or any(not isinstance(name, str) for name in required)
                or len(required) != len(set(required)) or set(required) - set(props)):
            raise ValueError("Output object properties/required fields are invalid")
        if any(not isinstance(name, str) or not FIELD_NAME.fullmatch(name) for name in props):
            raise ValueError("Output field names must be finite identifiers")
        if set(spec) & {"items", "maxItems", "minItems", "maxLength", "minLength", "minimum", "maximum", "enum"}:
            raise ValueError("Output object has scalar/array constraints")
        for child in props.values():
            _shape(child, depth + 1)
    elif kind == "array":
        if depth > 1 or type(spec.get("maxItems")) is not int or not 0 <= spec["maxItems"] <= MAX_ITEMS:
            raise ValueError("Output arrays need an explicit maxItems at most 100")
        if type(spec.get("minItems", 0)) is not int or not 0 <= spec.get("minItems", 0) <= spec["maxItems"]:
            raise ValueError("Output array minimum is invalid")
        if set(spec) & {"properties", "required", "additionalProperties", "maxLength", "minLength", "minimum", "maximum", "enum"}:
            raise ValueError("Output array has object/scalar constraints")
        _shape(spec.get("items"), depth + 1)
    elif kind in {"string", "integer", "number", "boolean"}:
        if set(spec) & {"items", "maxItems", "minItems", "properties", "required", "additionalProperties"}:
            raise ValueError("Output scalar has object/array constraints")
        if kind == "string":
            if type(spec.get("maxLength")) is not int or not 0 <= spec["maxLength"] <= MAX_STRING:
                raise ValueError("Output strings need an explicit maxLength at most 16000")
            if type(spec.get("minLength", 0)) is not int or not 0 <= spec.get("minLength", 0) <= spec["maxLength"]:
                raise ValueError("Output string minimum is invalid")
        elif "maxLength" in spec or "minLength" in spec:
            raise ValueError("Output string bounds on a non-string field")
        if kind not in {"integer", "number"} and ("minimum" in spec or "maximum" in spec):
            raise ValueError("Output numeric bounds on a non-number field")
        if "minimum" in spec and (type(spec["minimum"]) not in {int, float} or not math.isfinite(spec["minimum"])):
            raise ValueError("Output minimum is invalid")
        if "maximum" in spec and (type(spec["maximum"]) not in {int, float} or not math.isfinite(spec["maximum"])):
            raise ValueError("Output maximum is invalid")
        if "minimum" in spec and "maximum" in spec and spec["minimum"] > spec["maximum"]:
            raise ValueError("Output numeric bounds are inconsistent")
        if "enum" in spec and (not isinstance(spec["enum"], list) or len(spec["enum"]) > 100 or not spec["enum"]):
            raise ValueError("Output enum is invalid")
        for choice in spec.get("enum", []):
            matching_type = (isinstance(choice, str) if kind == "string" else type(choice) is int if kind == "integer" else
                             type(choice) in {int, float} and math.isfinite(choice) if kind == "number" else type(choice) is bool)
            if (not matching_type or kind == "string" and not spec.get("minLength", 0) <= len(choice) <= spec["maxLength"]
                    or "minimum" in spec and choice < spec["minimum"] or "maximum" in spec and choice > spec["maximum"]):
                raise ValueError("Output enum must satisfy its scalar type and bounds")
    else:
        raise ValueError("Output field type is unsupported")


def check_output_contract(definition):
    """Check a new function draft; do not run this gate on pinned legacy versions."""
    schema = definition.get("output_schema")
    if not isinstance(schema, dict) or set(schema) - ROOT_KEYS or schema.get("type") != "object":
        raise ValueError("New non-fixture versions require a finite output_schema object")
    _shape(schema)
    presentation = definition.get("presentation") or {}
    if not isinstance(presentation, dict):
        raise ValueError("Presentation must be an explicit mapping")
    field_map = presentation.get("field_map")
    props = schema["properties"]
    if "files" in props:
        raise ValueError("Inline files require a separate approved files_field boundary")
    if (not isinstance(field_map, dict) or any(not isinstance(value, str) for value in field_map.values())
            or len(field_map) != len(props) or set(field_map.values()) != set(props)
            or len(set(field_map.values())) != len(field_map)):
        raise ValueError("Every approved output field needs one explicit presentation mapping")
    if any(not isinstance(label, str) or not 1 <= len(label) <= 80 for label in field_map):
        raise ValueError("Output labels must be short and nonempty")
    files_field = presentation.get("files_field")
    if files_field is not None and (files_field != "files" or files_field in props):
        raise ValueError("Only a separately approved inline files field is supported")
    if not props and files_field is None:
        raise ValueError("Approve at least one display field or the inline files boundary")
    bindings = definition.get("result_bindings", [])
    if not isinstance(bindings, list) or len(bindings) > MAX_PROPERTIES:
        raise ValueError("Result bindings must be a finite list")
    for binding in bindings:
        if not isinstance(binding, dict) or not all(isinstance(binding.get(key), str) for key in ("collection_path", "id_field", "label_field")):
            raise ValueError("Result bindings need explicit collection, ID and label fields")
        collection = binding.get("collection_path")
        field = props.get(collection)
        item = field.get("items", {}) if isinstance(field, dict) and field.get("type") == "array" else {}
        row = item.get("properties", {}) if isinstance(item, dict) and item.get("type") == "object" else {}
        if not collection or "." in collection or binding.get("id_field") not in row or binding.get("label_field") not in row or binding["id_field"] not in item.get("required", []) or binding["label_field"] not in item.get("required", []):
            raise ValueError("Result bindings must use approved top-level rows and required ID/label fields")
    return schema


def _project(spec, value):
    kind = spec["type"]
    if kind == "object":
        if not isinstance(value, dict):
            raise OutputContractError("Result does not match approved output contract")
        if set(spec.get("required", [])) - set(value):
            raise OutputContractError("Result does not match approved output contract")
        return {name: _project(child, value[name]) for name, child in spec["properties"].items() if name in value}
    if kind == "array":
        if not isinstance(value, list) or not spec.get("minItems", 0) <= len(value) <= spec["maxItems"]:
            raise OutputContractError("Result does not match approved output contract")
        return [_project(spec["items"], item) for item in value]
    valid = (isinstance(value, str) if kind == "string" else type(value) is int if kind == "integer" else
             type(value) in {int, float} and math.isfinite(value) if kind == "number" else type(value) is bool)
    if not valid:
        raise OutputContractError("Result does not match approved output contract")
    if kind == "string" and not spec.get("minLength", 0) <= len(value) <= spec["maxLength"]:
        raise OutputContractError("Result does not match approved output contract")
    if "minimum" in spec and value < spec["minimum"] or "maximum" in spec and value > spec["maximum"] or "enum" in spec and value not in spec["enum"]:
        raise OutputContractError("Result does not match approved output contract")
    return value


def project_output(definition, raw):
    """Return (display data, optional approved raw inline artifacts).

    Unmapped top-level and nested keys are discarded. Never include raw values
    in errors, even when a connector supplies a malformed object.
    """
    schema = check_output_contract(definition)
    data = _project(schema, raw)
    try:
        size = len(json.dumps(data, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8"))
    except (ValueError, TypeError, RecursionError):
        raise OutputContractError("Result does not match approved output contract") from None
    if size > MAX_OUTPUT_BYTES:
        raise OutputContractError("Result exceeds approved display size")
    files_field = definition["presentation"].get("files_field")
    return data, raw.get(files_field) if files_field else None
