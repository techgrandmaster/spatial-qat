"""
Spatial-QAT: Authoritative Pydantic v2 Data & Transport Schemas (backend/app/models/schemas.py)
Companion to: db/schema.sql, docs/PRD.md, apps/web/src/api/types.ts
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# =============================================================================
# 1. CANONICAL ENUMS & CONSTANTS
# =============================================================================

class ErrorCode(StrEnum):
    VALIDATION_ERROR = "validation_error"
    UNAUTHORIZED = "unauthorized"
    NOT_FOUND = "not_found"
    BUDGET_EXCEEDED = "budget_exceeded"
    RATE_LIMITED = "rate_limited"
    UPSTREAM_ERROR = "upstream_error"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    NEEDS_OCR = "needs_ocr"


class AssetKind(StrEnum):
    BOARD_IMAGE = "board_image"
    DATASHEET_PDF = "datasheet_pdf"
    MASK_PNG = "mask_png"
    SOM_IMAGE = "som_image"
    AUDIO_CLIP = "audio_clip"
    FIX_ILLUSTRATION = "fix_illustration"


class AssetStatus(StrEnum):
    PENDING = "pending"
    PROCESSING = "processing"
    READY = "ready"
    NEEDS_OCR = "needs_ocr"
    FAILED = "failed"


class ComponentCategory(StrEnum):
    IC_MICROCONTROLLER = "ic_microcontroller"
    VOLTAGE_REGULATOR = "voltage_regulator"
    CONNECTOR_HEADER = "connector_header"
    PASSIVE_COMPONENT = "passive_component"
    SENSOR_ACTUATOR = "sensor_actuator"
    POWER_PROTECTION = "power_protection"
    CRYSTAL_OSCILLATOR = "crystal_oscillator"
    SWITCH_BUTTON = "switch_button"
    LED_INDICATOR = "led_indicator"
    OTHER = "other"


class VerificationStatus(StrEnum):
    PROPOSED = "proposed"
    VERIFIED = "verified"
    REJECTED = "rejected"


class IdentitySource(StrEnum):
    AGENT = "agent"
    USER = "user"
    POINT_PROMPT = "point_prompt"
    SEED_FIXTURE = "seed_fixture"


class EvidenceRelationType(StrEnum):
    SPECIFICATION = "specification"
    PINOUT = "pinout"
    OPERATING_LIMIT = "operating_limit"
    TROUBLESHOOTING = "troubleshooting"
    SAFETY_WARNING = "safety_warning"


class UncertaintyLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"


class QuizKind(StrEnum):
    LOCATE = "locate"       # P0: Click component on board
    IDENTIFY = "identify"   # P1: Name highlighted component
    EXPLAIN = "explain"     # P2: Click component + explain function


class DifficultyLevel(StrEnum):
    EASY = "easy"
    MEDIUM = "medium"
    HARD = "hard"


class ConceptualStatus(StrEnum):
    GRADED = "graded"
    NOT_GRADED = "not_graded"
    SKIPPED = "skipped"


class SSEEventType(StrEnum):
    RUN_STARTED = "run.started"
    STEP_STARTED = "step.started"
    TOOL_CALLED = "tool.called"
    TOOL_RESULT = "tool.result"
    COMPONENT_FOUND = "component.found"
    MESSAGE_DELTA = "message.delta"
    ANSWER_FINAL = "answer.final"
    RUN_COMPLETED = "run.completed"
    RUN_FAILED = "run.failed"


# =============================================================================
# 2. NORMALIZED GEOMETRY PRIMITIVES ([0.0, 1.0] TOP-LEFT ORIGIN)
# =============================================================================

class NormalizedPoint(BaseModel):
    """Normalized 2D point in [0.0, 1.0] relative to EXIF-corrected image dimensions."""
    model_config = ConfigDict(extra="forbid")

    x: float = Field(..., ge=0.0, le=1.0, description="Normalized X coordinate in [0, 1]")
    y: float = Field(..., ge=0.0, le=1.0, description="Normalized Y coordinate in [0, 1]")


class NormalizedBBox(BaseModel):
    """Normalized bounding box {x, y, w, h} in [0.0, 1.0] with top-left origin."""
    model_config = ConfigDict(extra="forbid")

    x: float = Field(..., ge=0.0, le=1.0, description="Top-left X coordinate in [0, 1]")
    y: float = Field(..., ge=0.0, le=1.0, description="Top-left Y coordinate in [0, 1]")
    w: float = Field(..., gt=0.0, le=1.0, description="Width in (0, 1]")
    h: float = Field(..., gt=0.0, le=1.0, description="Height in (0, 1]")

    @model_validator(mode="after")
    def check_bounds(self) -> NormalizedBBox:
        if self.x + self.w > 1.0001 or self.y + self.h > 1.0001:
            raise ValueError("Bounding box (x + w, y + h) must not exceed normalized [0.0, 1.0] image bounds.")
        return self


# Type alias matching PRD Section 12:
# polygons: list of polygons -> list of rings (outer first, holes next) -> [x, y] pairs
PolygonRing = list[tuple[float, float]]
PolygonWithHoles = list[PolygonRing]
MultiPolygonNormalized = list[PolygonWithHoles]


class SAMPromptPoint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    x: float = Field(..., ge=0.0, le=1.0)
    y: float = Field(..., ge=0.0, le=1.0)
    positive: bool = Field(default=True, description="True for foreground click, False for background exclusion")


class SegmentPointRequest(BaseModel):
    """P1: On-demand click-to-segment request payload."""
    model_config = ConfigDict(extra="forbid")

    image_asset_id: UUID
    points: list[SAMPromptPoint] = Field(..., min_length=1, max_length=10)
    suggested_label: str | None = Field(default=None, max_length=120)


# =============================================================================
# 3. STRUCTURED AGENT & MODEL OUTPUT CONTRACTS (muse-spark-1.3)
# =============================================================================

class ProposedComponentPhrase(BaseModel):
    """Single item in Muse Spark 1.3 ComponentProposal output (FR-3 / FR-04)."""
    model_config = ConfigDict(extra="forbid")

    noun_phrase: str = Field(
        ...,
        min_length=2,
        max_length=80,
        description=(
            "Short visual concept noun phrase for SAM 3.1 segmentation "
            "(e.g., 'USB-C connector', 'voltage regulator IC')"
        )
    )
    canonical_label: str = Field(
        ...,
        min_length=2,
        max_length=120,
        description="Specific component name from datasheet if identifiable (e.g., 'AMS1117-3.3 LDO Regulator')"
    )
    category: ComponentCategory = Field(default=ComponentCategory.OTHER)
    expected_count: int = Field(default=1, ge=1, le=32)
    datasheet_page_hints: list[int] = Field(
        default_factory=list,
        description="Candidate 1-based datasheet page numbers mentioning this part"
    )


class ComponentProposal(BaseModel):
    """Structured output enforced on muse-spark-1.3 during Stage 1 board analysis."""
    model_config = ConfigDict(extra="forbid")

    board_name: str = Field(..., min_length=2, max_length=160)
    phrases: list[ProposedComponentPhrase] = Field(
        ...,
        min_length=1,
        max_length=20,
        description="Between 1 and 20 distinct component phrases to segment via sam-3.1"
    )


class CitationItem(BaseModel):
    """Verifiable datasheet reference anchored to a stored page and chunk ID."""
    model_config = ConfigDict(extra="forbid")

    document_page_id: UUID | None = None
    chunk_id: UUID | None = None
    page_number: int = Field(..., ge=1, description="1-based PDF page number")
    quote: str = Field(..., min_length=3, max_length=600, description="Exact supporting snippet from the datasheet")
    relation_type: EvidenceRelationType = Field(default=EvidenceRelationType.SPECIFICATION)


class GroundedComponentSpec(BaseModel):
    """Structured output from muse-spark-1.3 Set-of-Mark [1..N] grounding step."""
    model_config = ConfigDict(extra="forbid")

    som_index: int = Field(..., ge=1, description="1-based Set-of-Mark numeric badge drawn at mask centroid")
    label: str = Field(..., min_length=2, max_length=160)
    category: ComponentCategory = Field(default=ComponentCategory.OTHER)
    role: str = Field(..., min_length=5, max_length=240, description="Concise functional role on the board")
    description: str = Field(..., min_length=10, max_length=1200, description="Detailed datasheet-grounded summary")
    citations: list[CitationItem] = Field(default_factory=list)


class GroundedBoardSynthesis(BaseModel):
    """Batch Set-of-Mark grounding output from muse-spark-1.3."""
    model_config = ConfigDict(extra="forbid")

    grounded_components: list[GroundedComponentSpec] = Field(..., min_length=1)


class AnswerPayload(BaseModel):
    """Structured output enforced on muse-spark-1.3 at the end of the 8-step Q&A agent loop (FR-7 / FR-08)."""
    model_config = ConfigDict(extra="forbid")

    text: str = Field(..., min_length=5, description="Clear, learner-facing explanation grounded in the datasheet")
    citations: list[CitationItem] = Field(
        default_factory=list,
        description="Verified page/chunk citations supporting the claims"
    )
    highlight_component_ids: list[UUID] = Field(
        default_factory=list,
        description="Component UUIDs to highlight on the interactive SVG board overlay"
    )
    uncertainty: UncertaintyLevel = Field(default=UncertaintyLevel.LOW)
    ungrounded: bool = Field(
        default=False,
        description="True if datasheet evidence was insufficient or schema repair fallback occurred"
    )


class QuizRubricSpec(BaseModel):
    """Private server-side rubric for grading conceptual explanations."""
    model_config = ConfigDict(extra="forbid")

    expected_concepts: list[str] = Field(
        ..., min_length=1,
        description="Core functional points the learner should mention",
    )
    required_page_numbers: list[int] = Field(default_factory=list)
    reference_answer: str = Field(..., min_length=10)
    common_misconceptions: list[str] = Field(default_factory=list)


class QuizItemDraft(BaseModel):
    """Single quiz question drafted by muse-spark-1.3 (create_quiz tool)."""
    model_config = ConfigDict(extra="forbid")

    target_component_id: UUID = Field(..., description="Verified component UUID the learner must click")
    kind: QuizKind = Field(default=QuizKind.LOCATE)
    prompt: str = Field(
        ...,
        min_length=12,
        max_length=400,
        description=(
            "Diagnostic question asking the learner to click the component "
            "by function without giving away its visual position"
        )
    )
    hint: str | None = Field(default=None, max_length=240)
    rubric: QuizRubricSpec


class QuizDraft(BaseModel):
    """Structured output enforced on muse-spark-1.3 when generating a spatial quiz."""
    model_config = ConfigDict(extra="forbid")

    title: str = Field(..., min_length=3, max_length=160)
    difficulty: DifficultyLevel = Field(default=DifficultyLevel.MEDIUM)
    items: list[QuizItemDraft] = Field(..., min_length=1, max_length=10)


class ConceptualGradeOutput(BaseModel):
    """Structured output from muse-spark-1.3 when grading a learner's conceptual explanation (FR-032)."""
    model_config = ConfigDict(extra="forbid")

    score: float = Field(..., ge=0.0, le=1.0, description="Normalized conceptual accuracy score in [0.0, 1.0]")
    matched_concepts: list[str] = Field(default_factory=list)
    missing_concepts: list[str] = Field(default_factory=list)
    feedback: str = Field(..., min_length=10, max_length=800)
    citations: list[CitationItem] = Field(default_factory=list)


# =============================================================================
# 4. REST API TRANSPORT DTOs (Workspace, Assets, Components, Q&A)
# =============================================================================

class GuestSessionResponse(BaseModel):
    session_id: UUID
    bearer_token: str = Field(..., description="Shown once upon creation; hashed at rest in guest_sessions")
    expires_at: datetime


class WorkspaceCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1, max_length=160)
    domain: Literal["electronics", "schematic", "other"] = "electronics"


class AssetDTO(BaseModel):
    id: UUID
    workspace_id: UUID
    kind: AssetKind
    sha256: str
    mime_type: str
    original_filename: str
    content_url: str = Field(..., description="Authenticated /api/v1/assets/{id}/content route")
    width_px: int | None = None
    height_px: int | None = None
    page_count: int | None = None
    status: AssetStatus
    license: str | None = None
    source_url: str | None = None
    attribution: str | None = None
    created_at: datetime


class ComponentEvidenceDTO(BaseModel):
    id: UUID
    component_id: UUID
    document_page_id: UUID
    chunk_id: UUID | None = None
    page_number: int
    relation_type: EvidenceRelationType
    quoted_text: str
    relevance: float
    verification_status: VerificationStatus


class ComponentDTO(BaseModel):
    id: UUID
    workspace_id: UUID
    image_asset_id: UUID
    som_index: int | None = None
    label: str
    noun_phrase: str
    category: ComponentCategory
    role: str | None = None
    description: str | None = None
    bbox: NormalizedBBox
    polygons: MultiPolygonNormalized
    area_fraction: float
    confidence: float
    identity_source: IdentitySource
    verification_status: VerificationStatus
    evidence: list[ComponentEvidenceDTO] = Field(default_factory=list)


class ComponentPatchRequest(BaseModel):
    """FR-013: Human-in-the-loop confirmation, relabeling, or rejection of a component."""
    model_config = ConfigDict(extra="forbid")

    label: str | None = Field(default=None, min_length=1, max_length=160)
    category: ComponentCategory | None = None
    role: str | None = Field(default=None, max_length=240)
    description: str | None = Field(default=None, max_length=1200)
    verification_status: VerificationStatus | None = None


class WorkspaceDetailDTO(BaseModel):
    id: UUID
    name: str
    domain: str
    is_sample: bool
    assets: list[AssetDTO]
    components: list[ComponentDTO]
    budget_spent_usd: float = Field(default=0.0, ge=0.0)
    budget_cap_usd: float = Field(default=120.0, gt=0.0)
    created_at: datetime


class QuestionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question_text: str = Field(..., min_length=2, max_length=1000)
    selected_component_id: UUID | None = None
    learner_session_id: UUID | None = None

    @field_validator("question_text")
    @classmethod
    def strip_text(cls, v: str) -> str:
        return v.strip()


class QuestionRunResponse(BaseModel):
    run_id: UUID
    message_id: UUID
    transcript: str | None = None
    answer: AnswerPayload | None = None


# =============================================================================
# 5. ZERO-TRUST SPATIAL ACTIVE RECALL DTOs (Public vs. Post-Attempt Reveal)
# =============================================================================

class CreateAssessmentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    count: int = Field(default=3, ge=1, le=10)
    difficulty: DifficultyLevel = Field(default=DifficultyLevel.MEDIUM)
    kinds: list[QuizKind] = Field(default_factory=lambda: [QuizKind.LOCATE])


class PublicAssessmentItemDTO(BaseModel):
    """
    CRITICAL SECURITY CONTRACT (FR-030):
    Never includes target_component_id, target mask coordinates, or private rubric_json.
    """
    model_config = ConfigDict(extra="forbid")

    id: UUID
    assessment_id: UUID
    position: int
    kind: QuizKind
    prompt: str
    hint: str | None = None


class PublicAssessmentDTO(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    workspace_id: UUID
    image_asset_id: UUID
    title: str
    difficulty: DifficultyLevel
    items: list[PublicAssessmentItemDTO]


class SubmitQuizAttemptRequest(BaseModel):
    """Learner click submission in normalized [0.0, 1.0] image coordinates."""
    model_config = ConfigDict(extra="forbid")

    click: NormalizedPoint
    explanation: str | None = Field(default=None, max_length=1500)
    hints_used: int = Field(default=0, ge=0, le=5)
    learner_session_id: UUID | None = None


class QuizAttemptResultDTO(BaseModel):
    """
    Returned ONLY after deterministic server-side grading completes.
    Reveals target_component_id, clicked_component_id (if confused), and separable scores.
    """
    model_config = ConfigDict(extra="forbid")

    attempt_id: UUID
    assessment_item_id: UUID
    # Post-submission reveal
    target_component_id: UUID
    target_component_label: str
    clicked_component_id: UUID | None = None
    clicked_component_label: str | None = None
    # Deterministic geometric grading
    spatial_hit: bool
    near_miss: bool
    distance_norm: float = Field(..., ge=0.0, description="Distance to target mask as fraction of image diagonal")
    overlap_ambiguous: bool = False
    spatial_score: float = Field(..., ge=0.0, le=1.0)
    # Separable rubric-grounded conceptual grading
    conceptual_status: ConceptualStatus
    conceptual_score: float | None = Field(default=None, ge=0.0, le=1.0)
    sentiment_aligned: bool | None = None
    # Feedback & verifiable citations
    feedback_text: str
    citations: list[CitationItem] = Field(default_factory=list)
    ai_evaluation_disclaimer: str = Field(
        default=(
            "Spatial hit-testing is deterministic geometry; "
            "conceptual feedback is AI-assisted against datasheet citations."
        )
    )
    latency_ms: int


# =============================================================================
# 6. AGENT TRACE, TELEMETRY & SERVER-SENT EVENTS (SSE)
# =============================================================================

class ModelUsageMetrics(BaseModel):
    model_config = ConfigDict(extra="forbid")

    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    images: int = Field(default=0, ge=0)
    audio_seconds: float = Field(default=0.0, ge=0.0)


class AgentStepDTO(BaseModel):
    id: UUID
    run_id: UUID
    seq: int
    kind: str
    name: str
    args: dict[str, Any]
    result_summary: str | None = None
    cache_hit: bool
    latency_ms: int
    cost_usd: float
    created_at: datetime


class AgentRunTraceDTO(BaseModel):
    id: UUID
    workspace_id: UUID
    kind: str
    status: str
    model: str
    prompt_version: str
    steps: list[AgentStepDTO]
    totals: dict[str, Any]
    created_at: datetime
    completed_at: datetime | None = None


class SSEEventEnvelope(BaseModel):
    """Canonical Server-Sent Event payload streamed over GET /api/v1/runs/{run_id}/events."""
    model_config = ConfigDict(extra="forbid")

    run_id: UUID
    seq: int = Field(..., ge=1)
    event: SSEEventType
    timestamp: datetime
    payload: dict[str, Any]


# =============================================================================
# 7. CANONICAL ERROR & HEALTH ENVELOPES
# =============================================================================

class ErrorDetail(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: ErrorCode
    message: str
    request_id: str | None = None
    details: dict[str, Any] | None = None


class ErrorResponse(BaseModel):
    """Canonical error shape across all /api/v1 endpoints."""
    model_config = ConfigDict(extra="forbid")

    error: ErrorDetail


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    database_connected: bool
    meta_api_configured: bool
    budget_spent_usd: float
    budget_hard_cap_usd: float
    budget_remaining_usd: float


# =============================================================================
# 8. MISSING SCHEMA CLASSES (imported by routers but previously undefined)
# =============================================================================

class GuestSessionCreate(BaseModel):
    """Empty body for POST /api/v1/sessions — server generates token."""
    model_config = ConfigDict(extra="forbid")


class WorkspaceCreate(BaseModel):
    """Alias for WorkspaceCreateRequest — used by workspaces router."""
    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1, max_length=160)
    domain: Literal["electronics", "schematic", "other"] = "electronics"


class WorkspaceResponse(BaseModel):
    """Alias for WorkspaceDetailDTO — used by workspaces router."""
    id: UUID
    name: str
    domain: str
    is_sample: bool
    assets: list[AssetDTO]
    components: list[ComponentDTO]
    budget_spent_usd: float = Field(default=0.0, ge=0.0)
    budget_cap_usd: float = Field(default=120.0, gt=0.0)
    created_at: datetime


class AssetResponse(BaseModel):
    """Alias for AssetDTO — used by assets router."""
    id: UUID
    workspace_id: UUID
    kind: AssetKind
    sha256: str
    mime_type: str
    original_filename: str
    content_url: str = Field(..., description="Authenticated /api/v1/assets/{id}/content route")
    width_px: int | None = None
    height_px: int | None = None
    page_count: int | None = None
    status: AssetStatus
    license: str | None = None
    source_url: str | None = None
    attribution: str | None = None
    created_at: datetime


class ComponentResponse(BaseModel):
    """Alias for ComponentDTO — used by components router."""
    id: UUID
    workspace_id: UUID
    image_asset_id: UUID
    som_index: int | None = None
    label: str
    noun_phrase: str
    category: ComponentCategory
    role: str | None = None
    description: str | None = None
    bbox: NormalizedBBox
    polygons: MultiPolygonNormalized
    area_fraction: float
    confidence: float
    identity_source: IdentitySource
    verification_status: VerificationStatus
    evidence: list[ComponentEvidenceDTO] = Field(default_factory=list)


class ComponentUpdate(BaseModel):
    """Alias for ComponentPatchRequest — used by components router."""
    model_config = ConfigDict(extra="forbid")

    label: str | None = Field(default=None, min_length=1, max_length=160)
    category: ComponentCategory | None = None
    role: str | None = Field(default=None, max_length=240)
    description: str | None = Field(default=None, max_length=1200)
    verification_status: VerificationStatus | None = None


class RunResponse(BaseModel):
    """Alias for QuestionRunResponse — used by qa and assessments routers."""
    run_id: UUID
    message_id: UUID
    transcript: str | None = None
    answer: AnswerPayload | None = None


class AssessmentCreate(BaseModel):
    """Alias for CreateAssessmentRequest — used by assessments router."""
    model_config = ConfigDict(extra="forbid")

    count: int = Field(default=3, ge=1, le=10)
    difficulty: DifficultyLevel = Field(default=DifficultyLevel.MEDIUM)
    kinds: list[QuizKind] = Field(default_factory=lambda: [QuizKind.LOCATE])


class AssessmentAttemptRequest(BaseModel):
    """Alias for SubmitQuizAttemptRequest — used by assessments router."""
    model_config = ConfigDict(extra="forbid")

    click: NormalizedPoint
    explanation: str | None = Field(default=None, max_length=1500)
    hints_used: int = Field(default=0, ge=0, le=5)
    learner_session_id: UUID | None = None


class AssessmentAttemptResponse(BaseModel):
    """Alias for QuizAttemptResultDTO — used by assessments router."""
    model_config = ConfigDict(extra="forbid")

    attempt_id: UUID
    assessment_item_id: UUID
    target_component_id: UUID
    target_component_label: str
    clicked_component_id: UUID | None = None
    clicked_component_label: str | None = None
    spatial_hit: bool
    near_miss: bool
    distance_norm: float = Field(..., ge=0.0, description="Distance to target mask as fraction of image diagonal")
    overlap_ambiguous: bool = False
    spatial_score: float = Field(..., ge=0.0, le=1.0)
    conceptual_status: ConceptualStatus
    conceptual_score: float | None = Field(default=None, ge=0.0, le=1.0)
    sentiment_aligned: bool | None = None
    feedback_text: str
    citations: list[CitationItem] = Field(default_factory=list)
    ai_evaluation_disclaimer: str = Field(
        default=(
            "Spatial hit-testing is deterministic geometry; "
            "conceptual feedback is AI-assisted against datasheet citations."
        )
    )
    latency_ms: int


class PublicAssessmentResponse(BaseModel):
    """Alias for PublicAssessmentDTO — used by assessments router."""
    model_config = ConfigDict(extra="forbid")

    id: UUID
    workspace_id: UUID
    image_asset_id: UUID
    title: str
    difficulty: DifficultyLevel
    items: list[PublicAssessmentItemDTO]
