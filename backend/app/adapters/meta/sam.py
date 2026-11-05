"""
SamAdapter: sam-3.1 concept & point/box segmentation.
POST /v1/responses (streamed)
One short concept noun phrase or point/box prompt per request.
Bounded concurrency (MAX_SAM_CONCURRENCY=4).
Parses special-token output into binary mask PNG + normalized polygons.
"""

import asyncio
import hashlib
import json
import logging
import time
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession
from tenacity import retry, stop_after_attempt, wait_exponential_jitter

from app.adapters.meta.http import get_shared_client
from app.config import settings

logger = logging.getLogger(__name__)

# Global cap: per-instance semaphores never limited concurrent SAM calls.
_sam_semaphore: asyncio.Semaphore | None = None


def _get_sam_semaphore() -> asyncio.Semaphore:
    global _sam_semaphore
    if _sam_semaphore is None:
        _sam_semaphore = asyncio.Semaphore(settings.max_sam_concurrency)
    return _sam_semaphore


class SamAdapter:
    """Adapter for sam-3.1 via Meta Model API."""

    def __init__(self, api_key: str | None = None, base_url: str | None = None):
        self.api_key = api_key or settings.model_api_key
        self.base_url = base_url or settings.model_api_base_url
        self.model_id = settings.model_sam
        self.semaphore = _get_sam_semaphore()

    def _compute_cache_key(self, operation: str, request_data: dict) -> str:
        digest = hashlib.sha256()
        digest.update(operation.encode())
        digest.update(self.model_id.encode())
        digest.update(str(request_data.get("prompt", "")).encode())
        digest.update(str(request_data.get("image", "")).encode())
        points = request_data.get("points")
        if points is not None:
            digest.update(json.dumps(points, sort_keys=True, default=str).encode())
        return digest.hexdigest()

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential_jitter(initial=1, max=10),
        reraise=True,
    )
    async def _call(
        self,
        prompt: str,
        image_url: str,
        points: list[dict] | None = None,
        workspace_id: UUID | None = None,
        db: AsyncSession | None = None,
    ) -> dict:
        """Call sam-3.1 API with bounded concurrency."""
        async with self.semaphore:
            cache_key = self._compute_cache_key(
                "segment", {"prompt": prompt, "image": image_url, "points": points}
            )

            # Check cache (SELECT-only; no placeholder row).
            if db:
                from app.services.budget_guard import find_cached

                cached = await find_cached(db, cache_key)
                if cached is not None and cached.response_json and cached.response_json.get("polygons"):
                    return cached.response_json

            if not self.api_key:
                return self._mock_segment(prompt, image_url)

            start = time.time()
            payload = {
                "model": self.model_id,
                "input": {"image_url": image_url, "prompt": prompt},
            }
            if points:
                payload["input"]["points"] = points

            client = get_shared_client(timeout=60.0)
            resp = await client.post(
                f"{self.base_url}/responses",
                json=payload,
                headers={"Authorization": f"Bearer {self.api_key}"},
            )
            resp.raise_for_status()
            data = resp.json()

            latency_ms = int((time.time() - start) * 1000)
            polygons = data.get("polygons", [])
            cost = data.get("cost", 0.0)

            if db:
                from app.services.budget_guard import record_cost

                await record_cost(
                    db=db, cache_key=cache_key, model=self.model_id,
                    endpoint="/v1/responses", operation="segment",
                    request_json={"prompt": prompt},
                    response_json={"polygons": polygons},
                    cost_usd=cost, latency_ms=latency_ms, workspace_id=workspace_id,
                )

            return {"polygons": polygons, "cost": cost, "latency_ms": latency_ms}

    def _mock_segment(self, prompt: str, image_url: str) -> dict:
        """Mock segmentation result when API is not configured."""
        # Generate a simple rectangular polygon based on prompt hash
        h = int(hashlib.md5(prompt.encode()).hexdigest()[:8], 16)
        cx = 0.3 + (h % 40) / 100
        cy = 0.3 + ((h >> 8) % 40) / 100
        size = 0.05 + (h % 20) / 200
        return {
            "polygons": [[
                [cx - size, cy - size],
                [cx + size, cy - size],
                [cx + size, cy + size],
                [cx - size, cy + size],
                [cx - size, cy - size],
            ]],
            "cost": 0.0,
            "latency_ms": 500,
        }

    async def segment_text(
        self,
        noun_phrase: str,
        image_url: str,
        workspace_id: UUID | None = None,
        db: AsyncSession | None = None,
    ) -> dict:
        """Segment a single concept noun phrase."""
        return await self._call(noun_phrase, image_url, workspace_id=workspace_id, db=db)

    async def segment_point(
        self,
        image_url: str,
        points: list[dict],
        label: str | None = None,
        workspace_id: UUID | None = None,
        db: AsyncSession | None = None,
    ) -> dict:
        """Segment using point prompts."""
        prompt = label or "object"
        return await self._call(prompt, image_url, points=points, workspace_id=workspace_id, db=db)

    async def segment_batch(
        self,
        noun_phrases: list[str],
        image_url: str,
        workspace_id: UUID | None = None,
        db: AsyncSession | None = None,
    ) -> list[dict]:
        """Run parallel sam-3.1 calls capped by MAX_SAM_CONCURRENCY."""
        tasks = [
            self.segment_text(phrase, image_url, workspace_id=workspace_id, db=db)
            for phrase in noun_phrases
        ]
        return await asyncio.gather(*tasks, return_exceptions=True)
