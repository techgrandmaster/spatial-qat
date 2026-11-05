"""
PDF ingestion service.
Extracts per-page text and character-offset chunks (500-1000 chars) via PyMuPDF.
Indexes in PostgreSQL full-text search (GIN index on tsvector).
Flags empty/scanned PDFs as needs_ocr.
"""

import logging
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.orm import Asset, DocumentChunk, DocumentPage
from app.models.schemas import AssetStatus

logger = logging.getLogger(__name__)

CHUNK_SIZE = 800
CHUNK_OVERLAP = 100


async def ingest_pdf(asset_id: UUID, workspace_id: UUID, db: AsyncSession) -> bool:
    """
    Extract per-page text and chunks from a PDF asset.
    Returns True if text was extracted, False if the PDF appears scanned (needs_ocr).
    """
    import fitz  # PyMuPDF

    from app.api.v1.assets import STORAGE_DIR

    stmt = select(Asset).where(Asset.id == asset_id)
    result = await db.execute(stmt)
    asset = result.scalar_one_or_none()
    if asset is None:
        raise ValueError(f"Asset {asset_id} not found")

    storage_path = STORAGE_DIR / asset.storage_key
    if not storage_path.exists():
        raise ValueError(f"Asset file not found: {storage_path}")

    doc = fitz.open(str(storage_path))
    total_pages = len(doc)
    has_text = False

    pages: list[DocumentPage] = []
    for page_idx in range(total_pages):
        page = doc[page_idx]
        text = page.get_text("text")
        page_number = page_idx + 1

        if text.strip():
            has_text = True

        # Create document page record (single flush later assigns all ids)
        doc_page = DocumentPage(
            asset_id=asset_id,
            workspace_id=workspace_id,
            page_number=page_number,
            text_content=text,
        )
        db.add(doc_page)
        pages.append((doc_page, text, page_number))

    # One round-trip for all pages instead of N flush+refresh cycles.
    await db.flush()
    for doc_page, text, page_number in pages:
        # Chunk the text with exact offsets (no repeated str.find scans).
        if text.strip():
            for idx, (chunk_text, char_start, char_end) in enumerate(_chunk_text_with_offsets(text)):
                chunk = DocumentChunk(
                    page_id=doc_page.id,
                    asset_id=asset_id,
                    workspace_id=workspace_id,
                    page_number=page_number,
                    chunk_index=idx,
                    char_start=char_start,
                    char_end=char_end,
                    text_content=chunk_text,
                )
                db.add(chunk)
    # One round-trip for all chunks.
    await db.flush()

    doc.close()

    # Update asset status
    if not has_text:
        asset.status = AssetStatus.NEEDS_OCR.value
        logger.warning("PDF %s appears scanned (no text extracted), marked needs_ocr", asset_id)
    else:
        asset.status = AssetStatus.READY.value

    return has_text


def _chunk_text(text: str) -> list[str]:
    """Split text into overlapping chunks of CHUNK_SIZE characters."""
    return [chunk for chunk, _, _ in _chunk_text_with_offsets(text)]


def _chunk_text_with_offsets(text: str) -> list[tuple[str, int, int]]:
    """Split text into overlapping chunks, tracking exact char offsets."""
    chunks: list[tuple[str, int, int]] = []
    start = 0
    text_len = len(text)
    while start < text_len:
        end = min(start + CHUNK_SIZE, text_len)
        raw = text[start:end]
        chunk = raw.strip()
        if chunk:
            # Adjust offsets for stripped whitespace without rescanning.
            leading = len(raw) - len(raw.lstrip())
            trailing = len(raw) - len(raw.rstrip())
            chunks.append((chunk, start + leading, end - trailing))
        if end >= text_len:
            break
        # Find a good break point (whitespace) near the end
        break_pos = text.rfind(" ", start + CHUNK_SIZE - CHUNK_OVERLAP, end)
        if break_pos == -1 or break_pos <= start:
            break_pos = end
        new_start = break_pos - CHUNK_OVERLAP
        if new_start <= start:
            # Guarantee forward progress (fixes dead `start <= start - OVERLAP` branch).
            new_start = end - CHUNK_OVERLAP
        if new_start <= start:
            new_start = start + max(CHUNK_SIZE - CHUNK_OVERLAP, 1)
        start = new_start
    if not chunks and text.strip():
        stripped = text.strip()
        leading = len(text) - len(text.lstrip())
        return [(stripped, leading, leading + len(stripped))]
    return chunks


async def search_spec(document_asset_id: UUID, query: str, k: int = 5, db: AsyncSession | None = None):
    """
    Execute PostgreSQL full-text search over document_chunks.
    Returns top-k bounded snippets with page numbers.
    """
    if db is None:
        from app.db import async_session_factory

        async with async_session_factory() as db:
            return await _search_spec_impl(document_asset_id, query, k, db)
    else:
        return await _search_spec_impl(document_asset_id, query, k, db)


async def _search_spec_impl(document_asset_id: UUID, query: str, k: int, db: AsyncSession) -> list[dict]:
    """Internal FTS search implementation (plain-English safe, bounded)."""
    from sqlalchemy import text as sql_text

    cleaned = (query or "").strip()
    if not cleaned:
        return []
    # Clamp top-k: prevents LIMIT injection / unbounded scans.
    limit = max(1, min(int(k) if isinstance(k, int) else 5, 50))

    # plainto_tsquery parses plain English (to_tsquery raised on unquoted text).
    # tsv is GENERATED ALWAYS STORED (db/schema.sql), so no trigger is needed.
    search_sql = sql_text("""
        SELECT dc.id, dc.page_number, dc.chunk_index, dc.text_content,
               dp.id as page_id,
               ts_rank_cd(dc.tsv, plainto_tsquery('english', :query)) as rank
        FROM document_chunks dc
        JOIN document_pages dp ON dc.page_id = dp.id
        WHERE dc.asset_id = :asset_id
          AND dc.tsv @@ plainto_tsquery('english', :query)
        ORDER BY rank DESC
        LIMIT :limit
    """)

    results = await db.execute(search_sql, {
        "asset_id": str(document_asset_id),
        "query": cleaned[:500],
        "limit": limit,
    })

    snippets = []
    for row in results:
        snippets.append({
            "chunk_id": str(row[0]),
            "page_number": row[1],
            "chunk_index": row[2],
            "text": row[3],
            "page_id": str(row[4]),
            "rank": row[5],
        })

    return snippets


async def get_page(asset_id: UUID, page_number: int, db: AsyncSession | None = None) -> dict | None:
    """Read the complete text and chunks of a specific datasheet page."""
    if db is None:
        from app.db import async_session_factory

        async with async_session_factory() as db:
            return await _get_page_impl(asset_id, page_number, db)
    else:
        return await _get_page_impl(asset_id, page_number, db)


async def _get_page_impl(asset_id: UUID, page_number: int, db: AsyncSession) -> dict | None:
    stmt = (
        select(DocumentPage)
        .where(DocumentPage.asset_id == asset_id, DocumentPage.page_number == page_number)
    )
    result = await db.execute(stmt)
    page = result.scalar_one_or_none()
    if page is None:
        return None

    return {
        "page_id": str(page.id),
        "page_number": page.page_number,
        "text_content": page.text_content,
    }
