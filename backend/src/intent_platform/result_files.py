"""Materialize bounded connector artifacts; never follow a returned download URL."""
import base64
import binascii
from pathlib import PurePosixPath
from .models import ResultFile
from .config import settings

TYPES = {"application/pdf", "text/plain", "text/csv", "application/json", "application/octet-stream",
         "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
         "application/vnd.openxmlformats-officedocument.wordprocessingml.document"}


def prepare_inline_files(raw):
    """Validate all artifacts before any bytes are written or metadata exposed."""
    if not isinstance(raw, list) or not 1 <= len(raw) <= 8:
        raise ValueError("Invalid artifact list")
    prepared, total = [], 0
    for item in raw:
        if not isinstance(item, dict) or set(item) - {"filename", "media_type", "content_base64"}:
            raise ValueError("Only inline controlled artifacts are accepted")
        encoded = item.get("content_base64")
        if not isinstance(encoded, str) or len(encoded) > settings().max_upload_bytes * 2:
            raise ValueError("Artifact exceeds configured limit")
        payload = base64.b64decode(encoded, validate=True)
        total += len(payload)
        if total > settings().max_upload_bytes:
            raise ValueError("Combined artifacts exceed configured limit")
        name = item.get("filename", "download.bin")
        if not isinstance(name, str) or not name or len(name) > 200 or any(ord(c) < 32 for c in name) or "/" in name or "\\" in name or name in {".", ".."}:
            raise ValueError("Unsafe artifact filename")
        mime = item.get("media_type", "application/octet-stream")
        if mime not in TYPES:
            raise ValueError("Unsupported download media type")
        prepared.append((name, mime, payload))
    return prepared


def inspect_inline_files(raw):
    try:
        return [{"filename": name, "media_type": mime, "size": len(payload)} for name, mime, payload in prepare_inline_files(raw)]
    except (ValueError, TypeError, binascii.Error):
        raise ValueError("Connector artifact rejected; no arbitrary URL or unvalidated bytes exposed") from None


def materialize_files(db, run, call, data):
    if not isinstance(data, dict) or "files" not in data:
        return data, [], None
    safe_data = {k: v for k, v in data.items() if k != "files"}
    raw = data["files"]
    if call.status != "SUCCEEDED":
        return safe_data, [], "Files are available only after confirmed business success"
    try:
        prepared = prepare_inline_files(raw)
    except (ValueError, TypeError, binascii.Error):
        return safe_data, [], "Connector artifact rejected; no arbitrary URL or unvalidated bytes exposed"
    files = []
    for name, mime, payload in prepared:
        f = ResultFile(workspace_id=run.workspace_id, conversation_id=run.conversation_id, run_id=run.id, call_id=call.id,
                       filename=name, media_type=mime, data=payload)
        db.add(f); db.flush()
        files.append({"id": f.id, "filename": name, "media_type": mime, "size": len(payload)})
    return safe_data, files, None
