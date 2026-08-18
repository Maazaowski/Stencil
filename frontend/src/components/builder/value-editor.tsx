"use client";

import { Plus, Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import type { DraftColumnDef, DraftValueExpr, DraftValueOperand } from "@/hooks/use-builder";

const NONE = "__none__";
const ANY_ROW = "__any__";

/** Operations over operands. "extract" (a single source) is the default and is
 * represented by the field's normal `source`, so it maps to `value = null`. */
const OPS: { op: DraftValueExpr["op"]; label: string; hint: string }[] = [
  { op: "extract", label: "Extract (one source)", hint: "Read one value from the page." },
  { op: "sum", label: "Sum (a + b + …)", hint: "Add the operands; a missing one counts as 0." },
  { op: "subtract", label: "Subtract (a − b − …)", hint: "First operand minus the rest (e.g. Gross − Discount)." },
  { op: "product", label: "Multiply (a × b)", hint: "Multiply the operands (e.g. amount × rate)." },
];

/** Fields/totals an operand can reference. */
const REF_OPTIONS = ["amount", "subtotal", "tax", "tax_rate", "total_due", "current_charges"];

function emptyOperand(): DraftValueOperand {
  return {
    kind: "extract",
    source: { rows: "role", row_role: null, column: null, pattern: null },
    const: null,
    ref: null,
  };
}

function OperandEditor({
  operand,
  roles,
  columns,
  onChange,
  onRemove,
}: {
  operand: DraftValueOperand;
  roles: string[];
  columns: DraftColumnDef[];
  onChange: (o: DraftValueOperand) => void;
  onRemove: () => void;
}) {
  const src = operand.source ?? { rows: "role" };
  const roleValue = src.rows === "all_in_group" ? ANY_ROW : (src.row_role ?? "");

  return (
    <div className="space-y-1.5 rounded-md border bg-muted/20 p-1.5">
      <div className="flex items-center gap-1.5">
        <Select value={operand.kind} onValueChange={(v) => v && onChange({ ...operand, kind: v as DraftValueOperand["kind"] })}>
          <SelectTrigger className="h-7 w-32 text-xs">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="extract">From page</SelectItem>
            <SelectItem value="const">Number</SelectItem>
            <SelectItem value="ref">Reference</SelectItem>
          </SelectContent>
        </Select>
        <div className="flex-1" />
        <Button
          variant="ghost"
          size="icon-sm"
          className="text-muted-foreground hover:text-destructive"
          onClick={onRemove}
        >
          <Trash2 className="h-3.5 w-3.5" />
        </Button>
      </div>

      {operand.kind === "extract" && (
        <div className="grid grid-cols-2 gap-1.5">
          <Select
            value={roleValue || ANY_ROW}
            onValueChange={(v) =>
              onChange({
                ...operand,
                source:
                  v === ANY_ROW
                    ? { ...src, rows: "all_in_group", row_role: null }
                    : { ...src, rows: "role", row_role: v },
              })
            }
          >
            <SelectTrigger className="h-7 text-xs">
              <SelectValue placeholder="Row role" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={ANY_ROW}>any row in group</SelectItem>
              {roles.map((r) => (
                <SelectItem key={r} value={r}>
                  {r}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Select
            value={src.column ?? ""}
            onValueChange={(v) => onChange({ ...operand, source: { ...src, column: v === NONE ? null : v } })}
          >
            <SelectTrigger className="h-7 text-xs">
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
        </div>
      )}

      {operand.kind === "const" && (
        <Input
          value={operand.const ?? ""}
          onChange={(e) => onChange({ ...operand, const: e.target.value || null })}
          className="h-7 font-mono text-xs"
          placeholder="number, e.g. 0.2"
          inputMode="decimal"
        />
      )}

      {operand.kind === "ref" && (
        <Select value={operand.ref ?? ""} onValueChange={(v) => v && onChange({ ...operand, ref: v })}>
          <SelectTrigger className="h-7 text-xs">
            <SelectValue placeholder="field / total…" />
          </SelectTrigger>
          <SelectContent>
            {REF_OPTIONS.map((r) => (
              <SelectItem key={r} value={r}>
                {r}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      )}
    </div>
  );
}

export interface ValueBuilderProps {
  value: DraftValueExpr | null | undefined;
  roles: string[];
  columns: DraftColumnDef[];
  onChange: (value: DraftValueExpr | null) => void;
}

/** Compose a field's value: Extract (one source, the default) or a computation
 * over operands. Used for numeric fields (amount, tax_amount, …). */
export function ValueBuilder({ value, roles, columns, onChange }: ValueBuilderProps) {
  const op = value?.op ?? "extract";
  const operands = value?.operands ?? [];

  const setOp = (next: DraftValueExpr["op"] | null) => {
    if (!next || next === "extract") {
      onChange(null); // fall back to the field's single source
      return;
    }
    const ops = operands.length >= 2 ? operands : [emptyOperand(), emptyOperand()];
    onChange({ op: next, operands: ops });
  };

  const patchOperand = (i: number, o: DraftValueOperand) =>
    onChange({ op: op as DraftValueExpr["op"], operands: operands.map((x, idx) => (idx === i ? o : x)) });
  const addOperand = () =>
    onChange({ op: op as DraftValueExpr["op"], operands: [...operands, emptyOperand()] });
  const removeOperand = (i: number) => {
    const next = operands.filter((_, idx) => idx !== i);
    onChange(next.length ? { op: op as DraftValueExpr["op"], operands: next } : null);
  };

  const hint = OPS.find((o) => o.op === op)?.hint;

  return (
    <div className="space-y-1.5">
      <div className="flex items-center gap-2">
        <span className="text-xs font-medium">Value</span>
        <Select value={op} onValueChange={setOp}>
          <SelectTrigger className="h-7 flex-1 text-xs">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {OPS.map((o) => (
              <SelectItem key={o.op} value={o.op}>
                {o.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>
      {op !== "extract" && (
        <>
          {hint && <p className="text-[11px] text-muted-foreground">{hint}</p>}
          <div className="space-y-1.5">
            {operands.map((o, i) => (
              <OperandEditor
                key={i}
                operand={o}
                roles={roles}
                columns={columns}
                onChange={(next) => patchOperand(i, next)}
                onRemove={() => removeOperand(i)}
              />
            ))}
          </div>
          <Button variant="outline" size="sm" className="h-7 w-full text-xs" onClick={addOperand}>
            <Plus className="mr-1.5 h-3.5 w-3.5" />
            Add operand
          </Button>
        </>
      )}
    </div>
  );
}
