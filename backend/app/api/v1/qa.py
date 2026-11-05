"""
Q&A router: submit text queries, execute agent loop, return structured answers.
"""

import uuid
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, UploadFile, WebSocket, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.meta.voice import MuseVoiceAdapter
from app.agent.loop import AgentToolRegistry, run_agent_loop, run_analyze_pipeline
from app.agent.tools import build_tool_registry
from app.api.deps import get_session_id, load_owned_workspace
from app.db import get_db
from app.models.orm import AgentRun, Asset, Message
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
    session_id: UUID = Depends(get_session_id),
    db: AsyncSession = Depends(get_db),
):
    """Submit a text query; executes the agent loop and returns run_id / structured AnswerPayload."""
    workspace = await load_owned_workspace(workspace_id, session_id, db)
    wid = workspace.id

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
    session_id: UUID = Depends(get_session_id),
    db: AsyncSession = Depends(get_db),
):
    """Upload push-to-talk audio; transcribes via muse-voice-transcribe-1.0 and triggers grounded Q&A."""
    workspace = await load_owned_workspace(workspace_id, session_id, db)
    wid = workspace.id

    # Transcribe audio (streamed with an early cap, not unbounded buffering).
    from app.api.v1.assets import _read_upload_capped
    from app.config import settings as _settings

    audio_data = await _read_upload_capped(
        audio, _settings.max_audio_size_mb * 1024 * 1024
    )
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
    session_id: UUID = Depends(get_session_id),
    db: AsyncSession = Depends(get_db),
):
    """Trigger full board analysis (muse-spark-1.3 proposal → parallel sam-3.1 → SoM grounding)."""
    workspace = await load_owned_workspace(workspace_id, session_id, db)
    wid = workspace.id

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
    session_id: UUID = Depends(get_session_id),
    db: AsyncSession = Depends(get_db),
):
    """Trigger on-demand sam-3.1 point/box prompt segmentation (P1)."""
    from app.adapters.meta.sam import SamAdapter
    from app.models.orm import Component

    workspace = await load_owned_workspace(workspace_id, session_id, db)
    wid = workspace.id

    # Find image asset
    assets_stmt = select(Asset).where(Asset.workspace_id == wid)
    assets_result = await db.execute(assets_stmt)
    assets = assets_result.scalars().all()
    image_asset = next((a for a in assets if a.kind == "board_image"), None)
    if image_asset is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No board image found")

    sam = SamAdapter()
    from app.config import settings as _settings

    image_url = f"{_settings.public_base_url.rstrip()}/api/v1/assets/{image_asset.id}/content"
    points = [{"x": x, "y": y, "positive": True}]
    result = await sam.segment_point(image_url, points, label=label, workspace_id=wid, db=db)

    polygons = result.get("polygons", [])
    if polygons:
        from app.agent.loop import _area_fraction_from_polygons, _bbox_from_polygons

        bbox_x, bbox_y, bbox_w, bbox_h = _bbox_from_polygons(polygons)
        comp = Component(
            workspace_id=wid,
            image_asset_id=image_asset.id,
            label=label or f"Point segment at ({x:.2f}, {y:.2f})",
            noun_phrase=label or "point-segmented object",
            category="other",
            bbox_x=bbox_x,
            bbox_y=bbox_y,
            bbox_w=bbox_w,
            bbox_h=bbox_h,
            bbox={"x": bbox_x, "y": bbox_y, "w": bbox_w, "h": bbox_h},
            polygons=polygons,
            area_fraction=_area_fraction_from_polygons(polygons, bbox_w * bbox_h),
            confidence=float(result.get("confidence", 0.7) or 0.7),
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


@router.post("/workspaces/{workspace_id}/illustrations")
async def generate_fix_illustration(
    workspace_id: str,
    prompt: str,
    source_asset_id: str | None = None,
    session_id: UUID = Depends(get_session_id),
    db: AsyncSession = Depends(get_db),
):
    """Generate a labeled illustrative fix image via muse-image-1.0 (P2, FR-13).

    Output is always marked illustrative and never used as a component mask.
    """
    from app.adapters.meta.image import MuseImageAdapter

    workspace = await load_owned_workspace(workspace_id, session_id, db)

    source_url: str | None = None
    if source_asset_id is not None:
        try:
            sid = UUID(source_asset_id)
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid UUID") from exc
        stmt = select(Asset).where(Asset.id == sid, Asset.workspace_id == workspace.id)
        result = await db.execute(stmt)
        asset = result.scalar_one_or_none()
        if asset is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Source asset not found")
        from app.config import settings as _settings

        source_url = f"{_settings.public_base_url.rstrip()}/api/v1/assets/{asset.id}/content"

    adapter = MuseImageAdapter()
    try:
        illustration = await adapter.generate_illustration(prompt, source_url)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail=f"Image generation failed: {exc}"
        ) from exc
    return illustration


@router.websocket("/workspaces/{workspace_id}/voice-stream")
async def voice_stream(websocket: WebSocket, workspace_id: str):
    """Realtime push-to-talk proxy (P2): binary audio frames in, transcript text out.

    Best-effort streaming wrapper over muse-voice-transcribe-1.0: each binary
    message is transcribed and the text is sent back. Authentication uses the
    `token` query parameter (the guest Bearer value) since browsers cannot set
    WebSocket Authorization headers.
    """
    from fastapi import WebSocketDisconnect

    from app.api.v1.sessions import verify_session_token
    from app.db import async_session_factory

    await websocket.accept()
    try:
        raw_token = websocket.query_params.get("token", "")
        async with async_session_factory() as db:
            session_id = await verify_session_token(raw_token, db)
            if session_id is None:
                await websocket.send_json({"error": "invalid or expired session"})
                return
            workspace = await load_owned_workspace(workspace_id, session_id, db)
        adapter = MuseVoiceAdapter()
        while True:
            try:
                message = await websocket.receive()
            except WebSocketDisconnect:
                break
            data = message.get("bytes")
            if data is None:
                text = message.get("text")
                if text in (None, "close"):
                    break
                await websocket.send_json({"error": "binary audio frames expected"})
                continue
            if len(data) > 10 * 1024 * 1024:
                await websocket.send_json({"error": "audio frame exceeds 10MB"})
                continue
            try:
                transcript = await adapter.transcribe(bytes(data), "audio/webm")
            except Exception as exc:
                await websocket.send_json({"error": f"transcription failed: {exc}"})
                continue
            await websocket.send_json(
                {"workspace_id": str(workspace.id), "transcript": transcript}
            )
    finally:
        try:
            await websocket.close()
        except Exception:
            pass
