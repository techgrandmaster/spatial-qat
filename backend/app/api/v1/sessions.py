"""
Sessions router: POST /api/v1/sessions
Issues a 7-day expiring guest session bearer token.
"""

import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import get_db
from app.models.orm import GuestSession
from app.models.schemas import GuestSessionCreate, GuestSessionResponse

router = APIRouter()


def _generate_token() -> str:
    """Generate a cryptographically secure bearer token."""
    return f"sqat_{secrets.token_urlsafe(32)}"


def _hash_token(token: str) -> str:
    """Hash a bearer token for storage (SHA-256)."""
    return hashlib.sha256(token.encode()).hexdigest()


@router.post("", response_model=GuestSessionResponse)
async def create_session(_body: GuestSessionCreate, db: AsyncSession = Depends(get_db)):
    """Issue a 7-day expiring guest session bearer token (hashed at rest)."""
    token = _generate_token()
    expires_at = datetime.now(UTC) + timedelta(days=settings.guest_session_ttl_days)

    session_row = GuestSession(
        token_hash=_hash_token(token),
        expires_at=expires_at,
    )
    db.add(session_row)
    await db.flush()
    await db.refresh(session_row)

    return GuestSessionResponse(
        session_id=session_row.id,
        bearer_token=token,
        expires_at=session_row.expires_at,
    )


async def verify_session_token(bearer_token: str, db: AsyncSession) -> UUID | None:
    """
    Verify a bearer token against the DB.
    Returns the session_id if valid, None otherwise.
    """
    token_hash = _hash_token(bearer_token)
    stmt = select(GuestSession).where(
        GuestSession.token_hash == token_hash,
        GuestSession.expires_at > datetime.now(UTC),
    )
    result = await db.execute(stmt)
    row = result.scalar_one_or_none()
    return row.id if row else None
