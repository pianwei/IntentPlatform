"""Finite HTTP wire mapping, independently registered from the v1 adapter."""
import math
import re
import httpx
from urllib.parse import urlsplit, quote, unquote
from .config import settings
from .network import request_json, request_json_parts

DANGEROUS_HEADERS = {"authorization", "proxy-authorization", "host", "cookie", "set-cookie", "content-length", "transfer-encoding", "connection", "te", "trailer", "upgrade", "expect", "origin", "referer", "content-type", "accept-encoding", "forwarded", "via", "x-api-key", "api-key", "x-auth-token", "x-csrf-token"}


def safe_header(name):
    return bool(re.fullmatch(r"[A-Za-z][A-Za-z0-9-]{0,63}", name)) and name.lower() not in DANGEROUS_HEADERS and not name.lower().startswith(("x-forwarded-", "proxy-", "sec-"))


def validate_server_headers(c):
    for key in ("authorization_header", "idempotency_header"):
        header = c.get(key)
        if header and not (safe_header(header) or key == "authorization_header" and header.lower() in {"authorization", "x-api-key", "api-key", "x-auth-token"}):
            raise ValueError("Server header cannot override routing or HTTP framing")
    if any(ord(x) < 32 or ord(x) == 127 for x in c.get("authorization_prefix", "")):
        raise ValueError("Credential prefix contains control characters")


def validate_mapping(definition):
    c = definition["connector"]; mapping = c.get("http_mapping")
    if c["kind"] != "http" or c["version"] != "2" or not isinstance(mapping, dict) or set(mapping) != {"path", "query", "headers", "body"}:
        raise ValueError("HTTP v2 requires explicit path/query/headers/body mapping objects")
    properties = definition["input_schema"].get("properties", {})
    validate_server_headers(c)
    if any(type(code) is not int or code not in {400, 404, 409, 422} for code in c.get("http_failure_statuses", [])):
        raise ValueError("Only explicitly reviewed business HTTP failure codes 400/404/409/422 are supported")
    used = []
    for location, fields in mapping.items():
        if not isinstance(fields, dict) or len(fields) > 100:
            raise ValueError("Invalid or excessive HTTP mappings")
        lowered = set()
        for wire, arg in fields.items():
            if not isinstance(wire, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.-]{0,99}", wire) or not isinstance(arg, str) or arg not in properties:
                raise ValueError("Mapping references an unsupported wire name or undefined argument")
            if location == "headers":
                if not safe_header(wire) or wire.lower() in lowered or wire.lower() in {str(c.get("authorization_header", "")).lower(), str(c.get("idempotency_header", "")).lower()}:
                    raise ValueError("Mapped headers cannot supply security, routing or framing headers")
                lowered.add(wire.lower())
            if location != "body" and properties[arg].get("type") not in ("string", "integer", "number", "boolean"):
                raise ValueError("Path/query/header mappings only support non-null scalar values")
            used.append(arg)
    if len(used) != len(set(used)) or set(used) != set(properties):
        raise ValueError("Every input must have exactly one HTTP destination")
    p = urlsplit(c["endpoint"])
    if p.query or p.fragment or c["method"] == "GET" and (mapping["body"] or c.get("http_body_required")):
        raise ValueError("Endpoint queries/fragments and GET bodies are unsupported")
    placeholders = re.findall(r"\{([^{}]+)\}", p.path)
    if len(set(placeholders)) != len(placeholders) or set(placeholders) != set(mapping["path"]):
        raise ValueError("Path placeholders and explicit mappings must match exactly")
    if re.search(r"[{}]", re.sub(r"\{[^{}]+\}", "", p.path)) or "{" in p.netloc or "}" in p.netloc:
        raise ValueError("Only path-segment placeholders are supported")
    if set(mapping["path"].values()) - set(definition["input_schema"].get("required", [])):
        raise ValueError("Path arguments must be required")
    return mapping


def scalar(value):
    if isinstance(value, bool): return "true" if value else "false"
    if type(value) in (int, float) and math.isfinite(value): return str(value)
    if isinstance(value, str) and not any(ord(c) < 32 or ord(c) == 127 for c in value): return value
    raise ValueError("HTTP scalar must be a finite number, boolean or control-free string")


def wire_request(definition, arguments):
    mapping = validate_mapping(definition); c = definition["connector"]
    url, query, headers, body = c["endpoint"], {}, {}, {}
    for location, fields in mapping.items():
        for name, arg in fields.items():
            if arg not in arguments:
                if location == "path": raise ValueError("Path value missing")
                continue
            value = arguments[arg]
            if location == "path":
                value = scalar(value)
                decoded = value
                for _ in range(3): decoded = unquote(decoded)
                if not value or decoded in {".", ".."} or any(x in decoded for x in ("/", "\\", "?", "#", "%")) or any(ord(x) < 32 for x in decoded):
                    raise ValueError("Unsafe path segment")
                url = url.replace("{" + name + "}", quote(value, safe=""))
            elif location == "query": query[name] = scalar(value)
            elif location == "headers": headers[name] = scalar(value)
            else: body[name] = value
    return url, query, headers, body if mapping["body"] or c.get("http_body_required") else None


def execute_http_v2(definition, arguments, call_id, user_id, workspace_id, reconcile_operation=None, before_send=None):
    from .ports import (connector_checks, target_authorized, credential, at, condition,
                        legacy_object_authorization, configured_identity_headers)
    c = definition["connector"]; connector_checks(definition, workspace_id)
    url, query, headers, body = wire_request(definition, arguments)
    if reconcile_operation:
        if not c.get("status_endpoint"):
            return {"status": "UNKNOWN", "data": {}, "operation_id": reconcile_operation, "fixture_mode": False}
        url = c["status_endpoint"].replace("{operation_id}", quote(reconcile_operation, safe="")); query = {}; body = None; headers = {}
    target_authorized(workspace_id, c, url)
    secret = credential(c.get("credential_ref"), workspace_id, c)
    if secret:
        if not c.get("authorization_header"): raise ValueError("Explicit server credential header required")
        headers[c["authorization_header"]] = c.get("authorization_prefix", "") + secret
    if c.get("idempotency_header") and not reconcile_operation: headers[c["idempotency_header"]] = call_id
    legacy_object_authorization({**definition, "arguments": arguments}, user_id, workspace_id, url)
    headers.update(configured_identity_headers(definition, user_id, workspace_id))
    if before_send: before_send()
    try:
        empty_allowed = (definition.get("output_mode") == "none" and not definition.get("success_condition")
                         and not any(c.get(k) for k in ("status_path", "result_path", "operation_id_path")))
        kwargs = {"allow_empty": True} if empty_allowed else {}
        raw = request_json_parts("GET" if reconcile_operation else c["method"], url, params=query, json_body=body, headers=headers, timeout=c["timeout_seconds"], **kwargs)
    except httpx.HTTPStatusError as exc:
        code = exc.response.status_code
        if reconcile_operation or code not in c.get("http_failure_statuses", []):
            raise
        if len(exc.response.content) > 5_000_000:
            raise ValueError("Connector response exceeds permitted size")
        raw = exc.response.json()
        if not isinstance(raw, dict): raise ValueError("Business HTTP failure requires a JSON object response")
        return {"status": "FAILED", "data": at(raw, c.get("result_path", ""), {}), "operation_id": None, "fixture_mode": False, "http_status_code": code}
    status = at(raw, c["status_path"]) if c.get("status_path") else None
    state = "SUCCEEDED" if not c.get("status_path") or status in c["success_values"] else "FAILED" if status in c["failure_values"] else "UNKNOWN"
    if state == "SUCCEEDED" and definition.get("success_condition") and condition(definition["success_condition"], raw) is not True: state = "FAILED"
    operation = at(raw, c["operation_id_path"]) if c.get("operation_id_path") else reconcile_operation
    return {"status": state, "data": at(raw, c.get("result_path", ""), {}), "operation_id": str(operation) if operation is not None else None, "fixture_mode": False}
