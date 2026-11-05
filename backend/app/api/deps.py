"""Shared auth/ownership dependencies (FR-01).

Every workspace-scoped route requires a valid guest Bearer [REDACTED]
(Authorization: Bearer sqat_...). Workspaces are isolated per session:
a missing workspace yields 404, a workspace owned by another session
yields 403 — never a cross-session payload.
"""

from uuid import UUID

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.models.orm import Workspace

_bearer = HTTPBearer(auto_error=False)


async def get_session_id(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    db: AsyncSession = Depends(get_db),
) -> UUID:
    """Validate `Authorization: Bearer <token>`; return the session id."""
    from app.api.v1.sessions import verify_session_token

    if credentials is None or not credentials.credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing Bearer [REDACTED]"
        )
    session_id = await verify_session_token(credentials.credentials, db)
    if session_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired session token"
        )
    return session_id


async def load_owned_workspace(
    workspace_id: str,
    session_id: UUID,
    db: AsyncSession,
) -> Workspace:
    """Fetch a workspace owned by this session (404 missing, 403 foreign)."""
    try:
        wid = UUID(str(workspace_id))
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid UUID") from exc

    stmt = select(Workspace).where(Workspace.id == wid)
    result = await db.execute(stmt)
    workspace = result.scalar_one_or_none()
    if workspace is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace not found")
    if workspace.guest_session_id != session_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Workspace access denied")
    return workspace
