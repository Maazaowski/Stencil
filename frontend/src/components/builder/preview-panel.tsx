"use client";

import { useRef, useState } from "react";
import {
  Play,
  Loader2,
  CheckCircle2,
  AlertTriangle,
  MinusCircle,
  Plus,
  ChevronDown,
  ChevronRight,
  FolderSearch,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  useDraftTest,
  useCreateSample,
  useEvalFolder,
  type DraftModel,
  type DraftTestResult,
  type DroppedItem,
  type EvalFolderResult,
  type EvalFolderFile,
  type BuilderTarget,
  type PreviewLabelValue,
} from "@/hooks/use-builder";
import { draftToModelJson } from "./assemble";
import { formatApiError } from "@/lib/api/errors";
import { toast } from "sonner";

const MAX_ROWS = 50;

const cellText = (v: unknown): string => (v === null || v === undefined ? "" : String(v));

interface Target {
  sampleId: string;
  name: string;
}

type RunState =
  | { state: "idle" }
  | { state: "running" }
  | { state: "done"; res: DraftTestResult }
  | { state: "error"; error: string };

function MatchBadge({ isMatch }: { isMatch: boolean | null }) {
  if (isMatch === true)
    return (
      <Badge className="gap-1 bg-success/12">
        <CheckCircle2 className="h-3 w-3" /> Matches AI
      </Badge>
    );
  if (isMatch === false)
    return (
      <Badge variant="secondary" className="gap-1 text-warning">
        <AlertTriangle className="h-3 w-3" /> Differs from AI
      </Badge>
    );
  return (
    <Badge variant="outline" className="gap-1 text-muted-foreground">
      <MinusCircle className="h-3 w-3" /> No AI baseline
    </Badge>
  );
}

/** Groups that looked like items but were dropped for a missing required field.
 *
 * Without this a near-miss just vanishes from the output, leaving no clue which
 * rule is wrong — so show what was dropped and why, dimmed below the real rows. */
function DroppedRows({ dropped }: { dropped: DroppedItem[] }) {
  if (!dropped.length) return null;
  const shown = dropped.slice(0, MAX_ROWS);
  return (
    <div className="border-t bg-muted/30 p-3">
      <p className="flex items-center gap-1.5 text-xs font-medium text-warning">
        <AlertTriangle className="h-3.5 w-3.5" />
        {dropped.length} row{dropped.length === 1 ? "" : "s"} dropped
      </p>
      <ul className="mt-1.5 space-y-1">
        {shown.map((d, i) => (
          <li key={i} className="flex flex-wrap items-baseline gap-x-2 text-xs text-muted-foreground">
            <span className="font-mono opacity-70">{d.text}</span>
            <span className="text-warning">— {d.reason}</span>
          </li>
        ))}
      </ul>
      {dropped.length > MAX_ROWS && (
        <p className="mt-1 text-xs text-muted-foreground">
          …and {dropped.length - MAX_ROWS} more
        </p>
      )}
    </div>
  );
}

/** Document-level values the interpreter resolved, shown above the row grid.
 *
 * The builder is meant to be filled in incrementally — configure the header
 * labels, run, confirm they resolved, then move on to the region. Without this
 * a header-only draft produces zero rows and the panel showed nothing at all,
 * so there was no way to check the labels against the real interpreter (the
 * Header tab's inline sample is a client-side approximation of it).
 *
 * `configured` is the set of header names the draft actually defines: a null
 * value for one of those is a rule that failed, everything else is just a field
 * this model doesn't produce. */
function HeaderSummary({
  headerFields,
  totals,
  configured,
}: {
  headerFields: PreviewLabelValue[];
  totals: PreviewLabelValue[];
  configured: Set<string>;
}) {
  // `supplier_name` comes from the assemble.ts identity placeholder, not from a
  // rule the user wrote, so it would always read "draft" here.
  const shown = headerFields.filter((f) => f.name !== "supplier_name");
  const entries = [
    ...shown.map((f) => ({ ...f, missing: f.value == null, expected: configured.has(f.name ?? "") })),
    ...totals.map((t) => ({ ...t, missing: false, expected: true })),
  ];
  if (!entries.length) return null;

  return (
    <div className="grid grid-cols-2 gap-x-6 gap-y-2 border-b p-3 sm:grid-cols-3 lg:grid-cols-5">
      {entries.map((e) => (
        <div key={`${e.name ?? e.label}`} className="space-y-0.5">
          <p className="text-[11px] uppercase tracking-wide text-muted-foreground">{e.label}</p>
          {e.missing ? (
            <p
              className={
                e.expected
                  ? "text-xs text-warning"
                  : "text-sm text-muted-foreground"
              }
            >
              {e.expected ? "not found — check label/position" : "—"}
            </p>
          ) : (
            <p className="truncate text-sm font-medium" title={cellText(e.value)}>
              {cellText(e.value)}
            </p>
          )}
        </div>
      ))}
    </div>
  );
}

function OutputTable({ res, configured }: { res: DraftTestResult; configured: Set<string> }) {
  const dropped = res.trace?.dropped_items ?? [];
  const summary = (
    <HeaderSummary
      headerFields={res.output.header_fields ?? []}
      totals={res.output.totals ?? []}
      configured={configured}
    />
  );
  if (res.output.row_count === 0) {
    return (
      <div>
        {summary}
        <p className="p-3 text-sm text-muted-foreground">
          No line items produced — check the region, grouping, and that an{" "}
          <span className="font-mono">amount</span> field is mapped.
        </p>
        <DroppedRows dropped={dropped} />
      </div>
    );
  }
  return (
    <div className="max-h-72 overflow-auto">
      {summary}
      <Table>
        <TableHeader>
          <TableRow>
            {res.output.columns.map((c, i) => (
              <TableHead key={i} className="whitespace-nowrap">
                {c.header}
              </TableHead>
            ))}
          </TableRow>
        </TableHeader>
        <TableBody>
          {res.output.rows.slice(0, MAX_ROWS).map((row, ri) => (
            <TableRow key={ri}>
              {row.map((v, ci) => (
                <TableCell key={ci} className="whitespace-nowrap">
                  {cellText(v)}
                </TableCell>
              ))}
            </TableRow>
          ))}
        </TableBody>
      </Table>
      {res.output.row_count > MAX_ROWS && (
        <p className="p-2 text-center text-xs text-muted-foreground">
          Showing first {MAX_ROWS} of {res.output.row_count} rows
        </p>
      )}
      <DroppedRows dropped={dropped} />
    </div>
  );
}

export interface PreviewPanelProps {
  draft: DraftModel;
  primarySampleId: string;
  primaryName: string;
  /** What the model delivers — decides which columns the dry-run renders. */
  target: BuilderTarget;
  /** Pre-fills the eval-folder layout id (the profile the model is bound to). */
  defaultEvalLayoutId?: string | null;
}

/** Run the draft model against the primary sample and any additional invoice
 * PDFs the user adds, so they can check it generalizes across the layout. */
export function PreviewPanel({
  draft,
  primarySampleId,
  primaryName,
  target,
  defaultEvalLayoutId,
}: PreviewPanelProps) {
  const test = useDraftTest();
  const createSample = useCreateSample();
  const inputRef = useRef<HTMLInputElement>(null);
  // Which document-level fields this draft claims to produce — a null value for
  // one of these is a broken rule, not just an absent field.
  const configuredHeaders = new Set(Object.keys(draft.header_fields));

  const [targets, setTargets] = useState<Target[]>([
    { sampleId: primarySampleId, name: primaryName },
  ]);
  const [results, setResults] = useState<Record<string, RunState>>({});
  const [expanded, setExpanded] = useState<string | null>(null);
  const [collapsed, setCollapsed] = useState(false);

  const runOne = async (t: Target) => {
    setResults((r) => ({ ...r, [t.sampleId]: { state: "running" } }));
    try {
      const res = await test.mutateAsync({
        intakeId: t.sampleId,
        modelJson: draftToModelJson(draft, target.profileId),
        target,
      });
      setResults((r) => ({ ...r, [t.sampleId]: { state: "done", res } }));
    } catch (e) {
      setResults((r) => ({ ...r, [t.sampleId]: { state: "error", error: formatApiError(e) } }));
    }
  };

  const runAll = () => targets.forEach(runOne);

  const addInvoice = (file: File | undefined) => {
    if (!file) return;
    if (!file.name.toLowerCase().endsWith(".pdf")) {
      toast.error("Please choose a PDF file");
      return;
    }
    createSample.mutate(file, {
      onSuccess: (res) =>
        setTargets((ts) =>
          ts.some((t) => t.sampleId === res.sample_id)
            ? ts
            : [...ts, { sampleId: res.sample_id, name: res.filename }],
        ),
      onError: (e) => toast.error(formatApiError(e)),
    });
  };

  return (
    <div className="rounded-lg border">
      <div className="flex flex-wrap items-center gap-2 border-b p-3">
        <button
          className="flex items-center gap-1.5 text-sm font-medium"
          onClick={() => setCollapsed((c) => !c)}
          title={collapsed ? "Expand preview" : "Collapse preview"}
        >
          {collapsed ? (
            <ChevronRight className="h-4 w-4 text-muted-foreground" />
          ) : (
            <ChevronDown className="h-4 w-4 text-muted-foreground" />
          )}
          Preview
        </button>
        <Button size="sm" onClick={runAll} disabled={test.isPending}>
          {test.isPending ? (
            <Loader2 className="mr-2 h-4 w-4 animate-spin" />
          ) : (
            <Play className="mr-2 h-4 w-4" />
          )}
          Run all
        </Button>
        <input
          ref={inputRef}
          type="file"
          accept="application/pdf,.pdf"
          className="hidden"
          onChange={(e) => {
            addInvoice(e.target.files?.[0]);
            e.target.value = "";
          }}
        />
        <Button
          size="sm"
          variant="outline"
          onClick={() => inputRef.current?.click()}
          disabled={createSample.isPending}
        >
          {createSample.isPending ? (
            <Loader2 className="mr-2 h-4 w-4 animate-spin" />
          ) : (
            <Plus className="mr-2 h-4 w-4" />
          )}
          Add invoice
        </Button>
        <span className="ml-auto text-xs text-muted-foreground">Deterministic dry-run · $0</span>
      </div>

      {!collapsed && (
      <ul className="divide-y">
        {targets.map((t, i) => {
          const rs = results[t.sampleId] ?? { state: "idle" };
          const isOpen = expanded === t.sampleId;
          const done = rs.state === "done" ? rs.res : null;
          return (
            <li key={t.sampleId}>
              <div className="flex items-center gap-3 p-3">
                <button
                  className="flex min-w-0 flex-1 items-center gap-2 text-left"
                  onClick={() => setExpanded(isOpen ? null : t.sampleId)}
                >
                  {done ? (
                    isOpen ? (
                      <ChevronDown className="h-4 w-4 shrink-0 text-muted-foreground" />
                    ) : (
                      <ChevronRight className="h-4 w-4 shrink-0 text-muted-foreground" />
                    )
                  ) : (
                    <span className="w-4" />
                  )}
                  <span className="truncate text-sm">
                    {t.name}
                    {i === 0 && <span className="ml-1 text-xs text-muted-foreground">(primary)</span>}
                  </span>
                </button>

                {rs.state === "running" && <Loader2 className="h-4 w-4 animate-spin" />}
                {rs.state === "error" && (
                  <span className="max-w-xs truncate text-xs text-destructive" title={rs.error}>
                    {rs.error}
                  </span>
                )}
                {done && (
                  <>
                    <span className="text-xs tabular-nums text-muted-foreground">
                      {done.output.row_count} rows
                    </span>
                    <MatchBadge isMatch={done.is_match} />
                  </>
                )}
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={() => runOne(t)}
                  disabled={rs.state === "running"}
                >
                  <Play className="h-4 w-4" />
                </Button>
              </div>
              {isOpen && done && (
                <div className="border-t bg-muted/20">
                  <OutputTable res={done} configured={configuredHeaders} />
                </div>
              )}
            </li>
          );
        })}
      </ul>
      )}

      {!collapsed && (
        <EvalFolderScorer draft={draft} defaultLayoutId={defaultEvalLayoutId} />
      )}
    </div>
  );
}

/** Why one eval file scored what it did.
 *
 * Row matching is whole-row, so a single wrong column zeroes the file — the bare
 * "0/384" that results looks catastrophic and says nothing about the cause. The
 * per-column breakdown turns that into "EXT_DATE, 384 rows, expected 06/01/2026,
 * got 01/06/2026", which names the rule to fix. */
function FileDiffDetail({ file }: { file: EvalFolderFile }) {
  const columns = file.cell_diff_summary ?? [];
  const extra = file.extra_rows ?? 0;
  const missing = file.missing_rows ?? 0;
  if (!columns.length && !extra && !missing) return null;

  return (
    <div className="mt-1.5 space-y-1 border-l-2 border-muted pl-3">
      {columns.map((c) => (
        <p key={c.column} className="flex flex-wrap items-baseline gap-x-2 text-[11px]">
          <span className="font-mono font-medium">{c.column}</span>
          <span className="text-muted-foreground">
            {c.rows} row{c.rows === 1 ? "" : "s"}
          </span>
          <span className="text-muted-foreground">
            expected <span className="font-mono text-success">{cellText(c.expected) || "∅"}</span>
            {" · got "}
            <span className="font-mono text-warning">{cellText(c.actual) || "∅"}</span>
          </span>
        </p>
      ))}
      {(extra > 0 || missing > 0) && (
        <p className="text-[11px] text-muted-foreground">
          {missing > 0 && `${missing} expected row${missing === 1 ? "" : "s"} not produced`}
          {missing > 0 && extra > 0 && " · "}
          {extra > 0 && `${extra} row${extra === 1 ? "" : "s"} produced that shouldn't exist`}
        </p>
      )}
    </div>
  );
}

/** Score the model against a committed eval-case folder (ST_DEBUG only): each
 * invoice's produced rows are diffed against its expected XLSX, so the author
 * sees the model generalize across a real labelled set as matched/expected. */
function EvalFolderScorer({
  draft,
  defaultLayoutId,
}: {
  draft: DraftModel;
  defaultLayoutId?: string | null;
}) {
  const evalFolder = useEvalFolder();
  const [open, setOpen] = useState(false);
  const [layoutId, setLayoutId] = useState(defaultLayoutId ?? "");
  const [result, setResult] = useState<EvalFolderResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  const run = async () => {
    if (!layoutId.trim()) return;
    setError(null);
    try {
      setResult(await evalFolder.mutateAsync({ layoutId: layoutId.trim(), modelJson: draftToModelJson(draft) }));
    } catch (e) {
      setResult(null);
      setError(formatApiError(e));
    }
  };

  return (
    <div className="border-t">
      <button
        className="flex w-full items-center gap-2 p-3 text-left text-sm font-medium"
        onClick={() => setOpen((o) => !o)}
      >
        {open ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}
        <FolderSearch className="h-4 w-4 text-muted-foreground" />
        Score against eval folder
      </button>
      {open && (
        <div className="space-y-3 px-3 pb-3">
          <p className="text-xs text-muted-foreground">
            Diff this model against every invoice in an{" "}
            <span className="font-mono">eval_cases/&lt;layout&gt;</span> folder that has an expected
            answer key. Debug builds only.
          </p>
          <div className="flex gap-2">
            <Input
              value={layoutId}
              onChange={(e) => setLayoutId(e.target.value)}
              placeholder="eval layout, e.g. att.vpn"
              className="h-8"
              onKeyDown={(e) => e.key === "Enter" && run()}
            />
            <Button size="sm" onClick={run} disabled={evalFolder.isPending || !layoutId.trim()}>
              {evalFolder.isPending ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : null}
              Score folder
            </Button>
          </div>
          {error && <p className="text-xs text-destructive">{error}</p>}
          {result && (
            <div className="rounded-md border">
              <div className="flex items-center justify-between border-b bg-muted/30 px-3 py-1.5 text-xs font-medium">
                <span>{result.layout_id}</span>
                <span className="tabular-nums">
                  {result.files_matched}/{result.files_total} files match
                </span>
              </div>
              <ul className="divide-y">
                {result.files.map((f) => (
                  <li key={f.file} className="px-3 py-1.5 text-xs">
                    <div className="flex items-center gap-2">
                      <span className="min-w-0 flex-1 truncate font-mono" title={f.file}>
                        {f.file}
                      </span>
                      {f.error ? (
                        <span className="text-destructive" title={f.error}>
                          error
                        </span>
                      ) : (
                        <>
                          <span className="tabular-nums text-muted-foreground">
                            {f.matched_rows}/{f.expected_rows} rows
                          </span>
                          {f.is_match ? (
                            <CheckCircle2 className="h-3.5 w-3.5 text-success" />
                          ) : (
                            <AlertTriangle className="h-3.5 w-3.5 text-warning" />
                          )}
                        </>
                      )}
                    </div>
                    {!f.error && !f.is_match && <FileDiffDetail file={f} />}
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
