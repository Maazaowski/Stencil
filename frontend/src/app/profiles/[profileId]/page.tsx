"use client";

import { use, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { toast } from "sonner";
import { api } from "@/lib/api/client";
import { toastErrorMessage } from "@/lib/api/errors";
import {
  useProfile,
  useUpdateProfile,
  useCreateProfile,
  useStartTraining,
  useActivateProfile,
  useRetireProfile,
  useTrainingStatus,
  useProfilePreview,
} from "@/hooks/use-profiles";
import { PdfPreviewPanel } from "@/components/pdf-preview-panel";
import { ErrorState, LoadingState } from "@/components/states";
import { useFieldSchema, useFieldSchemas } from "@/hooks/use-field-schemas";
import { useOutputSpecs, useOutputSpec } from "@/hooks/use-output-specs";
import { useApproveModel, useBootstrapModel } from "@/hooks/use-models";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { useConfirm } from "@/components/ui/confirm-dialog";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Switch } from "@/components/ui/switch";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import {
  applyOutputMappingOverrides,
  ExtractionFieldSettings,
  FixedDocumentValues,
  GeneratedProfileDetails,
  mergeProfileFields,
  OutputMappingEditor,
} from "@/components/profiles/profile-configuration";
import { TagInput } from "@/components/form/tag-input";
import { EnumFieldHint, EnumSelectField } from "@/components/form/enum-select-field";
import {
  AMOUNT_SOURCE_OPTIONS,
  BILLING_REFERENCE_PREFERENCE_OPTIONS,
  isAllowedEnumValue,
  LINE_ITEM_GRANULARITY_OPTIONS,
  OUTPUT_TYPE_OPTIONS,
  PROFILE_STATUS_OPTIONS,
  SERVICE_ID_PREFERENCE_OPTIONS,
  TAX_OUTPUT_MODE_OPTIONS,
  TAX_RATE_SOURCE_OPTIONS,
  TAX_SOURCE_OPTIONS,
} from "@/lib/profile-options";
import {
  ArrowLeft,
  Save,
  Loader2,
  Code,
  Download,
  Upload,
  GraduationCap,
  CheckCircle2,
  XCircle,
  Power,
  Archive,
  Boxes,
  Plus,
  Trash2,
  Sparkles,
  Wrench,
  Landmark,
  MoreHorizontal,
} from "lucide-react";
import Link from "next/link";
import { modelDetailPath } from "@/lib/model-routes";
import type {
  AdvancedHints,
  CurrencyRules,
  DeliveryAccount,
  DocumentStructure,
  FieldDef,
  FieldSchema,
  LayoutFingerprintRules,
  LineItemHints,
  OutputColumn,
  OutputMappingOverride,
  SetupConflict,
  SupplierProfile,
  TrainingConfig,
} from "@/types";

const DATE_FORMAT_SUGGESTIONS = [
  { value: "%m/%d/%Y", label: "US — month/day/year (12/31/2026)" },
  { value: "%d/%m/%Y", label: "European — day/month/year (31/12/2026)" },
  { value: "%Y-%m-%d", label: "ISO — year-month-day (2026-12-31)" },
  { value: "%m-%d-%Y", label: "US with dashes (12-31-2026)" },
  { value: "%d-%m-%Y", label: "Day-month-year (31-12-2026)" },
  { value: "%d.%m.%Y", label: "Day.month.year (31.12.2026)" },
  { value: "%d %B %Y", label: "Day and month name (31 December 2026)" },
] as const;

// Normalized accounts: explicit list when set, else the legacy single folder pair
// as one account, else empty. Read-only in the editor now — accounts are mapped
// in the Accounts view.
function deliveryAccountsOf(p: SupplierProfile): DeliveryAccount[] {
  const accts = p.delivery.accounts ?? [];
  if (accts.length > 0) return accts;
  if (p.delivery.inbound_path && p.delivery.output_path) {
    return [{
      label: p.identity.canonical_name || p.profile_id || "default",
      inbound_path: p.delivery.inbound_path,
      output_path: p.delivery.output_path,
    }];
  }
  return [];
}

function emptyLayoutFingerprint(): LayoutFingerprintRules {
  return {
    summary_anchors: [],
    currency_codes: [],
    ignore_label_patterns: [],
    optional_column_patterns: [],
    exclude_span_patterns: [],
  };
}

function emptyAdvanced(): AdvancedHints {
  return {
    document_structure: {
      detail_start_marker: null,
      detail_end_marker: null,
    },
    line_item_hints: {
      subtotal_keywords: [],
      tax_keywords: [],
      detail_table_anchors: [],
      table_column_labels: [],
      line_item_granularity: null,
      service_id_preference: null,
      billing_reference_preference: null,
      amount_column_label: null,
      tax_amount_column_label: null,
      amount_source: null,
      tax_source: null,
      tax_output_mode: "auto",
      tax_rate_source: null,
      service_id_value_pattern: null,
      billing_reference_value_pattern: null,
      skip_row_keywords: [],
    },
    currency: {
      default_code: null,
      allowed_codes: [],
      aliases: {},
    },
    document_field_defaults: {},
    include_zero_amount_line_items: false,
  };
}

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

function nullableString(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value : null;
}

function stringArray(value: unknown): string[] {
  if (Array.isArray(value)) {
    return value
      .map((item) => (typeof item === "string" ? item.trim() : String(item).trim()))
      .filter(Boolean);
  }
  if (typeof value === "string") {
    return value
      .split(/[\n,]/)
      .map((item) => item.trim())
      .filter(Boolean);
  }
  return [];
}

function optionalEnumString(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value : null;
}

function currencyCode(value: unknown): string | null {
  if (typeof value !== "string") return null;
  const code = value.trim().toUpperCase();
  return /^[A-Z]{3}$/.test(code) ? code : null;
}

function normalizeCurrencyRules(raw: unknown): CurrencyRules {
  const rules = asRecord(raw);
  const aliasesRecord = asRecord(rules.aliases);
  const aliases = Object.fromEntries(
    Object.entries(aliasesRecord)
      .map(([alias, code]) => [alias.trim(), currencyCode(code)])
      .filter(([alias, code]) => alias && code)
  ) as Record<string, string>;
  return {
    default_code: currencyCode(rules.default_code),
    allowed_codes: stringArray(rules.allowed_codes)
      .map((code) => currencyCode(code))
      .filter((code): code is string => Boolean(code)),
    aliases,
  };
}

function normalizeAdvanced(rawProfile: unknown): AdvancedHints {
  const profileRecord = asRecord(rawProfile);
  const rawAdvanced = asRecord(profileRecord.advanced);
  const rawDocumentStructure = {
    ...asRecord(profileRecord.document_structure),
    ...asRecord(rawAdvanced.document_structure),
  };
  const rawLineItemHints = {
    ...asRecord(profileRecord.line_item_hints),
    ...asRecord(rawAdvanced.line_item_hints),
  };
  const defaults = emptyAdvanced();

  return {
    // Spread the raw blocks first so any field this form does not model (e.g. a
    // newly-added backend hint) survives the load -> save round-trip. The explicit
    // normalized fields below override the known keys. This is the safety net that
    // stops the editor from silently dropping backend-supported config.
    ...rawAdvanced,
    document_structure: {
      ...rawDocumentStructure,
      detail_start_marker:
        nullableString(rawDocumentStructure.detail_start_marker) ??
        defaults.document_structure.detail_start_marker,
      detail_end_marker:
        nullableString(rawDocumentStructure.detail_end_marker) ??
        defaults.document_structure.detail_end_marker,
    } as DocumentStructure,
    line_item_hints: {
      ...rawLineItemHints,
      subtotal_keywords: stringArray(rawLineItemHints.subtotal_keywords),
      tax_keywords: stringArray(rawLineItemHints.tax_keywords),
      detail_table_anchors: stringArray(rawLineItemHints.detail_table_anchors),
      table_column_labels: stringArray(rawLineItemHints.table_column_labels),
      skip_row_keywords: stringArray(rawLineItemHints.skip_row_keywords),
      line_item_granularity: optionalEnumString(rawLineItemHints.line_item_granularity),
      service_id_preference: optionalEnumString(rawLineItemHints.service_id_preference),
      billing_reference_preference: optionalEnumString(
        rawLineItemHints.billing_reference_preference
      ),
      service_id_column_label: optionalEnumString(rawLineItemHints.service_id_column_label),
      billing_reference_column_label: optionalEnumString(
        rawLineItemHints.billing_reference_column_label
      ),
      amount_column_label: optionalEnumString(rawLineItemHints.amount_column_label),
      tax_amount_column_label: optionalEnumString(rawLineItemHints.tax_amount_column_label),
      amount_source: optionalEnumString(rawLineItemHints.amount_source),
      tax_source: optionalEnumString(rawLineItemHints.tax_source),
      tax_output_mode: optionalEnumString(rawLineItemHints.tax_output_mode),
      tax_rate_source: optionalEnumString(rawLineItemHints.tax_rate_source),
      service_id_value_pattern: nullableString(rawLineItemHints.service_id_value_pattern),
      billing_reference_value_pattern: nullableString(
        rawLineItemHints.billing_reference_value_pattern
      ),
    } as LineItemHints,
    currency: normalizeCurrencyRules(rawAdvanced.currency),
    include_zero_amount_line_items:
      typeof rawAdvanced.include_zero_amount_line_items === "boolean"
        ? rawAdvanced.include_zero_amount_line_items
        : typeof profileRecord.include_zero_amount_line_items === "boolean"
          ? profileRecord.include_zero_amount_line_items
          : defaults.include_zero_amount_line_items,
    require_line_item_identifier:
      typeof rawAdvanced.require_line_item_identifier === "boolean"
        ? rawAdvanced.require_line_item_identifier
        : typeof profileRecord.require_line_item_identifier === "boolean"
          ? profileRecord.require_line_item_identifier
          : defaults.require_line_item_identifier,
  } as AdvancedHints;
}

function normalizeLayoutFingerprint(raw: unknown): LayoutFingerprintRules | null {
  if (raw == null) return null;
  const rules = asRecord(raw);
  // Spread first so any fingerprint rule this form does not model survives the
  // round-trip; the normalized known lists override. (Same lossless pattern as
  // normalizeAdvanced - prevents silent drift when the backend adds a rule.)
  return {
    ...rules,
    summary_anchors: stringArray(rules.summary_anchors),
    currency_codes: stringArray(rules.currency_codes),
    ignore_label_patterns: stringArray(rules.ignore_label_patterns),
    optional_column_patterns: stringArray(rules.optional_column_patterns),
    exclude_span_patterns: stringArray(rules.exclude_span_patterns),
  } as LayoutFingerprintRules;
}

function normalizeDelivery(raw: unknown): SupplierProfile["delivery"] {
  const delivery = asRecord(raw);
  const accounts = Array.isArray(delivery.accounts)
    ? delivery.accounts.map((account) => {
        const record = asRecord(account);
        return {
          ...record,
          label: typeof record.label === "string" ? record.label : "",
          inbound_path: typeof record.inbound_path === "string" ? record.inbound_path : "",
          output_path: typeof record.output_path === "string" ? record.output_path : "",
        };
      })
    : [];
  // Spread the raw delivery block first so unmodeled keys survive; override the
  // known fields with normalized values.
  return {
    ...delivery,
    inbound_path: typeof delivery.inbound_path === "string" ? delivery.inbound_path : null,
    output_path: typeof delivery.output_path === "string" ? delivery.output_path : null,
    accounts,
  } as SupplierProfile["delivery"];
}

function normalizeProfile(rawProfile: unknown): SupplierProfile {
  const raw = asRecord(rawProfile);
  const defaults = emptyProfile();
  const identity = asRecord(raw.identity);
  const classification = asRecord(raw.classification);

  return {
    ...defaults,
    ...(raw as Partial<SupplierProfile>),
    profile_id: typeof raw.profile_id === "string" ? raw.profile_id : defaults.profile_id,
    version: typeof raw.version === "number" ? raw.version : defaults.version,
    status: typeof raw.status === "string" ? raw.status : defaults.status,
    layout_description:
      typeof raw.layout_description === "string" ? raw.layout_description : null,
    identity: {
      canonical_name:
        typeof identity.canonical_name === "string"
          ? identity.canonical_name
          : defaults.identity.canonical_name,
      aliases: stringArray(identity.aliases),
    },
    classification: {
      output_type:
        typeof classification.output_type === "string"
          ? classification.output_type
          : defaults.classification.output_type,
    },
    output_spec_id:
      typeof raw.output_spec_id === "string" ? raw.output_spec_id : defaults.output_spec_id,
    field_schema_id:
      typeof raw.field_schema_id === "string" ? raw.field_schema_id : defaults.field_schema_id,
    field_overrides: Array.isArray(raw.field_overrides)
      ? (raw.field_overrides as FieldDef[])
      : defaults.field_overrides,
    output_mapping_overrides: Array.isArray(raw.output_mapping_overrides)
      ? (raw.output_mapping_overrides as OutputMappingOverride[])
      : defaults.output_mapping_overrides,
    delivery: normalizeDelivery(raw.delivery),
    notes: typeof raw.notes === "string" ? raw.notes : null,
    advanced: normalizeAdvanced(rawProfile),
    layout_fingerprint: normalizeLayoutFingerprint(raw.layout_fingerprint),
    owner: typeof raw.owner === "string" ? raw.owner : null,
    created_date:
      typeof raw.created_date === "string" ? raw.created_date : defaults.created_date,
    last_updated_date:
      typeof raw.last_updated_date === "string"
        ? raw.last_updated_date
        : defaults.last_updated_date,
    audit: asRecord(raw.audit),
  };
}

function auditDisplay(audit: Record<string, unknown> | null | undefined, key: "created" | "updated") {
  const item = audit?.[key];
  if (!item || typeof item !== "object" || Array.isArray(item)) return null;
  const record = item as Record<string, unknown>;
  const display = typeof record.display === "string" ? record.display : "system";
  const at = typeof record.at === "string" ? record.at : null;
  return { display, at };
}

function schemaHasRowFields(schema: FieldSchema | undefined): boolean {
  return !!schema?.fields.some((f) => f.scope === "row");
}

function schemaSupportsReconciliation(schema: FieldSchema | undefined): boolean {
  if (!schema) return false;
  const hasAmount = schema.fields.some((f) => f.scope === "row" && f.role === "amount");
  const hasTotal = schema.fields.some(
    (f) => f.scope === "document" && (f.role === "total" || f.role === "subtotal")
  );
  return hasAmount && hasTotal;
}

function schemaSupportsGroupedRows(schema: FieldSchema | undefined): boolean {
  return !!schema?.fields.some((f) => f.scope === "row" && f.name === "charge_type");
}

const FIELD_NAME_ACRONYMS: Record<string, string> = {
  id: "ID",
  po: "PO",
  ban: "BAN",
  vat: "VAT",
};
function humanizeFieldName(name: string): string {
  return name
    .split("_")
    .map((w) => FIELD_NAME_ACRONYMS[w] ?? w.charAt(0).toUpperCase() + w.slice(1))
    .join(" ");
}

// ── Output-driven field mapping ───────────────────────────
// A supplier's only per-field config is "what does this output value look like on
// the invoice?". We derive that set from the OutputSpec's columns (not the whole
// 34-field schema). This MUST mirror normalize_source_path in
// src/stencil/output/mapper.py so the UI resolves the same fields the writer does.

const BARE_TOTAL_FIELDS = new Set([
  "subtotal",
  "tax",
  "fees",
  "total_due",
  "tax_rate",
  "current_charges",
]);

// Derived output columns map back to the field a user would label.
const COMPUTED_FIELD_TARGETS: Record<string, { name: string; scope: "document" | "row" }> = {
  "computed.line_tax": { name: "tax_amount", scope: "row" },
};

function resolveSourceToField(source: string, schema: FieldSchema): FieldDef | undefined {
  if (!source) return undefined;
  const find = (name: string, scope: "document" | "row") =>
    schema.fields.find((f) => f.name === name && f.scope === scope);
  if (source.startsWith("computed.")) {
    const t = COMPUTED_FIELD_TARGETS[source];
    return t ? find(t.name, t.scope) : undefined;
  }
  if (source.startsWith("field.")) return find(source.slice(6), "document");
  if (source.startsWith("header.")) return find(source.slice(7), "document");
  if (source.startsWith("row.")) return find(source.slice(4), "row");
  if (source.startsWith("line_item.")) return find(source.slice(10), "row");
  if (BARE_TOTAL_FIELDS.has(source)) return find(source, "document");
  return undefined; // literals / constants / unknown → not user-labelable
}

// One entry per distinct underlying field, in column order, annotated with the
// output headers it feeds (so the user sees "Amount → EXT_AMOUNT").
function outputFieldTargets(
  spec: { columns: OutputColumn[] } | undefined,
  schema: FieldSchema | undefined,
): { field: FieldDef; columns: string[]; fallbackColumns: string[] }[] {
  if (!spec || !schema) return [];
  const byKey = new Map<
    string,
    { field: FieldDef; columns: string[]; fallbackColumns: string[] }
  >();
  // A field "feeds" a column only when it is that column's PRIMARY source.
  // Being a fallback (used only when the primary is empty) is tracked separately
  // so, e.g., service_id and billing_reference don't both claim both columns.
  const add = (field: FieldDef | undefined, header: string, primary: boolean) => {
    if (!field) return;
    const key = `${field.scope}.${field.name}`;
    const entry = byKey.get(key) ?? { field, columns: [], fallbackColumns: [] };
    const bucket = primary ? entry.columns : entry.fallbackColumns;
    if (!bucket.includes(header)) bucket.push(header);
    byKey.set(key, entry);
  };
  for (const col of spec.columns) {
    add(resolveSourceToField(col.source, schema), col.header, true);
    if (col.fallback) add(resolveSourceToField(col.fallback, schema), col.header, false);
  }
  return Array.from(byKey.values());
}

// ── Default empty profile ─────────────────────────────────

const PROFILE_ID_PATTERN = /^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/;

function validateProfileBeforeSave(profile: SupplierProfile, isNew: boolean): string | null {
  if (isNew && !PROFILE_ID_PATTERN.test(profile.profile_id)) {
    return "Profile ID is required and must start with a letter or number (letters, numbers, dots, dashes, underscores only; max 128 chars).";
  }
  if (!profile.identity.canonical_name.trim()) {
    return "Missing canonical supplier name.";
  }
  // Account folder mapping is validated server-side (a profile can only go
  // active/training once it has accounts) and managed in the Accounts view.
  if (!isAllowedEnumValue("status", profile.status)) {
    return `Invalid profile status "${profile.status}".`;
  }
  if (!isAllowedEnumValue("classification.output_type", profile.classification.output_type)) {
    return `Invalid document category "${profile.classification.output_type}".`;
  }
  const hints = profile.advanced.line_item_hints;
  if (!isAllowedEnumValue("line_item_hints.line_item_granularity", hints.line_item_granularity)) {
    return `Invalid line item granularity "${hints.line_item_granularity}".`;
  }
  if (!isAllowedEnumValue("line_item_hints.service_id_preference", hints.service_id_preference)) {
    return `Invalid service ID preference "${hints.service_id_preference}".`;
  }
  if (
    !isAllowedEnumValue(
      "line_item_hints.billing_reference_preference",
      hints.billing_reference_preference
    )
  ) {
    return `Invalid billing reference preference "${hints.billing_reference_preference}".`;
  }
  if (!isAllowedEnumValue("line_item_hints.tax_output_mode", hints.tax_output_mode)) {
    return `Invalid tax output mode "${hints.tax_output_mode}".`;
  }
  if (!isAllowedEnumValue("line_item_hints.tax_rate_source", hints.tax_rate_source)) {
    return `Invalid tax rate source "${hints.tax_rate_source}".`;
  }
  if (!isAllowedEnumValue("line_item_hints.amount_source", hints.amount_source)) {
    return `Invalid amount source "${hints.amount_source}".`;
  }
  if (!isAllowedEnumValue("line_item_hints.tax_source", hints.tax_source)) {
    return `Invalid tax source "${hints.tax_source}".`;
  }
  return null;
}

function emptyProfile(): SupplierProfile {
  return {
    profile_id: "",
    version: 1,
    status: "draft",
    layout_description: null,
    identity: {
      canonical_name: "",
      aliases: [],
    },
    classification: {
      output_type: "standard",
    },
    output_spec_id: "temforce.standard",
    field_schema_id: "invoice.standard",
    field_overrides: [],
    output_mapping_overrides: [],
    delivery: {
      inbound_path: "",
      output_path: "",
    },
    notes: "",
    advanced: emptyAdvanced(),
    layout_fingerprint: null,
    training_config: {
      min_validation_successes: 3,
      require_reconciliation: true,
    },
    owner: "",
    created_date: new Date().toISOString().split("T")[0],
    last_updated_date: new Date().toISOString().split("T")[0],
    audit: null,
  };
}

// ── Section wrapper ───────────────────────────────────────

function Section({
  title,
  description,
  children,
}: {
  title: string;
  description?: string;
  children: React.ReactNode;
}) {
  return (
    <Card>
      <CardHeader className="pb-3">
        <CardTitle className="text-sm font-medium">{title}</CardTitle>
        {description && (
          <p className="text-xs text-muted-foreground">{description}</p>
        )}
      </CardHeader>
      <CardContent className="space-y-4">{children}</CardContent>
    </Card>
  );
}

/**
 * Mirror the per-field labels entered in Setup §3 (field_overrides) into the
 * deterministic line-item column hints, so service_id / billing_reference are
 * asked for in exactly ONE place. When the §3 label is set it wins (resolving any
 * prior drift between the two). Amount/tax are deliberately NOT mirrored — their
 * deterministic column can legitimately differ from the AI label (e.g. a net
 * "before taxes" column vs a broader amount label), so those keep their own
 * explicit policy inputs and are never overwritten here.
 */
function mirrorRowLabelsToColumnHints(profile: SupplierProfile): SupplierProfile {
  const labelOf = (name: string) =>
    profile.field_overrides?.find((o) => o.scope === "row" && o.name === name)
      ?.label_hint?.trim() || undefined;
  const hints = profile.advanced.line_item_hints;
  return {
    ...profile,
    advanced: {
      ...profile.advanced,
      line_item_hints: {
        ...hints,
        service_id_column_label:
          labelOf("service_id") ?? hints.service_id_column_label ?? null,
        billing_reference_column_label:
          labelOf("billing_reference") ?? hints.billing_reference_column_label ?? null,
      },
    },
  };
}

function Field({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: React.ReactNode;
}) {
  return (
    <div className="space-y-1.5">
      <label className="text-xs font-medium text-muted-foreground">
        {label}
      </label>
      {children}
      {hint && <p className="text-[11px] text-muted-foreground/80">{hint}</p>}
    </div>
  );
}

function SetupConflictsPanel({ conflicts }: { conflicts: SetupConflict[] }) {
  if (!conflicts.length) return null;
  return (
    <div className="rounded-md border border-warning/30 bg-warning/12 px-3 py-2 text-xs text-warning">
      <p className="font-medium">
        {conflicts.length} setup conflict{conflicts.length === 1 ? "" : "s"} — a note
        contradicts a configured field
      </p>
      <ul className="mt-1 list-disc space-y-1 pl-4">
        {conflicts.map((c, i) => (
          <li key={i}>
            <span className="font-mono">{c.field}</span> ={" "}
            <span className="font-medium">{c.structured_value}</span>: {c.message}
            {c.resolution === "note_override" && (
              <span className="font-medium"> (note overrides this field)</span>
            )}
            {c.note_fragment && (
              <p className="mt-0.5 italic opacity-80">&ldquo;{c.note_fragment}&rdquo;</p>
            )}
          </li>
        ))}
      </ul>
    </div>
  );
}

const STATUS_BADGE: Record<string, string> = {
  draft: "bg-muted text-muted-foreground",
  training: "bg-primary/12 text-primary",
  active: "bg-success/12 text-success",
  retired: "bg-warning/12 text-warning",
};

// ── Training workflow panel ───────────────────────────────

function CurrencyAliasEditor({
  aliases,
  onChange,
}: {
  aliases: Record<string, string>;
  onChange: (aliases: Record<string, string>) => void;
}) {
  const [draftAlias, setDraftAlias] = useState("");
  const [draftCode, setDraftCode] = useState("");
  const entries = Object.entries(aliases);

  function replaceAlias(oldAlias: string, nextAlias: string, nextCode: string) {
    const updated = { ...aliases };
    delete updated[oldAlias];
    const alias = nextAlias.trim();
    const code = currencyCode(nextCode);
    if (alias && code) updated[alias] = code;
    onChange(updated);
  }

  function addAlias() {
    const alias = draftAlias.trim();
    const code = currencyCode(draftCode);
    if (!alias || !code) return;
    onChange({ ...aliases, [alias]: code });
    setDraftAlias("");
    setDraftCode("");
  }

  return (
    <div className="space-y-2">
      {entries.map(([alias, code]) => (
        <div key={alias} className="grid grid-cols-[1fr_90px_36px] gap-2">
          <Input
            value={alias}
            onChange={(e) => replaceAlias(alias, e.target.value, code)}
            placeholder="Printed alias"
          />
          <Input
            value={code}
            onChange={(e) => replaceAlias(alias, alias, e.target.value)}
            placeholder="INR"
            maxLength={3}
          />
          <Button
            type="button"
            variant="ghost"
            size="icon"
            onClick={() => {
              const updated = { ...aliases };
              delete updated[alias];
              onChange(updated);
            }}
            aria-label={`Remove currency alias ${alias}`}
          >
            <Trash2 className="h-4 w-4" />
          </Button>
        </div>
      ))}
      <div className="grid grid-cols-[1fr_90px_36px] gap-2">
        <Input
          value={draftAlias}
          onChange={(e) => setDraftAlias(e.target.value)}
          placeholder="e.g. Rupees"
        />
        <Input
          value={draftCode}
          onChange={(e) => setDraftCode(e.target.value.toUpperCase())}
          placeholder="INR"
          maxLength={3}
        />
        <Button type="button" variant="outline" size="icon" onClick={addAlias} aria-label="Add currency alias">
          <Plus className="h-4 w-4" />
        </Button>
      </div>
    </div>
  );
}

function TrainingPanel({ profile }: { profile: SupplierProfile }) {
  const profileId = profile.profile_id;
  const startTraining = useStartTraining();
  const activateProfile = useActivateProfile();
  const retireProfile = useRetireProfile();
  const approveModel = useApproveModel();
  const bootstrapModel = useBootstrapModel();
  const router = useRouter();
  const confirm = useConfirm();

  const isTraining = profile.status === "training";
  const { data: status } = useTrainingStatus(
    profileId,
    profile.status === "training" || profile.status === "active",
    isTraining ? 5000 : 30000
  );

  async function handleStartTraining() {
    const reviewWarnings = profile.authoring_evidence?.review_warnings ?? [];
    const acknowledgeWarnings = reviewWarnings.length > 0;
    if (acknowledgeWarnings) {
      const ok = await confirm({
        title: "Acknowledge authoring review warnings and continue?",
        description: (
          <ul className="list-disc space-y-1 pl-4">
            {reviewWarnings.map((w, i) => (
              <li key={i}>{w}</li>
            ))}
          </ul>
        ),
        confirmLabel: "Acknowledge & continue",
      });
      if (!ok) return;
    }
    try {
      await startTraining.mutateAsync({ profileId, acknowledgeWarnings });
      toast.success("Profile moved to training. Upload sample documents below.");
    } catch (err) {
      toast.error(toastErrorMessage(err, "Failed to start training."));
    }
  }

  async function handleOpenWorkbench() {
    try {
      const model = await bootstrapModel.mutateAsync(profileId);
      router.push(modelDetailPath(model.id));
    } catch (err) {
      toast.error(toastErrorMessage(err, "Failed to open the training workbench."));
    }
  }

  async function handleApprove() {
    if (!status?.model_id) return;
    try {
      await approveModel.mutateAsync({
        modelId: status.model_id,
        approvedBy: "ui",
      });
      toast.success("Model approved.");
    } catch (err) {
      toast.error(toastErrorMessage(err, "Failed to approve model."));
    }
  }

  async function handleActivate() {
    const reviewWarnings = profile.authoring_evidence?.review_warnings ?? [];
    const acknowledgeWarnings = reviewWarnings.length > 0;
    if (acknowledgeWarnings) {
      const ok = await confirm({
        title: "Acknowledge authoring review warnings and activate?",
        description: (
          <ul className="list-disc space-y-1 pl-4">
            {reviewWarnings.map((w, i) => (
              <li key={i}>{w}</li>
            ))}
          </ul>
        ),
        confirmLabel: "Acknowledge & activate",
      });
      if (!ok) return;
    }
    try {
      await activateProfile.mutateAsync({ profileId, acknowledgeWarnings });
      toast.success("Profile activated. Inbound documents now use the model.");
    } catch (err) {
      toast.error(toastErrorMessage(err, "Failed to activate profile."));
    }
  }

  async function handleRetire() {
    try {
      await retireProfile.mutateAsync(profileId);
      toast.success("Profile retired.");
    } catch (err) {
      toast.error(toastErrorMessage(err, "Failed to retire profile."));
    }
  }

  const successCount = status?.validation_success_count ?? 0;
  const minSuccesses = status?.min_validation_successes ?? 3;
  const readyForApproval = status?.ready_for_approval ?? false;
  const modelApproved = status?.model_status === "approved";

  return (
    <div className="space-y-6">
      <Section
        title="Lifecycle"
        description="draft → training (upload samples, AI builds the model) → active (model extracts inbound documents)"
      >
        <div className="flex items-center gap-3">
          <Badge className={STATUS_BADGE[profile.status] ?? ""}>
            {profile.status}
          </Badge>
          <div className="flex gap-2">
            {(profile.status === "draft" ||
              profile.status === "retired" ||
              profile.status === "active") && (
              <Button
                size="sm"
                onClick={handleStartTraining}
                disabled={startTraining.isPending}
              >
                <GraduationCap className="h-4 w-4" />
                {profile.status === "active" ? "Retrain" : "Start Training"}
              </Button>
            )}
            {profile.status === "training" && (
              <Button
                size="sm"
                onClick={handleActivate}
                disabled={activateProfile.isPending || !modelApproved}
                title={
                  modelApproved
                    ? undefined
                    : "Requires an approved extraction model"
                }
              >
                <Power className="h-4 w-4" />
                Activate
              </Button>
            )}
            {(profile.status === "training" || profile.status === "active") && (
              <Button
                size="sm"
                variant="outline"
                onClick={handleRetire}
                disabled={retireProfile.isPending}
              >
                <Archive className="h-4 w-4" />
                Retire
              </Button>
            )}
          </div>
        </div>
      </Section>

      {profile.status === "active" && modelApproved && isTraining === false && (
        <Card className="border-warning/30 bg-warning/12">
          <CardContent className="pt-4 pb-4">
            <p className="text-sm text-warning">
              This profile already has an approved model. Click <strong>Retrain</strong>{" "}
              to upload new samples, then delete or retire the existing model from the{" "}
              <Link href="/models" className="text-primary underline">
                Models
              </Link>{" "}
              page so AI can author a replacement.
            </p>
          </CardContent>
        </Card>
      )}

      {isTraining && (
        <Section
          title="Training Workbench"
          description="Build this profile's model: let AI author it in the workbench (upload samples, tag training/holdout, run Train, approve when it reproduces the AI output), or hand-author it in the visual builder. Nothing is delivered to the output folder during training."
        >
          <div className="flex flex-wrap gap-2">
            <Button onClick={handleOpenWorkbench} disabled={bootstrapModel.isPending}>
              {bootstrapModel.isPending ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <GraduationCap className="h-4 w-4" />
              )}
              Open Training Workbench
            </Button>
            <Button
              variant="outline"
              onClick={() => router.push(`/models/builder?profileId=${encodeURIComponent(profileId)}`)}
            >
              <Wrench className="h-4 w-4" />
              Build model manually
            </Button>
          </div>
        </Section>
      )}

      {status && (
        <Section title="Model Candidate">
          {status.model_id ? (
            <div className="space-y-3">
              <div className="grid grid-cols-2 gap-3 text-sm">
                <div>
                  <p className="text-xs text-muted-foreground">Model</p>
                  <Link
                    href={modelDetailPath(status.model_id)}
                    className="font-mono text-xs text-primary hover:underline"
                  >
                    {status.model_id}
                  </Link>
                </div>
                <div>
                  <p className="text-xs text-muted-foreground">Status</p>
                  <Badge variant="outline">{status.model_status}</Badge>
                </div>
                <div>
                  <p className="text-xs text-muted-foreground">
                    Layout Fingerprint
                  </p>
                  <p className="font-mono text-xs truncate">
                    {status.layout_fingerprint}
                  </p>
                </div>
                <div>
                  <p className="text-xs text-muted-foreground">Validations</p>
                  <p className="flex items-center gap-2 text-xs">
                    <span className="flex items-center gap-1">
                      <CheckCircle2 className="h-3.5 w-3.5 text-success" />
                      {successCount}/{minSuccesses} passed
                    </span>
                    {(status.validation_failure_count ?? 0) > 0 && (
                      <span className="flex items-center gap-1">
                        <XCircle className="h-3.5 w-3.5 text-destructive" />
                        {status.validation_failure_count} failed
                      </span>
                    )}
                  </p>
                </div>
              </div>
              {status.last_validation_error && (
                <p className="rounded-md bg-destructive/10 px-3 py-2 text-xs text-destructive">
                  {status.last_validation_error}
                </p>
              )}
              {readyForApproval && (
                <Button
                  size="sm"
                  onClick={handleApprove}
                  disabled={approveModel.isPending}
                >
                  <CheckCircle2 className="h-4 w-4" />
                  Approve Model
                </Button>
              )}
            </div>
          ) : (
            <p className="flex items-center gap-2 text-sm text-muted-foreground">
              <Boxes className="h-4 w-4" />
              No model candidate yet. Upload a training document to author one.
            </p>
          )}
        </Section>
      )}
    </div>
  );
}

// ── Profile Editor Page ───────────────────────────────────

export default function ProfileEditorPage({
  params,
}: {
  params: Promise<{ profileId: string }>;
}) {
  const { profileId } = use(params);
  const isNew = profileId === "new";
  const router = useRouter();

  const { data: existingProfile, isLoading, isError, refetch } = useProfile(
    isNew ? "" : profileId
  );
  const createProfile = useCreateProfile();
  const updateProfile = useUpdateProfile();
  const profilePreview = useProfilePreview(isNew ? "" : profileId);

  const [profile, setProfile] = useState<SupplierProfile>(emptyProfile());
  const [showJson, setShowJson] = useState(false);
  const [lint, setLint] = useState<{ conflicts: SetupConflict[]; ignored_notes: string[] }>({
    conflicts: [],
    ignored_notes: [],
  });
  // Advanced tab hides deep engine-tuning fields (regexes, executable policies,
  // fingerprint, training gates, read-only metadata) behind this toggle so the
  // load-bearing supplier config stays scannable.
  const [showInternals, setShowInternals] = useState(false);
  const { data: outputSpecList } = useOutputSpecs();
  const { data: fieldSchemaList } = useFieldSchemas();
  const { data: activeFieldSchema } = useFieldSchema(profile.field_schema_id);
  const { data: activeOutputSpec } = useOutputSpec(profile.output_spec_id);
  const effectiveFields = mergeProfileFields(activeFieldSchema, profile.field_overrides);
  const effectiveFieldSchema = activeFieldSchema
    ? { ...activeFieldSchema, fields: effectiveFields }
    : undefined;
  const effectiveOutputSpec = applyOutputMappingOverrides(
    activeOutputSpec,
    profile.output_mapping_overrides,
  );

  useEffect(() => {
    let cancelled = false;
    if (existingProfile) {
      queueMicrotask(() => {
        if (!cancelled) {
          setProfile(normalizeProfile(existingProfile));
        }
      });
    }
    return () => {
      cancelled = true;
    };
  }, [existingProfile]);

  // Deterministic setup lint (notes vs executable fields), debounced. The same
  // compiler the extraction path uses, so what it flags is what would confuse
  // extraction. Depends only on the fields the compiler reads.
  const lintKey = JSON.stringify({
    notes: profile.notes,
    hints: profile.advanced.line_item_hints,
    structure: profile.advanced.document_structure,
    currency: profile.advanced.currency,
  });
  useEffect(() => {
    let cancelled = false;
    const timer = setTimeout(() => {
      api
        .post<{ conflicts: SetupConflict[]; ignored_notes: string[] }>(
          "/profiles/lint",
          normalizeProfile(profile)
        )
        .then((result) => {
          if (!cancelled) setLint(result);
        })
        .catch(() => {
          if (!cancelled) setLint({ conflicts: [], ignored_notes: [] });
        });
    }, 500);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [lintKey]);

  function update<K extends keyof SupplierProfile>(
    key: K,
    value: SupplierProfile[K]
  ) {
    setProfile((prev) => ({ ...prev, [key]: value }));
  }

  function updateNested(
    section: keyof SupplierProfile,
    key: string,
    value: unknown,
  ) {
    setProfile((prev) => ({
      ...prev,
      [section]: {
        ...(prev[section] as unknown as Record<string, unknown>),
        [key]: value,
      },
    }));
  }

  function updateAdvanced<K extends keyof AdvancedHints>(
    key: K,
    value: AdvancedHints[K],
  ) {
    setProfile((prev) => ({
      ...prev,
      advanced: { ...prev.advanced, [key]: value },
    }));
  }

  function updateAdvancedSection(
    section: "document_structure" | "line_item_hints",
    key: string,
    value: unknown,
  ) {
    setProfile((prev) => ({
      ...prev,
      advanced: {
        ...prev.advanced,
        [section]: {
          ...(prev.advanced[section] as unknown as Record<string, unknown>),
          [key]: value,
        },
      },
    }));
  }

  function updateCurrency<K extends keyof CurrencyRules>(
    key: K,
    value: CurrencyRules[K],
  ) {
    setProfile((prev) => ({
      ...prev,
      advanced: {
        ...prev.advanced,
        currency: {
          ...prev.advanced.currency,
          [key]: value,
        },
      },
    }));
  }

  function updateTaxOutputMode(value: string | null) {
    setProfile((prev) => ({
      ...prev,
      advanced: {
        ...prev.advanced,
        line_item_hints: {
          ...prev.advanced.line_item_hints,
          tax_output_mode: value,
          tax_rate_source:
            value === "calculate" ? prev.advanced.line_item_hints.tax_rate_source : null,
        },
      },
    }));
  }

  function updateLayoutFingerprint(
    key: keyof LayoutFingerprintRules,
    value: LayoutFingerprintRules[keyof LayoutFingerprintRules],
  ) {
    setProfile((prev) => ({
      ...prev,
      layout_fingerprint: {
        ...(prev.layout_fingerprint ?? emptyLayoutFingerprint()),
        [key]: value,
      },
    }));
  }

  function updateTrainingConfig(
    key: keyof TrainingConfig,
    value: TrainingConfig[keyof TrainingConfig],
  ) {
    setProfile((prev) => ({
      ...prev,
      training_config: {
        ...(prev.training_config ?? { min_validation_successes: 3, require_reconciliation: true }),
        [key]: value,
      },
    }));
  }

  function setFieldOverride(
    field: FieldDef,
    update: { labelHint?: string; dateFormat?: string },
  ) {
    setProfile((prev) => {
      const existing = (prev.field_overrides ?? []).find(
        (override) => override.scope === field.scope && override.name === field.name,
      );
      const overrides = (prev.field_overrides ?? []).filter(
        (o) => !(o.scope === field.scope && o.name === field.name),
      );
      const labelHint =
        update.labelHint !== undefined
          ? update.labelHint
          : existing?.label_hint ?? "";
      const dateFormat =
        update.dateFormat !== undefined
          ? update.dateFormat
          : existing?.date_format ?? "";
      const trimmed = labelHint.trim();
      const defaultHint = field.label_hint?.trim() ?? "";
      const hasLabelOverride = Boolean(trimmed && trimmed !== defaultHint);
      const hasDateFormat = Boolean(dateFormat.trim());
      if (!existing && !hasLabelOverride && !hasDateFormat) {
        return prev;
      }

      // Preserve every unmodeled override property, then pin the base field's
      // structural contract. Label and date-format edits must be independent:
      // changing one must never erase the other.
      const entry: FieldDef = {
        ...existing,
        name: field.name,
        scope: field.scope,
        type: field.type,
        role: field.role,
        label_hint: hasLabelOverride ? labelHint : null,
        date_format: hasDateFormat ? dateFormat : null,
      };
      return { ...prev, field_overrides: [...overrides, entry] };
    });
  }

  function setFieldLabelHint(field: FieldDef, labelHint: string) {
    setFieldOverride(field, { labelHint });
  }

  function setFieldDateFormat(field: FieldDef, dateFormat: string) {
    setFieldOverride(field, { dateFormat });
  }

  const layoutFingerprintEnabled = profile.layout_fingerprint != null;
  const layoutRules = profile.layout_fingerprint ?? emptyLayoutFingerprint();
  const advanced = profile.advanced;
  const trainingConfig = profile.training_config ?? {
    min_validation_successes: 3,
    require_reconciliation: true,
  };
  // Reconciliation anchors live on the (document) subtotal + tax fields' labels.
  const subtotalField = effectiveFields.find(
    (f) => f.scope === "document" && f.name === "subtotal",
  );
  const taxTotalField = effectiveFields.find(
    (f) => f.scope === "document" && f.name === "tax",
  );
  const overrideHintOf = (name: string) =>
    profile.field_overrides?.find((o) => o.scope === "document" && o.name === name)
      ?.label_hint ?? "";

  async function handleSave() {
    const profileToSave = mirrorRowLabelsToColumnHints(normalizeProfile(profile));
    const validationError = validateProfileBeforeSave(profileToSave, isNew);
    if (validationError) {
      toast.error(validationError);
      return;
    }
    try {
      if (isNew) {
        await createProfile.mutateAsync(profileToSave);
        toast.success("Profile created.");
        router.push(`/profiles/${profileToSave.profile_id}`);
      } else {
        await updateProfile.mutateAsync({
          profileId,
          profile: profileToSave,
        });
        setProfile(profileToSave);
        toast.success("Profile updated.");
      }
    } catch (err) {
      toast.error(toastErrorMessage(err, "Failed to save profile."));
    }
  }

  function handleExport() {
    const blob = new Blob([JSON.stringify(profile, null, 2)], {
      type: "application/json",
    });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${profile.profile_id || "profile"}.json`;
    a.click();
    URL.revokeObjectURL(url);
  }

  function handleImport() {
    const input = document.createElement("input");
    input.type = "file";
    input.accept = ".json";
    input.onchange = async (e) => {
      const file = (e.target as HTMLInputElement).files?.[0];
      if (!file) return;
      try {
        const text = await file.text();
        const imported = normalizeProfile(JSON.parse(text));
        setProfile(imported);
        toast.success("Profile imported from JSON.");
      } catch {
        toast.error("Invalid JSON file.");
      }
    };
    input.click();
  }

  const isSaving = createProfile.isPending || updateProfile.isPending;

  if (!isNew && isLoading) {
    return <LoadingState rows={6} />;
  }

  if (!isNew && isError) {
    return <ErrorState what="this supplier profile" onRetry={() => refetch()} />;
  }

  return (
    <div className="space-y-6 max-w-3xl">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-4">
          <Link href="/profiles">
            <Button variant="ghost" size="sm">
              <ArrowLeft className="h-4 w-4 mr-2" />
              Back
            </Button>
          </Link>
          <h1 className="text-2xl font-bold tracking-tight">
            {isNew ? "New Profile" : profile.identity.canonical_name || "Edit Profile"}
          </h1>
          {!isNew && (
            <Badge className={STATUS_BADGE[profile.status] ?? ""}>
              {profile.status}
            </Badge>
          )}
        </div>
        <div className="flex gap-2">
          {!isNew && (
            <Button
              variant="outline"
              size="sm"
              onClick={() => router.push(`/profiles/new/assistant?from=${encodeURIComponent(profile.profile_id)}`)}
              title="Refine this profile's extraction hints with AI and save a new version"
            >
              <Sparkles className="h-4 w-4" />
              Edit with AI
            </Button>
          )}
          <DropdownMenu>
            <DropdownMenuTrigger
              render={
                <Button variant="ghost" size="sm">
                  <MoreHorizontal className="h-4 w-4" />
                  Developer tools
                </Button>
              }
            />
            <DropdownMenuContent align="end">
              <DropdownMenuItem onClick={() => setShowJson(!showJson)}>
                <Code className="h-4 w-4" />{showJson ? "Return to form" : "View JSON"}
              </DropdownMenuItem>
              <DropdownMenuItem onClick={handleImport}>
                <Upload className="h-4 w-4" />Import JSON
              </DropdownMenuItem>
              <DropdownMenuItem onClick={handleExport}>
                <Download className="h-4 w-4" />Export JSON
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
          <Button onClick={handleSave} disabled={isSaving}>
            {isSaving ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <Save className="h-4 w-4" />
            )}
            Save
          </Button>
        </div>
      </div>

      {/* JSON Preview */}
      {showJson ? (
        <Card>
          <CardContent className="pt-6">
            <pre className="text-xs font-mono whitespace-pre-wrap overflow-auto max-h-[600px] bg-muted p-4 rounded-lg">
              {JSON.stringify(profile, null, 2)}
            </pre>
          </CardContent>
        </Card>
      ) : (
        <Tabs defaultValue="simple">
          <TabsList>
            <TabsTrigger value="simple">Setup</TabsTrigger>
            <TabsTrigger value="mapping">Output Mapping</TabsTrigger>
            <TabsTrigger value="advanced">Advanced</TabsTrigger>
            {!isNew && <TabsTrigger value="preview">Preview Output</TabsTrigger>}
            {!isNew && <TabsTrigger value="training">Training</TabsTrigger>}
          </TabsList>

          {/* ── Setup (the common case) ──────────────────────── */}
          <TabsContent value="simple" className="space-y-6">
            <Section
              title="1. Identify the source"
              description="Basic identification for this source and document layout."
            >
              <div className="grid grid-cols-2 gap-4">
                <Field label="Profile ID" hint="A short unique ID, e.g. gtt.standard.v1">
                  <Input
                    value={profile.profile_id}
                    onChange={(e) => update("profile_id", e.target.value)}
                    placeholder=""
                    disabled={!isNew}
                  />
                </Field>
                <Field
                  label="Source / Supplier Name"
                  hint="The supplier's canonical name as printed on the document."
                >
                  <Input
                    value={profile.identity.canonical_name}
                    onChange={(e) =>
                      updateNested("identity", "canonical_name", e.target.value)
                    }
                    placeholder=""
                  />
                </Field>
              </div>
              <Field
                label="Layout Description"
                hint="If this source has multiple document layouts, describe which one this profile covers."
              >
                <Input
                  value={profile.layout_description ?? ""}
                  onChange={(e) =>
                    update("layout_description", e.target.value || null)
                  }
                  placeholder=""
                />
              </Field>
              <Field
                label="Supplier aliases"
                hint="Other supplier names that should route documents to this profile."
              >
                <TagInput
                  value={profile.identity.aliases}
                  onChange={(aliases) => updateNested("identity", "aliases", aliases)}
                  placeholder="Add another printed supplier name"
                />
              </Field>
            </Section>

            <Section
              title="2. Extraction & delivery"
              description="Choose the output spec (the columns delivered) and the document category."
            >
              <div className="grid gap-4 md:grid-cols-3">
                <Field label="Output Spec" hint="The columns delivered in the output file.">
                  <Select
                    value={profile.output_spec_id}
                    onValueChange={(v) => update("output_spec_id", v || "temforce.standard")}
                  >
                    <SelectTrigger>
                      <SelectValue placeholder="Select output spec" />
                    </SelectTrigger>
                    <SelectContent>
                      {(outputSpecList ?? []).map((s) => (
                        <SelectItem key={s.spec_id} value={s.spec_id}>
                          {s.name || s.spec_id}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </Field>
                <Field
                  label="Field Schema"
                  hint="The document and row fields this profile extracts."
                >
                  <Select
                    value={profile.field_schema_id}
                    onValueChange={(value) => update("field_schema_id", value || "invoice.standard")}
                  >
                    <SelectTrigger className="w-full">
                      <SelectValue placeholder="Select field schema" />
                    </SelectTrigger>
                    <SelectContent>
                      {(fieldSchemaList ?? []).map((schema) => (
                        <SelectItem key={schema.schema_id} value={schema.schema_id}>
                          {schema.name || schema.schema_id}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </Field>
                <Field
                  label="Document Category"
                  hint="Layout category label; salts the layout fingerprint."
                >
                  <EnumSelectField
                    value={profile.classification.output_type}
                    onChange={(v) =>
                      updateNested("classification", "output_type", v || "standard")
                    }
                    options={OUTPUT_TYPE_OPTIONS}
                    placeholder="Select category"
                  />
                  <EnumFieldHint
                    options={OUTPUT_TYPE_OPTIONS}
                    value={profile.classification.output_type}
                  />
                </Field>
              </div>
              {activeOutputSpec && (
                <div className="rounded-md border bg-muted/30 p-3 text-sm text-muted-foreground">
                  <p className="font-medium text-foreground">Delivered columns</p>
                  <p>{activeOutputSpec.columns.map((c) => c.header).join(", ")}</p>
                </div>
              )}
            </Section>

            {effectiveOutputSpec && effectiveFieldSchema && (
              <Section
                title="3. What each output field looks like on the invoice"
                description="Optionally specify each supplier label. For date fields, set the printed input format when day and month order is ambiguous; delivered dates remain MM/DD/YYYY."
              >
                <div className="grid grid-cols-2 gap-4">
                  {outputFieldTargets(effectiveOutputSpec, effectiveFieldSchema).map(
                    ({ field, columns, fallbackColumns }) => {
                      const overrideHint =
                        profile.field_overrides?.find(
                          (o) => o.scope === field.scope && o.name === field.name,
                        )
                          ?.label_hint ?? "";
                      const overrideDateFormat =
                        profile.field_overrides?.find(
                          (o) => o.scope === field.scope && o.name === field.name,
                        )
                          ?.date_format ?? "";
                      const feedsHint = columns.length
                        ? `Feeds: ${columns.join(", ")}`
                        : `Fallback for: ${fallbackColumns.join(", ")}`;
                      // Multiple possible labels are comma-separated; show them as
                      // bubbles only when commas are present. A single label stays
                      // plain text.
                      const tokens = overrideHint.includes(",")
                        ? overrideHint.split(",").map((s) => s.trim()).filter(Boolean)
                        : [];
                      return (
                        <Field
                          key={`${field.scope}.${field.name}`}
                          label={humanizeFieldName(field.name)}
                          hint={feedsHint}
                        >
                          <Input
                            value={overrideHint}
                            onChange={(e) => setFieldLabelHint(field, e.target.value)}
                            placeholder="What this supplier calls it (optional)"
                          />
                          {field.type === "date" && (
                            <div className="space-y-1.5 rounded-md border bg-muted/20 p-2.5">
                              <label
                                htmlFor={`date-format-${field.scope}-${field.name}`}
                                className="text-xs font-medium text-foreground"
                              >
                                Input date format
                              </label>
                              <Input
                                id={`date-format-${field.scope}-${field.name}`}
                                list={`date-format-options-${field.scope}-${field.name}`}
                                value={overrideDateFormat}
                                onChange={(e) => setFieldDateFormat(field, e.target.value)}
                                onBlur={(e) =>
                                  setFieldDateFormat(field, e.target.value.trim())
                                }
                                placeholder="Auto-detect, or enter e.g. %d/%m/%Y"
                                autoComplete="off"
                                className="font-mono"
                              />
                              <datalist
                                id={`date-format-options-${field.scope}-${field.name}`}
                              >
                                {DATE_FORMAT_SUGGESTIONS.map((option) => (
                                  <option key={option.value} value={option.value}>
                                    {option.label}
                                  </option>
                                ))}
                              </datalist>
                              <p className="text-[11px] text-muted-foreground/80">
                                Uses strptime tokens. Set this for ambiguous values such as
                                03/06/2026; leave blank only when automatic detection is safe.
                              </p>
                            </div>
                          )}
                          {tokens.length > 1 && (
                            <div className="flex flex-wrap gap-1.5 pt-1.5">
                              {tokens.map((t, i) => (
                                <Badge key={i} variant="secondary" className="text-xs font-normal">
                                  {t}
                                </Badge>
                              ))}
                            </div>
                          )}
                        </Field>
                      );
                    },
                  )}
                </div>
              </Section>
            )}

            <Section
              title="4. Accounts — where documents arrive and go"
              description="One profile (one layout) can serve many billing accounts. Accounts are mapped centrally in the Accounts view, so a folder can never be mapped to two profiles by mistake."
            >
              {(() => {
                const accounts = deliveryAccountsOf(profile);
                return (
                  <div className="space-y-3">
                    {accounts.length === 0 ? (
                      <p className="text-sm text-muted-foreground">
                        No accounts mapped to this profile yet.
                      </p>
                    ) : (
                      <div className="overflow-hidden rounded-md border">
                        {accounts.map((account, index) => (
                          <div
                            key={index}
                            className="flex flex-wrap items-center gap-x-4 gap-y-1 border-b px-3 py-2 text-sm last:border-b-0"
                          >
                            <span className="font-medium">{account.label || "(unlabeled)"}</span>
                            <span className="font-mono text-xs text-muted-foreground">
                              in: {account.inbound_path}
                            </span>
                            <span className="font-mono text-xs text-muted-foreground">
                              out: {account.output_path}
                            </span>
                          </div>
                        ))}
                      </div>
                    )}
                    <Link
                      href="/accounts"
                      className="inline-flex items-center gap-1.5 text-sm text-primary hover:underline"
                    >
                      <Landmark className="h-4 w-4" />
                      Manage accounts in the Accounts view
                    </Link>
                  </div>
                );
              })()}
            </Section>

            <Section
              title="5. Notes"
              description="Optional guidance passed to the AI extractor. Structured fields above win; a note that contradicts one is flagged below (prefix a line with 'override:' to make the note authoritative)."
            >
              <Field
                label="Notes"
                hint="Plain-English notes about this source's documents."
              >
                <textarea
                  className="w-full rounded-md border bg-background px-3 py-2 text-sm min-h-[220px] font-mono focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                  value={profile.notes ?? ""}
                  onChange={(e) => update("notes", e.target.value)}
                  placeholder=""
                />
              </Field>
              <SetupConflictsPanel conflicts={lint.conflicts} />
            </Section>
          </TabsContent>

          {/* ── Advanced (load-bearing extras) ───────────────── */}
          <TabsContent value="mapping" className="space-y-6">
            <Section
              title="Profile output mapping"
              description="Override only the columns that differ for this profile. Column names, order, and formatting continue to come from the selected output spec."
            >
              <OutputMappingEditor
                spec={activeOutputSpec}
                fields={effectiveFields}
                overrides={profile.output_mapping_overrides ?? []}
                onChange={(overrides) => update("output_mapping_overrides", overrides)}
              />
            </Section>
          </TabsContent>

          <TabsContent value="advanced" className="space-y-6">
            <div className="flex items-center justify-between rounded-lg border bg-muted/30 px-4 py-3">
              <div>
                <p className="text-sm font-medium">Show engine internals</p>
                <p className="text-xs text-muted-foreground">
                  Reveal deterministic-engine tuning: value-pattern regexes, executable
                  amount/tax policies, layout fingerprint, training gates, and metadata.
                  Most sources never need these.
                </p>
              </div>
              <Switch checked={showInternals} onCheckedChange={setShowInternals} />
            </div>

            {showInternals && (
            <Section title="Profile Metadata">
              <div className="grid grid-cols-2 gap-4">
                <Field label="Status">
                  <EnumSelectField
                    value={profile.status}
                    onChange={(v) => update("status", v || "draft")}
                    options={PROFILE_STATUS_OPTIONS}
                    placeholder="Select status"
                  />
                  <EnumFieldHint options={PROFILE_STATUS_OPTIONS} value={profile.status} />
                </Field>
                <Field label="Version">
                  <Input
                    type="number"
                    value={profile.version}
                    onChange={(e) =>
                      update("version", parseInt(e.target.value) || 1)
                    }
                  />
                </Field>
                <Field label="Owner" hint="Team or person responsible for this profile.">
                  <Input
                    value={profile.owner ?? ""}
                    onChange={(e) => update("owner", e.target.value)}
                    placeholder=""
                  />
                </Field>
                <div className="rounded-md border px-3 py-2 text-sm">
                  <p className="text-xs text-muted-foreground">Created by</p>
                  <p className="font-medium">
                    {auditDisplay(profile.audit, "created")?.display ?? "—"}
                  </p>
                  <p className="text-xs text-muted-foreground">
                    {auditDisplay(profile.audit, "created")?.at ?? profile.created_date ?? ""}
                  </p>
                </div>
                <div className="rounded-md border px-3 py-2 text-sm">
                  <p className="text-xs text-muted-foreground">Updated by</p>
                  <p className="font-medium">
                    {auditDisplay(profile.audit, "updated")?.display ?? "—"}
                  </p>
                  <p className="text-xs text-muted-foreground">
                    {auditDisplay(profile.audit, "updated")?.at ?? profile.last_updated_date ?? ""}
                  </p>
                </div>
              </div>
            </Section>
            )}

            {activeFieldSchema && schemaHasRowFields(activeFieldSchema) && (
              <Section
                title="Table / section hints"
                description="Where detail rows start and end, and how tables are labeled on this layout."
              >
                <Field
                  label="Detail Start Marker"
                  hint="Heading where the line-item section begins."
                >
                  <Input
                    value={advanced.document_structure.detail_start_marker ?? ""}
                    onChange={(e) =>
                      updateAdvancedSection(
                        "document_structure",
                        "detail_start_marker",
                        e.target.value || null
                      )
                    }
                    placeholder=""
                  />
                </Field>
                <Field
                  label="Detail End Marker"
                  hint="Heading where the line-item section ends. Anything after it is ignored — use it to skip repeated detail / summary pages."
                >
                  <Input
                    value={advanced.document_structure.detail_end_marker ?? ""}
                    onChange={(e) =>
                      updateAdvancedSection(
                        "document_structure",
                        "detail_end_marker",
                        e.target.value || null
                      )
                    }
                    placeholder=""
                  />
                </Field>
                <Field label="Detail Table Anchors">
                  <TagInput
                    value={advanced.line_item_hints.detail_table_anchors ?? []}
                    onChange={(v) =>
                      updateAdvancedSection("line_item_hints", "detail_table_anchors", v)
                    }
                    placeholder="Section headings above detail tables"
                  />
                </Field>
                <Field label="Table Column Labels">
                  <TagInput
                    value={advanced.line_item_hints.table_column_labels ?? []}
                    onChange={(v) =>
                      updateAdvancedSection("line_item_hints", "table_column_labels", v)
                    }
                    placeholder="Column headers on the detail table"
                  />
                </Field>
              </Section>
            )}

            <Section
              title="Currency extraction"
              description="Normalize printed currency words or symbols into ISO currency codes before validation and output."
            >
              <div className="grid grid-cols-2 gap-4">
                <Field
                  label="Default Currency"
                  hint="Used when currency is missing or unrecognized. Leave blank if this supplier can vary and should not default."
                >
                  <Input
                    value={advanced.currency.default_code ?? ""}
                    onChange={(e) =>
                      updateCurrency("default_code", currencyCode(e.target.value))
                    }
                    placeholder="e.g. INR"
                    maxLength={3}
                  />
                </Field>
                <Field
                  label="Allowed Currencies"
                  hint="ISO codes this profile may emit. If exactly one is set, it is also used as a fallback."
                >
                  <TagInput
                    value={advanced.currency.allowed_codes}
                    onChange={(v) =>
                      updateCurrency(
                        "allowed_codes",
                        v.map((code) => currencyCode(code)).filter((code): code is string => Boolean(code))
                      )
                    }
                    placeholder="e.g. INR, USD, EUR"
                  />
                </Field>
              </div>
              <Field
                label="Currency Aliases"
                hint="Printed words or symbols mapped to ISO codes. Profile aliases override built-in mappings."
              >
                <CurrencyAliasEditor
                  aliases={advanced.currency.aliases}
                  onChange={(aliases) => updateCurrency("aliases", aliases)}
                />
              </Field>
            </Section>

            <Section
              title="Extraction field settings"
              description="Customize printed labels and validation for this profile, or add a supplier-specific extraction field. Structural properties of schema fields remain read-only."
            >
              <ExtractionFieldSettings
                schema={activeFieldSchema}
                overrides={profile.field_overrides ?? []}
                onChange={(overrides) => update("field_overrides", overrides)}
              />
            </Section>

            <Section
              title="Fixed document values"
              description="Set profile-owned constants for downstream values that are not reliably printed on the document."
            >
              <FixedDocumentValues
                fields={effectiveFields}
                values={advanced.document_field_defaults ?? {}}
                onChange={(values) => updateAdvanced("document_field_defaults", values)}
              />
            </Section>

            {activeFieldSchema && schemaSupportsGroupedRows(activeFieldSchema) && (
              <Section
                title="Grouped row policy"
                description="Controls which identifier lands in service_id vs billing_reference when a layout shows both a parent section ID and child row IDs (e.g. Colt Service Level Activity). Map the chosen fields in your Output Spec (EXT_SERVICEID / EXT_BILLINGREFERENCE)."
              >
                <div className="grid grid-cols-2 gap-4">
                  <Field label="Line Item Granularity">
                    <EnumSelectField
                      value={advanced.line_item_hints.line_item_granularity}
                      onChange={(v) =>
                        updateAdvancedSection("line_item_hints", "line_item_granularity", v)
                      }
                      options={LINE_ITEM_GRANULARITY_OPTIONS}
                      allowUnset
                      unsetLabel="Auto (AI decides)"
                      placeholder="Select granularity"
                    />
                    <EnumFieldHint
                      options={LINE_ITEM_GRANULARITY_OPTIONS}
                      value={advanced.line_item_hints.line_item_granularity}
                    />
                  </Field>
                  <Field label="Service ID (primary row identifier)">
                    <EnumSelectField
                      value={advanced.line_item_hints.service_id_preference}
                      onChange={(v) =>
                        updateAdvancedSection("line_item_hints", "service_id_preference", v)
                      }
                      options={SERVICE_ID_PREFERENCE_OPTIONS}
                      allowUnset
                      unsetLabel="Auto (AI decides)"
                      placeholder="Select service ID source"
                    />
                    <EnumFieldHint
                      options={SERVICE_ID_PREFERENCE_OPTIONS}
                      value={advanced.line_item_hints.service_id_preference}
                    />
                  </Field>
                  <Field label="Billing reference (secondary identifier)">
                    <EnumSelectField
                      value={advanced.line_item_hints.billing_reference_preference}
                      onChange={(v) =>
                        updateAdvancedSection(
                          "line_item_hints",
                          "billing_reference_preference",
                          v
                        )
                      }
                      options={BILLING_REFERENCE_PREFERENCE_OPTIONS}
                      allowUnset
                      unsetLabel="Auto (AI decides)"
                      placeholder="Select billing reference source"
                    />
                    <EnumFieldHint
                      options={BILLING_REFERENCE_PREFERENCE_OPTIONS}
                      value={advanced.line_item_hints.billing_reference_preference}
                    />
                  </Field>
                  {showInternals && (
                  <>
                  <Field
                    label="Service ID column label"
                    hint="Printed table header for the primary row identifier (used by the deterministic grouping/child-id engine; separate from the AI label hints in Setup → 'What each output field looks like')."
                  >
                    <Input
                      value={advanced.line_item_hints.service_id_column_label ?? ""}
                      onChange={(e) =>
                        updateAdvancedSection(
                          "line_item_hints",
                          "service_id_column_label",
                          e.target.value || null
                        )
                      }
                      placeholder="e.g. ACCOUNT, Service Number"
                    />
                  </Field>
                  <Field
                    label="Billing reference column label"
                    hint="Printed table header for the secondary row identifier, when separate."
                  >
                    <Input
                      value={advanced.line_item_hints.billing_reference_column_label ?? ""}
                      onChange={(e) =>
                        updateAdvancedSection(
                          "line_item_hints",
                          "billing_reference_column_label",
                          e.target.value || null
                        )
                      }
                      placeholder="e.g. Contract Number"
                    />
                  </Field>
                  <Field
                    label="Amount column label"
                    hint="Printed table header for the row amount used in EXT_AMOUNT."
                  >
                    <Input
                      value={advanced.line_item_hints.amount_column_label ?? ""}
                      onChange={(e) =>
                        updateAdvancedSection(
                          "line_item_hints",
                          "amount_column_label",
                          e.target.value || null
                        )
                      }
                      placeholder="e.g. SUB-TOT, Current Charges"
                    />
                  </Field>
                  <Field label="Amount source (executable policy)">
                    <EnumSelectField
                      value={advanced.line_item_hints.amount_source ?? null}
                      onChange={(v) =>
                        updateAdvancedSection("line_item_hints", "amount_source", v)
                      }
                      options={AMOUNT_SOURCE_OPTIONS}
                      allowUnset
                      unsetLabel="Auto (AI decides)"
                      placeholder="Select amount source"
                    />
                    <EnumFieldHint
                      options={AMOUNT_SOURCE_OPTIONS}
                      value={advanced.line_item_hints.amount_source ?? null}
                    />
                  </Field>
                  <Field
                    label="Amount policy column"
                    hint="Only for the label_amount policies above: which printed column the deterministic amount is read from. May differ from the AI 'Amount' label in Setup (e.g. a net 'before taxes' column)."
                  >
                    <Input
                      value={advanced.line_item_hints.amount_column_label ?? ""}
                      onChange={(e) =>
                        updateAdvancedSection(
                          "line_item_hints",
                          "amount_column_label",
                          e.target.value || null
                        )
                      }
                      placeholder=""
                    />
                  </Field>
                  <Field
                    label="Service ID value pattern (regex)"
                    hint="Regex with one capture group to extract the service_id from a group (e.g. a phone number). Leave blank to use the raw identifier."
                  >
                    <Input
                      value={advanced.line_item_hints.service_id_value_pattern ?? ""}
                      onChange={(e) =>
                        updateAdvancedSection(
                          "line_item_hints",
                          "service_id_value_pattern",
                          e.target.value || null
                        )
                      }
                      placeholder=""
                    />
                  </Field>
                  <Field
                    label="Billing reference value pattern (regex)"
                    hint="Regex with one capture group to extract the billing_reference from a group. Leave blank to use the raw value."
                  >
                    <Input
                      value={advanced.line_item_hints.billing_reference_value_pattern ?? ""}
                      onChange={(e) =>
                        updateAdvancedSection(
                          "line_item_hints",
                          "billing_reference_value_pattern",
                          e.target.value || null
                        )
                      }
                      placeholder=""
                    />
                  </Field>
                  </>
                  )}
                </div>
                <p className="text-xs text-muted-foreground">
                  Example - child in EXT_SERVICEID, parent in EXT_BILLINGREFERENCE: set Service ID
                  to <strong>Child / row ID</strong> and Billing reference to{" "}
                  <strong>Opposite hierarchy level</strong>. Swap them for the opposite. Profile
                  notes guide AI extraction only; this section controls deterministic remapping.
                </p>
              </Section>
            )}

            {activeFieldSchema && schemaHasRowFields(activeFieldSchema) && (
              <Section
                title="Tax output policy"
                description="Controls how delivered EXT_TAX values are populated for each output row."
              >
                <div className="grid grid-cols-2 gap-4">
                  <Field label="Tax output mode">
                    <EnumSelectField
                      value={advanced.line_item_hints.tax_output_mode ?? "auto"}
                      onChange={updateTaxOutputMode}
                      options={TAX_OUTPUT_MODE_OPTIONS}
                      placeholder="Select tax mode"
                    />
                    <EnumFieldHint
                      options={TAX_OUTPUT_MODE_OPTIONS}
                      value={advanced.line_item_hints.tax_output_mode ?? "auto"}
                    />
                  </Field>
                  <Field
                    label="Tax policy column"
                    hint="Only for the label/table tax policies: which printed column the deterministic per-row tax is read from. May differ from the AI 'Tax' label in Setup."
                  >
                    <Input
                      value={advanced.line_item_hints.tax_amount_column_label ?? ""}
                      onChange={(e) =>
                        updateAdvancedSection(
                          "line_item_hints",
                          "tax_amount_column_label",
                          e.target.value || null
                        )
                      }
                      placeholder=""
                    />
                  </Field>
                  <Field label="Tax rate source">
                    <EnumSelectField
                      value={advanced.line_item_hints.tax_rate_source}
                      onChange={(v) =>
                        updateAdvancedSection("line_item_hints", "tax_rate_source", v)
                      }
                      options={TAX_RATE_SOURCE_OPTIONS}
                      allowUnset
                      unsetLabel="Auto"
                      placeholder="Select rate source"
                      disabled={advanced.line_item_hints.tax_output_mode !== "calculate"}
                    />
                    <EnumFieldHint
                      options={TAX_RATE_SOURCE_OPTIONS}
                      value={advanced.line_item_hints.tax_rate_source ?? "auto"}
                    />
                  </Field>
                  {showInternals && (
                  <Field label="Tax source (executable policy)">
                    <EnumSelectField
                      value={advanced.line_item_hints.tax_source ?? null}
                      onChange={(v) =>
                        updateAdvancedSection("line_item_hints", "tax_source", v)
                      }
                      options={TAX_SOURCE_OPTIONS}
                      allowUnset
                      unsetLabel="Auto (AI decides)"
                      placeholder="Select tax source"
                    />
                    <EnumFieldHint
                      options={TAX_SOURCE_OPTIONS}
                      value={advanced.line_item_hints.tax_source ?? null}
                    />
                  </Field>
                  )}
                </div>
              </Section>
            )}

            {activeFieldSchema && (
              <Section
                title="Reconciliation"
                description="How extracted totals are verified for this supplier. Point these at the printed totals on the bill; line items are reconciled against them."
              >
                {subtotalField && (
                  <Field
                    label="Amount total label"
                    hint="Printed total the line-item amounts must add up to (e.g. 'TOTAL MONTHLY SERVICE'). Blank = reconcile against current charges / total due."
                  >
                    <Input
                      value={overrideHintOf("subtotal")}
                      onChange={(e) => setFieldLabelHint(subtotalField, e.target.value)}
                      placeholder=""
                    />
                  </Field>
                )}
                {taxTotalField && (
                  <Field
                    label="Tax total label"
                    hint="Printed tax total the line-item taxes must add up to (e.g. 'TOTAL TAX APPLIED'). Blank = skip the tax check."
                  >
                    <Input
                      value={overrideHintOf("tax")}
                      onChange={(e) => setFieldLabelHint(taxTotalField, e.target.value)}
                      placeholder=""
                    />
                  </Field>
                )}
                <Field
                  label="Require line-item identifier"
                  hint="Drop delivered rows that have neither a service id nor a billing reference."
                >
                  <Select
                    value={advanced.require_line_item_identifier ? "yes" : "no"}
                    onValueChange={(v) =>
                      updateAdvanced("require_line_item_identifier", v === "yes")
                    }
                  >
                    <SelectTrigger>
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="no">No (keep rows without an identifier)</SelectItem>
                      <SelectItem value="yes">Yes (drop rows with no identifier)</SelectItem>
                    </SelectContent>
                  </Select>
                </Field>
              </Section>
            )}

            {activeFieldSchema && schemaHasRowFields(activeFieldSchema) && (
              <Section
                title="Extraction keywords"
                description="Optional keywords for subtotal/tax lines and row filtering."
              >
                <Field label="Skip Row Keywords">
                  <TagInput
                    value={advanced.line_item_hints.skip_row_keywords ?? []}
                    onChange={(v) =>
                      updateAdvancedSection("line_item_hints", "skip_row_keywords", v)
                    }
                    placeholder="Rows to ignore in grouping"
                  />
                </Field>
                {schemaSupportsReconciliation(activeFieldSchema) && (
                  <>
                    <Field
                      label="Subtotal Keywords"
                      hint="Words that mark a subtotal row, e.g. Subtotal, Net Total."
                    >
                      <TagInput
                        value={advanced.line_item_hints.subtotal_keywords}
                        onChange={(v) =>
                          updateAdvancedSection("line_item_hints", "subtotal_keywords", v)
                        }
                        placeholder="Add keyword"
                      />
                    </Field>
                    <Field
                      label="Tax Keywords"
                      hint="Words that mark a tax row, e.g. Tax, VAT."
                    >
                      <TagInput
                        value={advanced.line_item_hints.tax_keywords}
                        onChange={(v) =>
                          updateAdvancedSection("line_item_hints", "tax_keywords", v)
                        }
                        placeholder="Add keyword"
                      />
                    </Field>
                  </>
                )}
                <Field label="Keep Zero-Amount Line Items">
                  <Select
                    value={advanced.include_zero_amount_line_items ? "yes" : "no"}
                    onValueChange={(v) =>
                      updateAdvanced("include_zero_amount_line_items", v === "yes")
                    }
                  >
                    <SelectTrigger>
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="no">No (drop $0.00 rows)</SelectItem>
                      <SelectItem value="yes">Yes (keep every line)</SelectItem>
                    </SelectContent>
                  </Select>
                </Field>
              </Section>
            )}

            {showInternals && (
            <>
            <Section
              title="Layout Fingerprint"
              description="Profile-scoped layout matching for model routing. Header labels come from document field label hints; table columns from Table Column Labels and Detail Table Anchors."
            >
              <div className="flex items-center justify-between rounded-lg border px-4 py-3">
                <div>
                  <p className="text-sm font-medium">Enable profile fingerprinting</p>
                  <p className="text-xs text-muted-foreground">
                    When off, documents use the legacy generic layout hash.
                  </p>
                </div>
                <Switch
                  checked={layoutFingerprintEnabled}
                  onCheckedChange={(enabled) =>
                    update(
                      "layout_fingerprint",
                      enabled ? emptyLayoutFingerprint() : null
                    )
                  }
                />
              </div>

              {layoutFingerprintEnabled && (
                <>
                  <Field
                    label="Summary Anchors"
                    hint="Extra footer/summary labels not covered by document field label hints."
                  >
                    <TagInput
                      value={layoutRules.summary_anchors}
                      onChange={(v) =>
                        updateLayoutFingerprint("summary_anchors", v)
                      }
                      placeholder=""
                    />
                  </Field>
                  <Field
                    label="Fingerprint Currency Codes"
                    hint="Routing only: stripped from layout tokens before hashing. Use Currency extraction above for extracted values."
                  >
                    <TagInput
                      value={layoutRules.currency_codes}
                      onChange={(v) =>
                        updateLayoutFingerprint("currency_codes", v)
                      }
                      placeholder=""
                    />
                  </Field>
                  <Field
                    label="Ignore Label Patterns"
                    hint="Regex patterns for account-specific labels to exclude from the fingerprint."
                  >
                    <TagInput
                      value={layoutRules.ignore_label_patterns}
                      onChange={(v) =>
                        updateLayoutFingerprint("ignore_label_patterns", v)
                      }
                      placeholder=""
                    />
                  </Field>
                  <Field
                    label="Optional Column Patterns"
                    hint="Optional columns that may appear on some accounts but must not change the fingerprint."
                  >
                    <TagInput
                      value={layoutRules.optional_column_patterns}
                      onChange={(v) =>
                        updateLayoutFingerprint("optional_column_patterns", v)
                      }
                      placeholder=""
                    />
                  </Field>
                  <Field
                    label="Exclude Span Patterns"
                    hint="Regex patterns for bank/payment/footer text to drop entirely."
                  >
                    <TagInput
                      value={layoutRules.exclude_span_patterns}
                      onChange={(v) =>
                        updateLayoutFingerprint("exclude_span_patterns", v)
                      }
                      placeholder=""
                    />
                  </Field>
                </>
              )}
            </Section>

            <Section
              title="Training gates"
              description="Approval requirements for this profile's deterministic extraction model. These control when a trained model can be approved for zero-cost reuse."
            >
              <Field
                label="Minimum validation successes"
                hint="How many training documents the model must reproduce exactly before approval is enabled."
              >
                <Input
                  type="number"
                  min={1}
                  value={trainingConfig.min_validation_successes}
                  onChange={(e) => {
                    const parsed = Number.parseInt(e.target.value, 10);
                    updateTrainingConfig(
                      "min_validation_successes",
                      Number.isNaN(parsed) ? 1 : Math.max(1, parsed)
                    );
                  }}
                  placeholder=""
                />
              </Field>
              <div className="flex items-center justify-between rounded-lg border px-4 py-3">
                <div>
                  <p className="text-sm font-medium">Require reconciliation</p>
                  <p className="text-xs text-muted-foreground">
                    Training documents must also pass the totals reconciliation check, not
                    just match the deliverable rows.
                  </p>
                </div>
                <Switch
                  checked={trainingConfig.require_reconciliation}
                  onCheckedChange={(checked) =>
                    updateTrainingConfig("require_reconciliation", checked)
                  }
                />
              </div>
            </Section>

            <Section
              title="Generated configuration"
              description="Read-only extraction plan and evidence produced by profile authoring and validation."
            >
              <GeneratedProfileDetails
                extractionPlan={advanced.extraction_plan}
                evidence={profile.authoring_evidence}
              />
            </Section>
            </>
            )}

          </TabsContent>

          {/* ── Preview Output ───────────────────────────────── */}
          {!isNew && (
            <TabsContent value="preview" className="space-y-6">
              <Section
                title="Preview extraction"
                description="Upload a sample PDF to see how this profile's config extracts it, before going live. Save your changes first so the preview uses them."
              >
                <PdfPreviewPanel
                  buttonLabel="Upload a sample PDF"
                  runningLabel="Running AI extraction (can take a minute or two)…"
                  onPreview={(file) => profilePreview.mutateAsync(file)}
                />
              </Section>
            </TabsContent>
          )}

          {/* ── Training workflow ────────────────────────────── */}
          {!isNew && (
            <TabsContent value="training">
              <TrainingPanel profile={profile} />
            </TabsContent>
          )}
        </Tabs>
      )}
    </div>
  );
}
