// Pure, framework-free helpers shared by the builder overlays/panels. These
// mirror how the backend interpreter reasons over rows so the on-canvas preview
// matches what the saved model will actually do.

import type {
  DraftGroupingRule,
  DraftRegionRule,
  DraftRowClassifier,
  LayoutRow,
} from "@/hooks/use-builder";

export type RegionRowClass = "start" | "end" | "in" | "out";

export function matchesAny(text: string, anchors: string[]): boolean {
  const t = text.toLowerCase();
  return anchors.some((a) => a && t.includes(a.toLowerCase()));
}

/** Replay the interpreter's region state machine over the current page. */
export function classifyRegionRows(
  rows: LayoutRow[],
  region: DraftRegionRule,
): Map<string, RegionRowClass> {
  const out = new Map<string, RegionRowClass>();
  let inRegion = false;
  for (const row of rows) {
    if (matchesAny(row.text, region.start_anchors)) {
      out.set(row.row_id, "start");
      inRegion = true;
      continue;
    }
    if (matchesAny(row.text, region.end_anchors)) {
      out.set(row.row_id, "end");
      inRegion = false;
      continue;
    }
    out.set(row.row_id, inRegion ? "in" : "out");
  }
  return out;
}

/** Row ids that fall inside the line-item region (the classifiers' domain). */
export function inRegionRowIds(rows: LayoutRow[], region: DraftRegionRule): Set<string> {
  const classes = classifyRegionRows(rows, region);
  const ids = new Set<string>();
  for (const [id, k] of classes) if (k === "in") ids.add(id);
  return ids;
}

function testRegex(pattern: string, text: string): boolean {
  try {
    return new RegExp(pattern, "i").test(text);
  } catch {
    return false;
  }
}

const AMOUNT_TOKEN_RE = /\d[\d,]*\.\d{2}/;

/** First classifier whose predicate matches wins; unmatched rows are "detail".
 * This is the live-coloring approximation; the backend preview is authoritative.
 * Column/amount predicates are matched against the whole row text here (we don't
 * have the column bands in this pure helper). */
export function classifyRowRole(row: LayoutRow, classifiers: DraftRowClassifier[]): string {
  for (const rule of classifiers) {
    const { row_text, pattern, has_amount_in_column } = rule.where;
    if (!row_text && !pattern && !has_amount_in_column) continue;
    let ok = true;
    if (row_text) ok = ok && testRegex(row_text, row.text);
    if (ok && pattern) ok = ok && testRegex(pattern, row.text);
    if (ok && has_amount_in_column) ok = ok && AMOUNT_TOKEN_RE.test(row.text);
    if (ok) return rule.role;
  }
  return "detail";
}

/** Distinct role names declared across the classifiers (for grouping selects). */
export function distinctRoles(classifiers: DraftRowClassifier[]): string[] {
  const seen = new Set<string>();
  for (const c of classifiers) if (c.role && c.role !== "skip") seen.add(c.role);
  return [...seen];
}

export interface RowGroup {
  rowIds: string[];
}

/** Collapse the domain rows into groups, mirroring the interpreter's grouping
 * modes. `roleOf` returns each row's classified role. `skip` rows are dropped. */
export function computeGroups(
  domain: LayoutRow[],
  roleOf: (row: LayoutRow) => string,
  g: DraftGroupingRule,
): RowGroup[] {
  const groups: RowGroup[] = [];

  if (g.mode === "single_row") {
    for (const row of domain) {
      const role = roleOf(row);
      if (role === "skip") continue;
      if (!g.item_role || role === g.item_role) groups.push({ rowIds: [row.row_id] });
    }
    return groups;
  }

  if (g.mode === "span") {
    let cur: RowGroup | null = null;
    for (const row of domain) {
      const role = roleOf(row);
      if (role === "skip") continue;
      if (g.start_role && role === g.start_role) {
        if (cur) groups.push(cur); // a new start before closing restarts the group
        cur = { rowIds: [row.row_id] };
        continue;
      }
      if (cur) {
        if (g.end_role && role === g.end_role) {
          if (g.include_end_row) cur.rowIds.push(row.row_id);
          groups.push(cur);
          cur = null;
        } else {
          cur.rowIds.push(row.row_id);
        }
      }
    }
    if (cur) groups.push(cur);
    return groups;
  }

  if (g.mode === "role_transition") {
    let cur: RowGroup | null = null;
    for (const row of domain) {
      const role = roleOf(row);
      if (role === "skip") continue;
      if (g.start_role && role === g.start_role) {
        if (cur) groups.push(cur);
        cur = { rowIds: [row.row_id] };
      } else if (cur) {
        cur.rowIds.push(row.row_id);
      }
    }
    if (cur) groups.push(cur);
    return groups;
  }

  return groups;
}

const ESCAPE_RE = /[.*+?^${}()|[\]\\]/g;
const escapeRegex = (s: string) => s.replace(ESCAPE_RE, "\\$&");

/** Suggest a generalized row_text regex that matches the SHAPE of a row, never
 * its literal ids/amounts — so it holds across invoices of the same layout. */
export function suggestRowPattern(rowText: string): string {
  const text = rowText.trim();
  if (!text) return "";
  if (/^\d[\d,]*$/.test(text)) {
    const digits = text.replace(/\D/g, "").length;
    return `^\\d{${Math.max(1, digits)},}$`;
  }
  const firstToken = text.split(/\s+/)[0] ?? text;
  if (/^[A-Za-z]/.test(firstToken)) {
    return `^${escapeRegex(firstToken)}`;
  }
  return `^${escapeRegex(firstToken)}`;
}
