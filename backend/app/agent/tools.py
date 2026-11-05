"""
Agent tool registry.
Tools: segment_text, segment_point, search_spec, get_page, list_components,
create_quiz, generate_fix_image.
"""

import logging
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


async def tool_search_spec(query: str, k: int = 5, db: AsyncSession | None = None, **kwargs) -> list[dict]:
    """search_spec(document_id, query, k): PostgreSQL full-text search over document_chunks."""
    from app.services.pdf_ingest import search_spec

    # Find the first PDF asset in the workspace
    if db and kwargs.get("workspace_id"):
        from sqlalchemy import select

        from app.models.orm import Asset

        stmt = select(Asset).where(
            Asset.workspace_id == kwargs["workspace_id"],
            Asset.kind == "datasheet_pdf",
        )
        result = await db.execute(stmt)
        pdf_asset = result.scalar_one_or_none()
        if pdf_asset:
            return await search_spec(pdf_asset.id, query, k, db=db)
    return []


async def tool_get_page(page_number: int, db: AsyncSession | None = None, **kwargs) -> dict | None:
    """get_page(document_id, page_number): Read complete text of a datasheet page."""
    from app.services.pdf_ingest import get_page

    if db and kwargs.get("workspace_id"):
        from sqlalchemy import select

        from app.models.orm import Asset

        stmt = select(Asset).where(
            Asset.workspace_id == kwargs["workspace_id"],
            Asset.kind == "datasheet_pdf",
        )
        result = await db.execute(stmt)
        pdf_asset = result.scalar_one_or_none()
        if pdf_asset:
            return await get_page(pdf_asset.id, page_number, db=db)
    return None


async def tool_list_components(workspace_id: UUID, db: AsyncSession | None = None, **kwargs) -> list[dict]:
    """list_components(workspace_id): List discovered components with bboxes and statuses."""
    if db:
        from sqlalchemy import select

        from app.models.orm import Component

        stmt = select(Component).where(Component.workspace_id == workspace_id)
        result = await db.execute(stmt)
        components = result.scalars().all()
        return [
            {
                "id": str(c.id),
                "label": c.label,
                "category": c.category,
                "verification_status": c.verification_status,
                "bbox": c.bbox,
            }
            for c in components
        ]
    return []


def build_tool_registry() -> dict:
    """Build the agent tool registry mapping names to async functions."""
    return {
        "search_spec": tool_search_spec,
        "get_page": tool_get_page,
        "list_components": tool_list_components,
    }
