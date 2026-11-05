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

    for page_idx in range(total_pages):
        page = doc[page_idx]
        text = page.get_text("text")
        page_number = page_idx + 1

        if text.strip():
            has_text = True

        # Create document page record
        doc_page = DocumentPage(
            asset_id=asset_id,
            workspace_id=workspace_id,
            page_number=page_number,
            text_content=text,
        )
        db.add(doc_page)
        await db.flush()
        await db.refresh(doc_page)

        # Chunk the text
        if text.strip():
            chunks = _chunk_text(text)
            for idx, chunk_text in enumerate(chunks):
                # Find character offsets
                char_start = text.find(chunk_text, idx * (CHUNK_SIZE + CHUNK_OVERLAP) if idx > 0 else 0)
                if char_start == -1:
                    char_start = idx * CHUNK_SIZE
                char_end = char_start + len(chunk_text)

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
    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = start + CHUNK_SIZE
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= len(text):
            break
        # Find a good break point (whitespace) near the end
        break_pos = text.rfind(" ", start + CHUNK_SIZE - CHUNK_OVERLAP, end)
        if break_pos == -1:
            break_pos = end
        start = break_pos - CHUNK_OVERLAP
        if start <= start - CHUNK_OVERLAP:
            start = end - CHUNK_OVERLAP
    return chunks if chunks else [text[:CHUNK_SIZE]]


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
    """Internal FTS search implementation."""
    from sqlalchemy import text as sql_text

    # Search document_chunks using ts_rank_cd
    search_sql = sql_text("""
        SELECT dc.id, dc.page_number, dc.chunk_index, dc.text_content,
               dp.id as page_id,
               ts_rank_cd(dc.tsv, to_tsquery(:query)) as rank
        FROM document_chunks dc
        JOIN document_pages dp ON dc.page_id = dp.id
        WHERE dc.asset_id = :asset_id
          AND dc.tsv @@ to_tsquery(:query)
        ORDER BY rank DESC
        LIMIT :limit
    """)

    results = await db.execute(search_sql, {
        "asset_id": str(document_asset_id),
        "query": query,
        "limit": k,
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
