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


def _bbox_from_polygons(polygons: list) -> tuple[float, float, float, float]:
    """Tight normalized bbox over all rings (clamped to [0,1])."""
    xs: list[float] = []
    ys: list[float] = []
    for poly_with_holes in polygons or []:
        for ring in poly_with_holes or []:
            for pt in ring or []:
                try:
                    xs.append(float(pt[0]))
                    ys.append(float(pt[1]))
                except (IndexError, TypeError, ValueError):
                    continue
    if not xs:
        return 0.45, 0.45, 0.1, 0.1
    x0, x1 = max(0.0, min(xs)), min(1.0, max(xs))
    y0, y1 = max(0.0, min(ys)), min(1.0, max(ys))
    w, h = max(0.01, x1 - x0), max(0.01, y1 - y0)
    return round(x0, 6), round(y0, 6), round(w, 6), round(h, 6)


def _area_fraction_from_polygons(polygons: list, fallback: float) -> float:
    """Normalized polygon area via Shapely; falls back to bbox area."""
    try:
        from shapely.geometry import Polygon as _Polygon

        total = 0.0
        for poly_with_holes in polygons or []:
            if not poly_with_holes:
                continue
            outer = poly_with_holes[0]
            if len(outer) < 3:
                continue
            poly = _Polygon([(float(c[0]), float(c[1])) for c in outer])
            if not poly.is_empty:
                total += poly.area
        if total > 0:
            return round(min(1.0, total), 6)
    except Exception:
        pass
    try:
        return round(min(1.0, max(0.0001, float(fallback))), 6)
    except (TypeError, ValueError):
        return 0.01


def _render_mask_png_bytes(polygons: list, size: int = 512) -> bytes:
    """Rasterize normalized polygons to a binary mask PNG (white on black)."""
    import io as _io

    from PIL import Image as _Image
    from PIL import ImageDraw as _ImageDraw

    img = _Image.new("L", (size, size), 0)
    draw = _ImageDraw.Draw(img)
    for poly_with_holes in polygons or []:
        if not poly_with_holes:
            continue
        outer = poly_with_holes[0]
        if len(outer) < 3:
            continue
        pts = [(float(x) * size, float(y) * size) for x, y in outer]
        draw.polygon(pts, fill=255)
    buf = _io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


async def _persist_mask_assets(db, workspace_id, image_asset_id, specs) -> list:
    """Store one mask_png Asset per spec; returns mask asset ids (or None)."""
    import asyncio as _asyncio
    import hashlib as _hashlib

    if not specs:
        return []
    from app.api.v1.assets import STORAGE_DIR
    from app.models.orm import Asset as _Asset

    rendered: list[bytes] = []
    for _idx, _phrase, polygons, *_rest in specs:
        try:
            rendered.append(await _asyncio.to_thread(_render_mask_png_bytes, polygons))
        except Exception:
            rendered.append(b"")

    mask_assets: list = []
    for data, (idx, phrase, *_rest) in zip(rendered, specs):
        if not data:
            mask_assets.append(None)
            continue
        sha = _hashlib.sha256(data).hexdigest()
        storage_key = f"{sha}.png"
        storage_path = STORAGE_DIR / storage_key
        if not storage_path.exists():
            await _asyncio.to_thread(storage_path.write_bytes, data)
        asset = _Asset(
            workspace_id=workspace_id,
            kind="mask_png",
            sha256=sha,
            mime_type="image/png",
            original_filename=f"mask_som_{idx + 1}.png",
            storage_key=storage_key,
            byte_size=len(data),
            width_px=512,
            height_px=512,
            status="ready",
        )
        db.add(asset)
        mask_assets.append(asset)

    real = [a for a in mask_assets if a is not None]
    if real:
        await db.flush()
    return [a.id if a is not None else None for a in mask_assets]


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


async def _persist_run_trace(db, ctx, model: str, status: str) -> None:
    """Persist ctx.events to agent_runs/agent_steps so SSE works across workers.

    Events are also mirrored into agent_runs.totals["sse_events"] — the SSE
    router streams from the DB row, never from the in-memory dict.
    Persistence failures are logged, never raised (trace must not break QA).
    """
    try:
        from app.models.orm import AgentRun as _AgentRun
        from app.models.orm import AgentStep as _AgentStep

        events_payload = [
            {
                "seq": e.seq,
                "event": e.event.value if hasattr(e.event, "value") else str(e.event),
                "timestamp": e.timestamp.isoformat() if hasattr(e, "timestamp") else None,
                "payload": e.payload,
            }
            for e in ctx.events[:200]
        ]
        run = _AgentRun(
            id=ctx.run_id,
            workspace_id=ctx.workspace_id,
            kind=ctx.kind,
            status=status,
            model=model,
            totals={"sse_events": events_payload, "event_count": len(ctx.events)},
            completed_at=datetime.now(UTC),
        )
        db.add(run)
        for idx, e in enumerate(ctx.events[:200], 1):
            db.add(_AgentStep(
                run_id=ctx.run_id,
                seq=idx,
                kind=ctx.kind,
                name=e.event.value if hasattr(e.event, "value") else str(e.event),
                args={},
                result_summary=str(e.payload)[:2000],
            ))
        await db.flush()
    except Exception as exc:
        logger.warning("Run trace persistence failed: %s", exc)


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

    # Budgeted fan-out: at most agent_max_parallel_tools tool calls per step,
    # timeout checked before every tool and before the final answer call.
    # DB-bound tools share this session sequentially (AsyncSession is not
    # safe for concurrent execute); SAM batch fans out internally via gather.
    for step in range(max_steps):
        if ctx.is_timed_out():
            ctx.add_event(SSEEventType.RUN_FAILED, {"error": "timeout"})
            await _persist_run_trace(db, ctx, adapter.model_id, "timeout")
            return AnswerPayload(
                text="Agent timed out. Please try again.",
                uncertainty=UncertaintyLevel.HIGH,
                ungrounded=True,
            )

        ctx.add_event(SSEEventType.STEP_STARTED, {"step": step, "kind": "reasoning"})
        calls_this_step = 0
        max_calls = max(1, settings.agent_max_parallel_tools)

        def _budget_left() -> bool:
            return calls_this_step < max_calls and not ctx.is_timed_out()

        # Gather context from tools
        search_results: list = []
        if "search_spec" in tool_registry.tools and _budget_left():
            try:
                search_fn = tool_registry.tools["search_spec"]
                ctx.add_event(SSEEventType.TOOL_CALLED, {"tool": "search_spec", "args": {"query": question}})
                calls_this_step += 1
                search_results = await search_fn(
                    question, db=db, workspace_id=workspace_id,
                )
                ctx.add_event(SSEEventType.TOOL_RESULT, {"tool": "search_spec", "results_count": len(search_results)})
            except Exception as exc:
                logger.warning("search_spec failed: %s", exc)

        components_ctx: list = []
        if "list_components" in tool_registry.tools and _budget_left():
            try:
                list_fn = tool_registry.tools["list_components"]
                ctx.add_event(SSEEventType.TOOL_CALLED, {"tool": "list_components"})
                calls_this_step += 1
                components_ctx = await list_fn(workspace_id, db=db, workspace_id=workspace_id)
                ctx.add_event(SSEEventType.TOOL_RESULT, {
                    "tool": "list_components", "results_count": len(components_ctx or []),
                })
            except Exception as exc:
                logger.warning("list_components failed: %s", exc)

        if "get_page" in tool_registry.tools and search_results and _budget_left():
            try:
                top = search_results[0]
                page_number = top.get("page_number")
                if isinstance(page_number, int):
                    get_fn = tool_registry.tools["get_page"]
                    ctx.add_event(SSEEventType.TOOL_CALLED, {"tool": "get_page", "args": {"page": page_number}})
                    calls_this_step += 1
                    page = await get_fn(page_number, db=db, workspace_id=workspace_id)
                    if page and page.get("text_content"):
                        search_results = [{
                            "page_number": page.get("page_number"),
                            "text": page.get("text_content", "")[:2000],
                            "chunk_index": -1,
                        }] + search_results
                    ctx.add_event(SSEEventType.TOOL_RESULT, {"tool": "get_page"})
            except Exception as exc:
                logger.warning("get_page failed: %s", exc)

        # Build context from search results
        context_parts = []
        for sr in search_results[:5]:
            context_parts.append(f"[Page {sr.get('page_number', '?')}] {sr.get('text', '')[:500]}")
        if components_ctx:
            names = ", ".join(str(c.get("label", "?")) for c in components_ctx[:10])
            context_parts.append(f"[Components] {names}")
        context_text = "\n".join(context_parts) if context_parts else context_data.get("datasheet_summary", "")

        if ctx.is_timed_out():
            ctx.add_event(SSEEventType.RUN_FAILED, {"error": "timeout"})
            await _persist_run_trace(db, ctx, adapter.model_id, "timeout")
            return AnswerPayload(
                text="Agent timed out. Please try again.",
                uncertainty=UncertaintyLevel.HIGH,
                ungrounded=True,
            )

        # Get answer from Spark
        try:
            ctx.add_event(SSEEventType.TOOL_CALLED, {"tool": "answer_question"})
            answer = await adapter.answer_question(question, context_text, workspace_id=workspace_id, db=db)
            ctx.add_event(SSEEventType.TOOL_RESULT, {"tool": "answer_question"})
            ctx.add_event(SSEEventType.ANSWER_FINAL, {"text": answer.text[:200]})
            ctx.add_event(SSEEventType.RUN_COMPLETED, {"latency_ms": int(ctx.elapsed_seconds() * 1000)})
            await _persist_run_trace(db, ctx, adapter.model_id, "completed")
            return answer
        except Exception as exc:
            logger.error("Agent loop failed at step %d: %s", step, exc)

    # Fallback if loop exhausted
    ctx.add_event(SSEEventType.RUN_FAILED, {"error": "max_steps_exceeded"})
    await _persist_run_trace(db, ctx, adapter.model_id, "failed")
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

    # Step 2: Parallel SAM segmentation (absolute URL: external API cannot
    # fetch a relative /api/v1/... path).
    ctx.add_event(SSEEventType.STEP_STARTED, {"step": 2, "kind": "segmentation"})
    base = settings.public_base_url.rstrip("/")
    image_url = f"{base}/api/v1/assets/{image_asset_id}/content"
    phrases = [p.noun_phrase for p in proposal.phrases]
    segment_results = await sam.segment_batch(phrases, image_url, workspace_id=workspace_id, db=db)

    # Step 2b: SoM grounding — link each segmented mask to datasheet evidence.
    # Falls back to proposal labels when the grounding call fails (e.g. mock).
    grounded_by_index: dict[int, object] = {}
    try:
        som_lines = []
        for idx, (phrase, seg_result) in enumerate(zip(proposal.phrases, segment_results)):
            if isinstance(seg_result, Exception):
                continue
            polys = seg_result.get("polygons", [])
            if not polys:
                continue
            cx, cy = compute_centroid(polys)
            som_lines.append(f"[{idx + 1}] {phrase.canonical_label} at ({cx:.3f}, {cy:.3f})")
        if som_lines:
            ctx.add_event(SSEEventType.STEP_STARTED, {"step": 3, "kind": "som_grounding"})
            synthesis = await spark.ground_som(
                som_description="Segmented board marks:\n" + "\n".join(som_lines),
                datasheet_context=datasheet_summary or f"{len(phrases)} proposed phrases",
                workspace_id=workspace_id,
                db=db,
            )
            for g in synthesis.grounded_components:
                grounded_by_index[g.som_index] = g
            ctx.add_event(SSEEventType.TOOL_RESULT, {
                "tool": "ground_som", "grounded_count": len(grounded_by_index),
            })
    except Exception as exc:
        logger.warning("SoM grounding failed, using proposal labels: %s", exc)

    # Step 3: Create component records (single flush for masks + components).
    # Real geometry replaces the old hardcoded 0.1x0.1 box / 0.01 area / 0.8 conf.
    specs: list[tuple] = []
    for idx, (phrase, seg_result) in enumerate(zip(proposal.phrases, segment_results)):
        if isinstance(seg_result, Exception):
            logger.warning("Segmentation failed for %s: %s", phrase.noun_phrase, seg_result)
            continue

        polygons = seg_result.get("polygons", [])
        if not polygons:
            continue

        bbox_x, bbox_y, bbox_w, bbox_h = _bbox_from_polygons(polygons)
        area_fraction = _area_fraction_from_polygons(polygons, bbox_w * bbox_h)
        confidence = float(seg_result.get("confidence", 0.8) or 0.8)
        grounded = grounded_by_index.get(idx + 1)
        if grounded is not None:
            label = grounded.label
            category = grounded.category.value if hasattr(grounded.category, "value") else grounded.category
            role = grounded.role
            description = grounded.description
        else:
            label = phrase.canonical_label
            category = phrase.category.value if hasattr(phrase.category, "value") else phrase.category
            role = "Component on board"
            description = f"Segmented: {phrase.noun_phrase}"
        specs.append((idx, phrase, polygons, bbox_x, bbox_y, bbox_w, bbox_h,
                      area_fraction, confidence, label, category, role, description))

    # Render + persist one mask PNG per component, then link via mask_asset_id.
    mask_ids: list = await _persist_mask_assets(
        db, workspace_id, image_asset_id, specs
    )

    pending: list[Component] = []
    for (idx, phrase, polygons, bbox_x, bbox_y, bbox_w, bbox_h,
         area_fraction, confidence, label, category, role, description), mask_id in zip(specs, mask_ids):
        comp = Component(
            workspace_id=workspace_id,
            image_asset_id=image_asset_id,
            som_index=idx + 1,
            label=label,
            noun_phrase=phrase.noun_phrase,
            category=category,
            role=role,
            description=description,
            bbox_x=bbox_x,
            bbox_y=bbox_y,
            bbox_w=bbox_w,
            bbox_h=bbox_h,
            bbox={"x": bbox_x, "y": bbox_y, "w": bbox_w, "h": bbox_h},
            polygons=polygons,
            area_fraction=area_fraction,
            mask_asset_id=mask_id,
            mask_encoding="png" if mask_id is not None else None,
            confidence=confidence,
            identity_source="agent",
            verification_status="proposed",
        )
        db.add(comp)
        pending.append(comp)

    if pending:
        await db.flush()
    components_created = pending
    for comp in components_created:
        ctx.add_event(SSEEventType.COMPONENT_FOUND, {
            "component_id": str(comp.id),
            "label": comp.label,
        })

    ctx.add_event(SSEEventType.RUN_COMPLETED, {
        "components_found": len(components_created),
        "latency_ms": int(ctx.elapsed_seconds() * 1000),
    })
    await _persist_run_trace(db, ctx, spark.model_id, "completed")

    return {
        "run_id": run_id,
        "components_created": len(components_created),
        "events": ctx.events,
    }
