"use client";

import { useRef, useState } from "react";
import { Columns2, Wand2, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useCanvasGeometry } from "./layout-canvas";
import type { DraftRegionRule } from "@/hooks/use-builder";

const round1 = (n: number) => Math.round(n * 10) / 10;

export interface ColumnSplitOverlayProps {
  region: DraftRegionRule;
  /** Best-effort divider hint for this page (normalized), when not yet split. */
  suggested?: number | null;
  onChange: (patch: Partial<DraftRegionRule>) => void;
}

/** Canvas overlay: the draggable column divider.
 *
 * Two-column ("newspaper") pages print independent left/right stacks of blocks.
 * Without a divider they merge into shared rows and the model can't tell the
 * columns apart; with one, each column is read as its own top-to-bottom stream.
 * A detected gutter shows as a dashed hint the user accepts or ignores. */
export function ColumnSplitOverlay({ region, suggested, onChange }: ColumnSplitOverlayProps) {
  const g = useCanvasGeometry();
  const ref = useRef<HTMLDivElement>(null);
  const [dragging, setDragging] = useState(false);

  const split = region.column_split_x?.[0] ?? null;
  const localNX = (clientX: number) => {
    const r = ref.current!.getBoundingClientRect();
    return g.normX(clientX - r.left);
  };

  function onPointerDown(e: React.PointerEvent) {
    if (split === null) return;
    if (!(e.target as HTMLElement).dataset.splitHandle) return;
    setDragging(true);
    try {
      ref.current!.setPointerCapture(e.pointerId);
    } catch {
      /* pointer may not be capturable (e.g. synthetic events) */
    }
    e.preventDefault();
  }

  function onPointerMove(e: React.PointerEvent) {
    if (!dragging) return;
    onChange({ column_split_x: [round1(localNX(e.clientX))] });
  }

  function onPointerUp() {
    setDragging(false);
  }

  return (
    <div
      ref={ref}
      className="absolute inset-0"
      style={{ pointerEvents: split === null ? "none" : "auto" }}
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={onPointerUp}
    >
      {/* Detected gutter: a hint only, until the user accepts it. */}
      {split === null && suggested != null && (
        <div
          className="pointer-events-none absolute top-0 bottom-0"
          style={{ left: g.x(suggested), borderLeft: "2px dashed rgba(99,102,241,0.55)" }}
        >
          <span className="absolute left-1 top-1 rounded bg-primary/12 px-1 text-[10px] font-semibold text-white">
            gutter?
          </span>
        </div>
      )}

      {split !== null && (
        <>
          <div
            className="absolute top-0 bottom-0"
            style={{ left: g.x(split), borderLeft: "2px solid #6366f1" }}
          />
          {/* Wide invisible grab strip so the thin line is easy to hit. */}
          <div
            data-split-handle="1"
            className="absolute top-0 bottom-0 cursor-ew-resize"
            style={{ left: g.x(split) - 5, width: 10 }}
            title="Drag to move the column divider"
          />
          <span
            className="pointer-events-none absolute top-1 rounded bg-primary/12 px-1 text-[10px] font-semibold text-white"
            style={{ left: g.x(split) + 4 }}
          >
            column split
          </span>
        </>
      )}
    </div>
  );
}

export interface ColumnSplitPanelProps {
  region: DraftRegionRule;
  suggested?: number | null;
  onChange: (patch: Partial<DraftRegionRule>) => void;
}

/** Sidebar control for the reading-column divider. */
export function ColumnSplitPanel({ region, suggested, onChange }: ColumnSplitPanelProps) {
  const split = region.column_split_x?.[0] ?? null;

  return (
    <div className="space-y-2 rounded-md border p-3">
      <div className="flex items-center justify-between">
        <span className="flex items-center gap-1.5 text-sm font-medium">
          <Columns2 className="h-4 w-4" />
          Reading columns
        </span>
        {split === null ? (
          <Button size="sm" variant="outline" onClick={() => onChange({ column_split_x: [suggested ?? 500] })}>
            <Wand2 className="mr-2 h-4 w-4" />
            {suggested != null ? "Use detected gutter" : "Add divider"}
          </Button>
        ) : (
          <Button size="sm" variant="ghost" onClick={() => onChange({ column_split_x: [] })}>
            <X className="mr-2 h-4 w-4" />
            Clear
          </Button>
        )}
      </div>
      <p className="text-xs text-muted-foreground">
        {split === null
          ? suggested != null
            ? "This page looks like two independent columns — a gutter is marked on the page. Add the divider to read each column separately."
            : "Only for pages that print two independent columns of blocks side by side. Most invoices need no divider."
          : `Each side of the divider is read as its own top-to-bottom column. Drag the line on the page to adjust (at ${split}).`}
      </p>
    </div>
  );
}
