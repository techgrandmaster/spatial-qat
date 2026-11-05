"""
SQLAlchemy 2.0 Async ORM models.
Synced with db/schema.sql.
"""

from datetime import datetime
from uuid import uuid4

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    Double,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy import (
    Text as TSVector,
)
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP, UUID
from sqlalchemy.orm import DeclarativeBase, relationship


class Base(DeclarativeBase):
    pass


class GuestSession(Base):
    __tablename__ = "guest_sessions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    token_hash = Column(String(64), unique=True, nullable=False)
    expires_at = Column(TIMESTAMP(timezone=True), nullable=False)
    created_at = Column(TIMESTAMP(timezone=True), default=datetime.utcnow)

    workspaces = relationship("Workspace", back_populates="guest_session")


class Workspace(Base):
    __tablename__ = "workspaces"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    guest_session_id = Column(
        UUID(as_uuid=True), ForeignKey("guest_sessions.id"), nullable=False
    )
    name = Column(String(255), default="Untitled Board")
    domain = Column(String(100), default="electronics")
    is_sample = Column(Boolean, default=False)
    created_at = Column(TIMESTAMP(timezone=True), default=datetime.utcnow)
    updated_at = Column(TIMESTAMP(timezone=True), default=datetime.utcnow)

    guest_session = relationship("GuestSession", back_populates="workspaces")
    assets = relationship("Asset", back_populates="workspace")
    components = relationship("Component", back_populates="workspace")


class Asset(Base):
    __tablename__ = "assets"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    workspace_id = Column(
        UUID(as_uuid=True), ForeignKey("workspaces.id"), nullable=False
    )
    kind = Column(String(50), nullable=False)
    sha256 = Column(String(64), nullable=False)
    mime_type = Column(String(100), nullable=False)
    original_filename = Column(String(255), nullable=False, default="")
    storage_key = Column(String(255), nullable=False, unique=True)
    byte_size = Column(BigInteger, nullable=False, default=0)
    width_px = Column(Integer)
    height_px = Column(Integer)
    page_count = Column(Integer)
    status = Column(String(20), default="pending")
    license = Column(String(255))
    source_url = Column(Text)
    attribution = Column(Text)
    created_at = Column(TIMESTAMP(timezone=True), default=datetime.utcnow)

    workspace = relationship("Workspace", back_populates="assets")
    document_pages = relationship("DocumentPage", back_populates="asset")


class DocumentPage(Base):
    __tablename__ = "document_pages"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    asset_id = Column(UUID(as_uuid=True), ForeignKey("assets.id"), nullable=False)
    workspace_id = Column(
        UUID(as_uuid=True), ForeignKey("workspaces.id"), nullable=False
    )
    page_number = Column(Integer, nullable=False)
    text_content = Column(Text, default="")
    tsv = Column(TSVector)
    created_at = Column(TIMESTAMP(timezone=True), default=datetime.utcnow)

    asset = relationship("Asset", back_populates="document_pages")
    chunks = relationship("DocumentChunk", back_populates="page")


class DocumentChunk(Base):
    __tablename__ = "document_chunks"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    page_id = Column(UUID(as_uuid=True), ForeignKey("document_pages.id"), nullable=False)
    asset_id = Column(UUID(as_uuid=True), ForeignKey("assets.id"), nullable=False)
    workspace_id = Column(
        UUID(as_uuid=True), ForeignKey("workspaces.id"), nullable=False
    )
    page_number = Column(Integer, nullable=False)
    chunk_index = Column(Integer, nullable=False)
    char_start = Column(Integer, nullable=False)
    char_end = Column(Integer, nullable=False)
    text_content = Column(Text, nullable=False)
    tsv = Column(TSVector)
    created_at = Column(TIMESTAMP(timezone=True), default=datetime.utcnow)

    page = relationship("DocumentPage", back_populates="chunks")


class Component(Base):
    __tablename__ = "components"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    workspace_id = Column(
        UUID(as_uuid=True), ForeignKey("workspaces.id"), nullable=False
    )
    image_asset_id = Column(UUID(as_uuid=True), ForeignKey("assets.id"), nullable=False)
    som_index = Column(Integer)
    label = Column(String(255), nullable=False)
    noun_phrase = Column(String(500), nullable=False)
    category = Column(String(100), default="other")
    role = Column(String(255))
    description = Column(Text)
    bbox_x = Column(Double, nullable=False)
    bbox_y = Column(Double, nullable=False)
    bbox_w = Column(Double, nullable=False)
    bbox_h = Column(Double, nullable=False)
    bbox = Column(JSONB, nullable=False)
    polygons = Column(JSONB, nullable=False)
    area_fraction = Column(Double, nullable=False, default=0.0)
    mask_asset_id = Column(UUID(as_uuid=True), ForeignKey("assets.id"))
    mask_encoding = Column(String(20), default="png")
    confidence = Column(Float, default=1.0)
    identity_source = Column(String(20), default="agent")
    verification_status = Column(String(20), default="proposed")
    created_at = Column(TIMESTAMP(timezone=True), default=datetime.utcnow)
    updated_at = Column(TIMESTAMP(timezone=True), default=datetime.utcnow)

    workspace = relationship("Workspace", back_populates="components")
    evidence = relationship("ComponentEvidence", back_populates="component")


class ComponentEvidence(Base):
    __tablename__ = "component_evidence"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    component_id = Column(
        UUID(as_uuid=True), ForeignKey("components.id"), nullable=False
    )
    document_page_id = Column(
        UUID(as_uuid=True), ForeignKey("document_pages.id"), nullable=False
    )
    chunk_id = Column(UUID(as_uuid=True), ForeignKey("document_chunks.id"))
    relation_type = Column(String(50), default="specification")
    quoted_text = Column(Text, nullable=False, default="")
    relevance = Column(Float, default=1.0)
    verification_status = Column(String(20), default="proposed")
    created_at = Column(TIMESTAMP(timezone=True), default=datetime.utcnow)

    component = relationship("Component", back_populates="evidence")


class ComponentEdge(Base):
    __tablename__ = "component_edges"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    workspace_id = Column(
        UUID(as_uuid=True), ForeignKey("workspaces.id"), nullable=False
    )
    src_component_id = Column(
        UUID(as_uuid=True), ForeignKey("components.id"), nullable=False
    )
    dst_component_id = Column(
        UUID(as_uuid=True), ForeignKey("components.id"), nullable=False
    )
    relation_type = Column(String(100), nullable=False)
    evidence_page_id = Column(UUID(as_uuid=True), ForeignKey("document_pages.id"))
    evidence_quote = Column(Text)
    verification_status = Column(String(20), default="proposed")
    created_at = Column(TIMESTAMP(timezone=True), default=datetime.utcnow)


class AgentRun(Base):
    __tablename__ = "agent_runs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    workspace_id = Column(
        UUID(as_uuid=True), ForeignKey("workspaces.id"), nullable=False
    )
    learner_session_id = Column(UUID(as_uuid=True), ForeignKey("learner_sessions.id"))
    kind = Column(String(100), nullable=False)
    status = Column(String(20), default="running")
    model = Column(String(100), nullable=False)
    prompt_version = Column(String(50), default="v1.0")
    input_hash = Column(String(64))
    totals = Column(JSONB)
    error_code = Column(String(50))
    error_message = Column(Text)
    created_at = Column(TIMESTAMP(timezone=True), default=datetime.utcnow)
    completed_at = Column(TIMESTAMP(timezone=True))

    steps = relationship("AgentStep", back_populates="run")


class AgentStep(Base):
    __tablename__ = "agent_steps"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    run_id = Column(UUID(as_uuid=True), ForeignKey("agent_runs.id"), nullable=False)
    seq = Column(Integer, nullable=False)
    kind = Column(String(50), nullable=False)
    name = Column(String(100), nullable=False)
    args = Column(JSONB, default=dict)
    result_summary = Column(Text)
    cache_hit = Column(Boolean, default=False)
    latency_ms = Column(Integer, default=0)
    cost_usd = Column(Float, default=0.0)
    model_call_id = Column(UUID(as_uuid=True), ForeignKey("model_calls.id"))
    created_at = Column(TIMESTAMP(timezone=True), default=datetime.utcnow)

    run = relationship("AgentRun", back_populates="steps")


class ModelCall(Base):
    __tablename__ = "model_calls"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    workspace_id = Column(UUID(as_uuid=True), ForeignKey("workspaces.id"))
    cache_key = Column(String(64), unique=True, nullable=False)
    model = Column(String(100), nullable=False)
    endpoint = Column(String(255), nullable=False)
    operation = Column(String(50), nullable=False)
    request_json = Column(JSONB, nullable=False)
    response_json = Column(JSONB, nullable=False)
    status = Column(String(20), nullable=False, default="succeeded")
    latency_ms = Column(Integer, nullable=False, default=0)
    usage = Column(JSONB)
    cost_usd = Column(Float, default=0.0)
    created_at = Column(TIMESTAMP(timezone=True), default=datetime.utcnow)


class Assessment(Base):
    __tablename__ = "assessments"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    workspace_id = Column(
        UUID(as_uuid=True), ForeignKey("workspaces.id"), nullable=False
    )
    image_asset_id = Column(UUID(as_uuid=True), ForeignKey("assets.id"), nullable=False)
    learner_session_id = Column(UUID(as_uuid=True), ForeignKey("learner_sessions.id"))
    title = Column(String(255), default="Spatial Quiz")
    difficulty = Column(String(20), default="medium")
    status = Column(String(20), default="active")
    created_at = Column(TIMESTAMP(timezone=True), default=datetime.utcnow)

    items = relationship("AssessmentItem", back_populates="assessment")


class AssessmentItem(Base):
    __tablename__ = "assessment_items"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    assessment_id = Column(
        UUID(as_uuid=True), ForeignKey("assessments.id"), nullable=False
    )
    position = Column(Integer, nullable=False)
    kind = Column(String(20), nullable=False)
    prompt = Column(Text, nullable=False)
    target_component_id = Column(
        UUID(as_uuid=True), ForeignKey("components.id"), nullable=False
    )
    hint = Column(Text)
    rubric_json = Column(JSONB)
    config = Column(JSONB)
    created_at = Column(TIMESTAMP(timezone=True), default=datetime.utcnow)

    assessment = relationship("Assessment", back_populates="items")
    attempts = relationship("AssessmentAttempt", back_populates="item")


class LearnerSession(Base):
    __tablename__ = "learner_sessions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    workspace_id = Column(
        UUID(as_uuid=True), ForeignKey("workspaces.id"), nullable=False
    )
    created_at = Column(TIMESTAMP(timezone=True), default=datetime.utcnow)


class Message(Base):
    __tablename__ = "messages"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    workspace_id = Column(
        UUID(as_uuid=True), ForeignKey("workspaces.id"), nullable=False
    )
    learner_session_id = Column(UUID(as_uuid=True), ForeignKey("learner_sessions.id"))
    selected_component_id = Column(UUID(as_uuid=True), ForeignKey("components.id"))
    role = Column(String(20), nullable=False)
    modality = Column(String(20), default="text")
    content = Column(Text, nullable=False)
    audio_asset_id = Column(UUID(as_uuid=True), ForeignKey("assets.id"))
    run_id = Column(UUID(as_uuid=True), ForeignKey("agent_runs.id"))
    ungrounded = Column(Boolean, default=False)
    uncertainty = Column(String(20), default="low")
    payload = Column(JSONB)
    created_at = Column(TIMESTAMP(timezone=True), default=datetime.utcnow)


class MessageCitation(Base):
    __tablename__ = "message_citations"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    message_id = Column(UUID(as_uuid=True), ForeignKey("messages.id"), nullable=False)
    document_page_id = Column(
        UUID(as_uuid=True), ForeignKey("document_pages.id"), nullable=False
    )
    chunk_id = Column(UUID(as_uuid=True), ForeignKey("document_chunks.id"))
    page_number = Column(Integer, nullable=False)
    quoted_text = Column(Text, nullable=False)
    created_at = Column(TIMESTAMP(timezone=True), default=datetime.utcnow)


class Job(Base):
    __tablename__ = "jobs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    workspace_id = Column(
        UUID(as_uuid=True), ForeignKey("workspaces.id"), nullable=False
    )
    idempotency_key = Column(String(64), unique=True)
    kind = Column(String(50), nullable=False)
    status = Column(String(20), default="queued")
    payload = Column(JSONB)
    result = Column(JSONB)
    attempt_count = Column(Integer, default=0)
    max_attempts = Column(Integer, default=3)
    error_code = Column(String(50))
    error_message = Column(Text)
    locked_at = Column(TIMESTAMP(timezone=True))
    created_at = Column(TIMESTAMP(timezone=True), default=datetime.utcnow)
    updated_at = Column(TIMESTAMP(timezone=True), default=datetime.utcnow)


class Generation(Base):
    __tablename__ = "generations"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    workspace_id = Column(
        UUID(as_uuid=True), ForeignKey("workspaces.id"), nullable=False
    )
    source_asset_id = Column(UUID(as_uuid=True), ForeignKey("assets.id"), nullable=False)
    output_asset_id = Column(UUID(as_uuid=True), ForeignKey("assets.id"), nullable=False)
    prompt = Column(Text, nullable=False)
    component_ids = Column(JSONB)
    created_at = Column(TIMESTAMP(timezone=True), default=datetime.utcnow)


class AssessmentAttempt(Base):
    __tablename__ = "assessment_attempts"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    assessment_item_id = Column(
        UUID(as_uuid=True), ForeignKey("assessment_items.id"), nullable=False
    )
    learner_session_id = Column(UUID(as_uuid=True), ForeignKey("learner_sessions.id"))
    click_x = Column(Float, nullable=False)
    click_y = Column(Float, nullable=False)
    spatial_hit = Column(Boolean, default=False)
    near_miss = Column(Boolean, default=False)
    clicked_component_id = Column(UUID(as_uuid=True), ForeignKey("components.id"))
    distance_norm = Column(Float, nullable=False, default=0.0)
    overlap_ambiguous = Column(Boolean, default=False)
    hints_used = Column(Integer, default=0)
    spatial_score = Column(Float, default=0.0)
    user_explanation = Column(Text)
    conceptual_status = Column(String(20), default="skipped")
    conceptual_score = Column(Float)
    sentiment_aligned = Column(Boolean)
    feedback_text = Column(Text, nullable=False, default="")
    citations_json = Column(JSONB)
    latency_ms = Column(Integer, default=0)
    created_at = Column(TIMESTAMP(timezone=True), default=datetime.utcnow)

    item = relationship("AssessmentItem", back_populates="attempts")


class EvalRun(Base):
    __tablename__ = "eval_runs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    pipeline_variant = Column(String(50), nullable=False)
    prompt_version = Column(String(50), nullable=False)
    spark_model = Column(String(100), nullable=False)
    sam_model = Column(String(100), nullable=False)
    summary_metrics = Column(JSONB, nullable=False)
    created_at = Column(TIMESTAMP(timezone=True), default=datetime.utcnow)

    results = relationship("EvalResult", back_populates="run")


class EvalResult(Base):
    __tablename__ = "eval_results"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    eval_run_id = Column(UUID(as_uuid=True), ForeignKey("eval_runs.id"), nullable=False)
    board_key = Column(String(255), nullable=False)
    item_kind = Column(String(50), nullable=False)
    target_label = Column(String(255), nullable=False)
    iou_score = Column(Float)
    naming_matched = Column(Boolean)
    citation_valid = Column(Boolean)
    page_supported = Column(Boolean)
    latency_ms = Column(Integer, nullable=False)
    cost_usd = Column(Float, default=0.0)
    failure_category = Column(String(100))
    details = Column(JSONB)
    created_at = Column(TIMESTAMP(timezone=True), default=datetime.utcnow)

    run = relationship("EvalRun", back_populates="results")
