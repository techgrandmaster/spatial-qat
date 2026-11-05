"""
Assets router: multipart upload and content serving.
"""

import hashlib
import io
import logging
import os
from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, UploadFile, status
from PIL import Image
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import get_db
from app.models.orm import Asset, Workspace
from app.models.schemas import AssetKind, AssetResponse, AssetStatus

logger = logging.getLogger(__name__)

router = APIRouter()

# Storage directory for uploaded assets
STORAGE_DIR = Path(os.environ.get("SQAT_STORAGE_DIR", "data/uploads"))
STORAGE_DIR.mkdir(parents=True, exist_ok=True)

ALLOWED_IMAGE_MIMES = {"image/jpeg", "image/png", "image/webp"}
ALLOWED_PDF_MIMES = {"application/pdf"}
ALLOWED_AUDIO_MIMES = {"audio/webm", "audio/ogg", "audio/mp4", "audio/wav"}


def _detect_mime(data: bytes) -> str:
    """Detect MIME type from magic bytes using python-magic if available, else fallback."""
    try:
        import magic

        return magic.from_buffer(data, mime=True)
    except ImportError:
        # Fallback: basic header inspection
        if data[:4] == b"\x89PNG":
            return "image/png"
        if data[:2] == b"\xff\xd8":
            return "image/jpeg"
        if data[:8] in (b"RIFF", b"WEBP"):
            return "image/webp"
        if data[:4] == b"%PDF":
            return "application/pdf"
        return "application/octet-stream"


def _compute_sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _get_storage_key(sha256: str, ext: str) -> str:
    return f"{sha256}{ext}"


@router.post("/workspaces/{workspace_id}/assets", response_model=AssetResponse)
async def upload_asset(
    workspace_id: str,
    file: UploadFile,
    kind: str = "board_image",
    db: AsyncSession = Depends(get_db),
):
    """Multipart upload for board image or PDF datasheet (MIME sniffing + SHA-256 deduplication)."""
    # Validate workspace exists
    try:
        wid = UUID(workspace_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid UUID") from exc

    ws_stmt = select(Workspace).where(Workspace.id == wid)
    ws_result = await db.execute(ws_stmt)
    if ws_result.scalar_one_or_none() is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace not found")

    # Read file data
    file_data = await file.read()
    file_size = len(file_data)

    # Validate kind and MIME
    if kind in ("board_image", "mask_png", "som_image", "fix_illustration"):
        mime = _detect_mime(file_data)
        if mime not in ALLOWED_IMAGE_MIMES and mime != "image/png":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Unsupported image MIME: {mime}",
            )
        if file_size > settings.max_image_size_mb * 1024 * 1024:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Image exceeds {settings.max_image_size_mb}MB limit",
            )
        asset_kind = AssetKind(kind) if kind in AssetKind.__members__ else AssetKind.BOARD_IMAGE
    elif kind == "datasheet_pdf":
        mime = _detect_mime(file_data)
        if mime != "application/pdf":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Expected PDF, got {mime}",
            )
        if file_size > settings.max_pdf_size_mb * 1024 * 1024:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"PDF exceeds {settings.max_pdf_size_mb}MB limit",
            )
        asset_kind = AssetKind.DATASHEET_PDF
    elif kind == "audio_clip":
        mime = _detect_mime(file_data)
        if mime not in ALLOWED_AUDIO_MIMES:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Unsupported audio MIME: {mime}",
            )
        asset_kind = AssetKind.AUDIO_CLIP
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown asset kind: {kind}",
        )

    # Compute SHA-256 for dedup
    sha256 = _compute_sha256(file_data)

    # Check for duplicate
    dup_stmt = select(Asset).where(Asset.workspace_id == wid, Asset.sha256 == sha256)
    dup_result = await db.execute(dup_stmt)
    existing = dup_result.scalar_one_or_none()
    if existing is not None:
        # Return existing asset
        return AssetResponse(
            id=existing.id,
            workspace_id=existing.workspace_id,
            kind=AssetKind(existing.kind),
            sha256=existing.sha256,
            mime_type=existing.mime_type,
            original_filename=existing.original_filename,
            content_url=f"/api/v1/assets/{existing.id}/content",
            width_px=existing.width_px,
            height_px=existing.height_px,
            page_count=existing.page_count,
            status=AssetStatus(existing.status),
            license=existing.license,
            source_url=existing.source_url,
            attribution=existing.attribution,
            created_at=existing.created_at,
        )

    # Extract dimensions for images
    width_px = None
    height_px = None
    page_count = None

    if mime in ALLOWED_IMAGE_MIMES:
        try:
            img = Image.open(io.BytesIO(file_data))
            # Strip EXIF orientation
            img = img.copy()
            width_px, height_px = img.size
        except Exception as exc:
            logger.warning("Failed to read image dimensions: %s", exc)

    if mime == "application/pdf":
        try:
            import fitz  # PyMuPDF

            doc = fitz.open(stream=file_data, filetype="pdf")
            page_count = len(doc)
            doc.close()
        except Exception as exc:
            logger.warning("Failed to read PDF page count: %s", exc)

    # Store file
    ext = ".pdf" if mime == "application/pdf" else ".png" if mime == "image/png" else ".jpg"
    storage_key = _get_storage_key(sha256, ext)
    storage_path = STORAGE_DIR / storage_key
    storage_path.write_bytes(file_data)

    # Create asset record
    asset = Asset(
        workspace_id=wid,
        kind=asset_kind.value,
        sha256=sha256,
        mime_type=mime,
        original_filename=file.filename or "",
        storage_key=storage_key,
        byte_size=file_size,
        width_px=width_px,
        height_px=height_px,
        page_count=page_count,
        status=AssetStatus.READY.value,
    )
    db.add(asset)
    await db.flush()
    await db.refresh(asset)

    return AssetResponse(
        id=asset.id,
        workspace_id=asset.workspace_id,
        kind=AssetKind(asset.kind),
        sha256=asset.sha256,
        mime_type=asset.mime_type,
        original_filename=asset.original_filename,
        content_url=f"/api/v1/assets/{asset.id}/content",
        width_px=asset.width_px,
        height_px=asset.height_px,
        page_count=asset.page_count,
        status=AssetStatus(asset.status),
        license=asset.license,
        source_url=asset.source_url,
        attribution=asset.attribution,
        created_at=asset.created_at,
    )


@router.get("/{asset_id}/content")
async def get_asset_content(asset_id: str, db: AsyncSession = Depends(get_db)):
    """Serve authenticated binary image, PDF, or mask PNG content."""
    try:
        aid = UUID(asset_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid UUID") from exc

    stmt = select(Asset).where(Asset.id == aid)
    result = await db.execute(stmt)
    asset = result.scalar_one_or_none()

    if asset is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Asset not found")

    storage_path = STORAGE_DIR / asset.storage_key
    if not storage_path.exists():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Asset file not found")

    from fastapi.responses import FileResponse

    return FileResponse(
        path=str(storage_path),
        media_type=asset.mime_type,
        filename=asset.original_filename or asset.storage_key,
    )
