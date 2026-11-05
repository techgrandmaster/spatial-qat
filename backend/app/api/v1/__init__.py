"""
FastAPI v1 API router aggregation.
"""

from fastapi import APIRouter

from app.api.v1 import (
    assessments,
    assets,
    components,
    qa,
    runs,
    sessions,
    workspaces,
)

router = APIRouter()

router.include_router(sessions.router, prefix="/sessions", tags=["sessions"])
router.include_router(
    workspaces.router, prefix="/workspaces", tags=["workspaces"]
)
router.include_router(assets.router, prefix="/assets", tags=["assets"])
router.include_router(
    components.router, prefix="/components", tags=["components"]
)
router.include_router(qa.router, prefix="/qa", tags=["qa"])
router.include_router(
    assessments.router, prefix="/assessments", tags=["assessments"]
)
router.include_router(runs.router, prefix="/runs", tags=["runs"])
