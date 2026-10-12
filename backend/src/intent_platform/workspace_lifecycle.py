"""Live workspace admission, independent of immutable application releases.

Epochs only increase. Restoring a workspace never revives a pre-archive runtime
identity, ticket or intake task. Ledger writes for already dispatched calls do
not use this admission gate, so a real outcome can still be recorded.
"""
from fastapi import HTTPException
from sqlalchemy import select

from .models import Workspace


def workspace_state(db, ws):
    # Fetch columns, not a potentially stale Session identity-map instance.
    return db.execute(select(Workspace.status, Workspace.access_epoch).where(Workspace.id == ws)).first()


def workspace_available(db, ws, epoch=None):
    state = workspace_state(db, ws)
    return bool(state and state.status == "active" and (epoch is None or epoch == state.access_epoch))


def require_workspace(db, ws, *, epoch=None, lock=False):
    if lock:
        from .capacity import lock_workspace
        lock_workspace(db, ws)
    state = workspace_state(db, ws)
    if not state:
        raise HTTPException(404, "Workspace unavailable")
    if state.status != "active":
        raise HTTPException(403, {"code": "WORKSPACE_ARCHIVED", "message": "空间已归档，当前操作不可用。"})
    if epoch is not None and epoch != state.access_epoch:
        raise HTTPException(403, {"code": "WORKSPACE_SESSION_EXPIRED", "message": "空间访问已失效，请重新进入应用。"})
    return state.access_epoch
