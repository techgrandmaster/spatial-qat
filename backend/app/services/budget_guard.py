"""
Budget guard service.
Every call checks SELECT COALESCE(SUM(cost_usd), 0) FROM model_calls.
If spend reaches BUDGET_USD_HARD_CAP (default $120.00), new uncached live
calls raise budget_exceeded (HTTP 429) while cached demo boards continue
working at $0.00 cost.
"""

import logging
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.orm import ModelCall

logger = logging.getLogger(__name__)


class BudgetExceededError(Exception):
    """Raised when the API budget hard cap is reached."""

    def __init__(self, spent: float, cap: float):
        self.spent = spent
        self.cap = cap
        super().__init__(f"Budget exceeded: ${spent:.2f} / ${cap:.2f}")


async def get_total_spent(db: AsyncSession, workspace_id: UUID | None = None) -> float:
    """Get total USD spent on model calls, optionally filtered by workspace."""
    stmt = select(func.coalesce(func.sum(ModelCall.cost_usd), 0.0))
    if workspace_id is not None:
        stmt = stmt.where(ModelCall.workspace_id == workspace_id)
    result = await db.execute(stmt)
    return result.scalar() or 0.0


async def check_budget(db: AsyncSession, workspace_id: UUID | None = None) -> bool:
    """
    Check if the budget hard cap has been reached.
    Returns True if budget is available, raises BudgetExceededError otherwise.
    """
    spent = await get_total_spent(db, workspace_id)
    if spent >= settings.budget_usd_hard_cap:
        raise BudgetExceededError(spent, settings.budget_usd_hard_cap)
    return True


async def record_cost(
    db: AsyncSession,
    cache_key: str,
    model: str,
    endpoint: str,
    operation: str,
    request_json: dict,
    response_json: dict,
    cost_usd: float,
    latency_ms: int,
    usage: dict | None = None,
    workspace_id: UUID | None = None,
) -> ModelCall:
    """
    Record a model call with its cost.
    If the cache_key already exists, return the existing record (cache hit).
    """
    # Check for cache hit
    existing_stmt = select(ModelCall).where(ModelCall.cache_key == cache_key)
    existing_result = await db.execute(existing_stmt)
    existing = existing_result.scalar_one_or_none()
    if existing is not None:
        logger.info("Cache hit for model call: %s", cache_key[:16])
        return existing

    # Record new call
    call = ModelCall(
        workspace_id=workspace_id,
        cache_key=cache_key,
        model=model,
        endpoint=endpoint,
        operation=operation,
        request_json=request_json,
        response_json=response_json,
        status="succeeded",
        latency_ms=latency_ms,
        usage=usage or {},
        cost_usd=cost_usd,
    )
    db.add(call)
    await db.flush()
    await db.refresh(call)
    return call


async def raise_if_budget_exceeded(db: AsyncSession, workspace_id: UUID | None = None):
    """FastAPI-compatible: raise HTTP 429 if budget exceeded."""
    try:
        await check_budget(db, workspace_id)
    except BudgetExceededError as exc:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"API budget exceeded: ${exc.spent:.2f} / ${exc.cap:.2f}",
        )
