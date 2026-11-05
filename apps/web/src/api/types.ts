// TypeScript interfaces synced with backend/app/models/schemas.py

// --- Sessions & Workspaces ---

export interface GuestSession {
  id: string;
  expires_at: string;
  created_at: string;
}

export interface Workspace {
  id: string;
  guest_session_id: string;
  name: string;
  domain: string;
  is_sample: boolean;
  created_at: string;
}

// --- Assets ---

export type AssetKind =
  | 'board_image'
  | 'datasheet_pdf'
  | 'mask_png'
  | 'som_image'
  | 'audio_clip'
  | 'fix_illustration';

export type AssetStatus = 'pending' | 'processing' | 'ready' | 'needs_ocr' | 'failed';

export interface Asset {
  id: string;
  workspace_id: string;
  kind: AssetKind;
  sha256: string;
  mime_type: string;
  original_filename: string;
  storage_key: string;
  width_px?: number;
  height_px?: number;
  page_count?: number;
  status: AssetStatus;
  license?: string;
  source_url?: string;
  attribution?: string;
  created_at: string;
}

// --- Components ---

export type VerificationStatus = 'proposed' | 'verified' | 'rejected';

export interface BoundingBox {
  x: number;
  y: number;
  w: number;
  h: number;
}

export interface Component {
  id: string;
  workspace_id: string;
  image_asset_id: string;
  som_index?: number;
  label: string;
  noun_phrase: string;
  category?: string;
  role?: string;
  description?: string;
  bbox: BoundingBox;
  polygons: number[][][];
  area_fraction: number;
  mask_asset_id?: string;
  confidence: number;
  identity_source?: 'agent' | 'user' | 'point_prompt' | 'seed_fixture';
  verification_status: VerificationStatus;
}

export interface ComponentEvidence {
  id: string;
  component_id: string;
  document_page_id: string;
  chunk_id?: string;
  page_number: number;
  relation_type?: string;
  quoted_text: string;
  relevance: number;
  verification_status: VerificationStatus;
}

// --- Documents ---

export interface DocumentPage {
  id: string;
  asset_id: string;
  page_number: number;
  text_content: string;
}

export interface DocumentChunk {
  id: string;
  page_id: string;
  chunk_index: number;
  char_start: number;
  char_end: number;
  text_content: string;
}

// --- Q&A & Agent Runs ---

export interface Citation {
  document_page_id?: string;
  chunk_id?: string;
  page_number: number;
  quote: string;
  relation_type?: string;
}

export interface AnswerPayload {
  text: string;
  citations: Citation[];
  highlight_component_ids: string[];
  uncertainty?: 'low' | 'medium' | 'high' | 'insufficient_evidence';
  ungrounded?: boolean;
}

export type RunEventKind =
  | 'run.started'
  | 'step.started'
  | 'tool.called'
  | 'tool.result'
  | 'component.found'
  | 'message.delta'
  | 'answer.final'
  | 'run.completed'
  | 'run.failed'
  | 'run.failed';

export interface RunEvent {
  kind: RunEventKind;
  timestamp: string;
  data: Record<string, unknown>;
}

// --- Assessments (Spatial Quiz) ---

export type AssessmentItemKind = 'locate' | 'identify' | 'explain';

export interface PublicAssessmentItem {
  id: string;
  assessment_id: string;
  position: number;
  kind: AssessmentItemKind;
  prompt: string;
  hint?: string;
}

export interface PublicAssessment {
  id: string;
  workspace_id: string;
  image_asset_id: string;
  title: string;
  difficulty: string;
  items: PublicAssessmentItem[];
}

export type SpatialHit = 'hit' | 'near_miss' | 'miss';

export interface AssessmentAttemptResult {
  attempt_id: string;
  assessment_item_id: string;
  // Revealed after submission
  target_component_id: string;
  target_component_label: string;
  clicked_component_id?: string;
  clicked_component_label?: string;
  spatial_hit: boolean;
  near_miss: boolean;
  distance_norm: number;
  overlap_ambiguous?: boolean;
  spatial_score: number;
  conceptual_status?: 'graded' | 'not_graded' | 'skipped';
  conceptual_score?: number;
  sentiment_aligned?: boolean;
  feedback_text: string;
  citations?: Citation[];
  ai_evaluation_disclaimer?: string;
  latency_ms: number;
}

// --- Agent Trace ---

export interface AgentStep {
  seq: number;
  kind: string;
  name: string;
  args: Record<string, unknown>;
  result_summary?: string;
  latency_ms?: number;
  model_call_id?: string;
}

export interface AgentRun {
  id: string;
  workspace_id: string;
  kind: string;
  status: string;
  model?: string;
  prompt_version?: string;
  totals?: Record<string, unknown>;
  steps: AgentStep[];
}

// --- Health ---

export interface HealthStatus {
  status: 'ok' | 'degraded';
  database_connected: boolean;
  meta_api_configured: boolean;
  budget_spent_usd: number;
  budget_hard_cap_usd: number;
  budget_remaining_usd: number;
}

// --- Error Envelope ---

export interface ApiError {
  error: {
    code: string;
    message: string;
    request_id: string;
    details?: Record<string, unknown>;
  };
}
