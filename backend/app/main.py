"""
FastAPI app factory, CORS, SSE & lifespan.
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: initialize DB; Shutdown: close DB connection pool."""
    from app.db import close_db, init_db

    try:
        await init_db()
    except Exception as exc:
        # DB may already exist from schema.sql
        app.logger.warning("DB init skipped: %s", exc)
    yield
    await close_db()


app = FastAPI(
    title="Spatial-QAT API",
    version="0.1.0",
    lifespan=lifespan,
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routers (lazy import to avoid circular deps)
from app.api.v1 import router as api_router  # noqa: E402

app.include_router(api_router, prefix="/api/v1")


@app.get("/api/v1/health")
async def health():
    """Non-sensitive liveness check reporting DB connectivity, model reachability, and remaining budget cap."""
    from app.db import async_session_factory
    from app.models.schemas import HealthResponse
    from app.services.budget_guard import get_total_spent

    db_connected = False
    budget_spent = 0.0

    try:
        async with async_session_factory() as session:
            await session.execute("SELECT 1")
            db_connected = True
            budget_spent = await get_total_spent(session)
    except Exception:
        pass

    remaining = max(0, settings.budget_usd_hard_cap - budget_spent)

    return HealthResponse(
        status="ok" if db_connected else "degraded",
        database_connected=db_connected,
        meta_api_configured=bool(settings.model_api_key),
        budget_spent_usd=round(budget_spent, 2),
        budget_hard_cap_usd=settings.budget_usd_hard_cap,
        budget_remaining_usd=round(remaining, 2),
    )
