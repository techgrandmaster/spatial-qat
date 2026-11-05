"""
Runs router: SSE stream for agent run events.
"""

import json
import logging
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from app.db import get_db
from app.models.orm import AgentRun, AgentStep
from app.models.schemas import AgentRunTraceDTO, AgentStepDTO

logger = logging.getLogger(__name__)

router = APIRouter()

# In-memory event store for SSE streaming
run_events: dict[str, list[dict]] = {}


@router.get("/{run_id}/events")
async def stream_run_events(run_id: str):
    """SSE Stream: Emits run.started, step.started, tool.called, tool.result,
    component.found, message.delta, answer.final, run.completed, run.failed."""
    async def event_generator():
        events = run_events.get(run_id, [])
        for event in events:
            yield {
                "event": event.get("event", "message"),
                "data": json.dumps(event.get("payload", {})),
                "id": str(event.get("seq", 0)),
            }
        # Signal end of stream
        yield {"event": "end", "data": json.dumps({"status": "complete"})}

    return EventSourceResponse(event_generator())


@router.get("/{run_id}/trace", response_model=AgentRunTraceDTO)
async def get_run_trace(run_id: str, db: AsyncSession = Depends(get_db)):
    """Get the complete trace of an agent run."""
    try:
        rid = UUID(run_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid UUID") from exc

    stmt = select(AgentRun).where(AgentRun.id == rid)
    result = await db.execute(stmt)
    run = result.scalar_one_or_none()
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Run not found")

    # Fetch steps
    steps_stmt = select(AgentStep).where(AgentStep.run_id == rid).order_by(AgentStep.seq)
    steps_result = await db.execute(steps_stmt)
    steps = steps_result.scalars().all()

    step_dtos = [
        AgentStepDTO(
            id=s.id,
            run_id=s.run_id,
            seq=s.seq,
            kind=s.kind,
            name=s.name,
            args=s.args or {},
            result_summary=s.result_summary,
            cache_hit=s.cache_hit,
            latency_ms=s.latency_ms,
            cost_usd=s.cost_usd,
            created_at=s.created_at,
        )
        for s in steps
    ]

    return AgentRunTraceDTO(
        id=run.id,
        workspace_id=run.workspace_id,
        kind=run.kind,
        status=run.status,
        model=run.model,
        prompt_version=run.prompt_version,
        steps=step_dtos,
        totals=run.totals or {},
        created_at=run.created_at,
        completed_at=run.completed_at,
    )


def store_run_events(run_id: str, events: list[dict]):
    """Store events for SSE streaming."""
    run_events[run_id] = events
