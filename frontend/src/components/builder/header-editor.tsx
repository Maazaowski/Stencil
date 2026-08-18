"use client";

import { MousePointerClick, Trash2 } from "lucide-react";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { useCanvasGeometry } from "./layout-canvas";
import type { DraftHeaderFieldRule, LayoutCell, LayoutPage } from "@/hooks/use-builder";

/** Standard header fields that feed the deliverable's document-level columns. */
const HEADER_FIELDS: { key: string; label: string; isDate: boolean }[] = [
  { key: "invoice_number", label: "Invoice number", isDate: false },
  { key: "invoice_date", label: "Invoice date → EXT_DATE", isDate: true },
  { key: "due_date", label: "Due date → formula", isDate: true },
  { key: "account_number", label: "Account number → EXT_ACCOUNT", isDate: false },
];

const POSITIONS = ["right", "below", "left", "above"];

/** Client-side approximation of the interpreter's label-relative lookup — shows a
 * live sample value so the user can confirm the label/position before saving. */
export function resolveHeaderSample(pages: LayoutPage[], rule: DraftHeaderFieldRule): string | null {
  if (rule.literal) return rule.literal;
  const lab = (rule.label || "").toLowerCase().trim();
  if (!lab) return null;
  for (const page of pages) {
    for (let ri = 0; ri < page.rows.length; ri++) {
      const row = page.rows[ri];
      const ci = row.cells.findIndex((c) => c.text.toLowerCase().includes(lab));
      if (ci < 0) continue;
      const labelCell = row.cells[ci];
      let raw: string | null = null;
      if (rule.value_position === "right") {
        const idx = labelCell.text.toLowerCase().indexOf(lab);
        const after = labelCell.text.slice(idx + lab.length).trim();
        raw = after || row.cells[ci + 1]?.text || null;
      } else if (rule.value_position === "left") {
        raw = row.cells[ci - 1]?.text || null;
      } else {
        const other = page.rows[rule.value_position === "below" ? ri + 1 : ri - 1];
        if (other) {
          const lb = labelCell.normalized_bbox ?? labelCell.bbox;
          const aligned = other.cells.find((c) => {
            const b = c.normalized_bbox ?? c.bbox;
            return b.x0 < lb.x1 && b.x1 > lb.x0;
          });
          raw = aligned?.text ?? (other.cells.length === 1 ? other.cells[0].text : null);
        }
      }
      if (raw) {
        if (rule.value_pattern) {
          try {
            const m = new RegExp(rule.value_pattern).exec(raw);
            if (m) raw = m[1] ?? m[0];
          } catch {
            /* invalid regex while typing */
          }
        }
        return raw.trim();
      }
    }
  }
  return null;
}

// --- canvas overlay -------------------------------------------------------

export interface HeaderOverlayProps {
  headerFields: Record<string, DraftHeaderFieldRule>;
  /** The field key currently being picked (renders clickable cells), or null. */
  pickField: string | null;
  onPickCell: (cell: LayoutCell) => void;
}

/** In pick mode, every cell is clickable so the user selects a LABEL cell. */
export function HeaderOverlay({ headerFields, pickField, onPickCell }: HeaderOverlayProps) {
  const g = useCanvasGeometry();
  const picking = pickField !== null;
  const labels = Object.values(headerFields)
    .map((h) => (h.label || "").toLowerCase().trim())
    .filter(Boolean);

  return (
    <div className={cn("absolute inset-0", !picking && "pointer-events-none")}>
      {g.page.rows.flatMap((row) =>
        row.cells.map((cell) => {
          const b = cell.normalized_bbox ?? cell.bbox;
          if (!b) return null;
          const left = g.x(b.x0);
          const top = g.y(b.y0);
          const width = g.x(b.x1 - b.x0);
          const height = Math.max(g.y(b.y1 - b.y0), 6);
          const isLabel = labels.some((l) => cell.text.toLowerCase().includes(l));
          return (
            <div key={cell.cell_id}>
              {isLabel && (
                <div
                  className="pointer-events-none absolute rounded-[1px] ring-1 ring-primary/30"
                  style={{ left, top, width, height, background: "#8b5cf622" }}
                />
              )}
              {picking && (
                <div
                  className="absolute cursor-pointer hover:bg-primary/20 hover:ring-1 hover:ring-primary"
                  style={{ left, top, width, height }}
                  title={cell.text}
                  onClick={() => onPickCell(cell)}
                />
              )}
            </div>
          );
        }),
      )}
    </div>
  );
}

// --- sidebar panel --------------------------------------------------------

export interface HeaderEditorPanelProps {
  headerFields: Record<string, DraftHeaderFieldRule>;
  onChange: (name: string, rule: DraftHeaderFieldRule | null) => void;
  pickField: string | null;
  onSetPickField: (key: string | null) => void;
  pages: LayoutPage[];
}

export function HeaderEditorPanel({
  headerFields,
  onChange,
  pickField,
  onSetPickField,
  pages,
}: HeaderEditorPanelProps) {
  return (
    <div className="space-y-4">
      <p className="text-sm text-muted-foreground">
        Capture the document-level fields. Click <span className="font-medium">Pick label</span> then
        click the label on the page; the value is read from the position you choose.
      </p>

      {HEADER_FIELDS.map(({ key, label, isDate }) => {
        const rule = headerFields[key];
        const sample = rule ? resolveHeaderSample(pages, rule) : null;
        const picking = pickField === key;
        return (
          <div key={key} className="space-y-2 rounded-md border p-2">
            <div className="flex items-center justify-between gap-2">
              <span className="text-sm font-medium">{label}</span>
              <div className="flex items-center gap-1">
                <Button
                  variant={picking ? "default" : "outline"}
                  size="sm"
                  onClick={() => onSetPickField(picking ? null : key)}
                >
                  <MousePointerClick className="mr-1 h-3.5 w-3.5" />
                  {picking ? "Click label…" : "Pick label"}
                </Button>
                {rule && (
                  <Button
                    variant="ghost"
                    size="icon-sm"
                    className="text-muted-foreground hover:text-destructive"
                    onClick={() => onChange(key, null)}
                  >
                    <Trash2 className="h-4 w-4" />
                  </Button>
                )}
              </div>
            </div>

            {rule && (
              <div className="space-y-2">
                <div className="flex items-center gap-2">
                  <Input
                    value={rule.label}
                    onChange={(e) => onChange(key, { ...rule, label: e.target.value })}
                    className="h-8 flex-1"
                    placeholder="Label text"
                  />
                  <Select
                    value={rule.value_position}
                    onValueChange={(v) => v && onChange(key, { ...rule, value_position: v })}
                  >
                    <SelectTrigger className="h-8 w-24">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      {POSITIONS.map((p) => (
                        <SelectItem key={p} value={p}>
                          {p}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
                {isDate && (
                  <Input
                    value={rule.date_format ?? ""}
                    onChange={(e) => onChange(key, { ...rule, date_format: e.target.value || null })}
                    className="h-8 font-mono text-xs"
                    placeholder="date format e.g. %d-%b-%Y"
                  />
                )}
                <div className="flex items-center justify-between text-xs">
                  <span className="text-muted-foreground">Sample:</span>
                  <span className={cn("font-mono", sample ? "text-foreground" : "text-destructive")}>
                    {sample ?? "not found — check label/position"}
                  </span>
                </div>
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}
