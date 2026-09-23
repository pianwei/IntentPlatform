"""Non-network OpenAPI 3.0 subset parser with bounded, source-located output."""
import json
import math
import re
from urllib.parse import urlsplit
import yaml
from yaml.tokens import AliasToken, AnchorToken, TagToken
from .schemas import Definition
from .ontology import check_schema
from .http_v2 import safe_header, validate_mapping

PARSER_VERSION = "openapi-3.0-finite-v1"
MAX_EXPANDED_BYTES = 2_000_000
MAX_REPORT_BYTES = 4_000_000


def diagnostic(code, message, pointer="#", severity="error"):
    return {"code": code, "message": message, "pointer": pointer, "severity": severity}


def escape(value): return value.replace("~", "~0").replace("/", "~1")


def unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if not isinstance(key, str) or key in result:
            raise ValueError("Duplicate or non-string object key")
        result[key] = value
    return result


class UniqueLoader(yaml.SafeLoader):
    pass


def construct_mapping(loader, node, deep=False):
    return unique_pairs((loader.construct_object(k, deep=deep), loader.construct_object(v, deep=deep)) for k, v in node.value)


UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, construct_mapping)


def load_source(content):
    if len(content) > 500_000: raise ValueError("Source size exceeds parser limit")
    if content.lstrip().startswith(("{", "[")):
        raw = json.loads(content, object_pairs_hook=unique_pairs, parse_constant=lambda _: (_ for _ in ()).throw(ValueError("Nonfinite JSON numbers are unsupported")))
    else:
        for index, token in enumerate(yaml.scan(content)):
            if index > 50_000 or isinstance(token, (AliasToken, AnchorToken, TagToken)):
                raise ValueError("YAML aliases, anchors, explicit tags or excessive tokens are unsupported")
        raw = yaml.load(content, Loader=UniqueLoader)
    count = 0
    def check(node, depth=0):
        nonlocal count
        count += 1
        if depth > 32 or count > 25_000: raise ValueError("Source nesting/node budget exceeded")
        if isinstance(node, dict):
            for key, value in node.items():
                if not isinstance(key, str): raise ValueError("Only string object keys are supported")
                check(value, depth + 1)
        elif isinstance(node, list):
            for item in node: check(item, depth + 1)
        elif type(node) not in (str, int, float, bool, type(None)) or type(node) is float and not math.isfinite(node):
            raise ValueError("Source contains unsupported values")
    check(raw)
    if not isinstance(raw, dict): raise ValueError("OpenAPI source must be an object")
    return raw


def resolve_document(raw):
    count = 0
    byte_count = 0
    refs = set()
    def pointer(reference):
        if not reference.startswith("#/") or "%" in reference:
            raise ValueError("Only internal JSON Pointer references are supported")
        node = raw
        for part in reference[2:].split("/"):
            if re.search(r"~(?![01])", part): raise ValueError("Invalid JSON Pointer escape")
            key = part.replace("~1", "/").replace("~0", "~")
            if isinstance(node, list) and re.fullmatch(r"0|[1-9]\d*", key) and int(key) < len(node):
                node = node[int(key)]
            elif isinstance(node, dict) and key in node:
                node = node[key]
            else: raise ValueError("Unresolved local reference")
        return node
    def walk(node, stack=(), depth=0):
        nonlocal count, byte_count
        count += 1
        byte_count += len(node.encode("utf-8")) if isinstance(node, str) else sum(len(k.encode("utf-8")) for k in node) if isinstance(node, dict) else 8
        if byte_count > MAX_EXPANDED_BYTES: raise ValueError("Reference expansion text budget exceeded")
        if count > 40_000 or depth > 40: raise ValueError("Reference expansion budget exceeded")
        if isinstance(node, dict):
            if "$ref" in node:
                reference = node["$ref"]
                if not isinstance(reference, str) or set(node) != {"$ref"}: raise ValueError("Reference siblings or invalid references are unsupported")
                if reference in stack: raise ValueError("Cyclic references are unsupported")
                refs.add(reference)
                return walk(pointer(reference), (*stack, reference), depth + 1)
            return {k: walk(v, stack, depth + 1) for k, v in node.items()}
        if isinstance(node, list): return [walk(v, stack, depth + 1) for v in node]
        return node
    return walk(raw), sorted(refs)


def finite_schema(schema):
    if not isinstance(schema, dict): raise ValueError("Parameter schema must be explicit")
    allowed = {"type", "enum", "minimum", "maximum", "minLength", "maxLength", "items", "minItems", "maxItems", "title", "description", "nullable", "example", "default", "deprecated"}
    if set(schema) - allowed: raise ValueError("Unsupported schema keyword; supply a supported finite contract")
    result = {k: v for k, v in schema.items() if k not in {"nullable", "example", "default", "deprecated"}}
    if schema.get("nullable"):
        if result.get("type") in {"array", "object"}: raise ValueError("Nullable arrays/objects are unsupported")
        result["type"] = [result.get("type"), "null"]
    if result.get("type") == "array": result["items"] = finite_schema(result.get("items", {}))
    check_schema({"type": "object", "properties": {"value": result}, "additionalProperties": False})
    return result


def operation_definition(document, path, item, method, operation, pointer, refs):
    if not isinstance(operation, dict) or not isinstance(item, dict): raise ValueError("Operation must be an object")
    if method not in {"get", "post"}: raise ValueError("Only GET/POST are implemented by HTTP connector v2")
    if operation.get("callbacks") or document.get("webhooks"): raise ValueError("Callbacks/webhooks are unsupported")
    servers = operation.get("servers", item.get("servers", document.get("servers", [])))
    if not isinstance(servers, list) or len(servers) != 1 or not isinstance(servers[0], dict) or set(servers[0]) - {"url", "description"}:
        raise ValueError("Exactly one explicit server without variables is required")
    origin = servers[0].get("url", ""); parsed = urlsplit(origin)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.query or parsed.fragment or parsed.username or parsed.password or "{" in origin:
        raise ValueError("Server must be an explicit HTTP(S) endpoint without credentials/query/variables")
    parsed.port  # Reject malformed ports without opening the endpoint.
    if not path.startswith("/") or "?" in path or "#" in path: raise ValueError("Invalid API path")
    properties, required = {}, []
    mapping = {key: {} for key in ("path", "query", "headers", "body")}
    inherited = item.get("parameters", []); local = operation.get("parameters", [])
    if not isinstance(inherited, list) or not isinstance(local, list) or len(inherited) + len(local) > 100: raise ValueError("Invalid parameter list")
    params = {}
    for group in (inherited, local):
        seen = set()
        for parameter in group:
            if not isinstance(parameter, dict): raise ValueError("Parameter must be an object")
            location, name = parameter.get("in"), parameter.get("name")
            if location not in {"path", "query", "header"} or not isinstance(name, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.-]{0,99}", name): raise ValueError("Unsupported parameter location/name")
            key = (location, name.lower() if location == "header" else name)
            if key in seen: raise ValueError("Duplicate parameter in the same scope")
            seen.add(key); params[key] = parameter
    for (location, _), parameter in params.items():
        name = parameter["name"]
        if parameter.get("content") or parameter.get("allowReserved") or parameter.get("allowEmptyValue"):
            raise ValueError("Content/reserved/empty-value parameter encoding is unsupported")
        if parameter.get("style", "form" if location == "query" else "simple") != ("form" if location == "query" else "simple"):
            raise ValueError("Only default scalar parameter styles are supported")
        spec = finite_schema(parameter.get("schema"))
        if spec["type"] not in ("string", "integer", "number", "boolean"): raise ValueError("Nonbody parameters must be non-null scalars")
        if location == "path" and parameter.get("required") is not True: raise ValueError("Path parameters must explicitly be required")
        if location == "header" and not safe_header(name): raise ValueError("Unsafe header cannot be mapped from caller arguments")
        arg = location + "_" + name
        properties[arg] = spec
        if parameter.get("required"): required.append(arg)
        mapping["headers" if location == "header" else location][name] = arg
    request = operation.get("requestBody")
    if request is not None:
        if method == "get" or not isinstance(request, dict): raise ValueError("GET request bodies are unsupported")
        content = request.get("content", {})
        if set(content) != {"application/json"}: raise ValueError("Only an explicit application/json request body is supported")
        media = content["application/json"]
        if media.get("encoding"): raise ValueError("Custom request-body encoding is unsupported")
        schema = media.get("schema", {})
        if schema.get("type") != "object" or set(schema) - {"type", "properties", "required", "additionalProperties", "description", "title"} or schema.get("additionalProperties", False) is not False:
            raise ValueError("Request body requires a finite flat object schema")
        if set(schema.get("required", [])) - set(schema.get("properties", {})): raise ValueError("Body required field is undefined")
        if not request.get("required", False) and schema.get("required"):
            raise ValueError("Optional body with required children needs explicit manual modelling")
        for name, value in schema.get("properties", {}).items():
            arg = "body_" + name; properties[arg] = finite_schema(value); mapping["body"][name] = arg
            if name in schema.get("required", []): required.append(arg)
    if len(properties) > 100: raise ValueError("Operation input field limit exceeded")
    if not isinstance(operation.get("responses"), dict) or not operation["responses"]: raise ValueError("Operation responses are required")
    definition = Definition(name=(operation.get("summary") or operation.get("operationId") or method.upper() + " " + path)[:160],
                            description=operation.get("description") or operation.get("summary") or "Imported operation: review business semantics before publishing",
                            input_schema={"type": "object", "properties": properties, "required": required, "additionalProperties": False},
                            effect="READ_ONLY" if method == "get" else "WRITE",
                            connector={"kind": "http", "version": "2", "endpoint": origin.rstrip("/") + path, "method": method.upper(), "http_mapping": mapping,
                                       "http_body_required": request is not None and request.get("required") is True}).model_dump()
    validate_mapping(definition); check_schema(definition["input_schema"])
    facts = {key: {"value": value, "source_pointer": pointer, "ref_targets": refs} for key, value in
             {"input_schema": definition["input_schema"], "connector.endpoint": definition["connector"]["endpoint"], "connector.method": method.upper(), "connector.http_mapping": mapping,
              "connector.http_body_required": definition["connector"]["http_body_required"]}.items()}
    return definition, facts


def parse_openapi(content):
    try:
        raw = load_source(content)
        if not re.fullmatch(r"3\.0\.\d+", str(raw.get("openapi", ""))): raise ValueError("Only OpenAPI 3.0.x is supported")
        if not isinstance(raw.get("info"), dict) or not raw["info"].get("title") or not raw["info"].get("version"): raise ValueError("OpenAPI info title/version required")
        document, refs = resolve_document(raw)
        paths = document.get("paths")
        if not isinstance(paths, dict) or not paths or len(paths) > 100: raise ValueError("One to 100 paths are supported")
    except (ValueError, TypeError, AttributeError, RecursionError, yaml.YAMLError):
        return {"valid": False, "diagnostics": [diagnostic("invalid_source", "Source is invalid or exceeds the supported JSON/YAML/reference boundary")], "operations": []}
    operations = []; operation_ids = set(); report_bytes = 0
    for path, item in paths.items():
        if not isinstance(item, dict): return {"valid": False, "diagnostics": [diagnostic("invalid_path", "Path item must be an object", "#/paths/" + escape(path))], "operations": []}
        for method, operation in item.items():
            if method not in {"get", "post", "put", "patch", "delete", "options", "head", "trace"}: continue
            pointer = "#/paths/" + escape(path) + "/" + method
            identifier = operation.get("operationId") if isinstance(operation, dict) else None
            row = {"key": method.upper() + " " + path, "method": method.upper(), "path": path, "operation_id": identifier if isinstance(identifier, str) else None,
                   "summary": str(operation.get("summary", "")) if isinstance(operation, dict) else "", "source_pointer": pointer, "supported": False, "diagnostics": [], "definition": None, "facts": {}}
            try:
                if row["operation_id"]:
                    if row["operation_id"] in operation_ids: raise ValueError("Duplicate operationId")
                    operation_ids.add(row["operation_id"])
                definition, facts = operation_definition(document, path, item, method, operation, pointer, refs)
                row.update({"supported": True, "definition": definition, "facts": facts})
                if operation.get("security", document.get("security")):
                    row["diagnostics"].append(diagnostic("security_review", "Select a server-authorized credential reference; imported security schemes do not grant credentials", pointer + "/security", "review"))
            except (ValueError, TypeError, KeyError, AttributeError) as exc:
                row["diagnostics"].append(diagnostic("unsupported_operation", str(exc), pointer))
            operations.append(row)
            report_bytes += len(json.dumps(row, ensure_ascii=False, allow_nan=False).encode("utf-8"))
            if report_bytes > MAX_REPORT_BYTES:
                return {"valid": False, "diagnostics": [diagnostic("expansion_limit", "Expanded operation report exceeds the permitted size")], "operations": []}
            if len(operations) > 100: return {"valid": False, "diagnostics": [diagnostic("operation_limit", "Operation count exceeds 100")], "operations": []}
    return {"valid": any(o["supported"] for o in operations), "diagnostics": [] if operations else [diagnostic("no_operations", "No supported operations were found")], "operations": operations}
