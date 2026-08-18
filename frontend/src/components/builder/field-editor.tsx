"use client";

import { useState } from "react";
import { Plus, Trash2, ChevronDown, ChevronRight } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { useCanvasGeometry } from "./layout-canvas";
import { columnColor } from "./column-editor";
import { ValueBuilder } from "./value-editor";
import type { DraftColumnDef, DraftItemFieldRule } from "@/hooks/use-builder";

const NUMERIC_TRANSFORMS = new Set(["currency", "integer", "decimal"]);

const CANONICAL_FIELDS: { name: string; transform: string }[] = [
  { name: "service_id", transform: "string" },
  { name: "billing_reference", transform: "string" },
  { name: "description", transform: "string" },
  { name: "charge_type", transform: "string" },
  { name: "amount", transform: "currency" },
  { name: "tax_amount", transform: "currency" },
  { name: "unit_rate", transform: "currency" },
  { name: "quantity", transform: "integer" },
  { name: "currency", transform: "string" },
  { name: "billing_period_start", transform: "date" },
  { name: "billing_period_end", transform: "date" },
];

const TRANSFORM_TYPES = ["string", "currency", "integer", "decimal", "date"];
const ROWS_MODES = ["all_in_group", "first", "last", "role", "emit_row"];
const NONE = "__none__";

export function defaultFieldRule(name: string): DraftItemFieldRule {
  const def = CANONICAL_FIELDS.find((f) => f.name === name);
  const type = def?.transform ?? "string";
  return {
    name,
    source: {
      rows: "all_in_group",
      row_role: null,
      column: null,
      pattern: null,
      join: name === "description" ? "; " : null,
      occurrence: "first",
    },
    transform: {
      type,
      date_format: type === "date" ? "%d/%m/%Y" : null,
      default: null,
    },
    required: name === "amount",
  };
}

// --- canvas overlay -------------------------------------------------------

export interface FieldsOverlayProps {
  fields: DraftItemFieldRule[];
  columns: DraftColumnDef[];
  selectedField: string | null;
}

/** Highlight each mapped field's source column, labelled with the field name. */
export function FieldsOverlay({ fields, columns, selectedField }: FieldsOverlayProps) {
  const g = useCanvasGeometry();
  const colByName = new Map(columns.map((c, i) => [c.name, { col: c, index: i }]));

  return (
    <div className="pointer-events-none absolute inset-0">
      {fields.map((f, fi) => {
        const colName = f.source.column;
        if (!colName) return null;
        const entry = colByName.get(colName);
        if (!entry) return null;
        const { col, index } = entry;
        const x0 = Math.min(col.x0, col.x1);
        const x1 = Math.max(col.x0, col.x1);
        const color = columnColor(index);
        const selected = selectedField === f.name;
        return (
          <div key={f.name}>
            <div
              className="absolute bottom-0 top-0"
              style={{
                left: g.x(x0),
                width: g.x(x1 - x0),
                background: color + (selected ? "22" : "10"),
                borderLeft: `2px solid ${color}`,
                borderRight: `2px solid ${color}`,
              }}
            />
            <div
              className="absolute -translate-x-1/2 whitespace-nowrap rounded px-1 text-[10px] font-medium text-white shadow-sm"
              style={{ left: g.x((x0 + x1) / 2), top: 2 + fi * 12, background: color }}
            >
              {f.name}
            </div>
          </div>
        );
      })}
    </div>
  );
}

// --- sidebar panel --------------------------------------------------------

function FieldCard({
  field,
  columns,
  roles,
  onChange,
  onRemove,
  onFocus,
}: {
  field: DraftItemFieldRule;
  columns: DraftColumnDef[];
  roles: string[];
  onChange: (patch: Partial<DraftItemFieldRule>) => void;
  onRemove: () => void;
  onFocus: () => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const src = field.source;
  const tf = field.transform;
  const computed = !!field.value && field.value.op !== "extract";
  const patchSource = (p: Partial<DraftItemFieldRule["source"]>) =>
    onChange({ source: { ...src, ...p } });
  const patchTransform = (p: Partial<DraftItemFieldRule["transform"]>) =>
    onChange({ transform: { ...tf, ...p } });

  return (
    <li className="space-y-2 rounded-md border p-2" onClick={onFocus}>
      <div className="flex items-center gap-2">
        <span className="flex-1 font-mono text-sm">{field.name}</span>
        <Button
          variant="ghost"
          size="icon-sm"
          className="text-muted-foreground hover:text-destructive"
          onClick={(e) => {
            e.stopPropagation();
            onRemove();
          }}
        >
          <Trash2 className="h-4 w-4" />
        </Button>
      </div>

      <div className="grid grid-cols-2 gap-2">
        {!computed && (
          <Select
            value={src.column ?? ""}
            onValueChange={(v) => patchSource({ column: v === NONE ? null : v })}
          >
            <SelectTrigger className="h-8">
              <SelectValue placeholder="Column" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={NONE}>No column</SelectItem>
              {columns.map((c) => (
                <SelectItem key={c.name} value={c.name}>
                  {c.name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        )}

        <Select value={tf.type} onValueChange={(v) => v && patchTransform({ type: v })}>
          <SelectTrigger className="h-8">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {TRANSFORM_TYPES.map((t) => (
              <SelectItem key={t} value={t}>
                {t}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      {tf.type === "date" && (
        <Input
          value={tf.date_format ?? ""}
          onChange={(e) => patchTransform({ date_format: e.target.value })}
          className="h-8 font-mono text-xs"
          placeholder="%d/%m/%Y"
        />
      )}

      {NUMERIC_TRANSFORMS.has(tf.type) && (
        <ValueBuilder
          value={field.value}
          roles={roles}
          columns={columns}
          onChange={(v) => onChange({ value: v })}
        />
      )}

      <button
        className="flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground"
        onClick={(e) => {
          e.stopPropagation();
          setExpanded((x) => !x);
        }}
      >
        {expanded ? <ChevronDown className="h-3 w-3" /> : <ChevronRight className="h-3 w-3" />}
        Advanced
      </button>

      {expanded && (
        <div className="space-y-2 border-t pt-2">
          <div className="grid grid-cols-2 gap-2">
            <Select value={src.rows} onValueChange={(v) => v && patchSource({ rows: v })}>
              <SelectTrigger className="h-8">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {ROWS_MODES.map((m) => (
                  <SelectItem key={m} value={m}>
                    {m}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            {src.rows === "role" && (
              <Select
                value={src.row_role ?? ""}
                onValueChange={(v) => patchSource({ row_role: v === NONE ? null : v })}
              >
                <SelectTrigger className="h-8">
                  <SelectValue placeholder="Role" />
                </SelectTrigger>
                <SelectContent>
                  {roles.map((r) => (
                    <SelectItem key={r} value={r}>
                      {r}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            )}
          </div>
          <div className="flex items-center gap-2">
            <span className="text-xs text-muted-foreground">Occurrence</span>
            <Select
              value={src.occurrence ?? "first"}
              onValueChange={(v) => v && patchSource({ occurrence: v })}
            >
              <SelectTrigger className="h-8 w-28">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="first">first</SelectItem>
                <SelectItem value="last">last</SelectItem>
              </SelectContent>
            </Select>
          </div>
          <Input
            value={src.pattern ?? ""}
            onChange={(e) => patchSource({ pattern: e.target.value || null })}
            className="h-8 font-mono text-xs"
            placeholder="capture regex, e.g. ([0-9.,]+)"
          />
          {src.rows === "all_in_group" && (
            <Input
              value={src.join ?? ""}
              onChange={(e) => patchSource({ join: e.target.value || null })}
              className="h-8 text-xs"
              placeholder="join separator (e.g. '; ')"
            />
          )}
          <div className="flex items-center gap-2">
            <Switch
              checked={!!field.required}
              onCheckedChange={(c) => onChange({ required: c })}
            />
            <span className="text-sm">Required (drop items missing this)</span>
          </div>
        </div>
      )}
    </li>
  );
}

export interface FieldEditorPanelProps {
  fields: DraftItemFieldRule[];
  onChange: (fields: DraftItemFieldRule[]) => void;
  columns: DraftColumnDef[];
  roles: string[];
  selectedField: string | null;
  onSelectField: (name: string | null) => void;
}

/** Sidebar: add canonical fields and map each to a column + transform. */
export function FieldEditorPanel({
  fields,
  onChange,
  columns,
  roles,
  selectedField,
  onSelectField,
}: FieldEditorPanelProps) {
  const used = new Set(fields.map((f) => f.name));
  const available = CANONICAL_FIELDS.filter((f) => !used.has(f.name));

  const addField = (name: string) => onChange([...fields, defaultFieldRule(name)]);
  const patch = (i: number, p: Partial<DraftItemFieldRule>) =>
    onChange(fields.map((f, idx) => (idx === i ? { ...f, ...p } : f)));
  const remove = (i: number) => {
    const removed = fields[i];
    onChange(fields.filter((_, idx) => idx !== i));
    if (selectedField === removed.name) onSelectField(null);
  };

  return (
    <div className="space-y-3">
      <p className="text-sm text-muted-foreground">
        Map each field to a column. Amounts and dates are coerced by their transform.
      </p>

      {columns.length === 0 && (
        <div className="rounded-md border border-dashed p-3 text-xs text-muted-foreground">
          Tip: draw column bands on the <span className="font-medium">Columns</span> tab first.
        </div>
      )}

      <Select value="" onValueChange={(v) => v && addField(v)} disabled={available.length === 0}>
        <SelectTrigger className="h-9">
          <span className="flex items-center gap-2 text-sm">
            <Plus className="h-4 w-4" />
            {available.length === 0 ? "All fields added" : "Add field"}
          </span>
        </SelectTrigger>
        <SelectContent>
          {available.map((f) => (
            <SelectItem key={f.name} value={f.name}>
              {f.name}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>

      {fields.length > 0 && (
        <ul className="space-y-2">
          {fields.map((f, i) => (
            <FieldCard
              key={f.name}
              field={f}
              columns={columns}
              roles={roles}
              onChange={(p) => patch(i, p)}
              onRemove={() => remove(i)}
              onFocus={() => onSelectField(f.name)}
            />
          ))}
        </ul>
      )}
    </div>
  );
}
