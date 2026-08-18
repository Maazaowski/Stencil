import { useCallback, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api/client";

/** A rectangle in normalized 0-1000 page-space (x0/x1 across width, y0/y1 down height). */
export interface LayoutBBox {
  x0: number;
  y0: number;
  x1: number;
  y1: number;
}

export interface LayoutCell {
  cell_id: string;
  column_index: number;
  role: string;
  text: string;
  bbox: LayoutBBox;
  normalized_bbox: LayoutBBox | null;
}

export interface LayoutRow {
  row_id: string;
  role: string;
  /** Which independent reading column the row belongs to (0 unless split). */
  reading_column?: number;
  text: string;
  bbox: LayoutBBox;
  normalized_bbox: LayoutBBox | null;
  cells: LayoutCell[];
}

export interface LayoutPage {
  page_id: string;
  page_number: number;
  size: { width: number; height: number };
  rows: LayoutRow[];
  tables: unknown[];
  /** Best-effort divider hint (normalized 0-1000); only sent when unsplit. */
  suggested_column_split_x?: number | null;
}

export interface IntakeLayout {
  intake_id: string;
  page_count: number;
  pages: LayoutPage[];
  tables: unknown[];
  warnings: string[];
  column_split_x?: number[];
}

/** Coordinate-aware layout of a stored invoice — the source the builder canvas
 * reconstructs the page from (text + normalized bboxes + roles).
 *
 * ``columnSplitX`` re-reads two-column pages as independent reading columns, so
 * the canvas shows the same rows the model will execute against. */
export function useIntakeLayout(intakeId: string, columnSplitX: number[] = []) {
  const splitKey = columnSplitX.join(",");
  return useQuery({
    queryKey: ["intakes", intakeId, "layout", splitKey],
    queryFn: () => {
      const qs = columnSplitX.map((x) => `column_split_x=${encodeURIComponent(x)}`).join("&");
      return api.get<IntakeLayout>(`/intakes/${intakeId}/layout${qs ? `?${qs}` : ""}`);
    },
    enabled: !!intakeId,
    staleTime: 5 * 60 * 1000, // layout is deterministic per PDF+split; cache it
  });
}

// --- Draft model being authored in the builder ---------------------------
// Mirrors the backend ExtractionModel schema (the parts the visual editors
// touch). Columns use normalized 0-1000 x-bands, matching ColumnDef.

/** A named vertical column band in normalized 0-1000 page space. */
export interface DraftColumnDef {
  name: string;
  x0: number;
  x1: number;
  header_text?: string | null;
}

/** The line-item region: where it starts/ends and its column bands. */
export interface DraftRegionRule {
  start_anchors: string[];
  end_anchors: string[];
  end_scope: string; // "document" | "page"
  columns: DraftColumnDef[];
  /**
   * Keep the start-anchor row as a data row instead of consuming it as a pure
   * delimiter. Needed when one text both opens the region and marks each item.
   */
  include_anchor_row?: boolean;
  /**
   * Vertical gutters (normalized 0-1000) splitting a two-column "newspaper" page
   * into independent reading columns. Empty = single column (the default).
   */
  column_split_x: number[];
}

/** Predicate for a row classifier (subset of the interpreter's RowMatch). */
export interface DraftRowMatch {
  row_text?: string | null;
  column?: string | null;
  pattern?: string | null;
  has_amount_in_column?: string | null;
}

/** Assigns a role to matching region rows; first match wins, order matters. */
export interface DraftRowClassifier {
  role: string;
  where: DraftRowMatch;
}

/** How classified rows collapse into line items (subset of GroupingRule). */
export interface DraftGroupingRule {
  mode: string; // "single_row" | "span" | "role_transition"
  item_role?: string | null;
  start_role?: string | null;
  end_role?: string | null;
  include_end_row: boolean;
  emit: string; // "one_item_per_group" | "one_item_per_role_row"
  emit_role?: string | null;
}

/** Where a line-item field's value comes from (subset of FieldSource). */
export interface DraftFieldSource {
  rows: string; // "first" | "last" | "all_in_group" | "role" | "emit_row"
  row_role?: string | null;
  column?: string | null;
  pattern?: string | null;
  join?: string | null;
  occurrence?: string; // "first" | "last"
}

/** How the raw text is coerced (subset of FieldTransform). */
export interface DraftFieldTransform {
  type: string; // "string" | "currency" | "integer" | "decimal" | "date"
  date_format?: string | null;
  default?: string | null;
}

/** One input to a value expression: extract from the page, a constant, or a
 * reference to another field/total. */
export interface DraftValueOperand {
  kind: "extract" | "const" | "ref";
  source?: DraftFieldSource;
  const?: string | null;
  ref?: string | null;
}

/** A field's value as an operation over operands — the unified mechanism for
 * direct extraction and computed amount/tax (e.g. tax = Taxes + Surcharges). */
export interface DraftValueExpr {
  op: "extract" | "sum" | "subtract" | "product";
  operands: DraftValueOperand[];
}

/** One canonical line-item field's extraction rule. */
export interface DraftItemFieldRule {
  name: string;
  source: DraftFieldSource;
  transform: DraftFieldTransform;
  required?: boolean;
  /** When set (op != extract), wins over `source`: a computed value. */
  value?: DraftValueExpr | null;
}

/** A document-level header field (subset of the interpreter's HeaderFieldRule).
 * Feeds EXT_DATE (invoice_date), formula (due_date), EXT_ACCOUNT (account_number),
 * EXT_INVOICENUMBER (invoice_number). */
export interface DraftHeaderFieldRule {
  label: string;
  value_position: string; // "right" | "below" | "left" | "above"
  value_pattern?: string | null;
  date_format?: string | null;
  ignore_percent?: boolean;
  occurrence?: string; // "first" | "last"
  literal?: string | null;
  required?: boolean;
}

/** How the delivered EXT_TAX column is produced. */
export interface DraftTaxConfig {
  mode: string; // "none" | "flat_rate" | "per_line" | "subtotal_tax"
  rate?: number | null; // flat_rate: percent, e.g. 20
}

export interface DraftModel {
  region: DraftRegionRule;
  row_classifiers: DraftRowClassifier[];
  grouping: DraftGroupingRule;
  item_fields: DraftItemFieldRule[];
  header_fields: Record<string, DraftHeaderFieldRule>;
  tax: DraftTaxConfig;
}

export function emptyDraftModel(): DraftModel {
  return {
    region: {
      start_anchors: [],
      end_anchors: [],
      end_scope: "document",
      columns: [],
      include_anchor_row: false,
      column_split_x: [],
    },
    row_classifiers: [],
    grouping: {
      mode: "single_row",
      item_role: null,
      start_role: null,
      end_role: null,
      include_end_row: true,
      emit: "one_item_per_group",
      emit_role: null,
    },
    item_fields: [],
    header_fields: {},
    tax: { mode: "none", rate: null },
  };
}

/** Working state for the model being built. Editors patch the region; more of
 * the model schema is added as later editors (classifiers, fields) land. */
export function useDraftModel(initial?: DraftModel) {
  const [draft, setDraft] = useState<DraftModel>(initial ?? emptyDraftModel());

  const updateRegion = useCallback(
    (patch: Partial<DraftRegionRule>) =>
      setDraft((d) => ({ ...d, region: { ...d.region, ...patch } })),
    [],
  );

  const setColumns = useCallback(
    (columns: DraftColumnDef[]) => updateRegion({ columns }),
    [updateRegion],
  );

  const setClassifiers = useCallback(
    (row_classifiers: DraftRowClassifier[]) => setDraft((d) => ({ ...d, row_classifiers })),
    [],
  );

  const updateGrouping = useCallback(
    (patch: Partial<DraftGroupingRule>) =>
      setDraft((d) => ({ ...d, grouping: { ...d.grouping, ...patch } })),
    [],
  );

  const setFields = useCallback(
    (item_fields: DraftItemFieldRule[]) => setDraft((d) => ({ ...d, item_fields })),
    [],
  );

  const setHeaderField = useCallback(
    (name: string, rule: DraftHeaderFieldRule | null) =>
      setDraft((d) => {
        const header_fields = { ...d.header_fields };
        if (rule === null) delete header_fields[name];
        else header_fields[name] = rule;
        return { ...d, header_fields };
      }),
    [],
  );

  const updateTax = useCallback(
    (patch: Partial<DraftTaxConfig>) =>
      setDraft((d) => ({ ...d, tax: { ...d.tax, ...patch } })),
    [],
  );

  return {
    draft,
    setDraft,
    updateRegion,
    setColumns,
    setClassifiers,
    updateGrouping,
    setFields,
    setHeaderField,
    updateTax,
  };
}

// --- Dry-run the draft against a stored invoice --------------------------

/** A label/value pair from the preview payload. `name` is the canonical field
 * key, used to match the entry back to the draft rule that produced it. */
export interface PreviewLabelValue {
  name?: string;
  label: string;
  value: unknown;
}

export interface PreviewOutput {
  columns: { header: string }[];
  rows: unknown[][];
  row_count: number;
  header_fields?: PreviewLabelValue[];
  totals?: PreviewLabelValue[];
  extraction_path?: string;
  reconciliation?: { is_reconciled: boolean; variance_pct?: number } | null;
}

/** A group that looked like an item but was dropped for a missing required field. */
export interface DroppedItem {
  reason: string;
  field: string;
  raw: string | null;
  row_ids: string[];
  text: string;
}

export interface DraftTestResult {
  intake_id: string;
  invoice: Record<string, unknown>;
  output: PreviewOutput;
  trace: { dropped_items?: DroppedItem[] } & Record<string, unknown>;
  diff: Record<string, unknown> | null;
  is_match: boolean | null;
}

/** What the model is being authored for: an existing supplier profile, or —
 * before one exists — the document type and deliverable chosen directly.
 *
 * This decides which columns the preview renders, so it is collected up front
 * rather than at save time; otherwise the whole authoring session is spent
 * checking output against a deliverable that isn't the real one. */
export interface BuilderTarget {
  profileId: string | null;
  /** Deliverable columns (OutputSpec). Used only when profileId is null. */
  outputSpecId: string | null;
  /** Document type / extraction contract (FieldSchema). Used only when profileId is null. */
  fieldSchemaId: string | null;
}

export function emptyBuilderTarget(): BuilderTarget {
  return { profileId: null, outputSpecId: null, fieldSchemaId: null };
}

/** Execute a draft model (not yet persisted) against a stored invoice and get
 * back the deliverable output + trace + diff vs the AI ground truth. */
export function useDraftTest() {
  return useMutation({
    mutationFn: ({
      intakeId,
      modelJson,
      target,
    }: {
      intakeId: string;
      modelJson: Record<string, unknown>;
      target?: BuilderTarget;
    }) =>
      api.post<DraftTestResult>("/models/draft/test", {
        intake_id: intakeId,
        model_json: modelJson,
        output_spec_id: target?.outputSpecId ?? null,
        field_schema_id: target?.fieldSchemaId ?? null,
      }),
  });
}

/** One column's contribution to a mismatch: how many rows it broke, and a
 * representative expected/actual pair. */
export interface CellDiffSummary {
  column: string;
  rows: number;
  expected: unknown;
  actual: unknown;
}

export interface EvalFolderFile {
  file: string;
  matched_rows?: number;
  expected_rows?: number;
  actual_rows?: number;
  is_match?: boolean;
  /** Per-column breakdown of why rows failed to match. */
  cell_diff_summary?: CellDiffSummary[];
  missing_rows?: number;
  extra_rows?: number;
  missing_examples?: unknown[][];
  extra_examples?: unknown[][];
  error?: string;
}

export interface EvalFolderResult {
  layout_id: string;
  files: EvalFolderFile[];
  files_matched: number;
  files_total: number;
}

/** Score a draft model against a whole eval-case folder (ST_DEBUG only): per-file
 * matched/expected row counts, the same diff the eval runner uses, at $0. */
export function useEvalFolder() {
  return useMutation({
    mutationFn: ({ layoutId, modelJson }: { layoutId: string; modelJson: Record<string, unknown> }) =>
      api.post<EvalFolderResult>("/models/draft/eval-folder", {
        layout_id: layoutId,
        model_json: modelJson,
      }),
  });
}

export interface CreatedSample {
  sample_id: string;
  filename: string;
}

/** Upload a sample invoice PDF to author/preview a model against. Returns a
 * sample_id usable with the layout, draft-test, and create endpoints. */
export function useCreateSample() {
  return useMutation({
    mutationFn: (file: File) => api.upload<CreatedSample>("/intakes/samples", file),
  });
}

export interface CreatedModel {
  id: string;
  status: string;
  supplier_profile_id?: string | null;
}

/** Persist a hand-built draft as a candidate model under a profile. Routing keys
 * are derived server-side from the sample invoice. */
export function useCreateManualModel() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      profileId,
      sampleIntakeId,
      modelJson,
    }: {
      profileId: string;
      sampleIntakeId: string;
      modelJson: Record<string, unknown>;
    }) =>
      api.post<CreatedModel>(`/profiles/${profileId}/models`, {
        sample_intake_id: sampleIntakeId,
        model_json: modelJson,
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["models"] });
      queryClient.invalidateQueries({ queryKey: ["profiles"] });
    },
  });
}
