"""
MuseImageAdapter: muse-image-1.0 educational fix illustration (P2).
POST /v1/images/edits
Strictly for separately labeled conceptual illustrations.
"""

import logging

import httpx

from app.config import settings

logger = logging.getLogger(__name__)


class MuseImageAdapter:
    """Adapter for muse-image-1.0 via Meta Model API (P2 feature)."""

    def __init__(self, api_key: str | None = None, base_url: str | None = None):
        self.api_key = api_key or settings.model_api_key
        self.base_url = base_url or settings.model_api_base_url
        self.model_id = settings.model_image

    async def generate_illustration(self, prompt: str, source_image_url: str | None = None) -> dict:
        """
        Generate an educational fix illustration.
        Returns dict with image_url and metadata.
        """
        if not self.api_key:
            logger.warning("MODEL_API_KEY not set, returning mock illustration")
            return {
                "image_url": "",
                "prompt": prompt,
                "illustrative": True,
            }

        url = f"{self.base_url}/images/edits"
        payload = {
            "model": self.model_id,
            "prompt": prompt,
            "input_image": source_image_url,
        }

        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                url,
                json=payload,
                headers={"Authorization": f"Bearer {self.api_key}"},
            )
            resp.raise_for_status()
            data = resp.json()

        return {
            "image_url": data.get("url", ""),
            "prompt": prompt,
            "illustrative": True,
        }
