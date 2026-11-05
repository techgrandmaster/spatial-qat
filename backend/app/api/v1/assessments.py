"""
Assessments router: spatial quiz generation and attempt submission.
"""

import time
import uuid
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_session_id, load_owned_workspace
from app.db import get_db
from app.models.orm import (
    AgentRun,
    Assessment,
    AssessmentAttempt,
    AssessmentItem,
    Asset,
    Component,
    Job,
)
from app.models.schemas import (
    AnswerPayload,
    AssessmentAttemptRequest,
    AssessmentAttemptResponse,
    AssessmentCreate,
    PublicAssessmentItemDTO,
    PublicAssessmentResponse,
    RunResponse,
    UncertaintyLevel,
)
from app.services.grading import grade_conceptual_explanation, grade_spatial_attempt

router = APIRouter()


@router.post("/workspaces/{workspace_id}/assessments", response_model=RunResponse)
async def create_assessment(
    workspace_id: str,
    body: AssessmentCreate,
    session_id: UUID = Depends(get_session_id),
    db: AsyncSession = Depends(get_db),
):
    """Generate a spatial quiz with server-hidden target_component_id and private rubric."""
    workspace = await load_owned_workspace(workspace_id, session_id, db)
    wid = workspace.id

    # Get verified components
    comp_stmt = select(Component).where(
        Component.workspace_id == wid,
        Component.verification_status.in_(["proposed", "verified"]),
    )
    comp_result = await db.execute(comp_stmt)
    components = comp_result.scalars().all()

    if len(components) < body.count:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Not enough components ({len(components)}) for {body.count} quiz items",
        )

    # Get image asset
    img_stmt = select(Asset).where(Asset.workspace_id == wid, Asset.kind == "board_image")
    img_result = await db.execute(img_stmt)
    image_asset = img_result.scalar_one_or_none()
    if image_asset is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No board image found")

    # Try Spark for quiz prompts/rubrics; bind targets to local components
    # (Spark UUIDs are not trusted). Fall back to local generation on failure.
    draft = None
    try:
        from app.adapters.meta.spark import MuseSparkAdapter

        context = "; ".join(
            f"{c.label} ({c.category}): {c.role or c.description or ''}" for c in list(components)[:10]
        )
        spark = MuseSparkAdapter()
        draft = await spark.generate_quiz(
            context, body.count,
            body.difficulty.value if hasattr(body.difficulty, "value") else str(body.difficulty),
            workspace_id=wid, db=db,
        )
    except Exception as exc:
        import logging as _logging

        _logging.getLogger(__name__).warning("Spark quiz draft failed, using local items: %s", exc)
        draft = None

    def _scrub_hint(raw: str | None, comp) -> str:
        """Leak-free hint: never contains the answer's label or noun phrase."""
        fallback = f"Hint: look for the {comp.category} component."
        if not raw or not raw.strip():
            return fallback
        lowered = raw.lower()
        if comp.noun_phrase.lower() in lowered or comp.label.lower() in lowered:
            return fallback
        return raw[:240]

    draft_items = list(draft.items) if draft is not None and draft.items else []
    assessment_id = uuid.uuid4()
    assessment = Assessment(
        id=assessment_id,
        workspace_id=wid,
        image_asset_id=image_asset.id,
        title=draft.title if draft is not None and draft.title else f"Spatial Quiz - {body.difficulty}",
        difficulty=body.difficulty.value if hasattr(body.difficulty, "value") else body.difficulty,
    )
    db.add(assessment)
    await db.flush()

    # Generate quiz items (targets bound locally; prompts/rubrics prefer Spark draft)
    selected_components = list(components)[: body.count]
    for pos, comp in enumerate(selected_components, 1):
        kinds = [k.value if hasattr(k, "value") else k for k in body.kinds]
        kind = kinds[(pos - 1) % len(kinds)]

        prompt = f"Locate the component that serves as: {comp.role or comp.label}"
        hint = f"Hint: look for the {comp.category} component."
        rubric: dict = {
            "expected_concepts": [comp.label],
            "reference_answer": comp.description or comp.role or "",
        }
        if draft_items:
            d = draft_items[(pos - 1) % len(draft_items)]
            if d.prompt and len(d.prompt) >= 12:
                prompt = d.prompt[:400]
            try:
                rubric = d.rubric.model_dump()
            except AttributeError:
                rubric = dict(d.rubric) if isinstance(d.rubric, dict) else rubric
            if not rubric.get("expected_concepts"):
                rubric["expected_concepts"] = [comp.label]
            hint = _scrub_hint(d.hint, comp)

        item = AssessmentItem(
            assessment_id=assessment_id,
            position=pos,
            kind=kind,
            prompt=prompt,
            target_component_id=comp.id,
            hint=hint,
            rubric_json=rubric,
        )
        db.add(item)

    await db.flush()

    run_id = uuid.uuid4()
    message_id = uuid.uuid4()

    run = AgentRun(
        id=run_id,
        workspace_id=wid,
        kind="quiz_gen",
        status="completed",
        model="muse-spark-1.3",
        totals={"items": body.count},
    )
    db.add(run)
    await db.flush()

    return RunResponse(
        run_id=run_id,
        message_id=message_id,
        answer=AnswerPayload(
            text=f"Quiz generated with {body.count} items.",
            uncertainty=UncertaintyLevel.LOW,
        ),
    )


@router.get("/{assessment_id}", response_model=PublicAssessmentResponse)
async def get_assessment(
    assessment_id: str,
    session_id: UUID = Depends(get_session_id),
    db: AsyncSession = Depends(get_db),
):
    """Fetch public quiz prompt and metadata without exposing the target component or answer key."""
    try:
        aid = UUID(assessment_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid UUID") from exc

    stmt = select(Assessment).where(Assessment.id == aid)
    result = await db.execute(stmt)
    assessment = result.scalar_one_or_none()
    if assessment is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Assessment not found")

    await load_owned_workspace(str(assessment.workspace_id), session_id, db)

    # Fetch items - CRITICAL: never expose target_component_id or rubric
    items_stmt = select(AssessmentItem).where(AssessmentItem.assessment_id == aid).order_by(AssessmentItem.position)
    items_result = await db.execute(items_stmt)
    items = items_result.scalars().all()

    public_items = [
        PublicAssessmentItemDTO(
            id=item.id,
            assessment_id=item.assessment_id,
            position=item.position,
            kind=item.kind,
            prompt=item.prompt,
            hint=item.hint,
        )
        for item in items
    ]

    return PublicAssessmentResponse(
        id=assessment.id,
        workspace_id=assessment.workspace_id,
        image_asset_id=assessment.image_asset_id,
        title=assessment.title,
        difficulty=assessment.difficulty,
        items=public_items,
    )


@router.post("/{assessment_id}/attempts", response_model=AssessmentAttemptResponse)
async def submit_attempt(
    assessment_id: str,
    body: AssessmentAttemptRequest,
    request: Request,
    session_id: UUID = Depends(get_session_id),
    db: AsyncSession = Depends(get_db),
):
    """Submit normalized click (x, y) and optional explanation; returns deterministic spatial + rubric grade."""
    start = time.time()

    try:
        aid = UUID(assessment_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid UUID") from exc

    # Owner-only attempt (assessment -> workspace ownership; 404 missing, 403 foreign).
    _assess_stmt = select(Assessment).where(Assessment.id == aid)
    _assess = (await db.execute(_assess_stmt)).scalar_one_or_none()
    if _assess is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Assessment not found")
    await load_owned_workspace(str(_assess.workspace_id), session_id, db)

    # Idempotency-Key: replay the stored response instead of double-grading.
    idempotency_key = request.headers.get("idempotency-key")
    if idempotency_key:
        job_stmt = select(Job).where(
            Job.idempotency_key == idempotency_key, Job.kind == "attempt"
        )
        job_result = await db.execute(job_stmt)
        existing_job = job_result.scalar_one_or_none()
        if existing_job is not None and existing_job.status == "completed" and existing_job.result:
            return AssessmentAttemptResponse(**existing_job.result)

    # Get assessment item (explicit item_id wins; otherwise the first item).
    item_stmt = select(AssessmentItem).where(AssessmentItem.assessment_id == aid).order_by(AssessmentItem.position)
    item_result = await db.execute(item_stmt)
    items = item_result.scalars().all()
    if not items:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Assessment items not found")

    item = items[0]
    if body.assessment_item_id is not None:
        match = next((i for i in items if i.id == body.assessment_item_id), None)
        if match is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Assessment item not found")
        item = match

    # Get image asset (assessment -> asset: 2 round-trips, no separate target fetch).
    assessment_stmt = select(Assessment).where(Assessment.id == aid)
    assessment_result = await db.execute(assessment_stmt)
    assessment = assessment_result.scalar_one_or_none()

    img_stmt = select(Asset).where(Asset.id == assessment.image_asset_id)
    img_result = await db.execute(img_stmt)
    image_asset = img_result.scalar_one_or_none()
    if image_asset is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Image asset not found")

    # Get all components once; resolve target in-memory (saves 1 SELECT).
    all_comp_stmt = select(Component).where(Component.workspace_id == image_asset.workspace_id)
    all_comp_result = await db.execute(all_comp_stmt)
    all_components = all_comp_result.scalars().all()

    target_comp = next((c for c in all_components if c.id == item.target_component_id), None)
    if target_comp is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Target component not found")

    # Part A: Spatial grading
    spatial_result = await grade_spatial_attempt(
        click_x=body.click.x,
        click_y=body.click.y,
        target_component=target_comp,
        image_asset=image_asset,
        all_components=list(all_components),
        hints_used=body.hints_used,
        db=db,
    )

    # Part B: Conceptual grading
    rubric = item.rubric_json or {}
    conceptual_result = await grade_conceptual_explanation(
        explanation=body.explanation or "",
        rubric=rubric,
        workspace_id=target_comp.workspace_id,
        db=db,
    )

    # Resolve clicked component label from already-fetched rows (no extra SELECT).
    clicked_label = None
    if spatial_result["clicked_component_id"]:
        clicked_uuid = UUID(str(spatial_result["clicked_component_id"]))
        clicked_comp = next((c for c in all_components if c.id == clicked_uuid), None)
        if clicked_comp is not None:
            clicked_label = clicked_comp.label

    # Persist attempt
    attempt = AssessmentAttempt(
        assessment_item_id=item.id,
        click_x=body.click.x,
        click_y=body.click.y,
        spatial_hit=spatial_result["spatial_hit"],
        near_miss=spatial_result["near_miss"],
        clicked_component_id=(
            UUID(spatial_result["clicked_component_id"])
            if spatial_result["clicked_component_id"]
            else None
        ),
        distance_norm=spatial_result["distance_norm"],
        overlap_ambiguous=spatial_result["overlap_ambiguous"],
        hints_used=body.hints_used,
        spatial_score=spatial_result["spatial_score"],
        user_explanation=body.explanation,
        conceptual_status=conceptual_result["conceptual_status"],
        conceptual_score=conceptual_result.get("conceptual_score"),
        sentiment_aligned=conceptual_result.get("sentiment_aligned"),
        feedback_text=conceptual_result.get("feedback_text", ""),
        citations_json=conceptual_result.get("citations", []),
        latency_ms=int((time.time() - start) * 1000),
    )
    db.add(attempt)
    await db.flush()
    await db.refresh(attempt)

    # Build feedback
    if spatial_result["spatial_hit"]:
        feedback = "Correct! You found the right component."
    elif spatial_result["near_miss"]:
        feedback = "Close! You were very near the target component."
    else:
        feedback = "Not quite. Review the component's function and try again."

    if conceptual_result.get("feedback_text"):
        feedback += f" {conceptual_result['feedback_text']}"

    response = AssessmentAttemptResponse(
        attempt_id=attempt.id,
        assessment_item_id=item.id,
        target_component_id=target_comp.id,
        target_component_label=target_comp.label,
        clicked_component_id=(
            UUID(spatial_result["clicked_component_id"])
            if spatial_result["clicked_component_id"]
            else None
        ),
        clicked_component_label=clicked_label,
        spatial_hit=spatial_result["spatial_hit"],
        near_miss=spatial_result["near_miss"],
        distance_norm=spatial_result["distance_norm"],
        overlap_ambiguous=spatial_result["overlap_ambiguous"],
        spatial_score=spatial_result["spatial_score"],
        conceptual_status=conceptual_result["conceptual_status"],
        conceptual_score=conceptual_result.get("conceptual_score"),
        sentiment_aligned=conceptual_result.get("sentiment_aligned"),
        feedback_text=feedback,
        citations=conceptual_result.get("citations", []),
        latency_ms=attempt.latency_ms,
    )

    # Record idempotent result (savepoint closes the duplicate-key race).
    if idempotency_key:
        job = Job(
            workspace_id=target_comp.workspace_id,
            idempotency_key=idempotency_key,
            kind="attempt",
            status="completed",
            payload={"assessment_id": str(aid), "item_id": str(item.id)},
            result=response.model_dump(mode="json"),
        )
        try:
            async with db.begin_nested():
                db.add(job)
                await db.flush()
        except IntegrityError:
            job_result = await db.execute(select(Job).where(
                Job.idempotency_key == idempotency_key, Job.kind == "attempt"
            ))
            winner = job_result.scalar_one_or_none()
            if winner is not None and winner.result:
                return AssessmentAttemptResponse(**winner.result)
    return response
