"use client";

import { useMemo } from "react";
import { AlertTriangle, ChevronDown, Plus, RotateCcw, Trash2 } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectLabel,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { TagInput } from "@/components/form/tag-input";
import type {
  FieldDef,
  FieldSchema,
  OutputMappingOverride,
  OutputTransform,
  OutputSpec,
} from "@/types";

const COMPUTED_SOURCES = ["computed.line_tax"];
const OUTPUT_TRANSFORMS: Array<{ value: OutputTransform; label: string }> = [
  { value: "digits_only", label: "Keep digits only" },
  { value: "trim", label: "Trim spaces" },
  { value: "uppercase", label: "Uppercase" },
  { value: "lowercase", label: "Lowercase" },
];

function fieldKey(field: Pick<FieldDef, "scope" | "name">) {
  return `${field.scope}.${field.name}`;
}

function humanize(name: string) {
  return name
    .split("_")
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ");
}

export function mergeProfileFields(
  schema: FieldSchema | undefined,
  overrides: FieldDef[] | undefined,
): FieldDef[] {
  if (!schema) return overrides ?? [];
  const fields = new Map(schema.fields.map((field) => [fieldKey(field), { ...field }]));
  for (const override of overrides ?? []) {
    const key = fieldKey(override);
    const base = fields.get(key);
    fields.set(key, base ? {
      ...base,
      label_hint: override.label_hint || base.label_hint,
      date_format: override.date_format || base.date_format,
      description: override.description || base.description,
      required: Boolean(base.required || override.required),
      enum_values: override.enum_values?.length ? override.enum_values : base.enum_values,
    } : { ...override });
  }
  return Array.from(fields.values());
}

export function applyOutputMappingOverrides(
  spec: OutputSpec | undefined,
  overrides: OutputMappingOverride[] | undefined,
): OutputSpec | undefined {
  if (!spec) return undefined;
  const counts = new Map<string, number>();
  for (const override of overrides ?? []) {
    counts.set(override.output_header, (counts.get(override.output_header) ?? 0) + 1);
  }
  const unique = new Map(
    (overrides ?? [])
      .filter((override) => counts.get(override.output_header) === 1)
      .map((override) => [override.output_header, override]),
  );
  return {
    ...spec,
    columns: spec.columns.map((column) => {
      const override = unique.get(column.header);
      return override
        ? {
            ...column,
            source: override.source,
            fallback: override.fallback ?? null,
            transforms: override.transforms ?? [],
          }
        : column;
    }),
  };
}

function SourceSelect({
  value,
  fields,
  onChange,
  allowNone = false,
}: {
  value: string | null | undefined;
  fields: FieldDef[];
  onChange: (value: string | null) => void;
  allowNone?: boolean;
}) {
  const documentFields = fields.filter((field) => field.scope === "document");
  const rowFields = fields.filter((field) => field.scope === "row");
  const validPaths = new Set([
    ...documentFields.map((field) => `field.${field.name}`),
    ...rowFields.map((field) => `row.${field.name}`),
    ...COMPUTED_SOURCES,
  ]);
  return (
    <Select
      value={value || (allowNone ? "__none__" : undefined)}
      onValueChange={(next) => onChange(!next || next === "__none__" ? null : next)}
    >
      <SelectTrigger className="w-full">
        <SelectValue placeholder="Choose a source" />
      </SelectTrigger>
      <SelectContent>
        {allowNone && <SelectItem value="__none__">No fallback</SelectItem>}
        {value && !validPaths.has(value) && (
          <SelectItem value={value}>{value} (unavailable)</SelectItem>
        )}
        <SelectGroup>
          <SelectLabel>Document fields</SelectLabel>
          {documentFields.map((field) => (
            <SelectItem key={`field.${field.name}`} value={`field.${field.name}`}>
              {humanize(field.name)} — document
            </SelectItem>
          ))}
        </SelectGroup>
        <SelectGroup>
          <SelectLabel>Row fields</SelectLabel>
          {rowFields.map((field) => (
            <SelectItem key={`row.${field.name}`} value={`row.${field.name}`}>
              {humanize(field.name)} — row
            </SelectItem>
          ))}
        </SelectGroup>
        <SelectGroup>
          <SelectLabel>Computed values</SelectLabel>
          {COMPUTED_SOURCES.map((source) => (
            <SelectItem key={source} value={source}>{humanize(source.slice(9))} — computed</SelectItem>
          ))}
        </SelectGroup>
      </SelectContent>
    </Select>
  );
}

export function OutputMappingEditor({
  spec,
  fields,
  overrides,
  onChange,
}: {
  spec: OutputSpec | undefined;
  fields: FieldDef[];
  overrides: OutputMappingOverride[];
  onChange: (overrides: OutputMappingOverride[]) => void;
}) {
  const headerCounts = useMemo(() => {
    const counts = new Map<string, number>();
    for (const column of spec?.columns ?? []) {
      counts.set(column.header, (counts.get(column.header) ?? 0) + 1);
    }
    return counts;
  }, [spec]);
  const overrideCounts = useMemo(() => {
    const counts = new Map<string, number>();
    for (const override of overrides) {
      counts.set(override.output_header, (counts.get(override.output_header) ?? 0) + 1);
    }
    return counts;
  }, [overrides]);
  const availablePaths = new Set([
    ...fields.map((field) => `${field.scope === "document" ? "field" : "row"}.${field.name}`),
    ...COMPUTED_SOURCES,
  ]);
  const errors = overrides.flatMap((override) => {
    const result: string[] = [];
    if (!headerCounts.has(override.output_header)) result.push(`${override.output_header}: output column no longer exists.`);
    else if ((headerCounts.get(override.output_header) ?? 0) > 1) result.push(`${override.output_header}: header is duplicated in the output spec.`);
    if ((overrideCounts.get(override.output_header) ?? 0) > 1) result.push(`${override.output_header}: more than one profile override exists.`);
    if (!availablePaths.has(override.source)) result.push(`${override.output_header}: source ${override.source} is not in the selected field schema.`);
    if (override.fallback && !availablePaths.has(override.fallback)) result.push(`${override.output_header}: fallback ${override.fallback} is not available.`);
    if (new Set(override.transforms ?? []).size !== (override.transforms ?? []).length) result.push(`${override.output_header}: transforms are duplicated.`);
    if ((override.transforms ?? []).includes("uppercase") && (override.transforms ?? []).includes("lowercase")) result.push(`${override.output_header}: uppercase and lowercase cannot be combined.`);
    if ((override.transforms ?? []).length && override.source.startsWith("computed.")) result.push(`${override.output_header}: computed values cannot use string transforms.`);
    return result;
  });

  if (!spec) return <p className="text-sm text-muted-foreground">Choose an output spec to configure mappings.</p>;

  function replace(header: string, patch: Partial<OutputMappingOverride>) {
    onChange(overrides.map((item) => item.output_header === header ? { ...item, ...patch } : item));
  }

  return (
    <div className="space-y-4">
      {errors.length > 0 && (
        <div className="rounded-md border border-destructive/40 bg-destructive/5 p-3 text-sm text-destructive">
          <div className="flex items-center gap-2 font-medium"><AlertTriangle className="h-4 w-4" />Fix mapping issues before saving</div>
          <ul className="mt-2 list-disc space-y-1 pl-5">{Array.from(new Set(errors)).map((error) => <li key={error}>{error}</li>)}</ul>
        </div>
      )}
      {spec.columns.map((column, columnIndex) => {
        const override = overrides.find((item) => item.output_header === column.header);
        const ambiguous = (headerCounts.get(column.header) ?? 0) > 1;
        return (
          <div key={`${column.header}-${columnIndex}`} className="rounded-lg border p-4">
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div>
                <div className="flex items-center gap-2">
                  <span className="font-medium">{column.header}</span>
                  {override && <Badge variant="secondary">Profile override</Badge>}
                </div>
                <p className="mt-1 text-xs text-muted-foreground">
                  Output spec: <span className="font-mono">{column.source}</span>
                  {column.fallback ? <> · fallback <span className="font-mono">{column.fallback}</span></> : null}
                </p>
              </div>
              {override ? (
                <Button variant="ghost" size="sm" onClick={() => onChange(overrides.filter((item) => item.output_header !== column.header))}>
                  <RotateCcw className="h-4 w-4" />Reset
                </Button>
              ) : (
                <Button
                  variant="outline"
                  size="sm"
                  disabled={ambiguous}
                  onClick={() => onChange([...overrides, {
                    output_header: column.header,
                    source: column.source,
                    fallback: column.fallback ?? null,
                    transforms: [],
                  }])}
                >
                  Override
                </Button>
              )}
            </div>
            {override && (
              <div className="mt-4 space-y-3">
                <div>
                  <label className="mb-1.5 block text-xs font-medium">Source</label>
                  <SourceSelect value={override.source} fields={fields} onChange={(source) => source && replace(column.header, { source })} />
                </div>
                <details className="group rounded-md bg-muted/30 px-3 py-2">
                  <summary className="flex cursor-pointer list-none items-center justify-between text-xs font-medium">
                    Fallback value <ChevronDown className="h-4 w-4 transition-transform group-open:rotate-180" />
                  </summary>
                  <div className="pt-3">
                    <SourceSelect value={override.fallback} fields={fields} allowNone onChange={(fallback) => replace(column.header, { fallback })} />
                  </div>
                </details>
                <details className="group rounded-md bg-muted/30 px-3 py-2">
                  <summary className="flex cursor-pointer list-none items-center justify-between text-xs font-medium">
                    Value formatting
                    <span className="flex items-center gap-2">
                      {(override.transforms ?? []).length > 0 && <Badge variant="outline">{override.transforms?.length}</Badge>}
                      <ChevronDown className="h-4 w-4 transition-transform group-open:rotate-180" />
                    </span>
                  </summary>
                  <div className="space-y-3 pt-3">
                    {OUTPUT_TRANSFORMS.map((transform) => {
                      const enabled = (override.transforms ?? []).includes(transform.value);
                      const conflicts = (
                        (transform.value === "uppercase" && (override.transforms ?? []).includes("lowercase"))
                        || (transform.value === "lowercase" && (override.transforms ?? []).includes("uppercase"))
                      );
                      return (
                        <label key={transform.value} className="flex items-center justify-between gap-3 text-sm">
                          <span>{transform.label}</span>
                          <Switch
                            checked={enabled}
                            disabled={conflicts || override.source.startsWith("computed.")}
                            onCheckedChange={(checked) => replace(column.header, {
                              transforms: checked
                                ? [...(override.transforms ?? []), transform.value]
                                : (override.transforms ?? []).filter((value) => value !== transform.value),
                            })}
                          />
                        </label>
                      );
                    })}
                  </div>
                </details>
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}

const FIELD_TYPES = ["string", "date", "number", "currency", "integer", "enum"];
const FIELD_ROLES = ["none", "identifier", "amount", "tax", "subtotal", "total", "tax_rate", "excluded_total"];

export function ExtractionFieldSettings({
  schema,
  overrides,
  onChange,
}: {
  schema: FieldSchema | undefined;
  overrides: FieldDef[];
  onChange: (overrides: FieldDef[]) => void;
}) {
  const baseKeys = new Set(schema?.fields.map(fieldKey) ?? []);
  const fields = mergeProfileFields(schema, overrides);

  function updateOverride(field: FieldDef, patch: Partial<FieldDef>) {
    const key = fieldKey(field);
    const existing = overrides.find((item) => fieldKey(item) === key);
    const next: FieldDef = { ...field, ...existing, ...patch };
    onChange([...overrides.filter((item) => fieldKey(item) !== key), next]);
  }

  function addCustomField() {
    let suffix = 1;
    const names = new Set(fields.map((field) => field.name));
    while (names.has(`custom_field_${suffix}`)) suffix += 1;
    onChange([...overrides, {
      name: `custom_field_${suffix}`,
      scope: "document",
      type: "string",
      role: "none",
      label_hint: null,
      date_format: null,
      required: false,
      enum_values: [],
      description: "",
    }]);
  }

  return (
    <div className="space-y-3">
      <div className="flex justify-end">
        <Button variant="outline" size="sm" onClick={addCustomField}><Plus className="h-4 w-4" />Add custom field</Button>
      </div>
      {fields.map((field) => {
        const key = fieldKey(field);
        const isBase = baseKeys.has(key);
        const baseField = schema?.fields.find((item) => fieldKey(item) === key);
        const hasOverride = overrides.some((item) => fieldKey(item) === key);
        return (
          <details key={key} className="group rounded-lg border">
            <summary className="flex cursor-pointer list-none items-center justify-between gap-3 px-4 py-3">
              <div className="min-w-0">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="font-medium">{humanize(field.name)}</span>
                  <Badge variant="outline">{field.scope}</Badge>
                  <Badge variant="outline">{field.type}</Badge>
                  {hasOverride && <Badge variant="secondary">Customized</Badge>}
                </div>
                <p className="mt-1 truncate text-xs text-muted-foreground">{field.description || field.name}</p>
              </div>
              <ChevronDown className="h-4 w-4 shrink-0 transition-transform group-open:rotate-180" />
            </summary>
            <div className="grid gap-4 border-t p-4 md:grid-cols-2">
              {!isBase && (
                <>
                  <div><label className="mb-1 block text-xs font-medium">Field name</label><Input value={field.name} onChange={(event) => updateOverride(field, { name: event.target.value })} /></div>
                  <div><label className="mb-1 block text-xs font-medium">Scope</label><Select value={field.scope} onValueChange={(scope) => updateOverride(field, { scope: scope as "document" | "row" })}><SelectTrigger className="w-full"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="document">Document</SelectItem><SelectItem value="row">Row</SelectItem></SelectContent></Select></div>
                  <div><label className="mb-1 block text-xs font-medium">Type</label><Select value={field.type} onValueChange={(type) => updateOverride(field, { type: type ?? "string" })}><SelectTrigger className="w-full"><SelectValue /></SelectTrigger><SelectContent>{FIELD_TYPES.map((type) => <SelectItem key={type} value={type}>{humanize(type)}</SelectItem>)}</SelectContent></Select></div>
                  <div><label className="mb-1 block text-xs font-medium">Role</label><Select value={field.role} onValueChange={(role) => updateOverride(field, { role: role ?? "none" })}><SelectTrigger className="w-full"><SelectValue /></SelectTrigger><SelectContent>{FIELD_ROLES.map((role) => <SelectItem key={role} value={role}>{humanize(role)}</SelectItem>)}</SelectContent></Select></div>
                </>
              )}
              <div><label className="mb-1 block text-xs font-medium">Printed label</label><Input value={field.label_hint ?? ""} onChange={(event) => updateOverride(field, { label_hint: event.target.value || null })} /></div>
              {field.type === "date" && <div><label className="mb-1 block text-xs font-medium">Printed date format</label><Input className="font-mono" value={field.date_format ?? ""} onChange={(event) => updateOverride(field, { date_format: event.target.value || null })} placeholder="e.g. %d/%m/%Y" /></div>}
              <div className="md:col-span-2"><label className="mb-1 block text-xs font-medium">Description</label><Input value={field.description ?? ""} onChange={(event) => updateOverride(field, { description: event.target.value })} /></div>
              {field.type === "enum" && <div className="md:col-span-2"><label className="mb-1 block text-xs font-medium">Allowed values</label><TagInput value={field.enum_values ?? []} onChange={(enum_values) => updateOverride(field, { enum_values })} placeholder="Add value" /></div>}
              <div className="flex items-center justify-between rounded-md border px-3 py-2 md:col-span-2"><div><p className="text-sm font-medium">Required</p><p className="text-xs text-muted-foreground">{baseField?.required ? "Required by the selected field schema." : "Extraction must return this field."}</p></div><Switch checked={Boolean(field.required)} disabled={Boolean(baseField?.required)} onCheckedChange={(required) => updateOverride(field, { required })} /></div>
              <div className="flex justify-end md:col-span-2">
                {isBase ? (
                  <Button variant="ghost" size="sm" disabled={!hasOverride} onClick={() => onChange(overrides.filter((item) => fieldKey(item) !== key))}><RotateCcw className="h-4 w-4" />Reset field</Button>
                ) : (
                  <Button variant="ghost" size="sm" onClick={() => onChange(overrides.filter((item) => fieldKey(item) !== key))}><Trash2 className="h-4 w-4" />Remove custom field</Button>
                )}
              </div>
            </div>
          </details>
        );
      })}
    </div>
  );
}

export function FixedDocumentValues({
  fields,
  values,
  onChange,
}: {
  fields: FieldDef[];
  values: Record<string, unknown>;
  onChange: (values: Record<string, unknown>) => void;
}) {
  const documentFields = fields.filter((field) => field.scope === "document");
  const unused = documentFields.filter((field) => !(field.name in values));

  function parseValue(field: FieldDef | undefined, raw: string) {
    if (!field || !["number", "currency", "integer"].includes(field.type)) return raw;
    if (!raw.trim()) return "";
    const number = Number(raw);
    return Number.isFinite(number) ? number : raw;
  }

  return (
    <div className="space-y-3">
      <div className="rounded-md border border-warning/30 bg-warning/12 p-3 text-sm text-muted-foreground">
        Fixed values replace extracted document values. Use them only for values that are genuinely identical on every document for this profile.
      </div>
      {Object.entries(values).map(([name, value]) => {
        const field = documentFields.find((item) => item.name === name);
        return (
          <div key={name} className="grid items-center gap-2 rounded-md border p-3 md:grid-cols-[minmax(0,1fr)_minmax(0,1.4fr)_auto]">
            <div><p className="text-sm font-medium">{humanize(name)}</p><p className="font-mono text-xs text-muted-foreground">field.{name}</p></div>
            <Input type={field?.type === "date" ? "date" : ["number", "currency", "integer"].includes(field?.type ?? "") ? "number" : "text"} value={String(value ?? "")} onChange={(event) => onChange({ ...values, [name]: parseValue(field, event.target.value) })} />
            <Button variant="ghost" size="sm" onClick={() => { const next = { ...values }; delete next[name]; onChange(next); }}><Trash2 className="h-4 w-4" /><span className="sr-only">Remove {name}</span></Button>
          </div>
        );
      })}
      {unused.length > 0 && (
        <Select value={undefined} onValueChange={(name) => name && onChange({ ...values, [name]: "" })}>
          <SelectTrigger className="w-full"><SelectValue placeholder="Add a fixed document field" /></SelectTrigger>
          <SelectContent>{unused.map((field) => <SelectItem key={field.name} value={field.name}>{humanize(field.name)}</SelectItem>)}</SelectContent>
        </Select>
      )}
    </div>
  );
}

function recordOf(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {};
}

function stringList(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : [];
}

export function GeneratedProfileDetails({
  extractionPlan,
  evidence,
}: {
  extractionPlan: Record<string, unknown> | null | undefined;
  evidence: {
    evidence_level: string;
    status: "verified" | "review_required" | "failed";
    category_confidence: number;
    metrics: Record<string, unknown>;
    unresolved_risks: string[];
    hard_blockers?: string[];
    review_warnings?: string[];
    engine_version: string;
  } | null | undefined;
}) {
  const plan = recordOf(extractionPlan);
  const regions = Array.isArray(plan.regions) ? plan.regions.map(recordOf) : [];
  const rowSelector = recordOf(plan.row_selector);
  const documentRules = Object.keys(recordOf(plan.document_field_rules));
  const rowRules = Object.keys(recordOf(plan.row_field_rules));
  const reconciliationRules = Array.isArray(plan.reconciliation_rules)
    ? plan.reconciliation_rules.map(recordOf)
    : [];
  const contextRules = Array.isArray(plan.row_context_rules)
    ? plan.row_context_rules.map(recordOf)
    : [];
  return (
    <div className="space-y-4">
      <div className="rounded-lg border p-4">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div><p className="font-medium">Generated extraction plan</p><p className="text-xs text-muted-foreground">Created by profile authoring and used by the extraction engine.</p></div>
          {Object.keys(plan).length > 0 ? <Badge variant="secondary">Version {String(plan.version ?? "1.0")}</Badge> : <Badge variant="outline">Not generated</Badge>}
        </div>
        {Object.keys(plan).length > 0 && (
          <div className="mt-4 grid gap-3 text-sm md:grid-cols-2">
            <div><p className="text-xs text-muted-foreground">Document family</p><p>{String(plan.document_family ?? "standard")}</p></div>
            <div><p className="text-xs text-muted-foreground">Row selection</p><p>{String(rowSelector.scope ?? "row")}</p></div>
            <div><p className="text-xs text-muted-foreground">Regions</p><p>{regions.map((region) => String(region.name ?? "line_items")).join(", ") || "None"}</p></div>
            <div><p className="text-xs text-muted-foreground">Reconciliation rules</p><p>{reconciliationRules.map((rule) => String(rule.name ?? "rule")).join(", ") || "None"}</p></div>
            <div><p className="text-xs text-muted-foreground">Document field rules</p><p>{documentRules.join(", ") || "None"}</p></div>
            <div><p className="text-xs text-muted-foreground">Row field rules</p><p>{rowRules.join(", ") || "None"}</p></div>
            <div><p className="text-xs text-muted-foreground">Carried context</p><p>{contextRules.map((rule) => Object.keys(recordOf(rule.field_groups)).join(", ")).filter(Boolean).join("; ") || "None"}</p></div>
          </div>
        )}
      </div>
      <div className="rounded-lg border p-4">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div><p className="font-medium">Authoring evidence</p><p className="text-xs text-muted-foreground">Read-only evidence retained from profile discovery and validation.</p></div>
          {evidence ? <Badge variant={evidence.status === "verified" ? "default" : "secondary"}>{evidence.status.replaceAll("_", " ")}</Badge> : <Badge variant="outline">Not available</Badge>}
        </div>
        {evidence && (
          <div className="mt-4 space-y-3 text-sm">
            <div className="grid gap-3 md:grid-cols-3">
              <div><p className="text-xs text-muted-foreground">Evidence</p><p>{evidence.evidence_level.replaceAll("_", " ")}</p></div>
              <div><p className="text-xs text-muted-foreground">Confidence</p><p>{Math.round(evidence.category_confidence * 100)}%</p></div>
              <div><p className="text-xs text-muted-foreground">Engine</p><p>{evidence.engine_version}</p></div>
            </div>
            {(evidence.hard_blockers ?? []).length > 0 && <div className="rounded-md border border-warning/30 bg-warning/12 p-3"><p className="font-medium">Validation findings</p><p className="mt-1 text-xs text-muted-foreground">Informational only; these findings do not block saving or production delivery.</p><ul className="mt-2 list-disc pl-5">{evidence.hard_blockers?.map((risk) => <li key={risk}>{risk}</li>)}</ul></div>}
            {evidence.unresolved_risks.length > 0 && <div className="rounded-md bg-warning/12 p-3"><p className="font-medium">Unresolved risks</p><ul className="mt-1 list-disc pl-5">{evidence.unresolved_risks.map((risk) => <li key={risk}>{risk}</li>)}</ul></div>}
            {Object.keys(evidence.metrics).length > 0 && <details><summary className="cursor-pointer text-xs font-medium">Validation metrics</summary><dl className="mt-2 grid gap-2 md:grid-cols-2">{Object.entries(evidence.metrics).map(([key, value]) => <div key={key}><dt className="text-xs text-muted-foreground">{humanize(key)}</dt><dd>{Array.isArray(value) ? stringList(value).join(", ") : String(value)}</dd></div>)}</dl></details>}
          </div>
        )}
      </div>
    </div>
  );
}
