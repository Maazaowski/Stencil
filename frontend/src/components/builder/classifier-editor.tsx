"use client";

import { MousePointerClick, Trash2, ArrowUp, ArrowDown, Tag } from "lucide-react";
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
import { classifyRowRole } from "./model-logic";
import type { DraftColumnDef, DraftRowClassifier, DraftRowMatch, LayoutRow } from "@/hooks/use-builder";

type MatchMode = "text" | "column" | "amount";

function matchModeOf(where: DraftRowMatch): MatchMode {
  if (where.has_amount_in_column) return "amount";
  if (where.column && where.pattern) return "column";
  return "text";
}

const ROLE_COLORS = [
  "#6366f1", "#0ea5e9", "#10b981", "#f59e0b",
  "#ec4899", "#8b5cf6", "#14b8a6", "#ef4444",
];

const COMMON_ROLES = ["item_start", "item_end", "description_part", "skip"];

function hashRole(s: string): number {
  let h = 0;
  for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) >>> 0;
  return h;
}

/** Stable colour per role name; "detail" (unmatched) has none, "skip" is grey. */
export function roleColor(role: string): string | null {
  if (role === "detail") return null;
  if (role === "skip") return "#94a3b8";
  return ROLE_COLORS[hashRole(role) % ROLE_COLORS.length];
}

export interface RoleOverlayProps {
  classifiers: DraftRowClassifier[];
  picking: boolean;
  onPick: (row: LayoutRow) => void;
}

/** Canvas overlay: colour each row by the role its classifiers assign, and (in
 * pick mode) let the user click a row to seed a new classifier from it. */
export function RoleOverlay({ classifiers, picking, onPick }: RoleOverlayProps) {
  const g = useCanvasGeometry();

  return (
    <div className={cn("absolute inset-0", !picking && "pointer-events-none")}>
      {g.page.rows.map((row) => {
        const b = row.normalized_bbox;
        if (!b) return null;
        const role = classifyRowRole(row, classifiers);
        const color = roleColor(role);
        const top = g.y(b.y0);
        const height = Math.max(g.y(b.y1 - b.y0), 3);
        return (
          <div key={`role-${row.row_id}`}>
            {color && (
              <>
                <div
                  className="pointer-events-none absolute left-0 right-0"
                  style={{ top, height, background: color + "1f", borderLeft: `3px solid ${color}` }}
                />
                <div
                  className="pointer-events-none absolute rounded px-1 text-[10px] font-semibold text-white"
                  style={{ right: 3, top: top + 1, background: color }}
                >
                  {role}
                </div>
              </>
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

export interface ClassifierEditorPanelProps {
  classifiers: DraftRowClassifier[];
  onChange: (classifiers: DraftRowClassifier[]) => void;
  picking: boolean;
  onTogglePick: () => void;
  pendingRole: string;
  onPendingRoleChange: (role: string) => void;
  columns: DraftColumnDef[];
}

/** Sidebar controls: choose a role, click rows to seed classifiers, then tune
 * the ordered rule list (first match wins). */
export function ClassifierEditorPanel({
  classifiers,
  onChange,
  picking,
  onTogglePick,
  pendingRole,
  onPendingRoleChange,
  columns,
}: ClassifierEditorPanelProps) {
  const update = (i: number, patch: Partial<DraftRowClassifier>) =>
    onChange(classifiers.map((c, idx) => (idx === i ? { ...c, ...patch } : c)));
  const setWhere = (i: number, where: DraftRowMatch) =>
    onChange(classifiers.map((c, idx) => (idx === i ? { ...c, where } : c)));
  const setMatchMode = (i: number, mode: MatchMode) => {
    const firstCol = columns[0]?.name ?? null;
    if (mode === "text") setWhere(i, { row_text: classifiers[i].where.row_text ?? "" });
    else if (mode === "column")
      setWhere(i, { column: classifiers[i].where.column ?? firstCol, pattern: classifiers[i].where.pattern ?? "" });
    else setWhere(i, { has_amount_in_column: classifiers[i].where.has_amount_in_column ?? firstCol });
  };
  const remove = (i: number) => onChange(classifiers.filter((_, idx) => idx !== i));
  const move = (i: number, dir: -1 | 1) => {
    const j = i + dir;
    if (j < 0 || j >= classifiers.length) return;
    const next = [...classifiers];
    [next[i], next[j]] = [next[j], next[i]];
    onChange(next);
  };

  return (
    <div className="space-y-4">
      <p className="text-sm text-muted-foreground">
        Give each kind of row a role. Pick the role, then click matching rows on the page.
      </p>

      <div className="space-y-2">
        <span className="text-sm font-medium">Role to assign</span>
        <Input
          value={pendingRole}
          onChange={(e) => onPendingRoleChange(e.target.value)}
          placeholder="e.g. item_start"
        />
        <div className="flex flex-wrap gap-1">
          {COMMON_ROLES.map((r) => (
            <button
              key={r}
              onClick={() => onPendingRoleChange(r)}
              className={cn(
                "rounded-sm border px-2 py-0.5 text-xs",
                pendingRole === r ? "border-primary bg-primary/10" : "hover:bg-muted",
              )}
            >
              {r}
            </button>
          ))}
        </div>
        <Button
          variant={picking ? "default" : "outline"}
          size="sm"
          className="w-full"
          onClick={onTogglePick}
        >
          <MousePointerClick className="mr-2 h-4 w-4" />
          {picking ? "Click rows…  (click to stop)" : "Assign by clicking rows"}
        </Button>
      </div>

      {classifiers.length === 0 ? (
        <div className="flex flex-col items-center gap-1 rounded-md border border-dashed py-6 text-center text-sm text-muted-foreground">
          <Tag className="h-5 w-5" />
          No role rules yet
        </div>
      ) : (
        <ul className="space-y-2">
          {classifiers.map((c, i) => {
            const color = roleColor(c.role);
            return (
              <li key={i} className="space-y-1.5 rounded-md border p-2">
                <div className="flex items-center gap-2">
                  <span
                    className="h-3 w-3 shrink-0 rounded-sm"
                    style={{ background: color ?? "transparent", border: color ? undefined : "1px solid var(--border)" }}
                  />
                  <Input
                    value={c.role}
                    onChange={(e) => update(i, { role: e.target.value })}
                    className="h-8 flex-1"
                  />
                  <Button variant="ghost" size="icon-sm" onClick={() => move(i, -1)} disabled={i === 0}>
                    <ArrowUp className="h-4 w-4" />
                  </Button>
                  <Button
                    variant="ghost"
                    size="icon-sm"
                    onClick={() => move(i, 1)}
                    disabled={i === classifiers.length - 1}
                  >
                    <ArrowDown className="h-4 w-4" />
                  </Button>
                  <Button
                    variant="ghost"
                    size="icon-sm"
                    className="text-muted-foreground hover:text-destructive"
                    onClick={() => remove(i)}
                  >
                    <Trash2 className="h-4 w-4" />
                  </Button>
                </div>
                {(() => {
                  const mode = matchModeOf(c.where);
                  const colSelect = (value: string | null | undefined, onCol: (v: string) => void) => (
                    <Select value={value ?? ""} onValueChange={(v) => v && onCol(v)}>
                      <SelectTrigger className="h-8 w-32">
                        <SelectValue placeholder="column" />
                      </SelectTrigger>
                      <SelectContent>
                        {columns.map((col) => (
                          <SelectItem key={col.name} value={col.name}>
                            {col.name}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  );
                  return (
                    <div className="space-y-1.5">
                      <Select value={mode} onValueChange={(v) => v && setMatchMode(i, v as MatchMode)}>
                        <SelectTrigger className="h-8">
                          <SelectValue />
                        </SelectTrigger>
                        <SelectContent>
                          <SelectItem value="text">match on row text</SelectItem>
                          <SelectItem value="column">column matches regex</SelectItem>
                          <SelectItem value="amount">has amount in column</SelectItem>
                        </SelectContent>
                      </Select>
                      {mode === "text" && (
                        <Input
                          value={c.where.row_text ?? ""}
                          onChange={(e) => setWhere(i, { row_text: e.target.value })}
                          className="h-8 font-mono text-xs"
                          placeholder="^\\d{6,}$"
                        />
                      )}
                      {mode === "column" && (
                        <div className="flex items-center gap-2">
                          {colSelect(c.where.column, (v) => setWhere(i, { ...c.where, column: v }))}
                          <Input
                            value={c.where.pattern ?? ""}
                            onChange={(e) => setWhere(i, { ...c.where, pattern: e.target.value })}
                            className="h-8 flex-1 font-mono text-xs"
                            placeholder="^\\d{6,}$"
                          />
                        </div>
                      )}
                      {mode === "amount" &&
                        colSelect(c.where.has_amount_in_column, (v) =>
                          setWhere(i, { has_amount_in_column: v }),
                        )}
                    </div>
                  );
                })()}
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
