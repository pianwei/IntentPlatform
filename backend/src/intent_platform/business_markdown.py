"""Deterministic Markdown from approved result fields; never from raw responses."""
import html


def _text(value):
    text = "是" if value is True else "否" if value is False else "—" if value is None else str(value)
    return html.escape(text, quote=False).replace("|", "\\|").replace("\r", "").replace("\n", "<br>")


def render_rows(rows, definitions=(), prefix="已完成的业务结果", limit=12000):
    definitions = list(definitions)
    by_name = {d["name"]: d for d in definitions}
    parts = ["## " + prefix]
    def render(value, schema, labels, level=0):
        if isinstance(value, dict):
            lines = []
            for key, item in value.items():
                child = schema.get("properties", {}).get(key, {})
                label = labels.get(key) or child.get("title") or child.get("description") or key
                if isinstance(item, (dict, list)):
                    lines += ["\n" + "#" * min(6, 3 + level) + " " + _text(label), render(item, child, {}, level + 1)]
                else: lines.append("- **" + _text(label) + "**：" + _text(item))
            return "\n".join(lines)
        if isinstance(value, list):
            if not value: return "暂无符合条件的记录。"
            item_schema = schema.get("items", {})
            if all(isinstance(item, dict) and not any(isinstance(v, (dict, list)) for v in item.values()) for item in value):
                keys = list(dict.fromkeys(key for item in value for key in item))
                titles = [labels.get(key) or item_schema.get("properties", {}).get(key, {}).get("title") or key for key in keys]
                lines = ["| " + " | ".join(_text(title) for title in titles) + " |", "| " + " | ".join("---" for _ in keys) + " |"]
                lines += ["| " + " | ".join(_text(item.get(key)) for key in keys) + " |" for item in value]
                return "\n".join(lines)
            return "\n\n".join(("### " + str(index + 1) + "\n" + render(item, item_schema, {}, level + 1)) if isinstance(item, (list, dict)) else "- " + _text(item)
                               for index, item in enumerate(value))
        return _text(value)
    for row in rows:
        definition = by_name.get(row.get("name"), {})
        content = "业务执行已完成，无业务数据返回。" if definition.get("output_mode") == "none" else render(
            row.get("data"), definition.get("output_schema") or {},
            {key: label for label, key in definition.get("presentation", {}).get("field_map", {}).items()})
        parts += ["\n### " + _text(row.get("name", "业务结果")), content]
    text = "\n".join(parts)
    return text if limit is None else text[:limit]


def covers_rows(answer, rows, definitions):
    """Only exact, untruncated deterministic rendering can replace detail cards.

    A model summary is never treated as proof that all business facts are shown.
    Bounds mirror the finite Markdown table renderer, including legacy rows.
    """
    def finite(value):
        if isinstance(value, dict):
            return len(value) <= 20 and all(finite(item) for item in value.values())
        if isinstance(value, list):
            return len(value) <= 100 and all(finite(item) for item in value)
        return True
    if not rows or not all(finite(row.get("data")) for row in rows):
        return False
    for prefix in ("已完成的业务结果", "工程预览结果", "请求未全部完成，以下为已完成的结果"):
        full = render_rows(rows, definitions, prefix, limit=None)
        if len(full) <= 12000 and answer == full:
            return True
    return False
