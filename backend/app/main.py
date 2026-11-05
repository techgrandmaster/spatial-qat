"""
FastAPI app factory, CORS, SSE & lifespan.
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.config import settings

_ERROR_CODE_BY_STATUS = {
    400: "validation_error",
    401: "unauthorized",
    403: "unauthorized",
    404: "not_found",
    429: "budget_exceeded",
}


def _error_envelope(code: str, message: str, request: Request | None = None) -> dict:
    return {
        "error": {
            "code": code,
            "message": message,
            "request_id": getattr(getattr(request, "state", None), "request_id", None),
            "details": None,
        }
    }


async def _http_exception_handler(request: Request, exc: StarletteHTTPException):
    status = int(getattr(exc, "status_code", 500) or 500)
    code = _ERROR_CODE_BY_STATUS.get(status, "upstream_error")
    if status == 429 and "budget" in str(getattr(exc, "detail", "")).lower():
        code = "budget_exceeded"
    return JSONResponse(
        status_code=status,
        content=_error_envelope(code, str(getattr(exc, "detail", "error")), request),
    )


async def _validation_exception_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=422,
        content=_error_envelope("validation_error", str(exc), request),
    )


async def _unhandled_exception_handler(request: Request, exc: Exception):
    import logging as _logging

    _logging.getLogger(__name__).exception("Unhandled error: %s", exc)
    return JSONResponse(
        status_code=500,
        content=_error_envelope("upstream_error", "Internal server error", request),
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: initialize DB; Shutdown: close DB connection pool."""
    import logging as _logging

    from app.db import close_db, init_db

    _logger = _logging.getLogger(__name__)
    try:
        await init_db()
    except Exception as exc:
        # DB may already exist from schema.sql
        _logger.warning("DB init skipped: %s", exc)
    yield
    from app.adapters.meta.http import aclose_shared_client as _aclose

    try:
        await _aclose()
    except Exception as exc:
        _logger.warning("Shared HTTP client close skipped: %s", exc)
    await close_db()


app = FastAPI(
    title="Spatial-QAT API",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_exception_handler(StarletteHTTPException, _http_exception_handler)
app.add_exception_handler(RequestValidationError, _validation_exception_handler)
app.add_exception_handler(Exception, _unhandled_exception_handler)

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
    from sqlalchemy import text as _text

    from app.db import async_session_factory
    from app.models.schemas import HealthResponse
    from app.services.budget_guard import get_total_spent

    db_connected = False
    budget_spent = 0.0

    try:
        async with async_session_factory() as session:
            await session.execute(_text("SELECT 1"))
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
