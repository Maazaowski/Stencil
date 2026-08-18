"use client";

import { AlertTriangle, MousePointerClick } from "lucide-react";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import { TagInput } from "@/components/form/tag-input";
import { useCanvasGeometry } from "./layout-canvas";
import { classifyRegionRows } from "./model-logic";
import type { DraftRegionRule, DraftRowClassifier, LayoutRow } from "@/hooks/use-builder";

export type PickMode = "none" | "start" | "end";

export interface RegionOverlayProps {
  region: DraftRegionRule;
  pickMode: PickMode;
  onPick: (row: LayoutRow) => void;
}

/** Canvas overlay: tint the matched line-item region and, in pick mode, let the
 * user click a row to capture it as a start/end anchor. */
export function RegionOverlay({ region, pickMode, onPick }: RegionOverlayProps) {
  const g = useCanvasGeometry();
  const classes = classifyRegionRows(g.page.rows, region);
  const picking = pickMode !== "none";

  return (
    <div className={cn("absolute inset-0", !picking && "pointer-events-none")}>
      {g.page.rows.map((row) => {
        const b = row.normalized_bbox;
        if (!b) return null;
        const klass = classes.get(row.row_id) ?? "out";
        const top = g.y(b.y0);
        const height = Math.max(g.y(b.y1 - b.y0), 3);

        let fill = "";
        let leftBorder = "";
        let label = "";
        let labelColor = "";
        if (klass === "start") {
          fill = "rgba(16,185,129,0.16)";
          leftBorder = "#059669";
          label = "START";
          labelColor = "#059669";
        } else if (klass === "end") {
          fill = "rgba(244,63,94,0.16)";
          leftBorder = "#e11d48";
          label = "END";
          labelColor = "#e11d48";
        } else if (klass === "in") {
          fill = "rgba(16,185,129,0.10)";
        }

        return (
          <div key={`rgn-${row.row_id}`}>
            {(fill || leftBorder) && (
              <div
                className="pointer-events-none absolute left-0 right-0"
                style={{
                  top,
                  height,
                  background: fill || undefined,
                  borderLeft: leftBorder ? `3px solid ${leftBorder}` : undefined,
                }}
              />
            )}
            {label && (
              <div
                className="pointer-events-none absolute rounded px-1 text-[10px] font-bold"
                style={{ left: 4, top: top + 1, color: labelColor }}
              >
                {label}
              </div>
            )}
            {picking && (
              <div
                className="absolute left-0 right-0 cursor-pointer hover:bg-primary/15 hover:ring-1 hover:ring-primary"
                style={{ top, height }}
                title={row.text}
                onClick={() => onPick(row)}
              />
            )}
          </div>
        );
      })}
    </div>
  );
}

export interface RegionEditorPanelProps {
  region: DraftRegionRule;
  onChange: (patch: Partial<DraftRegionRule>) => void;
  pickMode: PickMode;
  onSetPickMode: (mode: PickMode) => void;
  /** Used only to warn when an anchor is also a row role (see below). */
  classifiers?: DraftRowClassifier[];
}

/** Does a start anchor double as a row-role pattern?
 *
 * The anchor row is consumed as a pure delimiter by default, so reusing one text
 * for both silently yields zero groups — the single most confusing failure in the
 * builder. Detect it and point at the fix. */
function anchorAlsoClassifies(region: DraftRegionRule, classifiers: DraftRowClassifier[]) {
  if (region.include_anchor_row) return null;
  for (const anchor of region.start_anchors) {
    const a = anchor.trim().toLowerCase();
    if (!a) continue;
    for (const c of classifiers) {
      const t = (c.where.row_text ?? "").trim().toLowerCase();
      if (t && (t.includes(a) || a.includes(t))) return { anchor, role: c.role };
    }
  }
  return null;
}

/** Sidebar controls for the line-item region: start/end anchors (typed or
 * picked from the page) and the end scope. */
export function RegionEditorPanel({
  region,
  onChange,
  pickMode,
  onSetPickMode,
  classifiers = [],
}: RegionEditorPanelProps) {
  const clash = anchorAlsoClassifies(region, classifiers);
  return (
    <div className="space-y-4">
      <p className="text-sm text-muted-foreground">
        Mark where line items start and end. Type the text, or pick a row on the page.
      </p>

      <div className="space-y-2">
        <div className="flex items-center justify-between">
          <span className="text-sm font-medium">Start anchors</span>
          <Button
            variant={pickMode === "start" ? "default" : "outline"}
            size="sm"
            onClick={() => onSetPickMode(pickMode === "start" ? "none" : "start")}
          >
            <MousePointerClick className="mr-2 h-4 w-4" />
            {pickMode === "start" ? "Click a row…" : "Pick start row"}
          </Button>
        </div>
        <TagInput
          value={region.start_anchors}
          onChange={(v) => onChange({ start_anchors: v })}
          placeholder="e.g. SERVICE LEVEL ACTIVITY"
        />
      </div>

      <div className="space-y-2">
        <div className="flex items-center justify-between">
          <span className="text-sm font-medium">End anchors</span>
          <Button
            variant={pickMode === "end" ? "default" : "outline"}
            size="sm"
            onClick={() => onSetPickMode(pickMode === "end" ? "none" : "end")}
          >
            <MousePointerClick className="mr-2 h-4 w-4" />
            {pickMode === "end" ? "Click a row…" : "Pick end row"}
          </Button>
        </div>
        <TagInput
          value={region.end_anchors}
          onChange={(v) => onChange({ end_anchors: v })}
          placeholder="e.g. Total Current Charges"
        />
      </div>

      <div className="space-y-1.5">
        <span className="text-sm font-medium">End scope</span>
        <div className="flex gap-1">
          {(["document", "page"] as const).map((scope) => (
            <Button
              key={scope}
              variant={region.end_scope === scope ? "default" : "outline"}
              size="sm"
              className="flex-1 capitalize"
              onClick={() => onChange({ end_scope: scope })}
            >
              {scope}
            </Button>
          ))}
        </div>
        <p className="text-xs text-muted-foreground">
          {region.end_scope === "page"
            ? "End anchor closes the section per page; it resumes on the next page."
            : "End anchor closes the section for the rest of the document."}
        </p>
      </div>

      <div className="space-y-2 rounded-md border p-3">
        <div className="flex items-center justify-between gap-3">
          <span className="text-sm font-medium">Start anchor row is also data</span>
          <Switch
            checked={!!region.include_anchor_row}
            onCheckedChange={(checked) => onChange({ include_anchor_row: checked })}
          />
        </div>
        <p className="text-xs text-muted-foreground">
          By default the start-anchor row is a delimiter and is dropped. Turn this on when the same
          text also marks each item (e.g. a per-block “Charges for” header).
        </p>
        {clash && (
          <p className="flex items-start gap-1.5 text-xs font-medium text-warning">
            <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
            <span>
              “{clash.anchor}” is both a start anchor and the <code>{clash.role}</code> row rule, so
              those rows are dropped and you will get no items. Turn the switch above on.
            </span>
          </p>
        )}
      </div>
    </div>
  );
}
