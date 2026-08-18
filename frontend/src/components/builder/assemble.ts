import type { DraftModel, DraftTaxConfig } from "@/hooks/use-builder";

/** Map the builder's tax config to the model's tax policy + any totals it needs.
 * Mirrors the backend vocab (tax_output_mode / tax_rate_source) so execute_model
 * computes EXT_TAX with no profile. */
function taxToModel(tax: DraftTaxConfig): {
  tax_output_mode: string | null;
  tax_rate_source: string | null;
  totals: Record<string, unknown>;
} {
  switch (tax.mode) {
    case "flat_rate":
      return {
        tax_output_mode: "calculate",
        tax_rate_source: "invoice_tax_rate",
        totals: tax.rate != null ? { tax_rate: { label: "", literal: String(tax.rate) } } : {},
      };
    case "per_line":
      // The user maps a `tax_amount` item field (Fields tab); the rate is derived.
      return { tax_output_mode: "calculate", tax_rate_source: "consistent_line_tax_rate", totals: {} };
    case "subtotal_tax":
      return {
        tax_output_mode: "calculate",
        tax_rate_source: "invoice_tax_divided_by_subtotal",
        totals: {},
      };
    case "none":
    default:
      return { tax_output_mode: "none", tax_rate_source: null, totals: {} };
  }
}

/**
 * Assemble a draft into a full ExtractionModel `model_json` the backend can
 * validate and execute. Identity fields are placeholders for the dry-run — the
 * create endpoint derives the real ones (fingerprint, family key, profile) from
 * the sample invoice when the model is saved.
 *
 * `profileId` is the exception: passing it makes the dry-run resolve that
 * profile's OutputSpec, so the preview shows the columns the model will really
 * deliver instead of the system default. Save-time identity is still forced
 * server-side, so this cannot mis-key the persisted model.
 */
export function draftToModelJson(
  draft: DraftModel,
  profileId?: string | null,
): Record<string, unknown> {
  const header_fields = Object.fromEntries(
    Object.entries(draft.header_fields).map(([name, h]) => [
      name,
      {
        label: h.label,
        value_position: h.value_position,
        value_pattern: h.value_pattern || null,
        date_format: h.date_format || null,
        ignore_percent: !!h.ignore_percent,
        occurrence: h.occurrence ?? "first",
        literal: h.literal || null,
        required: !!h.required,
      },
    ]),
  );

  const tax = taxToModel(draft.tax);

  return {
    model_id: "draft",
    supplier_profile_id: profileId || "draft",
    supplier: "draft",
    region: {
      start_anchors: draft.region.start_anchors,
      end_anchors: draft.region.end_anchors,
      end_scope: draft.region.end_scope,
      include_anchor_row: draft.region.include_anchor_row ?? false,
      column_split_x: draft.region.column_split_x ?? [],
      columns: draft.region.columns.map((c) => ({
        name: c.name,
        x0: c.x0,
        x1: c.x1,
        header_text: c.header_text ?? null,
      })),
    },
    row_classifiers: draft.row_classifiers.map((c) => ({
      role: c.role,
      where: {
        row_text: c.where.row_text ?? null,
        column: c.where.column ?? null,
        pattern: c.where.pattern ?? null,
        has_amount_in_column: c.where.has_amount_in_column ?? null,
      },
    })),
    grouping: {
      mode: draft.grouping.mode,
      item_role: draft.grouping.item_role ?? null,
      start_role: draft.grouping.start_role ?? null,
      end_role: draft.grouping.end_role ?? null,
      include_end_row: draft.grouping.include_end_row,
      emit: draft.grouping.emit,
      emit_role: draft.grouping.emit_role ?? null,
    },
    item_fields: draft.item_fields.map((f) => {
      const serializedSource = {
        rows: f.source.rows,
        row_role: f.source.row_role ?? null,
        column: f.source.column ?? null,
        pattern: f.source.pattern ?? null,
        join: f.source.join ?? null,
        occurrence: f.source.occurrence ?? "first",
      };
      const out: Record<string, unknown> = {
        name: f.name,
        source: serializedSource,
        transform: {
          type: f.transform.type,
          date_format: f.transform.date_format ?? null,
          default: f.transform.default ?? null,
        },
        required: !!f.required,
      };
      // A computed value (op != extract) wins over the single source on the backend.
      if (f.value && f.value.op !== "extract" && f.value.operands.length > 0) {
        out.value = {
          op: f.value.op,
          operands: f.value.operands.map((o) => ({
            kind: o.kind,
            source:
              o.kind === "extract"
                ? {
                    rows: o.source?.rows ?? "all_in_group",
                    row_role: o.source?.row_role ?? null,
                    column: o.source?.column ?? null,
                    pattern: o.source?.pattern ?? null,
                  }
                : null,
            const: o.kind === "const" ? (o.const ?? null) : null,
            ref: o.kind === "ref" ? (o.ref ?? null) : null,
          })),
        };
      }
      return out;
    }),
    header_fields,
    totals: tax.totals,
    tax_output_mode: tax.tax_output_mode,
    tax_rate_source: tax.tax_rate_source,
  };
}
