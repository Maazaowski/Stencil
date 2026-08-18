"use client";

import { cn } from "@/lib/utils";
import { Input } from "@/components/ui/input";
import type { DraftTaxConfig } from "@/hooks/use-builder";

const MODES: { key: string; label: string; hint: string }[] = [
  { key: "none", label: "No tax", hint: "EXT_TAX stays blank." },
  {
    key: "flat_rate",
    label: "Flat rate",
    hint: "EXT_TAX = amount × rate. The common case (e.g. 20% VAT).",
  },
  {
    key: "per_line",
    label: "Per-line amount",
    hint: "Derive the rate from a tax_amount field — map one on the Fields tab.",
  },
  {
    key: "subtotal_tax",
    label: "From subtotal + tax",
    hint: "Rate = document tax ÷ subtotal — capture both on the Header/Totals tab.",
  },
];

export interface TaxEditorPanelProps {
  tax: DraftTaxConfig;
  onChange: (patch: Partial<DraftTaxConfig>) => void;
}

/** Sidebar control for how the delivered EXT_TAX column is produced. */
export function TaxEditorPanel({ tax, onChange }: TaxEditorPanelProps) {
  const active = MODES.find((m) => m.key === tax.mode) ?? MODES[0];
  return (
    <div className="space-y-4">
      <p className="text-sm text-muted-foreground">How should the tax column be produced?</p>

      <div className="space-y-1.5">
        {MODES.map((m) => (
          <button
            key={m.key}
            onClick={() => onChange({ mode: m.key })}
            className={cn(
              "flex w-full flex-col items-start rounded-md border p-2 text-left transition-colors",
              tax.mode === m.key ? "border-primary bg-primary/5" : "hover:bg-muted",
            )}
          >
            <span className="text-sm font-medium">{m.label}</span>
            <span className="text-xs text-muted-foreground">{m.hint}</span>
          </button>
        ))}
      </div>

      {tax.mode === "flat_rate" && (
        <div className="space-y-1.5">
          <span className="text-sm font-medium">Rate (%)</span>
          <Input
            type="number"
            value={tax.rate ?? ""}
            onChange={(e) =>
              onChange({ rate: e.target.value === "" ? null : Number(e.target.value) })
            }
            className="h-8 w-28"
            placeholder="20"
          />
        </div>
      )}

      <p className="rounded-md border bg-muted/40 p-2 text-xs text-muted-foreground">{active.hint}</p>
    </div>
  );
}
