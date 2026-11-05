"""
Workspaces router: CRUD for isolated board workspaces.
"""

import logging
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.deps import get_session_id, load_owned_workspace
from app.config import settings
from app.db import get_db
from app.models.orm import Asset, Component, Workspace
from app.models.schemas import (
    AssetDTO,
    AssetKind,
    AssetStatus,
    ComponentDTO,
    ComponentEvidenceDTO,
    NormalizedBBox,
    WorkspaceCreate,
    WorkspaceResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter()


def _asset_to_dto(asset: Asset, workspace_id: UUID) -> AssetDTO:
    return AssetDTO(
        id=asset.id,
        workspace_id=workspace_id,
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


def _component_to_dto(component: Component, workspace_id: UUID) -> ComponentDTO:
    evidence: list[ComponentEvidenceDTO] = []
    for ev in getattr(component, "evidence", []) or []:
        evidence.append(ComponentEvidenceDTO(
            id=ev.id,
            component_id=ev.component_id,
            document_page_id=ev.document_page_id,
            chunk_id=ev.chunk_id,
            page_number=0,
            relation_type=ev.relation_type,
            quoted_text=ev.quoted_text,
            relevance=ev.relevance,
            verification_status=ev.verification_status,
        ))

    return ComponentDTO(
        id=component.id,
        workspace_id=workspace_id,
        image_asset_id=component.image_asset_id,
        som_index=component.som_index,
        label=component.label,
        noun_phrase=component.noun_phrase,
        category=component.category,
        role=component.role,
        description=component.description,
        bbox=NormalizedBBox(
            x=component.bbox_x,
            y=component.bbox_y,
            w=component.bbox_w,
            h=component.bbox_h,
        ),
        polygons=component.polygons or [],
        area_fraction=component.area_fraction or 0.0,
        confidence=component.confidence or 1.0,
        identity_source=component.identity_source,
        verification_status=component.verification_status,
        evidence=evidence,
    )


async def _workspace_to_dto(workspace: Workspace, db: AsyncSession) -> WorkspaceResponse:
    # Fetch budget spent for this workspace
    from app.models.orm import ModelCall

    budget_stmt = (
        select(func.coalesce(func.sum(ModelCall.cost_usd), 0.0))
        .where(ModelCall.workspace_id == workspace.id)
    )
    budget_result = await db.execute(budget_stmt)
    budget_spent = budget_result.scalar() or 0.0

    assets = [_asset_to_dto(a, workspace.id) for a in getattr(workspace, "assets", []) or []]
    components = [_component_to_dto(c, workspace.id) for c in getattr(workspace, "components", []) or []]

    return WorkspaceResponse(
        id=workspace.id,
        name=workspace.name,
        domain=workspace.domain,
        is_sample=workspace.is_sample,
        assets=assets,
        components=components,
        budget_spent_usd=budget_spent,
        budget_cap_usd=settings.budget_usd_hard_cap,
        created_at=workspace.created_at,
    )


@router.post("", response_model=WorkspaceResponse)
async def create_workspace(
    body: WorkspaceCreate,
    session_id: UUID = Depends(get_session_id),
    db: AsyncSession = Depends(get_db),
):
    """Create an isolated board workspace."""
    # For now, create with a placeholder session; in production, verify bearer token
    guest_session_id = session_id

    workspace = Workspace(
        guest_session_id=guest_session_id,
        name=body.name,
        domain=body.domain,
        is_sample=False,
    )
    db.add(workspace)
    await db.flush()
    await db.refresh(workspace)

    return await _workspace_to_dto(workspace, db)


@router.get("/{workspace_id}", response_model=WorkspaceResponse)
async def get_workspace(
    workspace_id: str,
    session_id: UUID = Depends(get_session_id),
    db: AsyncSession = Depends(get_db),
):
    """Retrieve a workspace with assets and components (owner only)."""
    workspace_ref = await load_owned_workspace(workspace_id, session_id, db)
    wid = workspace_ref.id

    stmt = (
        select(Workspace)
        .options(selectinload(Workspace.assets), selectinload(Workspace.components))
        .where(Workspace.id == wid)
    )
    result = await db.execute(stmt)
    workspace = result.scalar_one_or_none()

    if workspace is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace not found")

    return await _workspace_to_dto(workspace, db)
