"""
MuseSparkAdapter: muse-spark-1.3 for planning, SoM grounding, Q&A & rubric grading.
POST /v1/responses
1M-token context; constrained structured JSON output (text.format).
"""

import hashlib
import json
import logging
import time
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession
from tenacity import retry, stop_after_attempt, wait_exponential_jitter

from app.adapters.meta.http import get_shared_client
from app.config import settings
from app.models.schemas import (
    AnswerPayload,
    ComponentProposal,
    ConceptualGradeOutput,
    GroundedBoardSynthesis,
    QuizDraft,
)

logger = logging.getLogger(__name__)


class MuseSparkAdapter:
    """Adapter for muse-spark-1.3 via Meta Model API."""

    def __init__(self, api_key: str | None = None, base_url: str | None = None):
        self.api_key = api_key or settings.model_api_key
        self.base_url = base_url or settings.model_api_base_url
        self.model_id = settings.model_spark

    def _compute_cache_key(self, operation: str, request_data: dict) -> str:
        """Bounded SHA-256 key: streams message hashes, no full 1M-ctx JSON dump."""
        digest = hashlib.sha256()
        digest.update(operation.encode())
        digest.update(self.model_id.encode())
        messages = request_data.get("messages", [])
        digest.update(str(len(messages)).encode())
        for msg in messages:
            if not isinstance(msg, dict):
                digest.update(str(msg).encode())
                continue
            digest.update(str(msg.get("role", "")).encode())
            content = msg.get("content", "")
            if not isinstance(content, str):
                content = json.dumps(content, sort_keys=True, default=str)
            content_bytes = content.encode()
            digest.update(str(len(content_bytes)).encode())
            # Chunked update avoids a second giant concatenated copy.
            for i in range(0, len(content_bytes), 8192):
                digest.update(content_bytes[i : i + 8192])
        extra = {k: v for k, v in request_data.items() if k != "messages"}
        if extra:
            digest.update(json.dumps(extra, sort_keys=True, default=str).encode())
        return digest.hexdigest()

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential_jitter(initial=1, max=10),
        reraise=True,
    )
    async def _call(
        self,
        messages: list[dict],
        operation: str = "chat",
        structured_output: type | None = None,
        workspace_id: UUID | None = None,
        db: AsyncSession | None = None,
    ) -> dict:
        """
        Call the Meta Model API with retry and caching.
        Returns the parsed response JSON.
        """
        cache_key = self._compute_cache_key(operation, {"messages": messages})

        # Check cache first (SELECT-only; no placeholder row).
        if db:
            from app.services.budget_guard import find_cached

            cached = await find_cached(db, cache_key)
            # If this is a cache hit (record already existed), return cached response
            if cached is not None and cached.response_json and cached.response_json.get("content"):
                return cached.response_json

        # Check if API key is configured
        if not self.api_key:
            logger.warning("MODEL_API_KEY not set, returning mock response")
            return self._mock_response(operation, messages)

        # Call the API
        start = time.time()
        url = f"{self.base_url}/responses"
        payload = {
            "model": self.model_id,
            "messages": messages,
            "text": {"format": {"type": "json_object"}},
        }

        client = get_shared_client(timeout=60.0)
        resp = await client.post(
            url,
            json=payload,
            headers={"Authorization": f"Bearer {self.api_key}"},
        )
        resp.raise_for_status()
        data = resp.json()

        latency_ms = int((time.time() - start) * 1000)

        # Extract content from response
        content = ""
        usage = {}
        if "output" in data:
            output = data["output"]
            if isinstance(output, list) and output:
                content = output[0].get("content", "")
            elif isinstance(output, dict):
                content = output.get("content", "")
        usage = data.get("usage", {})
        cost = data.get("cost", 0.0)

        # Cache the result
        if db:
            from app.services.budget_guard import record_cost

            await record_cost(
                db=db,
                cache_key=cache_key,
                model=self.model_id,
                endpoint="/v1/responses",
                operation=operation,
                request_json={"messages_count": len(messages)},
                response_json={"content": content},
                cost_usd=cost,
                latency_ms=latency_ms,
                usage=usage,
                workspace_id=workspace_id,
            )

        return {"content": content, "usage": usage, "cost": cost}

    def _mock_response(self, operation: str, messages: list[dict]) -> dict:
        """Return mock responses when API is not configured."""
        if operation == "propose_components":
            return {
                "content": json.dumps({
                    "board_name": "Demo Board",
                    "phrases": [
                        {
                            "noun_phrase": "USB-C connector",
                            "canonical_label": "USB-C Power Connector",
                            "category": "connector_header",
                            "expected_count": 1,
                            "datasheet_page_hints": [1],
                        }
                    ],
                })
            }
        elif operation == "som_grounding":
            return {
                "content": json.dumps({
                    "grounded_components": [
                        {
                            "som_index": 1,
                            "label": "USB-C Connector",
                            "category": "connector_header",
                            "role": "Power and data input connector",
                            "description": "USB Type-C connector for power delivery and data communication.",
                            "citations": [],
                        }
                    ],
                })
            }
        elif operation == "grade":
            return {
                "content": json.dumps({
                    "score": 0.8,
                    "matched_concepts": ["power"],
                    "missing_concepts": [],
                    "feedback": "Good explanation covering key concepts.",
                    "citations": [],
                })
            }
        else:
            return {
                "content": json.dumps({
                    "text": "This is a mock response for demonstration purposes.",
                    "citations": [],
                    "highlight_component_ids": [],
                    "uncertainty": "low",
                    "ungrounded": False,
                })
            }

    async def propose_components(
        self,
        image_description: str,
        datasheet_summary: str,
        workspace_id: UUID | None = None,
        db: AsyncSession | None = None,
    ) -> ComponentProposal:
        """Stage 1: Propose 1-20 component noun phrases from board image + datasheet."""
        system_prompt = """You are a hardware component identification assistant.
Analyze the board image description and datasheet summary to identify distinct components.
Return a JSON object with board_name and phrases (1-20 items).
Each phrase has: noun_phrase, canonical_label, category, expected_count, datasheet_page_hints."""

        messages = [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": f"Board image: {image_description}\n\nDatasheet summary: {datasheet_summary}",
            },
        ]

        result = await self._call(messages, "propose_components", workspace_id=workspace_id, db=db)
        content = result.get("content", "{}")

        try:
            data = json.loads(content)
            return ComponentProposal(**data)
        except Exception as exc:
            logger.error("Failed to parse component proposal: %s", exc)
            raise ValueError(f"Invalid component proposal: {exc}")

    async def ground_som(
        self,
        som_description: str,
        datasheet_context: str,
        workspace_id: UUID | None = None,
        db: AsyncSession | None = None,
    ) -> GroundedBoardSynthesis:
        """Stage 3: Link SoM-indexed masks to datasheet citations."""
        system_prompt = """You are grounding Set-of-Mark [1..N] labeled components to datasheet evidence.
For each SoM index, provide label, category, role, description, and citations.
Return a JSON object with grounded_components array."""

        messages = [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": f"SoM image description: {som_description}\n\nDatasheet context: {datasheet_context}",
            },
        ]

        result = await self._call(messages, "som_grounding", workspace_id=workspace_id, db=db)
        content = result.get("content", "{}")

        try:
            data = json.loads(content)
            return GroundedBoardSynthesis(**data)
        except Exception as exc:
            logger.error("Failed to parse SoM grounding: %s", exc)
            raise ValueError(f"Invalid SoM grounding: {exc}")

    async def answer_question(
        self,
        question: str,
        context: str,
        workspace_id: UUID | None = None,
        db: AsyncSession | None = None,
    ) -> AnswerPayload:
        """Run Q&A with grounded citations."""
        system_prompt = """You are a hardware troubleshooting tutor. Answer questions grounded in the datasheet.
Return a JSON object with: text, citations, highlight_component_ids, uncertainty, ungrounded.
If evidence is insufficient, set ungrounded=true and flag uncertainty."""

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"Context: {context}\n\nQuestion: {question}"},
        ]

        result = await self._call(messages, "qa", workspace_id=workspace_id, db=db)
        content = result.get("content", "{}")

        try:
            data = json.loads(content)
            return AnswerPayload(**data)
        except Exception as exc:
            logger.error("Failed to parse answer: %s", exc)
            return AnswerPayload(
                text="Unable to process question. Please try again.",
                uncertainty="high",
                ungrounded=True,
            )

    async def generate_quiz(
        self,
        components_context: str,
        count: int,
        difficulty: str,
        workspace_id: UUID | None = None,
        db: AsyncSession | None = None,
    ) -> QuizDraft:
        """Generate spatial quiz items."""
        system_prompt = """Generate a spatial active-recall quiz.
Return a JSON object with title, difficulty, and items array.
Each item has: target_component_id, kind, prompt, hint, rubric."""

        messages = [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": f"Components: {components_context}\nGenerate {count} questions at {difficulty} difficulty.",
            },
        ]

        result = await self._call(messages, "quiz", workspace_id=workspace_id, db=db)
        content = result.get("content", "{}")

        try:
            data = json.loads(content)
            return QuizDraft(**data)
        except Exception as exc:
            logger.error("Failed to parse quiz: %s", exc)
            raise ValueError(f"Invalid quiz draft: {exc}")

    async def grade_explanation(
        self,
        explanation: str,
        rubric: dict,
        workspace_id: UUID | None = None,
        db: AsyncSession | None = None,
    ) -> dict:
        """Grade a learner's conceptual explanation against a rubric."""
        system_prompt = """Grade a learner's explanation against the rubric.
Return a JSON object with: score (0-1), matched_concepts, missing_concepts, feedback, citations."""

        messages = [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": f"Rubric: {json.dumps(rubric)}\n\nExplanation: {explanation}",
            },
        ]

        result = await self._call(messages, "grade", workspace_id=workspace_id, db=db)
        content = result.get("content", "{}")

        try:
            data = json.loads(content)
            grade = ConceptualGradeOutput(**data)
            return {
                "conceptual_status": "graded",
                "conceptual_score": grade.score,
                "sentiment_aligned": True,
                "feedback_text": grade.feedback,
                "citations": [c.model_dump() for c in grade.citations],
            }
        except Exception as exc:
            logger.error("Failed to parse grade: %s", exc)
            raise
