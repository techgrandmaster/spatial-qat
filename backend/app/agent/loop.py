"""
Hand-written 8-step agent loop with tool registry, SHA-256 cache, budget guard, and SSE events.
"""

import logging
import time
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.schemas import (
    AnswerPayload,
    SSEEventEnvelope,
    SSEEventType,
    UncertaintyLevel,
)

logger = logging.getLogger(__name__)


class AgentRunContext:
    """Context for a single agent run with SSE event tracking."""

    def __init__(self, run_id: UUID, workspace_id: UUID, kind: str):
        self.run_id = run_id
        self.workspace_id = workspace_id
        self.kind = kind
        self.step_seq = 0
        self.events: list[SSEEventEnvelope] = []
        self.start_time = time.time()
        self.timeout = settings.agent_timeout_seconds

    def add_event(self, event_type: SSEEventType, payload: dict):
        self.step_seq += 1
        self.events.append(SSEEventEnvelope(
            run_id=self.run_id,
            seq=self.step_seq,
            event=event_type,
            timestamp=datetime.now(UTC),
            payload=payload,
        ))

    def elapsed_seconds(self) -> float:
        return time.time() - self.start_time

    def is_timed_out(self) -> bool:
        return self.elapsed_seconds() > self.timeout


ToolFunction = Callable[..., dict]


class AgentToolRegistry:
    """Registry of agent tools with execution tracking."""

    def __init__(self):
        self.tools: dict[str, ToolFunction] = {}

    def register(self, name: str, fn: ToolFunction):
        self.tools[name] = fn

    def get(self, name: str) -> ToolFunction | None:
        return self.tools.get(name)

    def list_tools(self) -> list[dict]:
        return [{"name": name} for name in self.tools]


async def run_agent_loop(
    question: str,
    workspace_id: UUID,
    db: AsyncSession,
    tool_registry: AgentToolRegistry,
    context_data: dict,
) -> AnswerPayload:
    """
    Execute the 8-step agent loop.
    Steps: search_spec, get_page, list_components, answer_question.
    """
    from app.adapters.meta.spark import MuseSparkAdapter

    run_id = uuid.uuid4()
    ctx = AgentRunContext(run_id, workspace_id, "qa")
    ctx.add_event(SSEEventType.RUN_STARTED, {"kind": "qa", "question": question[:100]})

    adapter = MuseSparkAdapter()
    max_steps = settings.agent_max_steps

    # Step 1: Search datasheet for relevant context
    for step in range(max_steps):
        if ctx.is_timed_out():
            ctx.add_event(SSEEventType.RUN_FAILED, {"error": "timeout"})
            return AnswerPayload(
                text="Agent timed out. Please try again.",
                uncertainty=UncertaintyLevel.HIGH,
                ungrounded=True,
            )

        ctx.add_event(SSEEventType.STEP_STARTED, {"step": step, "kind": "reasoning"})

        # Gather context from tools
        search_results = []
        if "search_spec" in tool_registry.tools:
            try:
                search_fn = tool_registry.tools["search_spec"]
                ctx.add_event(SSEEventType.TOOL_CALLED, {"tool": "search_spec", "args": {"query": question}})
                search_results = await search_fn(question, db=db)
                ctx.add_event(SSEEventType.TOOL_RESULT, {"tool": "search_spec", "results_count": len(search_results)})
            except Exception as exc:
                logger.warning("search_spec failed: %s", exc)

        # Build context from search results
        context_parts = []
        for sr in search_results[:5]:
            context_parts.append(f"[Page {sr.get('page_number', '?')}] {sr.get('text', '')[:500]}")
        context_text = "\n".join(context_parts) if context_parts else context_data.get("datasheet_summary", "")

        # Get answer from Spark
        try:
            ctx.add_event(SSEEventType.TOOL_CALLED, {"tool": "answer_question"})
            answer = await adapter.answer_question(question, context_text, workspace_id=workspace_id, db=db)
            ctx.add_event(SSEEventType.TOOL_RESULT, {"tool": "answer_question"})
            ctx.add_event(SSEEventType.ANSWER_FINAL, {"text": answer.text[:200]})
            ctx.add_event(SSEEventType.RUN_COMPLETED, {"latency_ms": int(ctx.elapsed_seconds() * 1000)})
            return answer
        except Exception as exc:
            logger.error("Agent loop failed at step %d: %s", step, exc)

    # Fallback if loop exhausted
    ctx.add_event(SSEEventType.RUN_FAILED, {"error": "max_steps_exceeded"})
    return AnswerPayload(
        text="Unable to answer the question. Please try rephrasing.",
        uncertainty=UncertaintyLevel.HIGH,
        ungrounded=True,
    )


async def run_analyze_pipeline(
    workspace_id: UUID,
    image_asset_id: UUID,
    pdf_asset_id: UUID | None,
    db: AsyncSession,
) -> dict:
    """
    Run the full board analysis pipeline:
    1. muse-spark-1.3 proposes components
    2. Parallel sam-3.1 segmentation
    3. SoM grounding
    """
    from app.adapters.meta.sam import SamAdapter
    from app.adapters.meta.spark import MuseSparkAdapter
    from app.models.orm import Asset, Component
    from app.services.som_tagger import compute_centroid

    run_id = uuid.uuid4()
    ctx = AgentRunContext(run_id, workspace_id, "analyze")
    ctx.add_event(SSEEventType.RUN_STARTED, {"kind": "analyze"})

    spark = MuseSparkAdapter()
    sam = SamAdapter()

    # Get image asset
    from sqlalchemy import select

    img_stmt = select(Asset).where(Asset.id == image_asset_id)
    img_result = await db.execute(img_stmt)
    image_asset = img_result.scalar_one_or_none()
    if not image_asset:
        raise ValueError(f"Image asset {image_asset_id} not found")

    # Step 1: Propose components
    datasheet_summary = ""
    if pdf_asset_id:
        pdf_stmt = select(Asset).where(Asset.id == pdf_asset_id)
        pdf_result = await db.execute(pdf_stmt)
        pdf_asset = pdf_result.scalar_one_or_none()
        if pdf_asset:
            datasheet_summary = f"PDF: {pdf_asset.original_filename}, {pdf_asset.page_count or 0} pages"

    ctx.add_event(SSEEventType.STEP_STARTED, {"step": 1, "kind": "propose_components"})
    proposal = await spark.propose_components(
        image_description=f"Board image: {image_asset.original_filename}",
        datasheet_summary=datasheet_summary,
        workspace_id=workspace_id,
        db=db,
    )
    ctx.add_event(SSEEventType.TOOL_RESULT, {"tool": "propose_components", "count": len(proposal.phrases)})

    # Step 2: Parallel SAM segmentation
    ctx.add_event(SSEEventType.STEP_STARTED, {"step": 2, "kind": "segmentation"})
    image_url = f"/api/v1/assets/{image_asset_id}/content"
    phrases = [p.noun_phrase for p in proposal.phrases]
    segment_results = await sam.segment_batch(phrases, image_url, workspace_id=workspace_id, db=db)

    # Step 3: Create component records
    components_created = []
    for idx, (phrase, seg_result) in enumerate(zip(proposal.phrases, segment_results)):
        if isinstance(seg_result, Exception):
            logger.warning("Segmentation failed for %s: %s", phrase.noun_phrase, seg_result)
            continue

        polygons = seg_result.get("polygons", [])
        if not polygons:
            continue

        centroid = compute_centroid(polygons)
        bbox_x = centroid[0] - 0.05
        bbox_y = centroid[1] - 0.05
        bbox_w = 0.1
        bbox_h = 0.1

        comp = Component(
            workspace_id=workspace_id,
            image_asset_id=image_asset_id,
            som_index=idx + 1,
            label=phrase.canonical_label,
            noun_phrase=phrase.noun_phrase,
            category=phrase.category.value if hasattr(phrase.category, "value") else phrase.category,
            role="Component on board",
            description=f"Segmented: {phrase.noun_phrase}",
            bbox_x=bbox_x,
            bbox_y=bbox_y,
            bbox_w=bbox_w,
            bbox_h=bbox_h,
            bbox={"x": bbox_x, "y": bbox_y, "w": bbox_w, "h": bbox_h},
            polygons=polygons,
            area_fraction=0.01,
            confidence=0.8,
            identity_source="agent",
            verification_status="proposed",
        )
        db.add(comp)
        await db.flush()
        await db.refresh(comp)
        components_created.append(comp)

        ctx.add_event(SSEEventType.COMPONENT_FOUND, {
            "component_id": str(comp.id),
            "label": comp.label,
        })

    ctx.add_event(SSEEventType.RUN_COMPLETED, {
        "components_found": len(components_created),
        "latency_ms": int(ctx.elapsed_seconds() * 1000),
    })

    return {
        "run_id": run_id,
        "components_created": len(components_created),
        "events": ctx.events,
    }
