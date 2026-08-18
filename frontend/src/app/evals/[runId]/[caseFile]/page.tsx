"use client";

import { use } from "react";
import Link from "next/link";
import { ArrowLeft } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { RowTable } from "@/components/evals/row-table";
import { useEvalCase } from "@/hooks/use-evals";
import { useAiCall } from "@/hooks/use-ai-debug";

function num(v: number | null | undefined, digits = 3) {
  return v === null || v === undefined ? "—" : v.toFixed(digits);
}

export default function EvalCaseDetailPage({
  params,
}: {
  params: Promise<{ runId: string; caseFile: string }>;
}) {
  const { runId, caseFile } = use(params);
  const caseFileName = decodeURIComponent(caseFile);
  const detail = useEvalCase(runId, caseFileName);
  const data = detail.data;

  const metrics = (data?.metrics ?? {}) as {
    deliverable?: { row_f1?: number | null };
    hallucinations?: { row: number; column: string; value: string }[];
    consistency?: { is_reconciled?: boolean | null };
    est_cost_usd?: number | null;
  };
  const hallucinations = metrics.hallucinations ?? [];

  return (
    <div className="space-y-4">
      <Link href="/evals" className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground">
        <ArrowLeft className="h-4 w-4" /> Back to runs
      </Link>

      {detail.isLoading && <p className="text-sm text-muted-foreground">Loading…</p>}
      {detail.isError && <p className="text-sm text-destructive">Could not load this case (is ST_DEBUG on?).</p>}

      {data && (
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm">{data.case_id} · {data.call_type}</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3 text-xs">
            <div className="flex flex-wrap gap-x-4 gap-y-1 text-muted-foreground">
              <span>status <span className="font-medium text-foreground">{data.status}</span></span>
              <span>F1 <span className="font-medium text-foreground">{num(metrics.deliverable?.row_f1, 2)}</span></span>
              <span>hallucinations <span className="font-medium text-foreground">{hallucinations.length}</span></span>
              <span>reconciled <span className="font-medium text-foreground">
                {metrics.consistency?.is_reconciled === null || metrics.consistency?.is_reconciled === undefined
                  ? "—" : metrics.consistency.is_reconciled ? "✓" : "✗"}
              </span></span>
              <span>cost <span className="font-medium text-foreground">${num(metrics.est_cost_usd, 3)}</span></span>
            </div>

            {data.error && <p className="text-destructive">{data.error}</p>}

            {hallucinations.length > 0 && (
              <div className="rounded-md border border-warning/30 bg-warning/12 px-3 py-2 text-warning">
                <p className="font-medium">Possible hallucinations (value not in source)</p>
                {hallucinations.slice(0, 30).map((h, i) => (
                  <span key={i} className="mr-2 font-mono">{h.column}={h.value}</span>
                ))}
              </div>
            )}

            <div className="grid gap-3 md:grid-cols-2">
              <RowTable title="Expected" columns={data.columns} rows={data.expected_rows} />
              <RowTable title="Actual" columns={data.columns} rows={data.output_rows} />
            </div>

            {data.prompt_files.length > 0 && (
              <div className="space-y-2">
                <p className="font-medium">Prompts sent ({data.prompt_files.length})</p>
                {data.prompt_files.map((name) => <PromptDump key={name} name={name} />)}
              </div>
            )}
          </CardContent>
        </Card>
      )}
    </div>
  );
}

function PromptDump({ name }: { name: string }) {
  const call = useAiCall(name);
  return (
    <details className="rounded-md border">
      <summary className="cursor-pointer px-3 py-1 font-mono text-[11px]">{name}</summary>
      <pre className="max-h-96 overflow-auto whitespace-pre-wrap px-3 py-2 text-[11px]">
        {call.isLoading ? "Loading…" : call.data?.markdown ?? "Prompt dump not found (may have been pruned)."}
      </pre>
    </details>
  );
}
