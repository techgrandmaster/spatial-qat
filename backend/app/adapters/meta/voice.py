"""
MuseVoiceAdapter: muse-voice-transcribe-1.0 speech-to-text (ASR).
POST /v1/asr/transcribe (P0) / wss://.../v1/asr/realtime (P2)
"""

import logging
import time

from app.adapters.meta.http import get_shared_client
from app.config import settings

logger = logging.getLogger(__name__)


class MuseVoiceAdapter:
    """Adapter for muse-voice-transcribe-1.0 via Meta Model API."""

    def __init__(self, api_key: str | None = None, base_url: str | None = None):
        self.api_key = api_key or settings.model_api_key
        self.base_url = base_url or settings.model_api_base_url
        self.model_id = settings.model_voice

    async def transcribe(self, audio_data: bytes, content_type: str = "audio/webm") -> str:
        """Transcribe audio to text via muse-voice-transcribe-1.0."""
        if not self.api_key:
            logger.warning("MODEL_API_KEY not set, returning mock transcription")
            return "This is a mock transcription for demonstration purposes."

        start = time.time()
        url = f"{self.base_url}/asr/transcribe"

        client = get_shared_client(timeout=60.0)
        resp = await client.post(
            url,
            files={"file": ("audio.webm", audio_data, content_type)},
            data={"model": self.model_id},
            headers={"Authorization": f"Bearer {self.api_key}"},
        )
        resp.raise_for_status()
        data = resp.json()

        latency_ms = int((time.time() - start) * 1000)
        logger.info("Transcription completed in %dms", latency_ms)
        return data.get("text", "")
