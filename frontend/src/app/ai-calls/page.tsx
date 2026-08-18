"use client";

import { useState } from "react";
import { toast } from "sonner";
import { Trash2, RefreshCw } from "lucide-react";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { PageHeader } from "@/components/page-header";
import { useConfirm } from "@/components/ui/confirm-dialog";
import { cn } from "@/lib/utils";
import { safeFormatDate } from "@/lib/format-date";
import { useAiCalls, useAiCall, useClearAiCalls } from "@/hooks/use-ai-debug";

export default function AiCallsPage() {
  const { data, isLoading, isError, refetch, isFetching } = useAiCalls();
  const [selected, setSelected] = useState<string | null>(null);
  const detail = useAiCall(selected);
  const clear = useClearAiCalls();
  const confirm = useConfirm();

  const calls = data?.calls ?? [];

  async function handleClear() {
    const ok = await confirm({
      title: "Delete all captured AI-call dumps?",
      confirmLabel: "Delete all",
      variant: "destructive",
    });
    if (!ok) return;
    try {
      await clear.mutateAsync();
      setSelected(null);
      toast.success("Cleared captured AI calls.");
    } catch {
      toast.error("Could not clear AI calls.");
    }
  }

  return (
    <div className="space-y-6">
      <PageHeader
        title="AI Calls"
        description="Dev-only capture of every prompt sent to the model — system + user prompt, schema, response, and token counts — so you can tune prompting from evidence."
        actions={
          <div className="flex gap-2">
            <Button variant="outline" size="sm" onClick={() => refetch()} disabled={isFetching}>
              <RefreshCw className={cn("h-4 w-4", isFetching && "animate-spin")} />
              Refresh
            </Button>
            <Button variant="outline" size="sm" className="text-destructive" onClick={handleClear} disabled={clear.isPending}>
              <Trash2 className="h-4 w-4" />
              Clear
            </Button>
          </div>
        }
      />

      {isError ? (
        <Card className="border-destructive">
          <CardContent className="pt-6">
            <p className="text-sm text-destructive">
              Could not load AI calls. This view is only available when ST_DEBUG is on.
            </p>
          </CardContent>
        </Card>
      ) : (
        <div className="grid gap-4 lg:grid-cols-[minmax(0,440px)_minmax(0,1fr)]">
          {/* List */}
          <div className="space-y-2">
            {isLoading ? (
              Array.from({ length: 6 }).map((_, i) => <Skeleton key={i} className="h-16" />)
            ) : calls.length === 0 ? (
              <Card>
                <CardContent className="py-10 text-center text-sm text-muted-foreground">
                  No AI calls captured yet. Run an extraction or a Forge authoring turn.
                </CardContent>
              </Card>
            ) : (
              calls.map((call) => (
                <button
                  key={call.name}
                  onClick={() => setSelected(call.name)}
                  className={cn(
                    "w-full rounded-md border px-3 py-2 text-left text-xs transition-colors",
                    selected === call.name ? "border-primary bg-primary/5" : "border-border hover:bg-muted",
                  )}
                >
                  <div className="flex items-center justify-between gap-2">
                    <Badge variant="secondary" className="text-[10px]">{call.call_type ?? "?"}</Badge>
                    {call.status === "error" ? (
                      <Badge variant="outline" className="text-[10px] text-destructive">error</Badge>
                    ) : (
                      <span className="font-mono text-muted-foreground">
                        {call.tokens_input ?? 0}→{call.tokens_output ?? 0} tok
                      </span>
                    )}
                  </div>
                  <p className="mt-1 truncate font-medium" title={call.context ?? ""}>
                    {call.context || "—"}
                  </p>
                  <div className="mt-0.5 flex items-center justify-between text-muted-foreground">
                    <span className="font-mono">{call.model}</span>
                    <span>{call.ts ? safeFormatDate(call.ts, "HH:mm:ss") : ""}</span>
                  </div>
                  <div className="text-[11px] text-muted-foreground">
                    {call.finish_reason ?? ""}{call.finish_reason ? " · " : ""}
                    ${(call.est_cost_usd ?? 0).toFixed(4)} · {call.duration_ms ?? 0} ms
                  </div>
                </button>
              ))
            )}
          </div>

          {/* Detail */}
          <Card className="min-h-[60vh]">
            <CardContent className="pt-6">
              {!selected ? (
                <p className="text-sm text-muted-foreground">Select a call to see its full prompt and response.</p>
              ) : detail.isLoading ? (
                <Skeleton className="h-96" />
              ) : (
                <pre className="overflow-x-auto whitespace-pre-wrap break-words font-mono text-xs leading-relaxed">
                  {detail.data?.markdown}
                </pre>
              )}
            </CardContent>
          </Card>
        </div>
      )}
    </div>
  );
}
