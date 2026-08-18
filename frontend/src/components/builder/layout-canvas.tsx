"use client";

import { createContext, useContext } from "react";
import { cn } from "@/lib/utils";
import type { LayoutPage } from "@/hooks/use-builder";

const BASE_WIDTH = 960; // px at zoom = 1

/** Geometry of the current page render, shared with editor overlays so they can
 * map between normalized (0-1000) model coordinates and on-screen pixels. */
export interface CanvasGeometry {
  page: LayoutPage;
  widthPx: number;
  heightPx: number;
  /** normalized x (0-1000) -> px */
  x: (n: number) => number;
  /** normalized y (0-1000) -> px */
  y: (n: number) => number;
  /** px (relative to canvas left) -> normalized x (0-1000) */
  normX: (px: number) => number;
  /** px (relative to canvas top) -> normalized y (0-1000) */
  normY: (px: number) => number;
}

const CanvasGeometryContext = createContext<CanvasGeometry | null>(null);

export function useCanvasGeometry(): CanvasGeometry {
  const ctx = useContext(CanvasGeometryContext);
  if (!ctx) throw new Error("useCanvasGeometry must be used within a LayoutCanvas");
  return ctx;
}

/** Subtle per-role tint for reconstructed rows so table structure is visible. */
function rowRoleClass(role: string): string {
  switch (role) {
    case "header":
      return "bg-primary/12";
    case "group_total":
    case "tax_total":
      return "bg-warning/12";
    default:
      return "bg-transparent";
  }
}

export interface LayoutCanvasProps {
  page: LayoutPage;
  zoom?: number;
  showRowOutlines?: boolean;
  /** Overlay drawn in the same coordinate space (column bands, region, handles).
   * Children can call useCanvasGeometry() to position themselves. */
  children?: React.ReactNode;
  className?: string;
}

/**
 * Reconstructs a PDF page from its coordinate-aware layout: each cell is an
 * absolutely-positioned text box placed by its normalized (0-1000) bbox. No
 * page image — these PDFs carry real text. Editor overlays layer on top via
 * `children`, sharing geometry through CanvasGeometryContext.
 */
export function LayoutCanvas({
  page,
  zoom = 1,
  showRowOutlines = true,
  children,
  className,
}: LayoutCanvasProps) {
  const w = page.size.width || 612;
  const h = page.size.height || 792;
  const widthPx = BASE_WIDTH * zoom;
  const heightPx = widthPx * (h / w);

  const x = (n: number) => (n / 1000) * widthPx;
  const y = (n: number) => (n / 1000) * heightPx;
  const normX = (px: number) => Math.max(0, Math.min(1000, (px / widthPx) * 1000));
  const normY = (px: number) => Math.max(0, Math.min(1000, (px / heightPx) * 1000));

  const geometry: CanvasGeometry = { page, widthPx, heightPx, x, y, normX, normY };

  return (
    <CanvasGeometryContext.Provider value={geometry}>
      <div
        className={cn(
          "relative shrink-0 select-none overflow-hidden rounded-md border bg-white shadow-sm",
          className,
        )}
        style={{ width: widthPx, height: heightPx }}
      >
        {showRowOutlines &&
          page.rows.map((row) => {
            const b = row.normalized_bbox;
            if (!b) return null;
            return (
              <div
                key={`row-${row.row_id}`}
                className={cn("pointer-events-none absolute rounded-[1px]", rowRoleClass(row.role))}
                style={{ left: x(b.x0), top: y(b.y0), width: x(b.x1 - b.x0), height: y(b.y1 - b.y0) }}
              />
            );
          })}

        {page.rows.flatMap((row) =>
          row.cells.map((cell) => {
            const b = cell.normalized_bbox ?? cell.bbox;
            if (!b) return null;
            const cellH = y(b.y1 - b.y0);
            const cellW = x(b.x1 - b.x0);
            // Size the reconstructed glyphs to fit BOTH the cell height and its
            // width. The bbox width comes from the PDF's own font metrics, which
            // are narrower than our render font — without the width cap, wide
            // text (esp. right-aligned amounts) overflowed and got clipped.
            const fontByWidth = cellW / Math.max(cell.text.length * 0.52, 1);
            const fontPx = Math.max(5, Math.min(cellH * 0.78, fontByWidth, 20));
            const isAmount = cell.role === "amount";
            return (
              <div
                key={cell.cell_id}
                className={cn(
                  "pointer-events-none absolute flex items-center overflow-hidden whitespace-nowrap leading-none text-muted-foreground",
                  isAmount ? "justify-end" : "justify-start",
                )}
                style={{
                  left: x(b.x0),
                  top: y(b.y0),
                  width: cellW,
                  height: cellH,
                  fontSize: fontPx,
                }}
                title={cell.text}
              >
                {cell.text}
              </div>
            );
          }),
        )}

        {/* Editor overlays (column bands, region highlight, drag handles) */}
        {children}
      </div>
    </CanvasGeometryContext.Provider>
  );
}
