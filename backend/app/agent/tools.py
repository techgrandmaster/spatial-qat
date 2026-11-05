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


async def tool_segment_text(
    noun_phrase: str, db: AsyncSession | None = None, **kwargs
) -> dict:
    """segment_text(noun_phrase): run sam-3.1 text-prompt segmentation in this workspace."""
    from app.adapters.meta.sam import SamAdapter
    from app.config import settings

    workspace_id = kwargs.get("workspace_id")
    image_url = kwargs.get("image_url")
    if image_url is None and db is not None and workspace_id is not None:
        from sqlalchemy import select

        from app.models.orm import Asset

        stmt = select(Asset).where(
            Asset.workspace_id == workspace_id, Asset.kind == "board_image"
        )
        result = await db.execute(stmt)
        asset = result.scalar_one_or_none()
        if asset is not None:
            image_url = f"{settings.public_base_url.rstrip()}/api/v1/assets/{asset.id}/content"
    if image_url is None:
        return {"polygons": [], "error": "no_board_image"}
    adapter = SamAdapter()
    return await adapter.segment_text(noun_phrase, image_url, workspace_id=workspace_id, db=db)


async def tool_segment_point(
    x: float, y: float, label: str | None = None,
    db: AsyncSession | None = None, **kwargs,
) -> dict:
    """segment_point(x, y): run sam-3.1 point-prompt segmentation in this workspace."""
    from app.adapters.meta.sam import SamAdapter
    from app.config import settings

    workspace_id = kwargs.get("workspace_id")
    image_url = kwargs.get("image_url")
    if image_url is None and db is not None and workspace_id is not None:
        from sqlalchemy import select

        from app.models.orm import Asset

        stmt = select(Asset).where(
            Asset.workspace_id == workspace_id, Asset.kind == "board_image"
        )
        result = await db.execute(stmt)
        asset = result.scalar_one_or_none()
        if asset is not None:
            image_url = f"{settings.public_base_url.rstrip()}/api/v1/assets/{asset.id}/content"
    if image_url is None:
        return {"polygons": [], "error": "no_board_image"}
    adapter = SamAdapter()
    points = [{"x": x, "y": y, "positive": True}]
    return await adapter.segment_point(image_url, points, label=label, workspace_id=workspace_id, db=db)


async def tool_create_quiz(
    count: int = 3, difficulty: str = "medium",
    db: AsyncSession | None = None, **kwargs,
) -> dict:
    """create_quiz(count, difficulty): draft quiz items from workspace components."""
    from app.adapters.meta.spark import MuseSparkAdapter

    workspace_id = kwargs.get("workspace_id")
    components = await tool_list_components(workspace_id, db=db, **kwargs) if workspace_id else []
    context = "; ".join(f"{c['label']} ({c['category']})" for c in components[:20])
    adapter = MuseSparkAdapter()
    try:
        draft = await adapter.generate_quiz(context, int(count), str(difficulty), workspace_id=workspace_id, db=db)
        return draft.model_dump()
    except Exception as exc:
        logger.warning("create_quiz failed: %s", exc)
        return {"title": "Quiz draft", "items": [], "error": str(exc)}


async def tool_generate_fix_image(
    prompt: str, db: AsyncSession | None = None, **kwargs,
) -> dict:
    """generate_fix_image(prompt): render a labeled illustrative fix image (P2)."""
    from app.adapters.meta.image import MuseImageAdapter

    adapter = MuseImageAdapter()
    try:
        return await adapter.generate_illustration(prompt, kwargs.get("source_image_url"))
    except Exception as exc:
        logger.warning("generate_fix_image failed: %s", exc)
        return {"image_url": "", "prompt": prompt, "illustrative": True, "error": str(exc)}


def build_tool_registry() -> dict:
    """Build the agent tool registry mapping names to async functions."""
    return {
        "search_spec": tool_search_spec,
        "get_page": tool_get_page,
        "list_components": tool_list_components,
        "segment_text": tool_segment_text,
        "segment_point": tool_segment_point,
        "create_quiz": tool_create_quiz,
        "generate_fix_image": tool_generate_fix_image,
    }
