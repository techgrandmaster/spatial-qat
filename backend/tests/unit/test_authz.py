"""Ownership isolation tests (FR-01): 404 missing, 403 foreign, owner passes."""

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.api.deps import load_owned_workspace


def _db_returning(workspace_or_none):
    result = SimpleNamespace(scalar_one_or_none=lambda: workspace_or_none)
    return SimpleNamespace(execute=AsyncMock(return_value=result))


def _workspace(wid, session_id):
    return SimpleNamespace(id=wid, guest_session_id=session_id)


class TestLoadOwnedWorkspace:
    async def test_missing_workspace_raises_404(self):
        db = _db_returning(None)
        with pytest.raises(HTTPException) as exc:
            await load_owned_workspace(str(uuid.uuid4()), uuid.uuid4(), db)
        assert exc.value.status_code == 404

    async def test_foreign_workspace_raises_403(self):
        wid = uuid.uuid4()
        db = _db_returning(_workspace(wid, uuid.uuid4()))
        with pytest.raises(HTTPException) as exc:
            await load_owned_workspace(str(wid), uuid.uuid4(), db)
        assert exc.value.status_code == 403

    async def test_owned_workspace_returns_row(self):
        wid, sid = uuid.uuid4(), uuid.uuid4()
        ws = _workspace(wid, sid)
        db = _db_returning(ws)
        assert await load_owned_workspace(str(wid), sid, db) is ws

    async def test_invalid_uuid_raises_400(self):
        db = _db_returning(None)
        with pytest.raises(HTTPException) as exc:
            await load_owned_workspace("not-a-uuid", uuid.uuid4(), db)
        assert exc.value.status_code == 400
