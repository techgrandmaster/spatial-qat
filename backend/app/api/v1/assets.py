"""
Assets router: multipart upload and content serving.
"""

import asyncio
import hashlib
import io
import logging
import os
from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, UploadFile, status
from PIL import Image, ImageFile, ImageOps
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_session_id, load_owned_workspace
from app.config import settings
from app.db import get_db
from app.models.orm import Asset
from app.models.schemas import AssetKind, AssetResponse, AssetStatus

logger = logging.getLogger(__name__)

router = APIRouter()

# Storage directory for uploaded assets
STORAGE_DIR = Path(os.environ.get("SQAT_STORAGE_DIR", "data/uploads"))
STORAGE_DIR.mkdir(parents=True, exist_ok=True)

ALLOWED_IMAGE_MIMES = {"image/jpeg", "image/png", "image/webp"}
ALLOWED_PDF_MIMES = {"application/pdf"}
ALLOWED_AUDIO_MIMES = {"audio/webm", "audio/ogg", "audio/mp4", "audio/wav"}

# Pillow decompression-bomb guard (PRD §11): fail fast on gigapixel uploads.
Image.MAX_IMAGE_PIXELS = 50_000_000
ImageFile.LOAD_TRUNCATED_IMAGES = False

_READ_CHUNK_SIZE = 1024 * 1024  # 1 MiB streaming reads


def _detect_mime(data: bytes) -> str:
    """Detect MIME type from magic bytes using python-magic if available, else fallback."""
    try:
        import magic

        return magic.from_buffer(data, mime=True)
    except ImportError:
        # Fallback: basic header inspection (WebP is RIFF....WEBP, 12 bytes).
        if len(data) >= 4 and data[:4] == b"\x89PNG":
            return "image/png"
        if len(data) >= 2 and data[:2] == b"\xff\xd8":
            return "image/jpeg"
        if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
            return "image/webp"
        if len(data) >= 4 and data[:4] == b"%PDF":
            return "application/pdf"
        return "application/octet-stream"


async def _read_upload_capped(file: UploadFile, hard_limit_bytes: int) -> bytes:
    """Stream an upload with an early 413 instead of unbounded buffering."""
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await file.read(_READ_CHUNK_SIZE)
        if not chunk:
            break
        total += len(chunk)
        if total > hard_limit_bytes:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail=f"Upload exceeds {hard_limit_bytes // (1024 * 1024)}MB limit",
            )
        chunks.append(chunk)
    return b"".join(chunks)


def _strip_image_sync(data: bytes, mime: str) -> tuple[bytes, int, int]:
    """EXIF-normalize + strip in a worker thread. Returns (stored_bytes, w, h)."""
    img = Image.open(io.BytesIO(data))
    img = ImageOps.exif_transpose(img)
    if img.mode in ("RGBA", "LA"):
        pass
    elif img.mode == "P":
        img = img.convert("RGBA")
    else:
        img = img.convert("RGB") if mime == "image/jpeg" else img
    width_px, height_px = img.size
    out = io.BytesIO()
    if mime == "image/png":
        img.save(out, format="PNG")
        return out.getvalue(), width_px, height_px
    if mime == "image/webp":
        img.save(out, format="WEBP")
        return out.getvalue(), width_px, height_px
    img = img.convert("RGB")
    img.save(out, format="JPEG", quality=95)
    return out.getvalue(), width_px, height_px


def _pdf_page_count_sync(data: bytes) -> int | None:
    """Count PDF pages off the event loop."""
    try:
        import fitz  # PyMuPDF

        doc = fitz.open(stream=data, filetype="pdf")
        count = len(doc)
        doc.close()
        return count
    except Exception as exc:
        logger.warning("Failed to read PDF page count: %s", exc)
        return None


def _extension_for_mime(mime: str) -> str:
    if mime == "application/pdf":
        return ".pdf"
    if mime == "image/png":
        return ".png"
    if mime == "image/webp":
        return ".webp"
    return ".jpg"


def _compute_sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _get_storage_key(sha256: str, ext: str) -> str:
    return f"{sha256}{ext}"


@router.post("/workspaces/{workspace_id}/assets", response_model=AssetResponse)
async def upload_asset(
    workspace_id: str,
    file: UploadFile,
    kind: str = "board_image",
    session_id: UUID = Depends(get_session_id),
    db: AsyncSession = Depends(get_db),
):
    """Multipart upload for board image or PDF datasheet (MIME sniffing + SHA-256 deduplication)."""
    # Owner-only upload (404 missing, 403 foreign).
    workspace = await load_owned_workspace(workspace_id, session_id, db)
    wid = workspace.id

    # Streamed read with an early cap (largest configured kind limit).
    hard_limit = max(
        settings.max_image_size_mb, settings.max_pdf_size_mb, settings.max_audio_size_mb
    ) * 1024 * 1024
    raw_data = await _read_upload_capped(file, hard_limit)

    # Validate kind and MIME (sniff first bytes, not the filename).
    if kind in ("board_image", "mask_png", "som_image", "fix_illustration"):
        mime = _detect_mime(raw_data[:32])
        if mime not in ALLOWED_IMAGE_MIMES:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Unsupported image MIME: {mime}",
            )
        if len(raw_data) > settings.max_image_size_mb * 1024 * 1024:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Image exceeds {settings.max_image_size_mb}MB limit",
            )
        asset_kind = AssetKind(kind) if kind in AssetKind.__members__ else AssetKind.BOARD_IMAGE
    elif kind == "datasheet_pdf":
        mime = _detect_mime(raw_data[:32])
        if mime != "application/pdf":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Expected PDF, got {mime}",
            )
        if len(raw_data) > settings.max_pdf_size_mb * 1024 * 1024:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"PDF exceeds {settings.max_pdf_size_mb}MB limit",
            )
        asset_kind = AssetKind.DATASHEET_PDF
    elif kind == "audio_clip":
        mime = file.content_type or "audio/webm"
        if mime not in ALLOWED_AUDIO_MIMES:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Unsupported audio MIME: {mime}",
            )
        if len(raw_data) > settings.max_audio_size_mb * 1024 * 1024:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Audio exceeds {settings.max_audio_size_mb}MB limit",
            )
        asset_kind = AssetKind.AUDIO_CLIP
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown asset kind: {kind}",
        )

    # EXIF-normalize + strip images off the loop; store stripped bytes.
    file_data = raw_data
    width_px = None
    height_px = None
    page_count = None
    if mime in ALLOWED_IMAGE_MIMES:
        try:
            file_data, width_px, height_px = await asyncio.to_thread(
                _strip_image_sync, raw_data, mime
            )
        except Exception as exc:
            logger.warning("Failed to process image upload: %s", exc)
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid image file"
            ) from exc
    if mime == "application/pdf":
        page_count = await asyncio.to_thread(_pdf_page_count_sync, raw_data)

    # Compute SHA-256 for dedup (of stored bytes, so EXIF variants dedupe).
    sha256 = _compute_sha256(file_data)
    file_size = len(file_data)

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

    # Store file (extension preserves the sniffed MIME; write off the loop).
    ext = _extension_for_mime(mime)
    storage_key = _get_storage_key(sha256, ext)
    storage_path = STORAGE_DIR / storage_key
    if not storage_path.exists():
        await asyncio.to_thread(storage_path.write_bytes, file_data)

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
async def get_asset_content(
    asset_id: str,
    session_id: UUID = Depends(get_session_id),
    db: AsyncSession = Depends(get_db),
):
    """Serve authenticated binary image, PDF, or mask PNG content (owner only)."""
    try:
        aid = UUID(asset_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid UUID") from exc

    stmt = select(Asset).where(Asset.id == aid)
    result = await db.execute(stmt)
    asset = result.scalar_one_or_none()

    if asset is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Asset not found")

    await load_owned_workspace(str(asset.workspace_id), session_id, db)

    storage_path = STORAGE_DIR / asset.storage_key
    if not storage_path.exists():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Asset file not found")

    from fastapi.responses import FileResponse

    return FileResponse(
        path=str(storage_path),
        media_type=asset.mime_type,
        filename=asset.original_filename or asset.storage_key,
    )
