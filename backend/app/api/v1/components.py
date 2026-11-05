"""
Components router: confirm, rename, reject component mappings.
"""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_session_id, load_owned_workspace
from app.db import get_db
from app.models.orm import Component, ComponentEdge, ComponentEvidence
from app.models.schemas import (
    ComponentEvidenceDTO,
    ComponentResponse,
    ComponentUpdate,
    NormalizedBBox,
)

router = APIRouter()


def _component_to_response(comp: Component) -> ComponentResponse:
    evidence = []
    for ev in getattr(comp, "evidence", []) or []:
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

    return ComponentResponse(
        id=comp.id,
        workspace_id=comp.workspace_id,
        image_asset_id=comp.image_asset_id,
        som_index=comp.som_index,
        label=comp.label,
        noun_phrase=comp.noun_phrase,
        category=comp.category,
        role=comp.role,
        description=comp.description,
        bbox=NormalizedBBox(
            x=comp.bbox_x, y=comp.bbox_y, w=comp.bbox_w, h=comp.bbox_h,
        ),
        polygons=comp.polygons or [],
        area_fraction=comp.area_fraction or 0.0,
        confidence=comp.confidence or 1.0,
        identity_source=comp.identity_source,
        verification_status=comp.verification_status,
        evidence=evidence,
    )


@router.get("/{component_id}", response_model=ComponentResponse)
async def get_component(
    component_id: str,
    session_id: UUID = Depends(get_session_id),
    db: AsyncSession = Depends(get_db),
):
    """Get a component by ID (owner only; 404 missing, 403 foreign)."""
    try:
        cid = UUID(component_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid UUID") from exc

    stmt = select(Component).where(Component.id == cid)
    result = await db.execute(stmt)
    comp = result.scalar_one_or_none()
    if comp is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Component not found")

    await load_owned_workspace(str(comp.workspace_id), session_id, db)

    return _component_to_response(comp)


@router.patch("/{component_id}", response_model=ComponentResponse)
async def update_component(
    component_id: str,
    body: ComponentUpdate,
    session_id: UUID = Depends(get_session_id),
    db: AsyncSession = Depends(get_db),
):
    """Confirm, rename, or reject a proposed component mapping (owner only)."""
    try:
        cid = UUID(component_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid UUID") from exc

    stmt = select(Component).where(Component.id == cid)
    result = await db.execute(stmt)
    comp = result.scalar_one_or_none()
    if comp is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Component not found")

    await load_owned_workspace(str(comp.workspace_id), session_id, db)

    if body.label is not None:
        comp.label = body.label
    if body.category is not None:
        comp.category = body.category.value if hasattr(body.category, "value") else body.category
    if body.role is not None:
        comp.role = body.role
    if body.description is not None:
        comp.description = body.description
    if body.verification_status is not None:
        vs = body.verification_status
        comp.verification_status = vs.value if hasattr(vs, "value") else vs

    await db.flush()
    await db.refresh(comp)
    return _component_to_response(comp)


@router.post("/{component_id}/evidence")
async def add_evidence(
    component_id: str,
    page_id: str,
    chunk_id: str | None = None,
    quoted_text: str = "",
    relation_type: str = "specification",
    session_id: UUID = Depends(get_session_id),
    db: AsyncSession = Depends(get_db),
):
    """Link or verify datasheet chunk evidence for a component (owner only)."""
    try:
        cid = UUID(component_id)
        pid = UUID(page_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid UUID") from exc

    comp_stmt = select(Component).where(Component.id == cid)
    comp_result = await db.execute(comp_stmt)
    comp = comp_result.scalar_one_or_none()
    if comp is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Component not found")

    await load_owned_workspace(str(comp.workspace_id), session_id, db)

    evidence = ComponentEvidence(
        component_id=cid,
        document_page_id=pid,
        chunk_id=UUID(chunk_id) if chunk_id else None,
        relation_type=relation_type,
        quoted_text=quoted_text,
        relevance=1.0,
        verification_status="verified",
    )
    db.add(evidence)
    await db.flush()
    await db.refresh(evidence)

    return {"id": str(evidence.id), "component_id": str(evidence.component_id)}


@router.post("/{component_id}/edges")
async def create_edge(
    component_id: str,
    dst_component_id: str,
    relation_type: str = "connected_to",
    evidence_page_id: str | None = None,
    evidence_quote: str | None = None,
    session_id: UUID = Depends(get_session_id),
    db: AsyncSession = Depends(get_db),
):
    """Create a directed edge src -> dst with optional datasheet evidence (FR-12, P1)."""
    try:
        src_id = UUID(component_id)
        dst_id = UUID(dst_component_id)
        ev_page = UUID(evidence_page_id) if evidence_page_id else None
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid UUID") from exc

    src_stmt = select(Component).where(Component.id == src_id)
    src = (await db.execute(src_stmt)).scalar_one_or_none()
    if src is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Component not found")
    await load_owned_workspace(str(src.workspace_id), session_id, db)

    dst_stmt = select(Component).where(Component.id == dst_id)
    dst = (await db.execute(dst_stmt)).scalar_one_or_none()
    if dst is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Target component not found")
    if dst.workspace_id != src.workspace_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Components must share a workspace")

    edge = ComponentEdge(
        workspace_id=src.workspace_id,
        src_component_id=src_id,
        dst_component_id=dst_id,
        relation_type=relation_type,
        evidence_page_id=ev_page,
        evidence_quote=evidence_quote,
        verification_status="proposed",
    )
    db.add(edge)
    await db.flush()
    await db.refresh(edge)

    return {
        "id": str(edge.id),
        "src_component_id": str(edge.src_component_id),
        "dst_component_id": str(edge.dst_component_id),
        "relation_type": edge.relation_type,
    }


@router.get("/{component_id}/edges")
async def list_edges(
    component_id: str,
    session_id: UUID = Depends(get_session_id),
    db: AsyncSession = Depends(get_db),
):
    """List outgoing edges for a component (owner only, FR-12)."""
    try:
        cid = UUID(component_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid UUID") from exc

    comp_stmt = select(Component).where(Component.id == cid)
    comp = (await db.execute(comp_stmt)).scalar_one_or_none()
    if comp is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Component not found")
    await load_owned_workspace(str(comp.workspace_id), session_id, db)

    edge_stmt = select(ComponentEdge).where(
        (ComponentEdge.src_component_id == cid) | (ComponentEdge.dst_component_id == cid)
    )
    edges = (await db.execute(edge_stmt)).scalars().all()
    return [
        {
            "id": str(e.id),
            "src_component_id": str(e.src_component_id),
            "dst_component_id": str(e.dst_component_id),
            "relation_type": e.relation_type,
            "verification_status": e.verification_status,
        }
        for e in edges
    ]
