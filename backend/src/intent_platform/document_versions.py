"""Immutable source text, actual originals and stable within-version citations."""
import re
from collections import Counter
from fastapi import HTTPException
from sqlalchemy import select
from .db import uid
from .models import Document, DocumentVersion


def sections(content, previous=()):
    if len(content) > 500_000:
        raise ValueError("Extracted document exceeds the finite text budget")
    lines = content.splitlines() or [""]
    result, heading, start, buffer = [], "", 1, []
    def append():
        if buffer:
            result.append({"id": uid("section"), "heading": heading, "start_line": start, "end_line": start + len(buffer) - 1, "text": "\n".join(buffer)})
    for number, line in enumerate(lines, 1):
        if len(line) > 6000:
            append(); buffer = []
            for offset in range(0, len(line), 6000):
                result.append({"id": uid("section"), "heading": heading, "start_line": number, "end_line": number, "text": line[offset:offset + 6000]})
            continue
        new_heading = re.match(r"^#{1,6}\s+(.+)$", line)
        if buffer and (not line.strip() or new_heading or sum(len(x) + 1 for x in buffer) + len(line) > 6000):
            append(); buffer = []
        if new_heading: heading = new_heading.group(1)
        if not buffer: start = number
        if line.strip(): buffer.append(line)
    append()
    if not result: result = [{"id": uid("section"), "heading": "", "start_line": 1, "end_line": 1, "text": ""}]
    if len(result) > 512:
        raise ValueError("Document exceeds the section count limit")
    prior_counts = Counter((x["heading"], x["text"]) for x in previous)
    current_counts = Counter((x["heading"], x["text"]) for x in result)
    prior_headings, current_headings = Counter(x["heading"] for x in previous), Counter(x["heading"] for x in result)
    used = set()
    for item in result:
        key = item["heading"], item["text"]
        match = next((p for p in previous if (p["heading"], p["text"]) == key), None) if prior_counts[key] == current_counts[key] == 1 else None
        if not match and item["heading"] and prior_headings[item["heading"]] == current_headings[item["heading"]] == 1:
            match = next(p for p in previous if p["heading"] == item["heading"])
        if match and match["id"] not in used:
            item["id"] = match["id"]; used.add(match["id"])
    return result


def get_version(db, ws, document_id, revision):
    version = db.scalar(select(DocumentVersion).where(DocumentVersion.workspace_id == ws, DocumentVersion.document_id == document_id, DocumentVersion.revision == revision))
    if not version:
        raise HTTPException(404, "Document revision is unavailable in this workspace")
    return version


def view(version):
    return {"id": version.document_id, "filename": version.filename, "revision": version.revision, "text": version.text,
            "sections": version.sections, "original_available": version.original is not None,
            "source": {"document_id": version.document_id, "revision": version.revision}, "fixture_mode": False}


def create_document(db, ws, filename, content, original, user_id):
    parts = sections(content)
    document = Document(workspace_id=ws, filename=filename, text=content)
    db.add(document); db.flush()
    version = DocumentVersion(workspace_id=ws, document_id=document.id, revision=1, filename=filename, text=content,
                              original=original, sections=parts, created_by=user_id)
    db.add(version); db.flush(); return document, version
