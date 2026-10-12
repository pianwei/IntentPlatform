"""Resolve the one user-facing presentation chosen by a published function."""
from copy import deepcopy


_MODES = {"structured", "summary", "content"}


def markdown_paths(schema, prefix=()):
    """Return finite object paths to string properties explicitly named markdown."""
    if not isinstance(schema, dict) or schema.get("type") != "object":
        return []
    found = []
    for name, child in schema.get("properties", {}).items():
        path = (*prefix, name)
        if name == "markdown" and isinstance(child, dict) and child.get("type") == "string":
            found.append(".".join(path))
        elif isinstance(child, dict) and child.get("type") == "object":
            found.extend(markdown_paths(child, path))
    return found


def content_path(definition):
    presentation = definition.get("presentation") or {}
    declared = presentation.get("content_path")
    if isinstance(declared, str) and declared:
        return declared
    paths = markdown_paths(definition.get("output_schema") or {})
    return paths[0] if len(paths) == 1 else None


def response_mode(definition):
    presentation = definition.get("presentation") or {}
    configured = presentation.get("response_mode")
    if configured in _MODES:
        return configured

    # Compatibility for immutable releases created before response_mode existed.
    component = presentation.get("component", "detail")
    if component == "markdown" and content_path(definition):
        return "content"
    if component == "table":
        return "structured"
    if definition.get("execution_kind", "direct") != "composite":
        props = (definition.get("output_schema") or {}).get("properties", {})
        if any(isinstance(value, dict) and value.get("type") == "array"
               and value.get("items", {}).get("type") == "object" for value in props.values()):
            return "structured"
    if content_path(definition):
        return "content"
    return "summary"


def response_mode_for_rows(rows, capabilities):
    """Use a deterministic mode only when every visible result agrees."""
    by_id = {item.get("capability_id"): item.get("definition", {}) for item in capabilities}
    modes = []
    for row in rows:
        definition = by_id.get(row.get("capability_id"))
        if not definition:
            return "summary"
        modes.append(response_mode(definition))
    return modes[0] if modes and len(set(modes)) == 1 else "summary"


def content_value(data, definition):
    """Read only the configured/uniquely declared string from approved result data."""
    path = content_path(definition)
    if isinstance(data, str):
        return data
    if not path:
        return None
    value = data
    for part in path.split("."):
        if not isinstance(value, dict) or part not in value:
            return None
        value = value[part]
    return value if isinstance(value, str) else None


def response_intro(rows, mode):
    """A factual, non-model introduction for results shown by their own renderer."""
    if mode == "content":
        name = rows[0].get("name", "草稿") if len(rows) == 1 else "所需内容"
        return f"已生成“{name}”，正文如下。"
    if len(rows) == 1:
        data = rows[0].get("data")
        counts = []

        def collect(value):
            if isinstance(value, list) and all(isinstance(item, dict) for item in value):
                counts.append(len(value))
            elif isinstance(value, dict):
                for child in value.values():
                    collect(child)

        collect(data)
        if len(counts) == 1:
            return f"查询完成，共找到 {counts[0]} 条记录，结果如下。"
        return "查询已完成，结果如下。"
    return "查询已完成，以下为各项结果。"


def remove_result_components(mode, components):
    """A model-summary answer is the sole presentation of business result data."""
    return [] if mode == "summary" else deepcopy(components)
