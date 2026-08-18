/**
 * TypeScript type definitions matching backend Pydantic schemas.
 * Keep in sync with: src/stencil/api/schemas.py, validation/schema.py
 */

// ── Enums ──────────────────────────────────────────────────

export type IntakeStatus =
  | "received"
  | "processing"
  | "completed"
  | "completed_with_warnings"
  | "failed";

export type IntakeSource = "watcher" | "upload" | "training";

export type ExtractionPath = "ai" | "model" | "model_fallback_ai";

export type OutputType = "standard" | "wireless" | "time_and_material";

export type ChargeType =
  | "recurring"
  | "one_time"
  | "tax"
  | "fee"
  | "credit"
  | "adjustment"
  | "surcharge"
  | "usage"
  | "unknown";

export type ModelStatus =
  | "draft"
  | "candidate"
  | "approved"
  | "retired"
  | "failed_validation";

export type ProfileStatus = "draft" | "training" | "active" | "retired";

export type ExceptionReason =
  | "unknown_supplier"
  | "low_confidence"
  | "no_line_items"
  | "reconciliation_failure"
  | "layout_drift"
  | "unreadable_pdf"
  | "schema_validation_failure"
  | "extraction_error"
  | "missing_service_id";

// ── Paginated Response ─────────────────────────────────────

export interface PaginatedResponse<T> {
  items: T[];
  total: number;
  page: number;
  per_page: number;
  pages: number;
}

// ── Intake / Invoice ───────────────────────────────────────

export interface IntakeRecord {
  id: string;
  original_filename: string;
  original_pdf_path: string;
  archive_pdf_path: string | null;
  file_size_bytes: number;
  page_count: number;
  status: IntakeStatus;
  layout_fingerprint: string | null;
  intake_source: IntakeSource | null;
  supplier_profile_id: string | null;
  celery_task_id: string | null;
  created_at: string;
  updated_at: string;
  error_message: string | null;
}

export interface IntakeDetail extends IntakeRecord {
  jobs: ExtractionJob[];
  logs: ProcessingLog[];
}

// Intake enriched with fields from its latest extraction job
export interface InvoiceListItem extends IntakeRecord {
  supplier_name: string | null;
  output_type: OutputType | null;
  extraction_path: ExtractionPath | null;
  /** @deprecated Hardcoded to 1.0 by the default extraction path. */
  overall_confidence: number | null;
  /** The deterministic signal: do line items sum to the invoice total? */
  is_reconciled: boolean | null;
  reconciliation_variance: number | null;
  job_status: string | null;
  started_at: string | null;
  completed_at: string | null;
}

export interface InvoiceListResponse extends PaginatedResponse<InvoiceListItem> {
  status_counts: Record<string, number>;
}

// ── Extraction Job ─────────────────────────────────────────

export interface ExtractionJob {
  id: string;
  intake_id: string;
  extraction_path: ExtractionPath;
  extraction_model_id: string | null;
  supplier_profile_id: string | null;
  supplier_name: string | null;
  output_type: OutputType;
  status: string;
  ai_model_name: string | null;
  tokens_input: number;
  tokens_output: number;
  estimated_cost_usd: number;
  extraction_duration_ms: number;
  overall_confidence: number;
  line_item_count: number;
  is_reconciled: boolean | null;
  reconciliation_variance: number | null;
  warnings: Record<string, unknown> | null;
  error_message: string | null;
  started_at: string | null;
  completed_at: string | null;
  created_at: string;
  xlsx_output_filename: string | null;
}

// ── Processing Log ─────────────────────────────────────────

export interface ProcessingLog {
  id: number;
  intake_id: string;
  job_id: string | null;
  step: string;
  status: string;
  message: string | null;
  details: Record<string, unknown> | null;
  duration_ms: number | null;
  timestamp: string;
}

// ── Extraction Model ───────────────────────────────────────

export interface ExtractionModel {
  id: string;
  version: number;
  supplier: string;
  output_type: OutputType;
  supplier_profile_id: string | null;
  layout_fingerprint: string;
  layout_family_key: string | null;
  status: ModelStatus;
  model_json: Record<string, unknown>;
  confidence: number;
  allow_supplier_fallback: boolean;
  validation_attempt_count: number;
  validation_success_count: number;
  validation_failure_count: number;
  approved_after_intake_count: number;
  last_validation_error: string | null;
  validation_intake_ids: string[] | null;
  times_used: number;
  last_used_at: string | null;
  success_rate: number;
  created_by: string;
  created_from_intake: string | null;
  updated_by: string | null;
  approved_by: string | null;
  approved_at: string | null;
  retired_by: string | null;
  retired_at: string | null;
  notes: string | null;
  created_at: string;
  updated_at: string;
}

// ── Supplier Profile ───────────────────────────────────────

export interface SupplierIdentity {
  canonical_name: string;
  aliases: string[];
}

export interface ClassificationSignals {
  output_type: string;
}

export interface DocumentStructure {
  detail_start_marker: string | null;
  detail_end_marker?: string | null;
}

export interface LineItemHints {
  subtotal_keywords: string[];
  tax_keywords: string[];
  detail_table_anchors?: string[];
  line_item_granularity?: string | null;
  service_id_preference?: string | null;
  billing_reference_preference?: string | null;
  service_id_column_label?: string | null;
  billing_reference_column_label?: string | null;
  amount_column_label?: string | null;
  tax_amount_column_label?: string | null;
  amount_source?: string | null;
  tax_source?: string | null;
  tax_output_mode?: string | null;
  tax_rate_source?: string | null;
  service_id_value_pattern?: string | null;
  billing_reference_value_pattern?: string | null;
  skip_row_keywords?: string[];
  table_column_labels?: string[];
}

export interface CurrencyRules {
  default_code?: string | null;
  allowed_codes: string[];
  aliases: Record<string, string>;
}

export interface AdvancedHints {
  document_structure: DocumentStructure;
  line_item_hints: LineItemHints;
  currency: CurrencyRules;
  document_field_defaults?: Record<string, unknown>;
  include_zero_amount_line_items: boolean;
  require_line_item_identifier?: boolean;
  extraction_plan?: Record<string, unknown> | null;
}

export interface DeliveryAccount {
  label: string;
  inbound_path: string;
  output_path: string;
}

export interface DeliveryConfig {
  inbound_path: string | null;
  output_path: string | null;
  accounts?: DeliveryAccount[];
}

// ── Accounts view (discover folders on disk, reconcile vs profile mappings) ──

export type AccountState = "unmapped" | "mapped" | "conflict" | "missing";

export interface AccountRow {
  customer: string;
  account: string;
  inbound_path: string;
  output_path: string;
  pdf_count: number;
  state: AccountState;
  profile_ids: string[];
}

export interface AccountGroup {
  customer: string;
  accounts: AccountRow[];
  total: number;
  unmapped: number;
  conflict: number;
  missing: number;
}

export interface AccountsResponse {
  scan_base: string;
  groups: AccountGroup[];
  total_accounts: number;
  unmapped: number;
  conflict: number;
  missing: number;
  synced_at?: string | null;
  syncing?: boolean;
  never_synced?: boolean;
}

export interface AccountsSyncStatus {
  status?: "queued" | "running" | "done" | "error" | null;
  customers_done?: number | null;
  customers_total?: number | null;
  accounts_found?: number | null;
  scanned_at?: string | null;
  error?: string | null;
}

export interface LayoutFingerprintRules {
  summary_anchors: string[];
  currency_codes: string[];
  ignore_label_patterns: string[];
  optional_column_patterns: string[];
  exclude_span_patterns: string[];
}

export interface TrainingConfig {
  min_validation_successes: number;
  require_reconciliation: boolean;
}

export interface FieldDef {
  name: string;
  scope: "document" | "row";
  type: string;
  role: string;
  label_hint?: string | null;
  /** strptime-compatible format used to parse dates printed by this supplier. */
  date_format?: string | null;
  required?: boolean;
  enum_values?: string[];
  description?: string;
}

export interface FieldSchema {
  schema_id: string;
  name: string;
  fields: FieldDef[];
}

export interface FieldSchemaSummary {
  schema_id: string;
  name: string;
  field_count: number;
}

export interface OutputColumn {
  header: string;
  source: string;
  fallback?: string | null;
  width?: number;
  number_format?: string | null;
  transforms?: OutputTransform[];
}

export type OutputTransform = "digits_only" | "trim" | "uppercase" | "lowercase";

export interface OutputMappingOverride {
  output_header: string;
  source: string;
  fallback?: string | null;
  transforms?: OutputTransform[];
}

// ── Output preview (shared across processed / profile / model surfaces) ──

export interface PreviewColumn {
  header: string;
  number_format?: string | null;
}

export interface PreviewLabelValue {
  /** Canonical field key (e.g. "invoice_date") — lets callers correlate the
   * entry with the rule that produced it. */
  name?: string;
  label: string;
  value: string | number | boolean | null;
}

export interface PreviewReconciliation {
  is_reconciled: boolean;
  verification_status?: "reconciled" | "mismatch" | "unverifiable";
  line_items_sum?: number | null;
  reconcile_target?: number | null;
  stated_total?: number | null;
  computed_total?: number | null;
  variance?: number | null;
  variance_pct?: number | null;
  warnings?: string[];
}

/** A deterministic contradiction between a free-text note and a structured profile field. */
export interface SetupConflict {
  field: string;
  structured_value: string;
  note_fragment: string;
  /** "structured_wins" | "note_override" */
  resolution: string;
  message: string;
}

/** Which tax strategy actually delivered EXT_TAX for this extraction. */
export type TaxMethod = "per_line" | "allocated" | "rate" | "none";

export interface ExtractionPreview {
  columns: PreviewColumn[];
  rows: (string | number | boolean | null)[][];
  row_count: number;
  line_item_count: number;
  header_fields: PreviewLabelValue[];
  totals: PreviewLabelValue[];
  reconciliation?: PreviewReconciliation | null;
  extraction_path?: string | null;
  warnings?: string[];
  tax_method?: TaxMethod | string | null;
  conflicts?: SetupConflict[];
  ignored_notes?: string[];
}

export interface OutputSpec {
  spec_id: string;
  name: string;
  columns: OutputColumn[];
}

export interface OutputSpecSummary {
  spec_id: string;
  name: string;
  column_count: number;
}

export interface SupplierProfile {
  profile_id: string;
  version: number;
  status: ProfileStatus | string;
  layout_description: string | null;
  identity: SupplierIdentity;
  classification: ClassificationSignals;
  output_spec_id: string;
  field_schema_id: string;
  field_overrides?: FieldDef[];
  output_mapping_overrides?: OutputMappingOverride[];
  delivery: DeliveryConfig;
  notes: string | null;
  advanced: AdvancedHints;
  layout_fingerprint?: LayoutFingerprintRules | null;
  training_config?: TrainingConfig;
  authoring_evidence?: {
    evidence_level: string;
    status: "verified" | "review_required" | "failed";
    category_confidence: number;
    metrics: Record<string, unknown>;
    unresolved_risks: string[];
    hard_blockers?: string[];
    review_warnings?: string[];
    engine_version: string;
  } | null;
  owner: string | null;
  created_date: string | null;
  last_updated_date: string | null;
  audit?: Record<string, unknown> | null;
}

export interface ProfileSummary {
  profile_id: string;
  supplier_name: string;
  output_type: string;
  output_spec_id: string;
  version: number;
  status: string;
  layout_description?: string | null;
  inbound_path: string | null;
  output_path: string | null;
  account_count?: number;
  audit?: Record<string, unknown> | null;
}

// ── AI profile authoring assistant ─────────────────────────

export interface AuthoringMessage {
  role: "user" | "assistant";
  content: string;
  open_questions?: string[];
}

export interface AuthoringInvoiceSummary {
  id: string;
  filename: string;
  // "uploaded" = staged, not yet extracted (extraction is deferred to the first turn);
  // "pending" = extraction running; then "done" | "error".
  extraction_status: "uploaded" | "pending" | "done" | "error";
  has_expected: boolean;
  error_message?: string | null;
  scan_metadata?: Record<string, unknown> | null;
}

export interface AuthoringBlueprintSummary {
  id: string;
  filename: string;
  invoice_id?: string | null;
  matching_metadata?: Record<string, unknown> | null;
}

export interface AuthoringDiffReport {
  is_match: boolean;
  summary: string;
  expected_row_count: number;
  actual_row_count: number;
  columns: string[];
  missing_rows: (string | number | null)[][];
  extra_rows: (string | number | null)[][];
  cell_diffs: { row: number; column: string; expected: unknown; actual: unknown }[];
}

export interface AuthoringPreview {
  filename: string;
  preview?: ExtractionPreview;
  diff?: AuthoringDiffReport;
  diff_feedback?: string;
  error?: string;
}

export interface AuthoringJobSummary {
  id: string;
  kind: "turn" | "reextract" | "discover";
  status: "queued" | "running" | "done" | "error" | "cancelled";
  message?: string | null;
  invoice_id?: string | null;
  error_message?: string | null;
  created_at?: string | null;
  started_at?: string | null;
  finished_at?: string | null;
}

export interface AuthoringSessionState {
  session_id: string;
  status: "active" | "running" | "finalized";
  supplier_name: string | null;
  output_spec_id: string;
  field_schema_id: string;
  phase: string;
  inferred_category?: "standard" | "wireless" | "time_and_material" | null;
  category_override?: "standard" | "wireless" | "time_and_material" | null;
  discovery?: Record<string, unknown> | null;
  validation?: {
    evidence_level?: string;
    status?: "verified" | "review_required" | "failed";
    metrics?: Record<string, unknown>;
    unresolved_risks?: string[];
    hard_blockers?: string[];
    review_warnings?: string[];
  } | null;
  engine_version?: string | null;
  conversation: AuthoringMessage[];
  draft_profile: Record<string, unknown> | null;
  previews: Record<string, AuthoringPreview>;
  source_profile_id: string | null;
  finalized_profile_id: string | null;
  estimated_cost_usd: number;
  invoices: AuthoringInvoiceSummary[];
  blueprints: AuthoringBlueprintSummary[];
  jobs: AuthoringJobSummary[];
}

export interface AuthoringTurnResult {
  status: "queued" | "running" | "pending" | "done" | "error" | "cancelled";
  assistant_message?: string;
  open_questions?: string[];
  confidence?: number;
  draft_profile?: Record<string, unknown>;
  previews?: Record<string, AuthoringPreview>;
  reextracted_invoice_ids?: string[];
  detail?: string;
}

export interface AuthoringJobStart {
  job_id: string;
  status: "queued" | "running" | "pending";
}

// ── Training workflow ──────────────────────────────────────

export interface SimpleExtractionHints {
  output_spec_id: string;
  line_items_start_text: string | null;
  table_header_texts: string[];
  charge_listing_style: string | null;
  include_zero_amount_lines: boolean;
  notes: string | null;
}

export interface TrainingStatus {
  profile_id: string;
  profile_status: string;
  model_id: string | null;
  model_status: ModelStatus | null;
  layout_fingerprint: string | null;
  validation_success_count: number;
  validation_failure_count: number;
  validation_attempt_count: number;
  min_validation_successes: number;
  ready_for_approval: boolean;
  last_validation_error: string | null;
  validation_intake_ids: string[];
}

export interface TrainingUploadResponse {
  intake_id: string;
  profile_id: string;
  status: string;
}

// ── Dashboard ──────────────────────────────────────────────

export interface DashboardStats {
  total_invoices: number;
  completed_invoices: number;
  completed_with_warnings: number;
  failed_invoices: number;
  pending_exceptions: number;
  /** Share of scored jobs whose totals reconciled — the accuracy number. */
  success_rate: number;
  /** Share of invoices that finished the pipeline — the throughput number. */
  completion_rate: number;
  reconciled_jobs: number;
  scored_jobs: number;
  total_ai_cost: number;
  model_training_cost: number;
  extraction_cost: number;
  total_models: number;
  approved_models: number;
  model_path_count: number;
  ai_path_count: number;
}

export interface VolumeDataPoint {
  date: string;
  count: number;
}

export interface CostDataPoint {
  date: string;
  cost: number;
  tokens_input: number;
  tokens_output: number;
}

export interface MonthlyStats {
  month: string;
  total_cost: number;
  extraction_cost: number;
  classification_cost: number;
  training_cost: number;
  tokens_input: number;
  tokens_output: number;
  ai_calls: number;
  invoices_processed: number;
  invoices_completed: number;
  invoices_failed: number;
  ai_path_count: number;
  model_path_count: number;
}

export interface MonthlyCostPoint {
  month: string;
  cost: number;
}

export interface CostByModelPoint {
  model: string;
  cost: number;
  calls: number;
  tokens: number;
}

export interface CostBySupplierPoint {
  supplier_profile_id: string | null;
  account_label: string | null;
  cost: number;
  calls: number;
}

// ── Exceptions ─────────────────────────────────────────────

export interface ExceptionRecord {
  intake_id: string;
  reason: ExceptionReason;
  message: string;
  details: Record<string, unknown>;
  timestamp: string;
  original_filename: string | null;
  status: string;
  has_partial_extraction: boolean;
  has_original_pdf: boolean;
}

// ── AI call capture (dev-only prompt debugging) ────────────

export interface AiDebugCall {
  name: string;
  ts?: string;
  call_type?: string;
  context?: string | null;
  model?: string | null;
  max_tokens?: number | null;
  finish_reason?: string | null;
  tokens_input?: number;
  tokens_output?: number;
  est_cost_usd?: number;
  duration_ms?: number;
  status?: string;
  error?: string | null;
}

// ── Prompt evaluation (dev-only) ───────────────────────────

export interface EvalAggregate {
  cases: number;
  by_status: Record<string, number>;
  mean_row_f1: number | null;
  scored_cases: number;
  total_hallucinations: number;
  reconciled_pct: number | null;
  classification_pct: number | null;
  total_cost_usd: number;
  total_duration_ms?: number;
  total_ai_latency_ms?: number;
}

export interface EvalRunSummary {
  run_id: string;
  created_at: string;
  label: string;
  call_types: string[];
  model: string;
  git_sha: string;
  run_kind?: "baseline" | "current";
  baseline_run_id?: string | null;
  concurrency?: number | null;
  selected_case_count?: number | null;
  selected_layouts?: string[];
  total_work_items?: number | null;
  status?: string | null;
  done?: number | null;
  total?: number | null;
  aggregate: EvalAggregate;
}

export interface EvalCaseIndex {
  file: string;
  case_id: string;
  call_type: string;
  status: string;
  error?: string | null;
  row_f1?: number | null;
  hallucinations?: number;
  is_reconciled?: boolean | null;
  classification_match?: boolean | null;
  est_cost_usd?: number | null;
  duration_ms?: number | null;
  ai_latency_ms?: number | null;
}

export interface EvalRunDetail extends EvalRunSummary {
  cases: EvalCaseIndex[];
}

export interface EvalRunStatus {
  status: string; // queued | running | done | error
  done?: number;
  total?: number;
  detail?: string;
  created_at?: string;
  label?: string;
  call_types?: string[];
  run_kind?: "baseline" | "current";
  baseline_run_id?: string | null;
  concurrency?: number | null;
  selected_case_count?: number | null;
  selected_layouts?: string[];
  total_work_items?: number | null;
  model?: string;
  git_sha?: string;
}

export interface EvalCaseDetail {
  case_id: string;
  layout_id: string;
  call_type: string;
  status: string;
  error?: string | null;
  metrics: Record<string, unknown>;
  prompt_files: string[];
  output_rows: (string | number | null)[][];
  expected_rows: (string | number | null)[][];
  columns: string[];
}

export interface EvalCaseRef {
  case_id: string;
  layout_id: string;
  has_expected: boolean;
}

export interface EvalBaseline {
  run_id: string;
  accepted_at: string;
  accepted_from_label: string;
}

export interface EvalCompareRow {
  case_id: string;
  call_type: string;
  status?: "regressed" | "missing" | "new" | "unscored" | "improved" | "unchanged";
  a_status?: string | null;
  b_status?: string | null;
  a_row_f1: number | null;
  b_row_f1: number | null;
  delta_row_f1: number | null;
  a_hallucinations?: number | null;
  b_hallucinations?: number | null;
  a_is_reconciled?: boolean | null;
  b_is_reconciled?: boolean | null;
  a_est_cost_usd?: number | null;
  b_est_cost_usd?: number | null;
  a_duration_ms?: number | null;
  b_duration_ms?: number | null;
  a_ai_latency_ms?: number | null;
  b_ai_latency_ms?: number | null;
}

export interface EvalCompareResult {
  a: EvalAggregate;
  b: EvalAggregate;
  a_id: string;
  b_id: string;
  a_label?: string | null;
  b_label?: string | null;
  summary?: Record<string, number>;
  cases: EvalCompareRow[];
}

// ── Generic ────────────────────────────────────────────────

export interface MessageResponse {
  message: string;
  success: boolean;
}
