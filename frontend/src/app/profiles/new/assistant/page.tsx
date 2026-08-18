"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { useDropzone } from "react-dropzone";
import { toast } from "sonner";
import {
  AlertTriangle,
  CheckCircle2,
  FileText,
  Loader2,
  Paperclip,
  Send,
  Sparkles,
  Square,
  Upload,
  X,
} from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { PageHeader } from "@/components/page-header";
import { ExtractionPreviewView } from "@/components/extraction-preview";
import { cn } from "@/lib/utils";
import {
  useAuthoringSession,
  useCancelAuthoring,
  useCreateAuthoringSession,
  useDeleteAuthoringBlueprint,
  useDeleteAuthoringInvoice,
  useDiscoverAuthoring,
  useFinalizeAuthoringSession,
  useReextractAuthoring,
  useSendAuthoringMessage,
  useUploadAuthoringBlueprint,
  useUploadAuthoringInvoice,
} from "@/hooks/use-profile-authoring";
import { useFieldSchemas } from "@/hooks/use-field-schemas";
import { useOutputSpec, useOutputSpecs } from "@/hooks/use-output-specs";
import type { AuthoringMessage, AuthoringPreview } from "@/types";

interface StagedItem {
  id: string;
  file: File;
  expected?: File;
}

interface DraftOutputMapping {
  output_header: string;
  source: string;
  fallback?: string | null;
  transforms?: string[];
  reason?: string;
}

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {};
}

const authoringProgressWords = [
  "Processing",
  "Thinking",
  "Sampling",
  "Extracting",
  "Re-extracting",
  "Reconciling",
  "Previewing",
];

// Suggest the next version id for an edit session: bump a trailing `.vN`, else append `.v2`.
function nextVersionId(profileId: string): string {
  const m = profileId.match(/^(.*)\.v(\d+)$/);
  return m ? `${m[1]}.v${Number(m[2]) + 1}` : `${profileId}.v2`;
}

export default function ProfileAssistantPage() {
  const router = useRouter();
  const createSession = useCreateAuthoringSession();
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [profileId, setProfileId] = useState("");
  const [supplierName, setSupplierName] = useState("");
  const [outputSpecId, setOutputSpecId] = useState("temforce.standard");
  const [fieldSchemaId, setFieldSchemaId] = useState("invoice.standard");
  const [categoryOverride, setCategoryOverride] = useState("");
  const { data: outputSpecs } = useOutputSpecs();
  const { data: fieldSchemas } = useFieldSchemas();

  // Create the session once on mount. `?from=<id>` seeds an edit-with-AI session
  // from an existing profile (read client-side to avoid a Suspense boundary).
  const started = useRef(false);
  useEffect(() => {
    if (started.current) return;
    started.current = true;
    const from = new URLSearchParams(window.location.search).get("from") || undefined;
    if (!from) return;
    createSession.mutate(
      { source_profile_id: from },
      {
        onSuccess: (s) => {
          setSessionId(s.session_id);
          if (s.source_profile_id) setProfileId(nextVersionId(s.source_profile_id));
        },
        onError: () => toast.error("Could not start an authoring session. Is the backend running?"),
      },
    );
  }, [createSession]);

  const { data: session } = useAuthoringSession(sessionId);
  const { data: selectedOutputSpec } = useOutputSpec(session?.output_spec_id ?? outputSpecId);
  const uploadInvoice = useUploadAuthoringInvoice();
  const uploadBlueprint = useUploadAuthoringBlueprint();
  const deleteBlueprint = useDeleteAuthoringBlueprint();
  const deleteInvoice = useDeleteAuthoringInvoice();
  const sendMessage = useSendAuthoringMessage(sessionId ?? "");
  const reextract = useReextractAuthoring(sessionId ?? "");
  const cancelAuthoring = useCancelAuthoring(sessionId ?? "");
  const finalize = useFinalizeAuthoringSession(sessionId ?? "");
  const discover = useDiscoverAuthoring(sessionId ?? "");

  const [staged, setStaged] = useState<StagedItem[]>([]);
  const [message, setMessage] = useState("");
  const [pendingUserMessage, setPendingUserMessage] = useState<AuthoringMessage | null>(null);
  const [progressTick, setProgressTick] = useState(0);
  const [activePreview, setActivePreview] = useState<string | null>(null);
  // The freshest previews come straight from the last turn / re-extraction job;
  // prefer them over the session refetch so the panel updates immediately.
  const [latestPreviews, setLatestPreviews] = useState<Record<string, AuthoringPreview> | null>(null);
  const previews = useMemo(
    () => ({ ...(session?.previews ?? {}), ...(latestPreviews ?? {}) }),
    [session?.previews, latestPreviews],
  );
  const draftRoot = asRecord(session?.draft_profile);
  const draftProfile = asRecord(draftRoot.profile ?? draftRoot);
  const outputMappingDecisions = Array.isArray(draftProfile.output_mapping_overrides)
    ? draftProfile.output_mapping_overrides.map(asRecord) as unknown as DraftOutputMapping[]
    : [];
  const discovery = asRecord(session?.discovery);
  const candidatePlan = asRecord(discovery.candidate_plan);
  const planRegions = Array.isArray(candidatePlan.regions)
    ? candidatePlan.regions.map(asRecord)
    : [];
  const rowSelector = asRecord(candidatePlan.row_selector);
  const documentRules = Object.keys(asRecord(candidatePlan.document_field_rules));
  const rowRules = Object.keys(asRecord(candidatePlan.row_field_rules));
  const reconciliationRules = Array.isArray(candidatePlan.reconciliation_rules)
    ? candidatePlan.reconciliation_rules.map(asRecord)
    : [];
  const contextRules = Array.isArray(candidatePlan.row_context_rules)
    ? candidatePlan.row_context_rules.map(asRecord)
    : [];

  const doneInvoices = useMemo(
    () => (session?.invoices ?? []).filter((i) => i.extraction_status === "done"),
    [session],
  );
  // Staged or already-extracted samples both count as usable to start a turn.
  const sampleCount = (session?.invoices ?? []).filter(
    (i) => i.extraction_status === "uploaded" || i.extraction_status === "pending" || i.extraction_status === "done",
  ).length;
  const jobs = session?.jobs ?? [];
  const activeJobs = jobs.filter((j) => j.status === "queued" || j.status === "running");
  const activeTurnJobs = activeJobs.filter((j) => j.kind === "turn");
  const hasActiveJobs = activeJobs.length > 0 || session?.status === "running";
  const samplesLocked = hasActiveJobs || uploadInvoice.isPending || deleteInvoice.isPending || reextract.isPending;

  // The selected preview tab falls back to the first extracted invoice (no effect
  // needed — derive it during render so we don't trigger cascading renders).
  const effectiveActive = activePreview ?? doneInvoices[0]?.id ?? null;

  useEffect(() => {
    if (!sendMessage.isPending && !hasActiveJobs && !reextract.isPending) return;
    const timer = window.setInterval(() => setProgressTick((tick) => tick + 1), 1600);
    return () => window.clearInterval(timer);
  }, [hasActiveJobs, reextract.isPending, sendMessage.isPending]);

  const onDrop = useCallback((accepted: File[], rejected: { file: File }[]) => {
    if (rejected.length) toast.error("Only PDF files are accepted here.");
    setStaged((prev) => [
      ...prev,
      ...accepted.map((file) => ({ id: `${Date.now()}-${Math.random().toString(36).slice(2)}`, file })),
    ]);
  }, []);

  const { getRootProps, getInputProps, isDragActive, open } = useDropzone({
    accept: { "application/pdf": [".pdf"] },
    multiple: true,
    noClick: true,
    maxSize: 50 * 1024 * 1024,
    disabled: samplesLocked,
    onDrop,
  });

  function attachExpected(id: string, file: File) {
    setStaged((prev) => prev.map((s) => (s.id === id ? { ...s, expected: file } : s)));
  }

  async function uploadStaged() {
    if (!sessionId || !staged.length || samplesLocked) return;
    const items = [...staged];
    setStaged([]);
    for (const item of items) {
      try {
        await uploadInvoice.mutateAsync({ sessionId, file: item.file, expected: item.expected });
      } catch {
        toast.error(`Failed to upload ${item.file.name}`);
      }
    }
    toast.success(
      `Staged ${items.length} invoice${items.length === 1 ? "" : "s"} — send a message to extract & preview.`,
    );
  }

  function startNewSession() {
    createSession.mutate(
      {
        supplier_name: supplierName.trim() || undefined,
        output_spec_id: outputSpecId,
        field_schema_id: fieldSchemaId,
        category_override: categoryOverride
          ? categoryOverride as "standard" | "wireless" | "time_and_material"
          : undefined,
      },
      {
        onSuccess: (state) => setSessionId(state.session_id),
        onError: () => toast.error("Could not start the authoring session."),
      },
    );
  }

  async function handleStandaloneBlueprint(file: File) {
    if (!sessionId) return;
    try {
      await uploadBlueprint.mutateAsync({ sessionId, file });
      toast.success(`Added historical blueprint ${file.name}`);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Could not upload blueprint.");
    }
  }

  async function handleSend() {
    const text = message.trim();
    if (!text || !sessionId) return;
    setMessage("");
    setPendingUserMessage({ role: "user", content: text });
    setProgressTick(0);
    try {
      await sendMessage.mutateAsync(text);
      setPendingUserMessage(null);
    } catch (e) {
      setPendingUserMessage(null);
      setMessage(text);
      toast.error(e instanceof Error ? e.message : "The assistant could not respond.");
    }
  }

  async function handleDeleteInvoice(invoiceId: string, filename: string) {
    if (!sessionId || samplesLocked) return;
    try {
      await deleteInvoice.mutateAsync({ sessionId, invoiceId });
      setActivePreview((current) => (current === invoiceId ? null : current));
      setLatestPreviews((current) => {
        if (!current?.[invoiceId]) return current;
        const next = { ...current };
        delete next[invoiceId];
        return next;
      });
      toast.success(`Removed ${filename}`);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : `Could not remove ${filename}.`);
    }
  }

  async function handleReextract() {
    if (!sessionId) return;
    try {
      await reextract.mutateAsync(undefined);
      toast.success("Queued re-extraction with the current draft.");
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Could not re-extract the samples.");
    }
  }

  async function handleCancelAuthoring() {
    if (!sessionId || !hasActiveJobs) return;
    try {
      await cancelAuthoring.mutateAsync();
      setPendingUserMessage(null);
      toast.success("Authoring cancelled. In-flight AI requests were stopped.");
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Could not cancel authoring.");
    }
  }

  async function handleFinalize() {
    if (!profileId.trim()) {
      toast.error("Enter a profile ID first (e.g. acme.standard.v1).");
      return;
    }
    try {
      const res = await finalize.mutateAsync({ profile_id: profileId.trim() });
      toast.success(`Profile "${res.profile_id}" created as a draft.`);
      router.push(`/profiles/${res.profile_id}`);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Could not finalize the profile.");
    }
  }

  const editingProfileId = session?.source_profile_id ?? null;
  const isEditing = !!editingProfileId;
  const canChat = sampleCount > 0 && session?.status !== "finalized" && !sendMessage.isPending;
  const canFinalize = !!session?.draft_profile && session?.status === "active" && !hasActiveJobs;
  const activeEntry = effectiveActive ? previews[effectiveActive] : undefined;
  const canReextract = !!session?.draft_profile && sampleCount > 0
    && !samplesLocked && !reextract.isPending && !sendMessage.isPending;
  const progressWord = authoringProgressWords[progressTick % authoringProgressWords.length];
  const visiblePendingUserMessage = pendingUserMessage
    && !session?.conversation?.some((m) => m.role === "user" && m.content === pendingUserMessage.content)
    ? pendingUserMessage
    : null;
  const showEmptyChatPrompt = session && (session.conversation?.length ?? 0) === 0 && !visiblePendingUserMessage;

  if (!sessionId) {
    return (
      <div className="space-y-6">
        <PageHeader
          title="Create Profile with AI"
          description="Choose what to extract and deliver. The PDF provides printed evidence and totals; an optional blueprint defines expected delivery."
        />
        <Card className="max-w-2xl">
          <CardHeader><CardTitle className="text-base">Authoring setup</CardTitle></CardHeader>
          <CardContent className="space-y-4">
            <label className="block space-y-1 text-sm">
              <span>Supplier name (optional)</span>
              <Input value={supplierName} onChange={(event) => setSupplierName(event.target.value)} />
            </label>
            <label className="block space-y-1 text-sm">
              <span>Output specification</span>
              <select
                className="w-full rounded-md border bg-background px-3 py-2"
                value={outputSpecId}
                onChange={(event) => setOutputSpecId(event.target.value)}
              >
                {(outputSpecs ?? []).map((spec) => (
                  <option key={spec.spec_id} value={spec.spec_id}>{spec.name || spec.spec_id}</option>
                ))}
              </select>
            </label>
            <label className="block space-y-1 text-sm">
              <span>Field schema</span>
              <select
                className="w-full rounded-md border bg-background px-3 py-2"
                value={fieldSchemaId}
                onChange={(event) => setFieldSchemaId(event.target.value)}
              >
                {(fieldSchemas ?? []).map((schema) => (
                  <option key={schema.schema_id} value={schema.schema_id}>
                    {schema.name || schema.schema_id}
                  </option>
                ))}
              </select>
            </label>
            <label className="block space-y-1 text-sm">
              <span>Document layout family (optional)</span>
              <select
                className="w-full rounded-md border bg-background px-3 py-2"
                value={categoryOverride}
                onChange={(event) => setCategoryOverride(event.target.value)}
              >
                <option value="">Infer automatically</option>
                <option value="wireless">Wireless</option>
                <option value="time_and_material">Time and material</option>
                <option value="standard">Standard invoice</option>
              </select>
            </label>
            <Button onClick={startNewSession} disabled={createSession.isPending || !outputSpecId || !fieldSchemaId}>
              {createSession.isPending && <Loader2 className="h-4 w-4 animate-spin" />} Start session
            </Button>
          </CardContent>
        </Card>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <PageHeader
        title={isEditing ? "Edit Profile with AI" : "Create Profile with AI"}
        description={
          isEditing
            ? `Refining ${editingProfileId} — upload a few samples, tell the assistant what to change, and save it as a new version (the source's folders and training settings carry over).`
            : "Upload a few sample invoices, describe the output you expect, and the assistant drafts a supplier profile you can preview and refine."
        }
        actions={
          <Button variant="outline" onClick={() => router.push("/profiles")}>
            Back to profiles
          </Button>
        }
      />

      <div className="grid gap-4 lg:grid-cols-[320px_minmax(0,1fr)_minmax(0,1fr)]">
        {/* ── Left: invoice upload ─────────────────────────── */}
        <Card className="flex flex-col">
          <CardHeader className="pb-3">
            <CardTitle className="text-sm">Sample invoices</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            <div
              {...getRootProps()}
              className={cn(
                "flex flex-col items-center justify-center rounded-md border-2 border-dashed p-4 text-center text-xs",
                isDragActive ? "border-primary bg-primary/5" : "border-border",
              )}
            >
              <input {...getInputProps()} />
              <Upload className="mb-2 h-5 w-5 text-muted-foreground" />
              <p className="text-muted-foreground">Drag PDFs here</p>
              <Button
                variant="outline"
                size="sm"
                className="mt-2"
                onClick={open}
                type="button"
                disabled={samplesLocked}
              >
                Choose files
              </Button>
            </div>
            <p className="text-[11px] leading-relaxed text-muted-foreground">
              The PDF proves what was printed and its totals. The XLS blueprint defines expected output shape and mapping semantics; differences are reviewed rather than automatically treated as errors.
            </p>
            <label className="flex cursor-pointer items-center gap-2 rounded-md border px-2 py-2 text-xs hover:bg-muted">
              <Paperclip className="h-3.5 w-3.5" /> Add standalone historical blueprint
              <input
                type="file"
                accept=".xlsx,.xls"
                className="hidden"
                disabled={samplesLocked || uploadBlueprint.isPending}
                onChange={(event) => {
                  const file = event.target.files?.[0];
                  if (file) void handleStandaloneBlueprint(file);
                  event.target.value = "";
                }}
              />
            </label>
            {(session?.blueprints ?? []).map((blueprint) => (
              <div key={blueprint.id} className="flex items-center gap-2 rounded-md border px-2 py-1.5 text-xs">
                <FileText className="h-3.5 w-3.5" />
                <span className="truncate">{blueprint.filename}</span>
                <Badge variant="secondary" className="ml-auto text-[10px]">
                  {blueprint.invoice_id ? "paired" : "historical"}
                </Badge>
                <button
                  type="button"
                  disabled={samplesLocked || deleteBlueprint.isPending}
                  onClick={() => sessionId && deleteBlueprint.mutate({ sessionId, blueprintId: blueprint.id })}
                >
                  <X className="h-3.5 w-3.5" />
                </button>
              </div>
            ))}

            {/* Staged (not yet uploaded) */}
            {staged.map((item) => (
              <div key={item.id} className="rounded-md border p-2 text-xs">
                <div className="flex items-center gap-2">
                  <FileText className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
                  <span className="truncate" title={item.file.name}>{item.file.name}</span>
                  <button
                    className="ml-auto text-muted-foreground hover:text-destructive"
                    onClick={() => setStaged((p) => p.filter((s) => s.id !== item.id))}
                  >
                    <X className="h-3.5 w-3.5" />
                  </button>
                </div>
                <label className="mt-1 flex cursor-pointer items-center gap-1 text-muted-foreground hover:text-foreground">
                  <Paperclip className="h-3 w-3" />
                  {item.expected ? item.expected.name : "Attach XLS/XLSX blueprint (optional)"}
                  <input
                    type="file"
                    accept=".xlsx,.xls"
                    className="hidden"
                    onChange={(e) => e.target.files?.[0] && attachExpected(item.id, e.target.files[0])}
                  />
                </label>
              </div>
            ))}
            {staged.length > 0 && (
              <Button size="sm" className="w-full" onClick={uploadStaged} disabled={!sessionId || samplesLocked}>
                Upload {staged.length} invoice{staged.length === 1 ? "" : "s"}
              </Button>
            )}

            {/* Uploaded / extracting */}
            <div className="space-y-1">
              {(session?.invoices ?? []).map((inv) => (
                <div key={inv.id} className="flex items-center gap-2 rounded-md border px-2 py-1.5 text-xs">
                  {inv.extraction_status === "uploaded" && <FileText className="h-3.5 w-3.5 text-muted-foreground" />}
                  {inv.extraction_status === "pending" && <Loader2 className="h-3.5 w-3.5 animate-spin text-primary" />}
                  {inv.extraction_status === "done" && <CheckCircle2 className="h-3.5 w-3.5 text-success" />}
                  {inv.extraction_status === "error" && <AlertTriangle className="h-3.5 w-3.5 text-destructive" />}
                  <span className="truncate" title={inv.filename}>{inv.filename}</span>
                  {inv.has_expected && <Badge variant="secondary" className="ml-auto text-[10px]">blueprint</Badge>}
                  {!inv.has_expected && <span className="ml-auto" />}
                  <button
                    className="text-muted-foreground hover:text-destructive disabled:cursor-not-allowed disabled:opacity-40"
                    onClick={() => handleDeleteInvoice(inv.id, inv.filename)}
                    disabled={samplesLocked}
                    title="Remove sample"
                    type="button"
                  >
                    <X className="h-3.5 w-3.5" />
                  </button>
                </div>
              ))}
            </div>
            {session && session.estimated_cost_usd > 0 && (
              <p className="text-[11px] text-muted-foreground">
                AI cost so far: ${session.estimated_cost_usd.toFixed(3)}
              </p>
            )}
            {sampleCount > 0 && (
              <Button
                size="sm"
                variant="outline"
                className="w-full"
                disabled={hasActiveJobs || discover.isPending}
                onClick={() => discover.mutate()}
              >
                {discover.isPending && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
                Run discovery
              </Button>
            )}
          </CardContent>
        </Card>

        {/* ── Center: chat ─────────────────────────────────── */}
        <Card className="flex flex-col">
          <CardHeader className="pb-3">
            <CardTitle className="flex items-center gap-2 text-sm">
              <Sparkles className="h-4 w-4 text-primary" /> Assistant
            </CardTitle>
          </CardHeader>
          <CardContent className="flex flex-1 flex-col">
            <div className="flex-1 space-y-3 overflow-y-auto pr-1" style={{ maxHeight: "52vh" }}>
              {!session && <Skeleton className="h-16" />}
              {showEmptyChatPrompt && (
                <p className="text-sm text-muted-foreground">
                  {sampleCount === 0
                    ? "Upload a few invoices on the left, then tell me what output you expect — I’ll read the samples and draft the profile on your first message."
                    : "Tell me what you expect — for example, “Use each row’s billing period start for EXT_DATE and billing period end for formula.” I’ll read the samples and any XLS blueprint, then draft the profile."}
                </p>
              )}
              {session?.conversation?.map((m, i) => (
                <div
                  key={i}
                  className={cn(
                    "rounded-md px-3 py-2 text-sm",
                    m.role === "user" ? "ml-6 bg-primary/10" : "mr-6 bg-muted",
                  )}
                >
                  <p className="mb-0.5 text-[10px] uppercase tracking-wide text-muted-foreground">
                    {m.role === "user" ? "You" : "Assistant"}
                  </p>
                  <p className="whitespace-pre-wrap">{m.content}</p>
                  {m.open_questions && m.open_questions.length > 0 && (
                    <ul className="mt-1 list-disc pl-4 text-xs text-muted-foreground">
                      {m.open_questions.map((q, qi) => <li key={qi}>{q}</li>)}
                    </ul>
                  )}
                </div>
              ))}
              {visiblePendingUserMessage && (
                <div className="ml-6 rounded-md bg-primary/10 px-3 py-2 text-sm">
                  <p className="mb-0.5 text-[10px] uppercase tracking-wide text-muted-foreground">You</p>
                  <p className="whitespace-pre-wrap">{visiblePendingUserMessage.content}</p>
                </div>
              )}
              {activeTurnJobs.map((job) => (
                <div key={job.id} className="ml-6 rounded-md bg-primary/10 px-3 py-2 text-sm">
                  <p className="mb-0.5 text-[10px] uppercase tracking-wide text-muted-foreground">
                    You {job.status === "queued" ? "Queued" : "Running"}
                  </p>
                  <p className="whitespace-pre-wrap">{job.message}</p>
                </div>
              ))}
              {(sendMessage.isPending || hasActiveJobs || reextract.isPending) && (
                <div className="mr-6 rounded-md bg-muted px-3 py-2 text-sm text-muted-foreground">
                  <div className="flex items-center gap-2">
                    <Loader2 className="h-3.5 w-3.5 animate-spin" />
                    <span className="font-medium text-foreground">{progressWord}...</span>
                    <span>{doneInvoices.length === 0 ? "building the first draft" : "updating the draft"}</span>
                    {hasActiveJobs && (
                      <Button
                        variant="outline"
                        size="sm"
                        className="ml-auto h-7 text-xs"
                        onClick={handleCancelAuthoring}
                        disabled={cancelAuthoring.isPending}
                      >
                        {cancelAuthoring.isPending
                          ? <Loader2 className="h-3.5 w-3.5 animate-spin" />
                          : <Square className="h-3.5 w-3.5" />}
                        Cancel
                      </Button>
                    )}
                  </div>
                  <p className="mt-1 text-xs">
                    Scanning · Matching · Discovering · Planning · Extracting · Validating · Previewing ·
                    Re-extracting changed profiles
                  </p>
                </div>
              )}
            </div>

            <div className="mt-3 space-y-2 border-t pt-3">
              <textarea
                className="w-full resize-none rounded-md border bg-transparent px-3 py-2 text-sm outline-none focus:ring-1 focus:ring-ring"
                rows={3}
                placeholder={
                  sampleCount === 0
                    ? "Upload sample invoices first…"
                    : "Describe the output shape, or ask for a change…"
                }
                value={message}
                disabled={!canChat}
                onChange={(e) => setMessage(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) handleSend();
                }}
              />
              <div className="flex items-center justify-between">
                <span className="text-[11px] text-muted-foreground">
                  {sampleCount === 0
                    ? "Upload samples to begin"
                    : `${sampleCount} sample${sampleCount === 1 ? "" : "s"} ready`}
                  {" · ⌘/Ctrl+Enter to send"}
                </span>
                <Button size="sm" onClick={handleSend} disabled={!canChat || !message.trim()}>
                  <Send className="h-3.5 w-3.5" /> Send
                </Button>
              </div>
            </div>
          </CardContent>
        </Card>

        {/* ── Right: preview ───────────────────────────────── */}
        <Card className="flex flex-col">
          <CardHeader className="pb-3">
            <div className="flex items-center justify-between gap-2">
              <CardTitle className="text-sm">Output preview</CardTitle>
              {session?.draft_profile && sampleCount > 0 && (
                <div className="flex items-center gap-1">
                  {session.discovery && (
                    <Button
                      variant="ghost"
                      size="sm"
                      className="h-7 text-xs"
                      onClick={() => document.getElementById("evidence-plan")?.scrollIntoView({ behavior: "smooth" })}
                    >
                      View plan
                    </Button>
                  )}
                  <Button
                    variant="outline"
                    size="sm"
                    className="h-7 text-xs"
                    onClick={handleReextract}
                    disabled={!canReextract}
                    title="Re-read the sample PDFs with the current draft profile (uses AI)"
                  >
                    {reextract.isPending
                      ? <Loader2 className="h-3.5 w-3.5 animate-spin" />
                      : <Sparkles className="h-3.5 w-3.5" />}
                    Re-extract with AI
                  </Button>
                </div>
              )}
            </div>
          </CardHeader>
          <CardContent className="space-y-3">
            {reextract.isPending && (
              <div className="flex items-center gap-2 rounded-md bg-muted px-3 py-2 text-xs text-muted-foreground">
                <Loader2 className="h-3.5 w-3.5 animate-spin" /> Re-reading samples with the current draft…
              </div>
            )}
            {doneInvoices.length === 0 ? (
              <p className="text-sm text-muted-foreground">
                A deliverable preview for each invoice appears here after the assistant drafts the profile.
              </p>
            ) : (
              <>
                <div className="flex flex-wrap gap-1">
                  {doneInvoices.map((inv) => {
                    const entry = previews[inv.id];
                    const matched = entry?.diff?.is_match;
                    return (
                      <button
                        key={inv.id}
                        onClick={() => setActivePreview(inv.id)}
                        className={cn(
                          "flex items-center gap-1 rounded-md border px-2 py-1 text-xs",
                          effectiveActive === inv.id ? "border-primary bg-primary/5" : "border-border",
                        )}
                      >
                        <span className="max-w-[120px] truncate" title={inv.filename}>{inv.filename}</span>
                        {entry?.diff && (
                          matched
                            ? <CheckCircle2 className="h-3 w-3 text-success" />
                            : <AlertTriangle className="h-3 w-3 text-warning" />
                        )}
                      </button>
                    );
                  })}
                </div>

                {activeEntry?.error && (
                  <p className="text-sm text-destructive">{activeEntry.error}</p>
                )}
                {activeEntry?.diff && !activeEntry.diff.is_match && (
                  <div className="rounded-md border border-warning/30 bg-warning/12 px-3 py-2 text-xs text-warning">
                    <p className="font-medium">Differs from XLS blueprint</p>
                    <p>{activeEntry.diff.summary}</p>
                  </div>
                )}
                {activeEntry?.diff?.is_match && (
                  <div className="rounded-md border border-success/30 bg-success/12 px-3 py-2 text-xs text-success">
                    Matches the XLS blueprint.
                  </div>
                )}
                {activeEntry?.preview ? (
                  <div className="max-h-[58vh] overflow-y-auto overscroll-contain pr-1">
                    <ExtractionPreviewView preview={activeEntry.preview} />
                  </div>
                ) : (
                  <p className="text-sm text-muted-foreground">
                    No preview yet — send a message to draft the profile.
                  </p>
                )}
              </>
            )}
          </CardContent>
        </Card>
      </div>

      {session?.draft_profile && (
        <Card>
          <CardHeader className="pb-3">
            <CardTitle className="text-sm">Output mapping decisions</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3 text-sm">
            {outputMappingDecisions.length === 0 ? (
              <p className="text-muted-foreground">
                No profile overrides. Every output column inherits its selected output specification mapping.
              </p>
            ) : outputMappingDecisions.map((mapping) => {
              const inherited = selectedOutputSpec?.columns.find(
                (column) => column.header === mapping.output_header,
              );
              return (
                <div key={mapping.output_header} className="rounded-md border p-3">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="font-medium">{mapping.output_header}</span>
                    <Badge variant="secondary">Profile override</Badge>
                  </div>
                  <div className="mt-2 grid gap-2 text-xs md:grid-cols-2">
                    <div>
                      <p className="text-muted-foreground">Output specification</p>
                      <p className="font-mono">{inherited?.source ?? "Unknown column"}</p>
                      <p className="text-muted-foreground">
                        Fallback: <span className="font-mono">{inherited?.fallback || "none"}</span>
                      </p>
                    </div>
                    <div>
                      <p className="text-muted-foreground">Effective mapping</p>
                      <p className="font-mono">{mapping.source}</p>
                      <p className="text-muted-foreground">
                        Fallback: <span className="font-mono">{mapping.fallback || "none"}</span>
                      </p>
                      <p className="text-muted-foreground">
                        Formatting: <span>{mapping.transforms?.length ? mapping.transforms.join(", ") : "none"}</span>
                      </p>
                    </div>
                  </div>
                  {mapping.reason && <p className="mt-2 text-xs text-muted-foreground">{mapping.reason}</p>}
                  <p className="mt-2 text-[11px] text-muted-foreground">
                    To reset this column, ask the assistant to return it to the output specification.
                  </p>
                </div>
              );
            })}
          </CardContent>
        </Card>
      )}

      {session?.discovery && (
        <Card id="evidence-plan" className="scroll-mt-6">
          <CardHeader className="pb-3">
            <CardTitle className="flex items-center gap-2 text-sm">
              Evidence &amp; Plan
              <Badge variant="secondary">{session.inferred_category ?? "unknown"}</Badge>
              <Badge variant={session.validation?.status === "failed" ? "destructive" : "secondary"}>
                {session.validation?.status ?? "review_required"}
              </Badge>
            </CardTitle>
          </CardHeader>
          <CardContent className="grid gap-4 text-xs md:grid-cols-3">
            <div>
              <p className="font-medium">Progress</p>
              <p className="text-muted-foreground">{session.phase}</p>
              <p className="text-muted-foreground">Engine {session.engine_version}</p>
            </div>
            <div>
              <p className="font-medium">Coverage &amp; reconciliation</p>
              <dl className="mt-1 space-y-1 text-muted-foreground">
                {Object.entries(session.validation?.metrics ?? {}).map(([key, value]) => (
                  <div key={key} className="flex justify-between gap-3">
                    <dt>{key.replaceAll("_", " ")}</dt>
                    <dd className="font-medium text-foreground">
                      {typeof value === "object" ? JSON.stringify(value) : String(value)}
                    </dd>
                  </div>
                ))}
                {Object.keys(session.validation?.metrics ?? {}).length === 0 && (
                  <p>No validation metrics yet.</p>
                )}
              </dl>
            </div>
            <div>
              <p className="font-medium">Validation</p>
              {(session.validation?.hard_blockers ?? []).length > 0 && (
                <div className="mb-2 rounded-md border border-destructive/30 bg-destructive/5 p-2 text-destructive">
                  <p className="font-medium">Delivery blockers</p>
                  <ul className="list-disc pl-4">
                    {session.validation?.hard_blockers?.map((risk) => <li key={risk}>{risk}</li>)}
                  </ul>
                </div>
              )}
              {(session.validation?.unresolved_risks ?? []).length === 0
                ? <p className="text-muted-foreground">No unresolved risks recorded.</p>
                : (
                  <ul className="list-disc pl-4 text-muted-foreground">
                    {session.validation?.unresolved_risks?.map((risk) => <li key={risk}>{risk}</li>)}
                  </ul>
                )}
            </div>
            <div className="space-y-2 md:col-span-3">
              <p className="font-medium">Generated extraction plan</p>
              <div className="grid gap-3 rounded-md border p-3 md:grid-cols-3">
                <div><p className="text-muted-foreground">Regions</p><p>{planRegions.map((region) => String(region.name ?? "line items")).join(", ") || "None"}</p></div>
                <div><p className="text-muted-foreground">Row selection</p><p>{String(rowSelector.scope ?? "row")}</p></div>
                <div><p className="text-muted-foreground">Reconciliation</p><p>{reconciliationRules.map((rule) => String(rule.name ?? "rule")).join(", ") || "None"}</p></div>
                <div><p className="text-muted-foreground">Document mappings</p><p>{documentRules.join(", ") || "None"}</p></div>
                <div><p className="text-muted-foreground">Row mappings</p><p>{rowRules.join(", ") || "None"}</p></div>
                <div><p className="text-muted-foreground">Carried context</p><p>{contextRules.map((rule) => Object.keys(asRecord(rule.field_groups)).join(", ")).filter(Boolean).join("; ") || "None"}</p></div>
                <div><p className="text-muted-foreground">Document family</p><p>{String(candidatePlan.document_family ?? session.inferred_category ?? "unknown")}</p></div>
              </div>
            </div>
          </CardContent>
        </Card>
      )}

      {/* ── Finalize bar ───────────────────────────────────── */}
      {canFinalize && (
        <Card>
          <CardContent className="flex flex-wrap items-center gap-3 py-4">
            <div className="flex-1 min-w-[240px]">
              <p className="text-sm font-medium">
                {isEditing ? "Happy with the changes?" : "Happy with the draft?"}
              </p>
              <p className="text-xs text-muted-foreground">
                {isEditing
                  ? `Saves as a new version under the id below. ${editingProfileId}'s folders and training settings carry over; activate the new version when ready.`
                  : "Save it as a draft profile and open the editor to set delivery folders and run training."}
                {(session.validation?.hard_blockers ?? []).length > 0
                  ? " This draft cannot be activated or deliver files until its blockers are resolved."
                  : ""}
              </p>
            </div>
            <Input
              placeholder="profile id (e.g. acme.standard.v1)"
              className="max-w-xs font-mono"
              value={profileId}
              onChange={(e) => setProfileId(e.target.value)}
            />
            <Button onClick={handleFinalize} disabled={finalize.isPending}>
              {finalize.isPending && <Loader2 className="h-4 w-4 animate-spin" />}
              {isEditing ? "Save new version" : "Use this profile"}
            </Button>
          </CardContent>
        </Card>
      )}
    </div>
  );
}
