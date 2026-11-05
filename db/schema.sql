-- =============================================================================
-- Spatial-QAT: Authoritative PostgreSQL 16+ Database Schema (db/schema.sql)
-- Companion to: docs/PRD.md, backend/app/models/schemas.py, apps/web/src/api/types.ts
-- =============================================================================

CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- Automatic updated_at timestamp trigger function
CREATE OR REPLACE FUNCTION trigger_set_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- =============================================================================
-- 1. SESSION & WORKSPACE ISOLATION
-- =============================================================================

CREATE TABLE IF NOT EXISTS guest_sessions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    token_hash TEXT NOT NULL UNIQUE,
    expires_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_guest_sessions_expires_at ON guest_sessions(expires_at);

CREATE TABLE IF NOT EXISTS workspaces (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    guest_session_id UUID NOT NULL REFERENCES guest_sessions(id) ON DELETE CASCADE,
    name TEXT NOT NULL CHECK (char_length(trim(name)) BETWEEN 1 AND 160),
    domain TEXT NOT NULL DEFAULT 'electronics' CHECK (domain IN ('electronics', 'schematic', 'other')),
    is_sample BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_workspaces_session ON workspaces(guest_session_id);

DROP TRIGGER IF EXISTS set_workspaces_updated_at ON workspaces;
CREATE TRIGGER set_workspaces_updated_at
BEFORE UPDATE ON workspaces
FOR EACH ROW EXECUTE FUNCTION trigger_set_updated_at();

-- =============================================================================
-- 2. ASSETS & MULTIMODAL INGESTION
-- =============================================================================

CREATE TABLE IF NOT EXISTS assets (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    kind TEXT NOT NULL CHECK (kind IN (
        'board_image', 'datasheet_pdf', 'mask_png', 'som_image', 'audio_clip', 'fix_illustration'
    )),
    sha256 TEXT NOT NULL CHECK (char_length(sha256) = 64),
    mime_type TEXT NOT NULL,
    original_filename TEXT NOT NULL,
    storage_key TEXT NOT NULL UNIQUE,
    byte_size BIGINT NOT NULL CHECK (byte_size > 0),
    width_px INTEGER CHECK (width_px IS NULL OR width_px > 0),
    height_px INTEGER CHECK (height_px IS NULL OR height_px > 0),
    page_count INTEGER CHECK (page_count IS NULL OR page_count > 0),
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN (
        'pending', 'processing', 'ready', 'needs_ocr', 'failed'
    )),
    license TEXT,
    source_url TEXT,
    attribution TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (workspace_id, kind, sha256)
);

CREATE INDEX IF NOT EXISTS idx_assets_workspace ON assets(workspace_id);
CREATE INDEX IF NOT EXISTS idx_assets_sha256 ON assets(sha256);

-- =============================================================================
-- 3. PAGE-INDEXED PDF EXTRACTION & FULL-TEXT SEARCH (PostgreSQL FTS)
-- =============================================================================

CREATE TABLE IF NOT EXISTS document_pages (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    asset_id UUID NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
    workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    page_number INTEGER NOT NULL CHECK (page_number >= 1),
    text_content TEXT NOT NULL,
    tsv TSVECTOR GENERATED ALWAYS AS (to_tsvector('english', COALESCE(text_content, ''))) STORED,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (asset_id, page_number)
);

CREATE INDEX IF NOT EXISTS idx_document_pages_asset ON document_pages(asset_id, page_number);
CREATE INDEX IF NOT EXISTS idx_document_pages_tsv ON document_pages USING GIN (tsv);

CREATE TABLE IF NOT EXISTS document_chunks (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    page_id UUID NOT NULL REFERENCES document_pages(id) ON DELETE CASCADE,
    asset_id UUID NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
    workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    page_number INTEGER NOT NULL CHECK (page_number >= 1),
    chunk_index INTEGER NOT NULL CHECK (chunk_index >= 0),
    char_start INTEGER NOT NULL CHECK (char_start >= 0),
    char_end INTEGER NOT NULL CHECK (char_end >= char_start),
    text_content TEXT NOT NULL CHECK (char_length(text_content) > 0),
    tsv TSVECTOR GENERATED ALWAYS AS (to_tsvector('english', COALESCE(text_content, ''))) STORED,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (page_id, chunk_index)
);

CREATE INDEX IF NOT EXISTS idx_document_chunks_workspace ON document_chunks(workspace_id);
CREATE INDEX IF NOT EXISTS idx_document_chunks_page ON document_chunks(page_id);
CREATE INDEX IF NOT EXISTS idx_document_chunks_tsv ON document_chunks USING GIN (tsv);

-- =============================================================================
-- 4. SPATIAL COMPONENTS, SET-OF-MARK GROUNDING & EVIDENCE LINKS
-- =============================================================================

CREATE TABLE IF NOT EXISTS components (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    image_asset_id UUID NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
    som_index INTEGER CHECK (som_index IS NULL OR som_index >= 1),
    label TEXT NOT NULL CHECK (char_length(trim(label)) BETWEEN 1 AND 160),
    noun_phrase TEXT NOT NULL,
    category TEXT NOT NULL DEFAULT 'other' CHECK (category IN (
        'ic_microcontroller', 'voltage_regulator', 'connector_header',
        'passive_component', 'sensor_actuator', 'power_protection',
        'crystal_oscillator', 'switch_button', 'led_indicator', 'other'
    )),
    role TEXT,
    description TEXT,
    -- Normalized [0.0, 1.0] coordinates relative to EXIF-corrected image dimensions
    bbox_x DOUBLE PRECISION NOT NULL CHECK (bbox_x >= 0.0 AND bbox_x <= 1.0),
    bbox_y DOUBLE PRECISION NOT NULL CHECK (bbox_y >= 0.0 AND bbox_y <= 1.0),
    bbox_w DOUBLE PRECISION NOT NULL CHECK (bbox_w > 0.0 AND bbox_w <= 1.0),
    bbox_h DOUBLE PRECISION NOT NULL CHECK (bbox_h > 0.0 AND bbox_h <= 1.0),
    bbox JSONB NOT NULL,              -- {"x": float, "y": float, "w": float, "h": float}
    polygons JSONB NOT NULL,          -- List of polygons -> list of rings -> list of [x, y] pairs in [0,1]
    area_fraction DOUBLE PRECISION NOT NULL CHECK (area_fraction > 0.0 AND area_fraction <= 1.0),
    mask_asset_id UUID REFERENCES assets(id) ON DELETE SET NULL, -- Authoritative binary PNG mask
    mask_encoding TEXT NOT NULL DEFAULT 'png' CHECK (mask_encoding IN ('png', 'rle', 'polygon_only')),
    confidence DOUBLE PRECISION NOT NULL DEFAULT 1.0 CHECK (confidence >= 0.0 AND confidence <= 1.0),
    identity_source TEXT NOT NULL DEFAULT 'agent' CHECK (identity_source IN (
        'agent', 'user', 'point_prompt', 'seed_fixture'
    )),
    verification_status TEXT NOT NULL DEFAULT 'proposed' CHECK (verification_status IN (
        'proposed', 'verified', 'rejected'
    )),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_components_workspace ON components(workspace_id);
CREATE INDEX IF NOT EXISTS idx_components_image_asset ON components(image_asset_id);
CREATE INDEX IF NOT EXISTS idx_components_verification ON components(workspace_id, verification_status);

DROP TRIGGER IF EXISTS set_components_updated_at ON components;
CREATE TRIGGER set_components_updated_at
BEFORE UPDATE ON components
FOR EACH ROW EXECUTE FUNCTION trigger_set_updated_at();

CREATE TABLE IF NOT EXISTS component_evidence (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    component_id UUID NOT NULL REFERENCES components(id) ON DELETE CASCADE,
    document_page_id UUID NOT NULL REFERENCES document_pages(id) ON DELETE CASCADE,
    chunk_id UUID REFERENCES document_chunks(id) ON DELETE SET NULL,
    relation_type TEXT NOT NULL DEFAULT 'specification' CHECK (relation_type IN (
        'specification', 'pinout', 'operating_limit', 'troubleshooting', 'safety_warning'
    )),
    quoted_text TEXT NOT NULL CHECK (char_length(trim(quoted_text)) > 0),
    relevance DOUBLE PRECISION NOT NULL DEFAULT 1.0 CHECK (relevance >= 0.0 AND relevance <= 1.0),
    verification_status TEXT NOT NULL DEFAULT 'proposed' CHECK (verification_status IN (
        'proposed', 'verified', 'rejected'
    )),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (component_id, chunk_id, quoted_text)
);

CREATE INDEX IF NOT EXISTS idx_component_evidence_component ON component_evidence(component_id);
CREATE INDEX IF NOT EXISTS idx_component_evidence_page ON component_evidence(document_page_id);

-- P1: Provenance-aware relational knowledge graph edges between components
CREATE TABLE IF NOT EXISTS component_edges (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    src_component_id UUID NOT NULL REFERENCES components(id) ON DELETE CASCADE,
    dst_component_id UUID NOT NULL REFERENCES components(id) ON DELETE CASCADE,
    relation_type TEXT NOT NULL CHECK (relation_type IN (
        'regulates_power_for', 'communicates_with', 'protects', 'clock_source_for', 'connected_interface', 'other'
    )),
    evidence_page_id UUID REFERENCES document_pages(id) ON DELETE SET NULL,
    evidence_quote TEXT,
    verification_status TEXT NOT NULL DEFAULT 'proposed' CHECK (verification_status IN (
        'proposed', 'verified', 'rejected'
    )),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (src_component_id <> dst_component_id)
);

CREATE INDEX IF NOT EXISTS idx_component_edges_workspace ON component_edges(workspace_id);

-- =============================================================================
-- 5. GLOBAL CONTENT-ADDRESSED MODEL CACHE, AGENT RUNS & TELEMETRY
-- =============================================================================

-- Global table: deleting a workspace does NOT delete cached model calls (protects $150 credit cap)
CREATE TABLE IF NOT EXISTS model_calls (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id UUID REFERENCES workspaces(id) ON DELETE SET NULL,
    cache_key TEXT NOT NULL UNIQUE CHECK (char_length(cache_key) = 64), -- SHA-256 canonical request hash
    model TEXT NOT NULL,
    endpoint TEXT NOT NULL,
    operation TEXT NOT NULL CHECK (operation IN (
        'component_proposal', 'sam_segment_text', 'sam_segment_point',
        'som_grounding', 'qa_agent_step', 'quiz_generation',
        'conceptual_grading', 'voice_transcribe', 'image_edit'
    )),
    request_json JSONB NOT NULL,      -- Stores file SHA-256 hashes, never raw binary bytes
    response_json JSONB NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('succeeded', 'failed', 'rate_limited', 'timed_out')),
    latency_ms INTEGER NOT NULL CHECK (latency_ms >= 0),
    usage JSONB NOT NULL DEFAULT '{}'::jsonb, -- {"input_tokens": int, "output_tokens": int, "images": int, "audio_seconds": float}
    cost_usd NUMERIC(10, 6) NOT NULL DEFAULT 0.000000 CHECK (cost_usd >= 0),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_model_calls_cache_key ON model_calls(cache_key);
CREATE INDEX IF NOT EXISTS idx_model_calls_created_at ON model_calls(created_at);

CREATE TABLE IF NOT EXISTS learner_sessions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_learner_sessions_workspace ON learner_sessions(workspace_id);

CREATE TABLE IF NOT EXISTS agent_runs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    learner_session_id UUID REFERENCES learner_sessions(id) ON DELETE SET NULL,
    kind TEXT NOT NULL CHECK (kind IN ('analyze_board', 'grounded_qa', 'generate_quiz', 'grade_explanation', 'segment_point')),
    status TEXT NOT NULL DEFAULT 'running' CHECK (status IN ('queued', 'running', 'completed', 'failed', 'cancelled')),
    model TEXT NOT NULL,
    prompt_version TEXT NOT NULL DEFAULT 'v1.0',
    input_hash TEXT,
    totals JSONB NOT NULL DEFAULT '{"steps": 0, "cache_hits": 0, "latency_ms": 0, "cost_usd": 0.0}'::jsonb,
    error_code TEXT,
    error_message TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_agent_runs_workspace ON agent_runs(workspace_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_agent_runs_input_hash ON agent_runs(workspace_id, kind, input_hash);

CREATE TABLE IF NOT EXISTS agent_steps (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id UUID NOT NULL REFERENCES agent_runs(id) ON DELETE CASCADE,
    seq INTEGER NOT NULL CHECK (seq >= 1),
    kind TEXT NOT NULL CHECK (kind IN ('llm_plan', 'tool_call', 'tool_result', 'schema_repair', 'final_output')),
    name TEXT NOT NULL,
    args JSONB NOT NULL DEFAULT '{}'::jsonb,
    result_summary TEXT,
    cache_hit BOOLEAN NOT NULL DEFAULT FALSE,
    latency_ms INTEGER NOT NULL DEFAULT 0 CHECK (latency_ms >= 0),
    cost_usd NUMERIC(10, 6) NOT NULL DEFAULT 0.000000,
    model_call_id UUID REFERENCES model_calls(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (run_id, seq)
);

CREATE INDEX IF NOT EXISTS idx_agent_steps_run_seq ON agent_steps(run_id, seq);

-- =============================================================================
-- 6. CONVERSATIONAL Q&A MESSAGES & CITATIONS
-- =============================================================================

CREATE TABLE IF NOT EXISTS messages (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    learner_session_id UUID REFERENCES learner_sessions(id) ON DELETE CASCADE,
    selected_component_id UUID REFERENCES components(id) ON DELETE SET NULL,
    role TEXT NOT NULL CHECK (role IN ('user', 'assistant', 'system')),
    modality TEXT NOT NULL DEFAULT 'text' CHECK (modality IN ('text', 'voice')),
    content TEXT NOT NULL,
    audio_asset_id UUID REFERENCES assets(id) ON DELETE SET NULL,
    run_id UUID REFERENCES agent_runs(id) ON DELETE SET NULL,
    ungrounded BOOLEAN NOT NULL DEFAULT FALSE,
    uncertainty TEXT NOT NULL DEFAULT 'low' CHECK (uncertainty IN ('low', 'medium', 'high', 'insufficient_evidence')),
    payload JSONB NOT NULL DEFAULT '{}'::jsonb, -- Stores full validated AnswerPayload JSON
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_messages_workspace ON messages(workspace_id, created_at ASC);

CREATE TABLE IF NOT EXISTS message_citations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    message_id UUID NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
    document_page_id UUID NOT NULL REFERENCES document_pages(id) ON DELETE CASCADE,
    chunk_id UUID REFERENCES document_chunks(id) ON DELETE SET NULL,
    page_number INTEGER NOT NULL CHECK (page_number >= 1),
    quoted_text TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_message_citations_message ON message_citations(message_id);

-- =============================================================================
-- 7. SPATIAL ACTIVE RECALL QUIZZES & DETERMINISTIC GRADING ATTEMPTS
-- =============================================================================

CREATE TABLE IF NOT EXISTS assessments (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    image_asset_id UUID NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
    learner_session_id UUID REFERENCES learner_sessions(id) ON DELETE SET NULL,
    title TEXT NOT NULL,
    difficulty TEXT NOT NULL DEFAULT 'medium' CHECK (difficulty IN ('easy', 'medium', 'hard')),
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('draft', 'active', 'completed', 'archived')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_assessments_workspace ON assessments(workspace_id);

CREATE TABLE IF NOT EXISTS assessment_items (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    assessment_id UUID NOT NULL REFERENCES assessments(id) ON DELETE CASCADE,
    position INTEGER NOT NULL CHECK (position >= 1),
    kind TEXT NOT NULL DEFAULT 'locate' CHECK (kind IN ('locate', 'identify', 'explain')),
    prompt TEXT NOT NULL CHECK (char_length(trim(prompt)) >= 10),
    -- Kept strictly server-side until learner submits an attempt
    target_component_id UUID NOT NULL REFERENCES components(id) ON DELETE CASCADE,
    hint TEXT,
    rubric_json JSONB NOT NULL DEFAULT '{}'::jsonb, -- Expected key concepts, required page citations, common misconceptions
    config JSONB NOT NULL DEFAULT '{"near_miss_diagonal_threshold": 0.02, "hint_penalty": 0.25}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (assessment_id, position)
);

CREATE INDEX IF NOT EXISTS idx_assessment_items_assessment ON assessment_items(assessment_id, position);

CREATE TABLE IF NOT EXISTS assessment_attempts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    assessment_item_id UUID NOT NULL REFERENCES assessment_items(id) ON DELETE CASCADE,
    learner_session_id UUID REFERENCES learner_sessions(id) ON DELETE SET NULL,
    click_x DOUBLE PRECISION NOT NULL CHECK (click_x >= 0.0 AND click_x <= 1.0),
    click_y DOUBLE PRECISION NOT NULL CHECK (click_y >= 0.0 AND click_y <= 1.0),
    spatial_hit BOOLEAN NOT NULL,
    near_miss BOOLEAN NOT NULL DEFAULT FALSE,
    clicked_component_id UUID REFERENCES components(id) ON DELETE SET NULL,
    distance_norm DOUBLE PRECISION NOT NULL CHECK (distance_norm >= 0.0),
    overlap_ambiguous BOOLEAN NOT NULL DEFAULT FALSE,
    hints_used INTEGER NOT NULL DEFAULT 0 CHECK (hints_used >= 0),
    spatial_score DOUBLE PRECISION NOT NULL CHECK (spatial_score >= 0.0 AND spatial_score <= 1.0),
    user_explanation TEXT,
    conceptual_status TEXT NOT NULL DEFAULT 'skipped' CHECK (conceptual_status IN ('graded', 'not_graded', 'skipped')),
    conceptual_score DOUBLE PRECISION CHECK (conceptual_score IS NULL OR (conceptual_score >= 0.0 AND conceptual_score <= 1.0)),
    sentiment_aligned BOOLEAN,
    feedback_text TEXT NOT NULL,
    citations_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    latency_ms INTEGER NOT NULL DEFAULT 0 CHECK (latency_ms >= 0),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_assessment_attempts_item ON assessment_attempts(assessment_item_id, created_at DESC);

-- =============================================================================
-- 8. DURABLE BACKGROUND JOBS, P2 IMAGE GENERATIONS & EVALUATION HARNESS
-- =============================================================================

CREATE TABLE IF NOT EXISTS jobs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    idempotency_key TEXT UNIQUE,
    kind TEXT NOT NULL CHECK (kind IN ('extract_document', 'analyze_board', 'segment_component', 'generate_fix_image')),
    status TEXT NOT NULL DEFAULT 'queued' CHECK (status IN ('queued', 'running', 'completed', 'failed')),
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    result JSONB,
    attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
    max_attempts INTEGER NOT NULL DEFAULT 3 CHECK (max_attempts >= 1),
    error_code TEXT,
    error_message TEXT,
    locked_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_jobs_poll ON jobs(status, created_at ASC) WHERE status = 'queued';
CREATE INDEX IF NOT EXISTS idx_jobs_workspace ON jobs(workspace_id);

DROP TRIGGER IF EXISTS set_jobs_updated_at ON jobs;
CREATE TRIGGER set_jobs_updated_at
BEFORE UPDATE ON jobs
FOR EACH ROW EXECUTE FUNCTION trigger_set_updated_at();

-- P2: Illustrative image edits via muse-image-1.0
CREATE TABLE IF NOT EXISTS generations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    source_asset_id UUID NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
    output_asset_id UUID NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
    prompt TEXT NOT NULL,
    component_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Evaluation Harness Records (eval/run_evals.py)
CREATE TABLE IF NOT EXISTS eval_runs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    pipeline_variant TEXT NOT NULL CHECK (pipeline_variant IN (
        'agentic_som_full', 'generic_phrase_ablation', 'text_only_baseline'
    )),
    prompt_version TEXT NOT NULL,
    spark_model TEXT NOT NULL,
    sam_model TEXT NOT NULL,
    summary_metrics JSONB NOT NULL, -- Recall@IoU0.5, naming accuracy, citation validity, p50/p95 latency, total_cost_usd
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS eval_results (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    eval_run_id UUID NOT NULL REFERENCES eval_runs(id) ON DELETE CASCADE,
    board_key TEXT NOT NULL,
    item_kind TEXT NOT NULL CHECK (item_kind IN ('component_discovery', 'grounded_qa', 'spatial_grading')),
    target_label TEXT NOT NULL,
    iou_score DOUBLE PRECISION,
    naming_matched BOOLEAN,
    citation_valid BOOLEAN,
    page_supported BOOLEAN,
    latency_ms INTEGER NOT NULL,
    cost_usd NUMERIC(10, 6) NOT NULL DEFAULT 0.000000,
    failure_category TEXT, -- e.g., 'small_smd_part', 'dense_layout', 'ambiguous_silkscreen', 'none'
    details JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_eval_results_run ON eval_results(eval_run_id);
