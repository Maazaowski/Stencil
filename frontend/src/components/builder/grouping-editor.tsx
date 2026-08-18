"use client";

import { Layers } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { useCanvasGeometry } from "./layout-canvas";
import { classifyRowRole, computeGroups, inRegionRowIds } from "./model-logic";
import type { DraftGroupingRule, DraftRegionRule, DraftRowClassifier } from "@/hooks/use-builder";

const GROUP_COLORS = ["#6366f1", "#10b981"];

export interface GroupsOverlayProps {
  region: DraftRegionRule;
  classifiers: DraftRowClassifier[];
  grouping: DraftGroupingRule;
}

/** Canvas overlay: draw a band per computed line-item group so the user sees
 * item boundaries form as they tune the grouping. */
export function GroupsOverlay({ region, classifiers, grouping }: GroupsOverlayProps) {
  const g = useCanvasGeometry();
  const inReg = inRegionRowIds(g.page.rows, region);
  const domain = inReg.size > 0 ? g.page.rows.filter((r) => inReg.has(r.row_id)) : g.page.rows;
  const roleOf = (row: (typeof domain)[number]) => classifyRowRole(row, classifiers);
  const groups = computeGroups(domain, roleOf, grouping);
  const byId = new Map(g.page.rows.map((r) => [r.row_id, r]));

  return (
    <div className="pointer-events-none absolute inset-0">
      {groups.map((grp, i) => {
        const boxes = grp.rowIds
          .map((id) => byId.get(id)?.normalized_bbox)
          .filter((b): b is NonNullable<typeof b> => !!b);
        if (boxes.length === 0) return null;
        const y0 = Math.min(...boxes.map((b) => b.y0));
        const y1 = Math.max(...boxes.map((b) => b.y1));
        const color = GROUP_COLORS[i % GROUP_COLORS.length];
        const top = g.y(y0);
        const height = Math.max(g.y(y1 - y0), 4);
        return (
          <div key={`grp-${i}`}>
            <div
              className="absolute left-0 right-0"
              style={{
                top,
                height,
                background: color + "14",
                borderLeft: `3px solid ${color}`,
                borderTop: i > 0 ? `1px dashed ${color}66` : undefined,
              }}
            />
            <div
              className="absolute rounded px-1 text-[10px] font-semibold text-white"
              style={{ left: 4, top: top + 1, background: color }}
            >
              item {i + 1}
            </div>
          </div>
        );
      })}
    </div>
  );
}

const ANY = "__any__";

function RoleSelect({
  value,
  onChange,
  roles,
  placeholder,
  allowAny,
}: {
  value: string | null | undefined;
  onChange: (v: string | null) => void;
  roles: string[];
  placeholder: string;
  allowAny?: boolean;
}) {
  return (
    <Select
      value={value ?? ""}
      onValueChange={(v) => onChange(v === ANY ? null : v)}
    >
      <SelectTrigger className="h-8">
        <SelectValue placeholder={placeholder} />
      </SelectTrigger>
      <SelectContent>
        {allowAny && <SelectItem value={ANY}>Any row</SelectItem>}
        {roles.map((r) => (
          <SelectItem key={r} value={r}>
            {r}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}

export interface GroupingEditorPanelProps {
  grouping: DraftGroupingRule;
  onChange: (patch: Partial<DraftGroupingRule>) => void;
  roles: string[];
  itemCount: number;
}

const MODES: { key: string; label: string; hint: string }[] = [
  { key: "single_row", label: "Single row", hint: "Each matching row is one line item." },
  { key: "span", label: "Span", hint: "A group runs from a start row to a closing row." },
  { key: "role_transition", label: "By start", hint: "A new group begins at each start row." },
];

/** Sidebar controls for how classified rows collapse into line items. */
export function GroupingEditorPanel({ grouping, onChange, roles, itemCount }: GroupingEditorPanelProps) {
  const noRoles = roles.length === 0;
  const mode = MODES.find((m) => m.key === grouping.mode) ?? MODES[0];

  return (
    <div className="space-y-4">
      <p className="text-sm text-muted-foreground">
        Turn the classified rows into line items.
      </p>

      {noRoles && (
        <div className="rounded-md border border-dashed p-3 text-xs text-muted-foreground">
          Tip: define row roles on the <span className="font-medium">Rows</span> tab first — grouping
          uses them.
        </div>
      )}

      <div className="space-y-1.5">
        <span className="text-sm font-medium">Mode</span>
        <div className="grid grid-cols-3 gap-1">
          {MODES.map((m) => (
            <Button
              key={m.key}
              variant={grouping.mode === m.key ? "default" : "outline"}
              size="sm"
              onClick={() => onChange({ mode: m.key })}
            >
              {m.label}
            </Button>
          ))}
        </div>
        <p className="text-xs text-muted-foreground">{mode.hint}</p>
      </div>

      {grouping.mode === "single_row" && (
        <div className="space-y-1.5">
          <span className="text-sm font-medium">Item row role</span>
          <RoleSelect
            value={grouping.item_role}
            onChange={(v) => onChange({ item_role: v })}
            roles={roles}
            placeholder="Any row"
            allowAny
          />
        </div>
      )}

      {grouping.mode === "span" && (
        <div className="space-y-3">
          <div className="space-y-1.5">
            <span className="text-sm font-medium">Start role</span>
            <RoleSelect
              value={grouping.start_role}
              onChange={(v) => onChange({ start_role: v })}
              roles={roles}
              placeholder="Select role"
            />
          </div>
          <div className="space-y-1.5">
            <span className="text-sm font-medium">End role</span>
            <RoleSelect
              value={grouping.end_role}
              onChange={(v) => onChange({ end_role: v })}
              roles={roles}
              placeholder="Select role"
            />
          </div>
          <div className="flex items-center gap-2">
            <Switch
              id="include-end"
              checked={grouping.include_end_row}
              onCheckedChange={(c) => onChange({ include_end_row: c })}
            />
            <Label htmlFor="include-end" className="text-sm">
              Include the end row in the item
            </Label>
          </div>
        </div>
      )}

      {grouping.mode === "role_transition" && (
        <div className="space-y-1.5">
          <span className="text-sm font-medium">Start role</span>
          <RoleSelect
            value={grouping.start_role}
            onChange={(v) => onChange({ start_role: v })}
            roles={roles}
            placeholder="Select role"
          />
        </div>
      )}

      <div className="space-y-1.5">
        <span className="text-sm font-medium">Emit</span>
        <div className="grid grid-cols-2 gap-1">
          <Button
            variant={grouping.emit === "one_item_per_group" ? "default" : "outline"}
            size="sm"
            onClick={() => onChange({ emit: "one_item_per_group" })}
          >
            Per group
          </Button>
          <Button
            variant={grouping.emit === "one_item_per_role_row" ? "default" : "outline"}
            size="sm"
            onClick={() => onChange({ emit: "one_item_per_role_row" })}
          >
            Per role row
          </Button>
        </div>
      </div>

      {grouping.emit === "one_item_per_role_row" && (
        <div className="space-y-1.5">
          <span className="text-sm font-medium">Emit one item per</span>
          <RoleSelect
            value={grouping.emit_role}
            onChange={(v) => onChange({ emit_role: v })}
            roles={roles}
            placeholder="Select role"
          />
        </div>
      )}

      <div className="flex items-center gap-2 rounded-md border bg-muted/40 p-2 text-sm">
        <Layers className="h-4 w-4 text-muted-foreground" />
        <span>
          <span className="font-semibold tabular-nums">{itemCount}</span> line item
          {itemCount === 1 ? "" : "s"} on this page
        </span>
      </div>
    </div>
  );
}
