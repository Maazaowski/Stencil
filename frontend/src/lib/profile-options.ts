/** Canonical enum values for supplier profile configuration. */

export type ProfileEnumOption = {
  value: string;
  label: string;
  hint?: string;
};

export const PROFILE_STATUS_OPTIONS: ProfileEnumOption[] = [
  { value: "draft", label: "Draft", hint: "Not processing inbound documents." },
  { value: "training", label: "Training", hint: "Upload samples and build an extraction model." },
  { value: "active", label: "Active", hint: "Live processing for watched inbound folders." },
  { value: "retired", label: "Retired", hint: "Archived; no longer used for routing." },
];

export const OUTPUT_TYPE_OPTIONS: ProfileEnumOption[] = [
  {
    value: "standard",
    label: "Standard",
    hint: "Default invoice / statement layout category.",
  },
  {
    value: "wireless",
    label: "Wireless",
    hint: "Wireless carrier-style layouts (salts layout fingerprint).",
  },
  {
    value: "time_and_material",
    label: "Time & Material",
    hint: "T&M / professional-services style layouts.",
  },
];

export const LINE_ITEM_GRANULARITY_OPTIONS: ProfileEnumOption[] = [
  {
    value: "per_charge_row",
    label: "One row per charge",
    hint: "Each billing period / charge line becomes one output row.",
  },
  {
    value: "per_service_group",
    label: "Grouped by service",
    hint: "Collapse charges under a service group (legacy alias).",
  },
  {
    value: "per_total_group",
    label: "Grouped with totals",
    hint: "One output row per section total (parent service ID + total amount).",
  },
];

export const SERVICE_ID_PREFERENCE_OPTIONS: ProfileEnumOption[] = [
  {
    value: "first_identifier",
    label: "Child / row ID (table column)",
    hint: "Use the identifier printed on each charge row (e.g. Colt Service ID column).",
  },
  {
    value: "parent_identifier",
    label: "Parent / section ID (Total footer)",
    hint: "Use the section parent ID from the Total line above the group (e.g. Total 446036137).",
  },
  {
    value: "total_row_identifier",
    label: "Total footer ID (same as parent)",
    hint: "Alias for parent_identifier — reads the ID from the Total footer row.",
  },
  {
    value: "leftmost_identifier",
    label: "Leftmost identifier in group",
    hint: "First identifier cell found left-to-right in the charge group.",
  },
  {
    value: "child_identifier",
    label: "Innermost child ID (carried down)",
    hint: "Use the innermost identifier in force at each charge row, carried across page breaks. Falls back to the parent when a service has no children; never a Total row's ID.",
  },
];

export const BILLING_REFERENCE_PREFERENCE_OPTIONS: ProfileEnumOption[] = [
  {
    value: "same_as_service_id",
    label: "Same as service ID",
    hint: "Billing reference mirrors service_id (single-ID layouts).",
  },
  {
    value: "parent_identifier",
    label: "Opposite hierarchy level",
    hint: "When service_id is the child row ID, put the parent section ID here (and vice versa).",
  },
  {
    value: "separate_column",
    label: "Second identifier column",
    hint: "Use a second identifier column when the layout has two ID columns.",
  },
  {
    value: "none",
    label: "Leave blank",
    hint: "Do not populate billing_reference unless AI extracts it.",
  },
];

export const TAX_OUTPUT_MODE_OPTIONS: ProfileEnumOption[] = [
  {
    value: "auto",
    label: "Auto (recommended)",
    hint: "Resolve tax automatically: printed row tax if present, else allocate an invoice-level tax total across rows, else a resolved rate, else blank.",
  },
  {
    value: "extract_exact",
    label: "Extract exact row tax",
    hint: "Only write EXT_TAX from a printed row tax value; blanks stay blank.",
  },
  {
    value: "calculate",
    label: "Calculate missing row tax",
    hint: "Use printed row tax first, then calculate blanks from the selected tax rate source.",
  },
  {
    value: "none",
    label: "No row tax",
    hint: "Always leave EXT_TAX blank for delivered rows.",
  },
];

export const TAX_RATE_SOURCE_OPTIONS: ProfileEnumOption[] = [
  {
    value: "auto",
    label: "Auto",
    hint: "Choose the best available rate from invoice totals or consistent line taxes.",
  },
  {
    value: "invoice_tax_rate",
    label: "Invoice tax rate field",
    hint: "Use the extracted document-level tax_rate value.",
  },
  {
    value: "invoice_tax_divided_by_subtotal",
    label: "Invoice tax ÷ subtotal",
    hint: "Compute the rate from stated invoice tax and subtotal.",
  },
  {
    value: "consistent_line_tax_rate",
    label: "Consistent line tax rate",
    hint: "Infer one rate from line items that already have both amount and tax.",
  },
];

export const AMOUNT_SOURCE_OPTIONS: ProfileEnumOption[] = [
  {
    value: "group_total",
    label: "Group / section total",
    hint: "Use the section total (footer) amount for the delivered row.",
  },
  {
    value: "first_amount",
    label: "First amount in group",
    hint: "Use the first amount found in the charge group.",
  },
  {
    value: "label_amount",
    label: "Labelled amount",
    hint: "Use the amount printed against the amount_column_label.",
  },
  {
    value: "label_amount_minus_tax",
    label: "Labelled amount - tax",
    hint: "Use the labelled amount, then subtract the row tax (net).",
  },
  {
    value: "table_charges_column",
    label: "Table charges column",
    hint: "Read the amount from the detail table's charges column.",
  },
];

export const TAX_SOURCE_OPTIONS: ProfileEnumOption[] = [
  {
    value: "none",
    label: "No tax source",
    hint: "Do not derive a row tax from the layout.",
  },
  {
    value: "label_amount",
    label: "Labelled tax amount",
    hint: "Use the tax printed against the tax_amount_column_label.",
  },
  {
    value: "group_total_minus_amount",
    label: "Group total - amount",
    hint: "Derive tax as the group total minus the delivered amount.",
  },
  {
    value: "table_tax_column",
    label: "Table tax column",
    hint: "Read tax from the detail table's tax column.",
  },
];

export const YES_NO_OPTIONS: ProfileEnumOption[] = [
  { value: "no", label: "No" },
  { value: "yes", label: "Yes" },
];

/** Values allowed for each nullable advanced hint field (for save validation). */
export const PROFILE_ENUM_FIELDS = {
  "classification.output_type": OUTPUT_TYPE_OPTIONS,
  status: PROFILE_STATUS_OPTIONS,
  "line_item_hints.line_item_granularity": LINE_ITEM_GRANULARITY_OPTIONS,
  "line_item_hints.service_id_preference": SERVICE_ID_PREFERENCE_OPTIONS,
  "line_item_hints.billing_reference_preference": BILLING_REFERENCE_PREFERENCE_OPTIONS,
  "line_item_hints.amount_source": AMOUNT_SOURCE_OPTIONS,
  "line_item_hints.tax_source": TAX_SOURCE_OPTIONS,
  "line_item_hints.tax_output_mode": TAX_OUTPUT_MODE_OPTIONS,
  "line_item_hints.tax_rate_source": TAX_RATE_SOURCE_OPTIONS,
} as const;

export function isAllowedEnumValue(
  fieldKey: keyof typeof PROFILE_ENUM_FIELDS,
  value: string | null | undefined
): boolean {
  if (value == null || value === "") return true;
  return PROFILE_ENUM_FIELDS[fieldKey].some((o) => o.value === value);
}

export function optionHint(
  options: ProfileEnumOption[],
  value: string | null | undefined
): string | undefined {
  return options.find((o) => o.value === value)?.hint;
}
