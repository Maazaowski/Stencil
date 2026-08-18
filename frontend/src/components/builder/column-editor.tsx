"use client";

import { useRef, useState } from "react";
import { Plus, Trash2, Columns3 } from "lucide-react";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { useCanvasGeometry } from "./layout-canvas";
import type { DraftColumnDef } from "@/hooks/use-builder";

const COLORS = [
  "#6366f1", "#10b981", "#f59e0b", "#0ea5e9",
  "#f43f5e", "#8b5cf6", "#14b8a6", "#f97316",
];

export function columnColor(i: number): string {
  return COLORS[i % COLORS.length];
}

const round1 = (n: number) => Math.round(n * 10) / 10;

type DragState =
  | { type: "draw"; x0: number; x1: number }
  | { type: "resize"; index: number; edge: "x0" | "x1" }
  | null;

export interface ColumnBandsOverlayProps {
  columns: DraftColumnDef[];
  onChange: (columns: DraftColumnDef[]) => void;
  drawMode: boolean;
  selectedIndex: number | null;
  onSelect: (index: number | null) => void;
}

/** Canvas overlay: draw column bands by dragging, resize via edge handles, and
 * tint the cells each band captures. Writes normalized 0-1000 x-bands. */
export function ColumnBandsOverlay({
  columns,
  onChange,
  drawMode,
  selectedIndex,
  onSelect,
}: ColumnBandsOverlayProps) {
  const g = useCanvasGeometry();
  const ref = useRef<HTMLDivElement>(null);
  const [drag, setDrag] = useState<DragState>(null);

  const localNX = (clientX: number) => {
    const r = ref.current!.getBoundingClientRect();
    return g.normX(clientX - r.left);
  };

  const capture = (pointerId: number) => {
    try {
      ref.current!.setPointerCapture(pointerId);
    } catch {
      /* pointer may not be capturable (e.g. synthetic events) */
    }
  };

  const bandFor = (centerNx: number) =>
    columns.findIndex((c) => centerNx >= Math.min(c.x0, c.x1) && centerNx <= Math.max(c.x0, c.x1));

  function onPointerDown(e: React.PointerEvent) {
    const handle = (e.target as HTMLElement).dataset.handle;
    if (handle) {
      const [idx, edge] = handle.split(":");
      setDrag({ type: "resize", index: Number(idx), edge: edge as "x0" | "x1" });
      onSelect(Number(idx));
      capture(e.pointerId);
      e.preventDefault();
      return;
    }
    if (drawMode) {
      const nx = localNX(e.clientX);
      setDrag({ type: "draw", x0: nx, x1: nx });
      capture(e.pointerId);
      e.preventDefault();
    }
  }

  function onPointerMove(e: React.PointerEvent) {
    if (!drag) return;
    const nx = localNX(e.clientX);
    if (drag.type === "draw") {
      setDrag({ ...drag, x1: nx });
    } else {
      onChange(columns.map((c, i) => (i === drag.index ? { ...c, [drag.edge]: round1(nx) } : c)));
    }
  }

  function onPointerUp(e: React.PointerEvent) {
    if (!drag) return;
    if (drag.type === "draw") {
      const x0 = Math.min(drag.x0, drag.x1);
      const x1 = Math.max(drag.x0, drag.x1);
      if (x1 - x0 > 8) {
        onChange([...columns, { name: `col${columns.length + 1}`, x0: round1(x0), x1: round1(x1) }]);
        onSelect(columns.length);
      }
    } else {
      // Normalize so x0 < x1 after a handle crosses over.
      const c = columns[drag.index];
      if (c && c.x0 > c.x1) {
        onChange(columns.map((cc, i) => (i === drag.index ? { ...cc, x0: cc.x1, x1: cc.x0 } : cc)));
      }
    }
    setDrag(null);
    try {
      ref.current!.releasePointerCapture(e.pointerId);
    } catch {
      /* capture may already be gone */
    }
  }

  return (
    <div
      ref={ref}
      className={cn("absolute inset-0", drawMode && "cursor-crosshair")}
      style={{ touchAction: "none" }}
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={onPointerUp}
    >
      {/* Cells each band captures, tinted in the band colour. */}
      {g.page.rows.flatMap((row) =>
        row.cells.map((cell) => {
          const b = cell.normalized_bbox ?? cell.bbox;
          if (!b) return null;
          const ci = bandFor((b.x0 + b.x1) / 2);
          if (ci < 0) return null;
          return (
            <div
              key={`ac-${cell.cell_id}`}
              className="pointer-events-none absolute rounded-[1px]"
              style={{
                left: g.x(b.x0),
                top: g.y(b.y0),
                width: g.x(b.x1 - b.x0),
                height: g.y(b.y1 - b.y0),
                background: columnColor(ci) + "26",
              }}
            />
          );
        }),
      )}

      {/* Column bands + edge handles. */}
      {columns.map((c, i) => {
        const x0 = Math.min(c.x0, c.x1);
        const x1 = Math.max(c.x0, c.x1);
        const color = columnColor(i);
        const selected = selectedIndex === i;
        return (
          <div key={`band-${i}`}>
            <div
              className="pointer-events-none absolute bottom-0 top-0"
              style={{
                left: g.x(x0),
                width: g.x(x1 - x0),
                background: color + (selected ? "24" : "12"),
                borderLeft: `2px solid ${color}`,
                borderRight: `2px solid ${color}`,
              }}
            />
            <div
              className="pointer-events-none absolute -translate-x-1/2 whitespace-nowrap rounded px-1 text-[10px] font-medium text-white shadow-sm"
              style={{ left: g.x((x0 + x1) / 2), top: 2, background: color }}
            >
              {c.name}
            </div>
            <div
              data-handle={`${i}:x0`}
              className="absolute bottom-0 top-0 w-2 -translate-x-1/2 cursor-ew-resize"
              style={{ left: g.x(x0) }}
            />
            <div
              data-handle={`${i}:x1`}
              className="absolute bottom-0 top-0 w-2 -translate-x-1/2 cursor-ew-resize"
              style={{ left: g.x(x1) }}
            />
          </div>
        );
      })}

      {/* Live preview of the band being drawn. */}
      {drag?.type === "draw" &&
        (() => {
          const x0 = Math.min(drag.x0, drag.x1);
          const x1 = Math.max(drag.x0, drag.x1);
          return (
            <div
              className="pointer-events-none absolute bottom-0 top-0 border-x-2 border-dashed border-primary/30 bg-primary/12"
              style={{ left: g.x(x0), width: g.x(x1 - x0) }}
            />
          );
        })()}
    </div>
  );
}

export interface ColumnEditorPanelProps {
  columns: DraftColumnDef[];
  onChange: (columns: DraftColumnDef[]) => void;
  drawMode: boolean;
  onToggleDraw: () => void;
  selectedIndex: number | null;
  onSelect: (index: number | null) => void;
}

/** Sidebar controls for the column bands: draw toggle + editable list. */
export function ColumnEditorPanel({
  columns,
  onChange,
  drawMode,
  onToggleDraw,
  selectedIndex,
  onSelect,
}: ColumnEditorPanelProps) {
  const rename = (i: number, name: string) =>
    onChange(columns.map((c, idx) => (idx === i ? { ...c, name } : c)));
  const remove = (i: number) => {
    onChange(columns.filter((_, idx) => idx !== i));
    if (selectedIndex === i) onSelect(null);
  };

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <p className="text-sm text-muted-foreground">
          Drag across the page to draw a column, then name it.
        </p>
      </div>
      <Button
        variant={drawMode ? "default" : "outline"}
        size="sm"
        className="w-full"
        onClick={onToggleDraw}
      >
        <Plus className="mr-2 h-4 w-4" />
        {drawMode ? "Drawing… click to stop" : "Draw column"}
      </Button>

      {columns.length === 0 ? (
        <div className="flex flex-col items-center gap-1 rounded-md border border-dashed py-6 text-center text-sm text-muted-foreground">
          <Columns3 className="h-5 w-5" />
          No columns yet
        </div>
      ) : (
        <ul className="space-y-2">
          {columns.map((c, i) => (
            <li
              key={i}
              className={cn(
                "flex items-center gap-2 rounded-md border p-2",
                selectedIndex === i && "ring-2 ring-primary",
              )}
              onClick={() => onSelect(i)}
            >
              <span
                className="h-4 w-4 shrink-0 rounded-sm"
                style={{ background: columnColor(i) }}
              />
              <Input
                value={c.name}
                onChange={(e) => rename(i, e.target.value)}
                className="h-8"
              />
              <span className="w-24 shrink-0 text-right text-xs tabular-nums text-muted-foreground">
                {Math.round(Math.min(c.x0, c.x1))}–{Math.round(Math.max(c.x0, c.x1))}
              </span>
              <Button
                variant="ghost"
                size="icon-sm"
                className="text-muted-foreground hover:text-destructive"
                onClick={(e) => {
                  e.stopPropagation();
                  remove(i);
                }}
              >
                <Trash2 className="h-4 w-4" />
              </Button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
