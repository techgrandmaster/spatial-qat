"""
Q&A router: submit text queries, execute agent loop, return structured answers.
"""

import uuid
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, UploadFile, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.meta.voice import MuseVoiceAdapter
from app.agent.loop import AgentToolRegistry, run_agent_loop, run_analyze_pipeline
from app.agent.tools import build_tool_registry
from app.db import get_db
from app.models.orm import AgentRun, Asset, Message, Workspace
from app.models.schemas import (
    AnswerPayload,
    QuestionRequest,
    RunResponse,
    UncertaintyLevel,
)

router = APIRouter()


@router.post("/workspaces/{workspace_id}/questions", response_model=RunResponse)
async def ask_question(
    workspace_id: str,
    body: QuestionRequest,
    db: AsyncSession = Depends(get_db),
):
    """Submit a text query; executes the agent loop and returns run_id / structured AnswerPayload."""
    try:
        wid = UUID(workspace_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid UUID") from exc

    # Verify workspace
    ws_stmt = select(Workspace).where(Workspace.id == wid)
    ws_result = await db.execute(ws_stmt)
    if ws_result.scalar_one_or_none() is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace not found")

    # Build tool registry
    tool_registry = AgentToolRegistry()
    for name, fn in build_tool_registry().items():
        tool_registry.register(name, fn)

    # Run agent loop
    answer = await run_agent_loop(
        question=body.question_text,
        workspace_id=wid,
        db=db,
        tool_registry=tool_registry,
        context_data={"workspace_id": wid},
    )

    # Persist run and message
    run_id = uuid.uuid4()
    message_id = uuid.uuid4()

    run = AgentRun(
        id=run_id,
        workspace_id=wid,
        kind="qa",
        status="completed",
        model="muse-spark-1.3",
        totals={"spatial_hit": True},
    )
    db.add(run)

    message = Message(
        id=message_id,
        workspace_id=wid,
        role="user",
        content=body.question_text,
        run_id=run_id,
        payload=answer.model_dump(),
    )
    db.add(message)
    await db.flush()

    return RunResponse(
        run_id=run_id,
        message_id=message_id,
        answer=answer,
    )


@router.post("/workspaces/{workspace_id}/voice", response_model=RunResponse)
async def voice_question(
    workspace_id: str,
    audio: UploadFile,
    db: AsyncSession = Depends(get_db),
):
    """Upload push-to-talk audio; transcribes via muse-voice-transcribe-1.0 and triggers grounded Q&A."""
    try:
        wid = UUID(workspace_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid UUID") from exc

    # Transcribe audio
    audio_data = await audio.read()
    voice_adapter = MuseVoiceAdapter()
    transcript = await voice_adapter.transcribe(audio_data, audio.content_type or "audio/webm")

    # Run Q&A with transcribed text
    tool_registry = AgentToolRegistry()
    for name, fn in build_tool_registry().items():
        tool_registry.register(name, fn)

    answer = await run_agent_loop(
        question=transcript,
        workspace_id=wid,
        db=db,
        tool_registry=tool_registry,
        context_data={"workspace_id": wid},
    )

    run_id = uuid.uuid4()
    message_id = uuid.uuid4()

    run = AgentRun(
        id=run_id,
        workspace_id=wid,
        kind="voice_qa",
        status="completed",
        model="muse-spark-1.3",
        totals={},
    )
    db.add(run)

    message = Message(
        id=message_id,
        workspace_id=wid,
        role="user",
        content=transcript,
        modality="voice",
        run_id=run_id,
        payload=answer.model_dump(),
    )
    db.add(message)
    await db.flush()

    return RunResponse(
        run_id=run_id,
        message_id=message_id,
        transcript=transcript,
        answer=answer,
    )


@router.post("/workspaces/{workspace_id}/analyze", response_model=RunResponse)
async def analyze_board(
    workspace_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Trigger full board analysis (muse-spark-1.3 proposal → parallel sam-3.1 → SoM grounding)."""
    try:
        wid = UUID(workspace_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid UUID") from exc

    # Find image and PDF assets
    assets_stmt = select(Asset).where(Asset.workspace_id == wid)
    assets_result = await db.execute(assets_stmt)
    assets = assets_result.scalars().all()

    image_asset = None
    pdf_asset = None
    for a in assets:
        if a.kind in ("board_image", "mask_png", "som_image") and image_asset is None:
            image_asset = a
        elif a.kind == "datasheet_pdf":
            pdf_asset = a

    if image_asset is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No board image found in workspace",
        )

    # Run analysis pipeline
    result = await run_analyze_pipeline(
        workspace_id=wid,
        image_asset_id=image_asset.id,
        pdf_asset_id=pdf_asset.id if pdf_asset else None,
        db=db,
    )

    run_id = result["run_id"]
    message_id = uuid.uuid4()

    run = AgentRun(
        id=run_id,
        workspace_id=wid,
        kind="analyze",
        status="completed",
        model="muse-spark-1.3",
        totals={"components_found": result["components_created"]},
    )
    db.add(run)
    await db.flush()

    return RunResponse(
        run_id=run_id,
        message_id=message_id,
        answer=AnswerPayload(
            text=f"Analysis complete. Found {result['components_created']} components.",
            uncertainty=UncertaintyLevel.LOW,
        ),
    )


@router.post("/workspaces/{workspace_id}/segment-point", response_model=RunResponse)
async def segment_point(
    workspace_id: str,
    x: float,
    y: float,
    label: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    """Trigger on-demand sam-3.1 point/box prompt segmentation (P1)."""
    from app.adapters.meta.sam import SamAdapter
    from app.models.orm import Component
    from app.services.som_tagger import compute_centroid

    try:
        wid = UUID(workspace_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid UUID") from exc

    # Find image asset
    assets_stmt = select(Asset).where(Asset.workspace_id == wid)
    assets_result = await db.execute(assets_stmt)
    assets = assets_result.scalars().all()
    image_asset = next((a for a in assets if a.kind == "board_image"), None)
    if image_asset is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No board image found")

    sam = SamAdapter()
    image_url = f"/api/v1/assets/{image_asset.id}/content"
    points = [{"x": x, "y": y, "positive": True}]
    result = await sam.segment_point(image_url, points, label=label, workspace_id=wid, db=db)

    polygons = result.get("polygons", [])
    if polygons:
        centroid = compute_centroid(polygons)
        comp = Component(
            workspace_id=wid,
            image_asset_id=image_asset.id,
            label=label or f"Point segment at ({x:.2f}, {y:.2f})",
            noun_phrase=label or "point-segmented object",
            category="other",
            bbox_x=centroid[0] - 0.05,
            bbox_y=centroid[1] - 0.05,
            bbox_w=0.1,
            bbox_h=0.1,
            bbox={"x": centroid[0] - 0.05, "y": centroid[1] - 0.05, "w": 0.1, "h": 0.1},
            polygons=polygons,
            area_fraction=0.01,
            confidence=0.7,
            identity_source="point_prompt",
            verification_status="proposed",
        )
        db.add(comp)
        await db.flush()

    run_id = uuid.uuid4()
    message_id = uuid.uuid4()

    return RunResponse(
        run_id=run_id,
        message_id=message_id,
        answer=AnswerPayload(
            text=f"Segmented region at ({x:.2f}, {y:.2f}).",
            uncertainty=UncertaintyLevel.LOW,
        ),
    )
