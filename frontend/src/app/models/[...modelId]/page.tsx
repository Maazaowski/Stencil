"use client";

import { use, useState, useRef, useEffect } from "react";
import { useRouter } from "next/navigation";
import { useQueryClient } from "@tanstack/react-query";
import { useDropzone } from "react-dropzone";
import { ApiError } from "@/lib/api/client";
import {
  useModel,
  useApproveModel,
  useRetireModel,
  useDeleteModel,
  useUpdateModelRules,
  useActivateModel,
  useTestModel,
  useTrainingSet,
  useTrainModel,
  useModelTrainingStatus,
  useCloneModel,
  useUploadTrainingInvoiceToModel,
  useUpdateTrainingSet,
  useModelVersions,
  useRollbackModel,
  useModelPreview,
  type ModelTestResult,
  type TrainingSet,
  type TrainingSetInvoice,
} from "@/hooks/use-models";
import { PdfPreviewPanel } from "@/components/pdf-preview-panel";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";
import { ModelStatusBadge } from "@/components/status-badge";
import { TrainingProgress } from "@/components/training-progress";
import { toast } from "sonner";
import { safeFormatDate } from "@/lib/format-date";
import { modelApiPath, modelDetailPath, parseModelIdParam } from "@/lib/model-routes";
import Link from "next/link";
import {
  ArrowLeft,
  CheckCircle2,
  Archive,
  Hash,
  Cpu,  Clock,
  FileText,
  ChevronDown,
  ChevronRight,
  ExternalLink,
  User,
  Trash2,
  AlertTriangle,
  Play,
  Save,
  Rocket,
  Copy,
  Upload,
  FlaskConical,
  History,
  RotateCcw,
  RefreshCw,
  GitBranch,
  Tags,
  ClipboardCheck,
  FilePlus2,
  Circle,
  Download,
} from "lucide-react";
import { Input } from "@/components/ui/input";
import { useConfirm } from "@/components/ui/confirm-dialog";
import { cn } from "@/lib/utils";

// ── Helper Types (mirror the declarative ExtractionModel schema) ──

interface HeaderFieldRule {
  page?: number;
  label?: string;
  value_position?: string;
  value_pattern?: string | null;
  pattern?: string | null;
  date_format?: string | null;
  occurrence?: string;
  sample_value?: string | null;
  required?: boolean;
}

interface ColumnDef {
  name?: string;
  x0?: number;
  x1?: number;
  header_text?: string | null;
}

interface RegionRule {
  start_anchors?: string[];
  end_anchors?: string[];
  repeat_per_page?: boolean;
  include_unanchored_pages?: boolean;
  page_scope?: number[] | null;
  columns?: ColumnDef[];
}

interface RowMatch {
  row_text?: string | null;
  column?: string | null;
  pattern?: string | null;
  min_cells?: number | null;
  has_amount_in_column?: string | null;
}

interface RowClassifier {
  role?: string;
  where?: RowMatch;
}

interface GroupingRule {
  mode?: string;
  item_role?: string | null;
  start_role?: string | null;
  end_role?: string | null;
  include_end_row?: boolean;
  emit?: string;
  emit_role?: string | null;
}

interface FieldSource {
  rows?: string;
  row_role?: string | null;
  column?: string | null;
  pattern?: string | null;
  join?: string | null;
  occurrence?: string;
}

interface FieldTransform {
  type?: string;
  date_format?: string | null;
  value_map?: Record<string, string>;
  default?: string | null;
}

interface ItemFieldRule {
  name?: string;
  source?: FieldSource | null;
  transform?: FieldTransform;
  literal?: string | null;
  same_as?: string | null;
  required?: boolean;
}

interface SelfCheck {
  check?: string;
  field?: string;
  tolerance?: number;
}

interface ModelJson {
  header_fields?: Record<string, HeaderFieldRule>;
  header_rules?: Record<string, HeaderFieldRule>;
  region?: RegionRule;
  table_rules?: {
    start_marker?: string;
    end_marker?: string | null;
    columns?: Array<{
      name?: string;
      index?: number;
      data_type?: string;
      header_text?: string | null;
      x0?: number;
      x1?: number;
    }>;
  };
  row_classifiers?: RowClassifier[];
  grouping?: GroupingRule;
  item_fields?: ItemFieldRule[];
  totals?: Record<string, HeaderFieldRule>;
  self_checks?: SelfCheck[];
  [key: string]: unknown;
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  const kb = bytes / 1024;
  if (kb < 1024) return `${kb.toFixed(kb < 10 ? 1 : 0)} KB`;
  return `${(kb / 1024).toFixed(1)} MB`;
}

function isLegacyModelJson(modelJson: ModelJson | null | undefined): boolean {
  if (!modelJson) return false;
  return (
    (!!modelJson.header_rules && !modelJson.header_fields) ||
    !!modelJson.table_rules
  );
}

function describeRowMatch(where?: RowMatch): string {
  if (!where) return "—";
  const parts: string[] = [];
  if (where.row_text) parts.push(`row text ~ /${where.row_text}/`);
  if (where.column && where.pattern)
    parts.push(`${where.column} ~ /${where.pattern}/`);
  else if (where.column) parts.push(`column ${where.column}`);
  if (where.min_cells != null) parts.push(`≥ ${where.min_cells} cells`);
  if (where.has_amount_in_column)
    parts.push(`amount in ${where.has_amount_in_column}`);
  return parts.length ? parts.join(" AND ") : "—";
}

function HeaderRuleTable({ rules }: { rules: Record<string, HeaderFieldRule> }) {
  return (
    <div className="rounded-md border overflow-x-auto">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Field</TableHead>
            <TableHead>Page</TableHead>
            <TableHead>Label</TableHead>
            <TableHead>Value Position</TableHead>
            <TableHead>Pattern</TableHead>
            <TableHead>Sample</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {Object.entries(rules).map(([field, rule]) => (
            <TableRow key={field} className="border-b">
              <TableCell className="font-medium">{field}</TableCell>
              <TableCell className="text-muted-foreground">{rule.page ?? 1}</TableCell>
              <TableCell className="text-xs">{rule.label ?? "—"}</TableCell>
              <TableCell>
                <Badge variant="secondary" className="text-xs">
                  {rule.value_position ?? "right"}
                </Badge>
              </TableCell>
              <TableCell>
                {(rule.value_pattern ?? rule.pattern) ? (
                  <code className="text-xs bg-muted px-1.5 py-0.5 rounded font-mono">
                    {rule.value_pattern ?? rule.pattern}
                  </code>
                ) : "—"}
              </TableCell>
              <TableCell className="text-muted-foreground text-xs font-mono">
                {rule.sample_value ?? "—"}
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}

function describeFieldSource(rule: ItemFieldRule): string {
  if (rule.literal != null) return `literal "${rule.literal}"`;
  if (rule.same_as) return `same as ${rule.same_as}`;
  const src = rule.source;
  if (!src) return "—";
  const parts: string[] = [];
  if (src.rows === "role" && src.row_role) parts.push(`rows: ${src.row_role}`);
  else if (src.rows) parts.push(`rows: ${src.rows}`);
  if (src.column) parts.push(`col: ${src.column}`);
  if (src.pattern) parts.push(`/${src.pattern}/`);
  if (src.occurrence && src.occurrence !== "first") parts.push(src.occurrence);
  return parts.join(" · ") || "—";
}

function WorkbenchTable({
  rows,
  group,
  onSetRole,
  busy,
  reviewModelId,
}: {
  rows: TrainingSetInvoice[];
  group: "train" | "holdout";
  onSetRole: (intakeId: string, toRole: "train" | "holdout") => void;
  busy: boolean;
  reviewModelId?: string;
}) {
  const showOutput = !!reviewModelId;
  const colSpan = showOutput ? 5 : 4;
  return (
    <div className="rounded-md border overflow-x-auto">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Invoice</TableHead>
            <TableHead>Pages / Size</TableHead>
            <TableHead>Lines</TableHead>
            <TableHead>Reproduced</TableHead>
            {showOutput && <TableHead>Output</TableHead>}
            <TableHead />
          </TableRow>
        </TableHeader>
        <TableBody>
          {rows.map((inv) => (
            <TableRow key={inv.intake_id} className="border-b align-top">
              <TableCell>
                <Link
                  href={`/invoices/${inv.intake_id}`}
                  className="text-primary hover:underline font-mono text-xs"
                >
                  {inv.filename ?? inv.intake_id}
                </Link>
              </TableCell>
              <TableCell className="text-muted-foreground text-xs whitespace-nowrap">
                {inv.pages != null ? `${inv.pages}p` : "—"}
                {inv.size_bytes != null ? ` · ${formatBytes(inv.size_bytes)}` : ""}
              </TableCell>
              <TableCell className="text-muted-foreground">{inv.line_count ?? "—"}</TableCell>
              <TableCell className="max-w-xs">
                {!inv.trained ? (
                  <span className="inline-flex items-center gap-1 text-muted-foreground text-xs">
                    <Circle className="h-3.5 w-3.5" /> not trained yet
                  </span>
                ) : inv.ok ? (
                  <span className="inline-flex items-center gap-1 text-success text-xs">
                    <CheckCircle2 className="h-3.5 w-3.5" /> exact
                  </span>
                ) : (
                  <span className="inline-flex items-start gap-1 text-warning text-xs">
                    <AlertTriangle className="h-3.5 w-3.5 shrink-0 mt-0.5" />
                    <span>{inv.reason}</span>
                  </span>
                )}
              </TableCell>
              {showOutput && (
                <TableCell className="text-xs">
                  {inv.trained ? (
                    <div className="flex flex-col gap-0.5">
                      <a
                        className="text-primary inline-flex items-center gap-1 hover:underline"
                        href={`/api/v1${modelApiPath(reviewModelId!, `/review/${inv.intake_id}/model_output.xlsx`)}`}
                      >
                        <Download className="h-3 w-3" /> Model output
                      </a>
                      <a
                        className="text-primary inline-flex items-center gap-1 hover:underline"
                        href={`/api/v1${modelApiPath(reviewModelId!, `/review/${inv.intake_id}/ai_output.xlsx`)}`}
                      >
                        <Download className="h-3 w-3" /> AI ground truth
                      </a>
                    </div>
                  ) : (
                    <span className="text-muted-foreground">—</span>
                  )}
                </TableCell>
              )}
              <TableCell className="text-right">
                <Button
                  size="sm"
                  variant="ghost"
                  disabled={busy}
                  onClick={() => onSetRole(inv.intake_id, group === "train" ? "holdout" : "train")}
                >
                  {group === "train" ? "→ Holdout" : "→ Train"}
                </Button>
              </TableCell>
            </TableRow>
          ))}
          {rows.length === 0 && (
            <TableRow>
              <TableCell colSpan={colSpan} className="px-4 py-3 text-muted-foreground text-xs">
                {group === "train"
                  ? "No training invoices yet — upload some above."
                  : "No holdout invoices. Tag one to test whether the model generalizes."}
              </TableCell>
            </TableRow>
          )}
        </TableBody>
      </Table>
    </div>
  );
}

type WizardStepKey = "add" | "tag" | "train" | "review";

interface WizardStepDef {
  key: WizardStepKey;
  label: string;
  icon: typeof FilePlus2;
}

const WIZARD_STEPS: WizardStepDef[] = [
  { key: "add", label: "Add invoices", icon: FilePlus2 },
  { key: "tag", label: "Tag Train / Holdout", icon: Tags },
  { key: "train", label: "Train", icon: Play },
  { key: "review", label: "Review & Approve", icon: ClipboardCheck },
];

function WizardStepper({
  current,
  status,
  onSelect,
}: {
  current: WizardStepKey;
  status: Record<WizardStepKey, "done" | "active" | "todo">;
  onSelect: (key: WizardStepKey) => void;
}) {
  return (
    <div className="flex items-center">
      {WIZARD_STEPS.map((step, i) => {
        const Icon = step.icon;
        const st = status[step.key];
        const isCurrent = step.key === current;
        const done = st === "done";
        return (
          <div key={step.key} className="flex flex-1 items-center last:flex-none">
            <button
              type="button"
              onClick={() => onSelect(step.key)}
              className="group flex items-center gap-2.5 rounded-lg px-2 py-1.5 text-left transition-colors hover:bg-muted/60"
            >
              <span
                className={
                  "flex h-8 w-8 shrink-0 items-center justify-center rounded-sm border text-sm font-semibold transition-colors " +
                  (isCurrent
                    ? "border-primary bg-primary text-primary-foreground"
                    : done
                      ? "border-success/30 bg-success/12 text-white"
                      : "border-muted-foreground/30 bg-background text-muted-foreground")
                }
              >
                {done && !isCurrent ? (
                  <CheckCircle2 className="h-4 w-4" />
                ) : (
                  <Icon className="h-4 w-4" />
                )}
              </span>
              <span className="hidden sm:block">
                <span className="block text-[10px] uppercase tracking-wide text-muted-foreground">
                  Step {i + 1}
                </span>
                <span
                  className={
                    "block text-xs font-medium leading-tight " +
                    (isCurrent ? "text-foreground" : "text-muted-foreground")
                  }
                >
                  {step.label}
                </span>
              </span>
            </button>
            {i < WIZARD_STEPS.length - 1 && (
              <div
                className={
                  "mx-1 h-px flex-1 " +
                  (status[WIZARD_STEPS[i + 1].key] !== "todo" || done
                    ? "bg-primary/40"
                    : "bg-border")
                }
              />
            )}
          </div>
        );
      })}
    </div>
  );
}

// ── Page Component ──────────────────────────────────────────

export default function ModelDetailPage({
  params,
}: {
  params: Promise<{ modelId: string[] }>;
}) {
  const { modelId: modelIdSegments } = use(params);
  const modelId = parseModelIdParam(modelIdSegments);
  const router = useRouter();
  const { data: model, isLoading, isError } = useModel(modelId);
  const modelPreview = useModelPreview(modelId);
  const approveModel = useApproveModel();
  const retireModel = useRetireModel();
  const deleteModel = useDeleteModel();
  const updateRules = useUpdateModelRules();
  const activateModel = useActivateModel();
  const testModel = useTestModel();
  const trainModel = useTrainModel();
  const cloneModel = useCloneModel();
  const uploadToModel = useUploadTrainingInvoiceToModel();
  const updateTrainingSet = useUpdateTrainingSet();
  const rollbackModel = useRollbackModel();
  const isCandidate = model?.status === "candidate" || model?.status === "failed_validation";
  const queryClient = useQueryClient();
  const confirm = useConfirm();

  const [wizardStep, setWizardStep] = useState<WizardStepKey>("add");

  // The model-training run is its OWN pipeline (separate from invoice
  // processing); poll its status while a run is active or just-triggered.
  const [trainingArmed, setTrainingArmed] = useState(false);
  const { data: trainingStatus } = useModelTrainingStatus(modelId, isCandidate, trainingArmed);
  const trainingRun = trainingStatus?.run ?? null;
  const trainingRunning = trainingStatus?.running ?? false;
  const trainingActive = trainingRunning || trainingArmed;

  // The training-set view is now cheap (no model execution), so poll it lightly
  // while a run is active so rows/results update without a manual refresh.
  const { data: trainingSet, refetch: refetchTrainingSet } = useTrainingSet(modelId, {
    pollMs: trainingActive ? 2000 : undefined,
  });
  const { data: versions } = useModelVersions(modelId);

  const prevRunningRef = useRef(false);
  useEffect(() => {
    // Once the worker picks up the run, it's tracked by `running` — disarm.
    if (trainingRunning && trainingArmed) {
      // eslint-disable-next-line react-hooks/set-state-in-effect -- syncing UI state to the external training-run poll
      setTrainingArmed(false);
    }
    // When a run transitions running -> finished, refresh the model + set and
    // jump the operator to the Review step.
    if (prevRunningRef.current && !trainingRunning) {
      refetchTrainingSet();
      setWizardStep("review");
    }
    prevRunningRef.current = trainingRunning;
  }, [trainingRunning, trainingArmed, refetchTrainingSet]);
  // Safety: never stay armed forever if the task never starts.
  useEffect(() => {
    if (!trainingArmed) return;
    const t = setTimeout(() => setTrainingArmed(false), 30_000);
    return () => clearTimeout(t);
  }, [trainingArmed]);

  const [showRawJson, setShowRawJson] = useState(false);
  const [rulesText, setRulesText] = useState<string | null>(null);
  const [testIntakeId, setTestIntakeId] = useState("");
  const [testResult, setTestResult] = useState<ModelTestResult | null>(null);

  async function handleClone() {
    if (!model) return;
    try {
      const clone = await cloneModel.mutateAsync(model.id);
      toast.success("New version started — upload invoices and train, then approve to switch over.");
      router.push(modelDetailPath(clone.id));
    } catch {
      toast.error("Failed to start a new version.");
    }
  }

  async function handleAddFiles(files: File[]) {
    if (!model || files.length === 0) return;
    const count = files.length;
    const key = ["models", model.id, "training-set"];

    // Optimistic "staged" rows so they appear instantly (uploads run in parallel).
    const temps: TrainingSetInvoice[] = files.map((f, i) => ({
      intake_id: `pending-${Date.now()}-${i}`,
      filename: f.name,
      line_count: null,
      pages: null,
      size_bytes: f.size,
      ok: false,
      reason: "Staged — click Train to process",
      staged: true,
      trained: false,
    }));
    const prev = queryClient.getQueryData<TrainingSet>(key);
    if (prev) {
      queryClient.setQueryData<TrainingSet>(key, {
        ...prev,
        training_intake_ids: [...prev.training_intake_ids, ...temps.map((t) => t.intake_id)],
        invoices: [...prev.invoices, ...temps],
      });
    }

    try {
      const results = await Promise.all(
        files.map((file) => uploadToModel.mutateAsync({ modelId: model.id, file, role: "train" })),
      );
      const cloned = results.some((r) => r.cloned);
      const landed = results[results.length - 1]?.model_id ?? model.id;
      toast.success(`Staged ${count} invoice(s). They are processed when you click Train.`);
      if (cloned && landed !== model.id) {
        toast.info("Production stays live — building a new candidate version.");
        router.push(modelDetailPath(landed));
      } else {
        queryClient.invalidateQueries({ queryKey: key });
      }
    } catch {
      toast.error("Upload failed.");
      if (prev) queryClient.setQueryData(key, prev);
    }
  }

  async function handleSetRole(intakeId: string, toRole: "train" | "holdout") {
    if (!model || !trainingSet) return;
    const train = new Set(trainingSet.training_intake_ids);
    const holdout = new Set(trainingSet.holdout_intake_ids);
    train.delete(intakeId);
    holdout.delete(intakeId);
    if (toRole === "holdout") holdout.add(intakeId);
    else train.add(intakeId);
    try {
      const updated = await updateTrainingSet.mutateAsync({
        modelId: model.id,
        trainIntakeIds: [...train],
        holdoutIntakeIds: [...holdout],
      });
      if (updated.id !== model.id) router.push(modelDetailPath(updated.id));
    } catch {
      toast.error("Failed to update the Train/Holdout split.");
    }
  }

  async function handleTrain() {
    if (!model) return;
    try {
      setTrainingArmed(true);
      await trainModel.mutateAsync({ modelId: model.id, addIntakeIds: [] });
      toast.success("Training started — extracting invoices and authoring the model…");
    } catch (e) {
      setTrainingArmed(false);
      const detail =
        e instanceof ApiError ? String((e.body as { detail?: string })?.detail ?? "") : "";
      toast.error(detail || "Failed to start training.");
    }
  }

  async function handleRollback(targetId: string) {
    const ok = await confirm({
      title: "Make this version live again?",
      description:
        "It replaces the current live version, which is kept (retired) so you can roll back again.",
      confirmLabel: "Make live",
    });
    if (!ok) return;
    try {
      await rollbackModel.mutateAsync({ modelId: targetId });
      toast.success("This version is live again.");
      if (targetId !== model?.id) router.push(modelDetailPath(targetId));
    } catch {
      toast.error("Failed to roll back.");
    }
  }

  function parseRules(): Record<string, unknown> | null {
    try {
      return JSON.parse(rulesText ?? "");
    } catch {
      toast.error("Rules are not valid JSON.");
      return null;
    }
  }

  async function handleSaveRules() {
    if (!model) return;
    const parsed = parseRules();
    if (!parsed) return;
    try {
      await updateRules.mutateAsync({ modelId: model.id, modelJson: parsed });
      toast.success("Rules saved.");
    } catch {
      toast.error("Failed to save rules (invalid model schema?).");
    }
  }

  async function handleTest() {
    if (!model) return;
    const intakeId = testIntakeId || model.created_from_intake || "";
    if (!intakeId) {
      toast.error("Enter an invoice (intake) ID to test against.");
      return;
    }
    const parsed = rulesText !== null ? parseRules() : undefined;
    if (rulesText !== null && !parsed) return;
    try {
      const result = await testModel.mutateAsync({
        modelId: model.id,
        intakeId,
        modelJson: parsed ?? undefined,
      });
      setTestResult(result);
      toast.success(result.is_valid ? "Test passed — output matches AI." : "Test ran — see comparison.");
    } catch {
      toast.error("Test failed to run (PDF or baseline missing?).");
    }
  }

  async function handleActivate() {
    if (!model) return;
    const ok = await confirm({
      title: `Activate model ${model.id}?`,
      description:
        "It will be used to extract future invoices of this layout (no AI cost). Make sure you've reviewed its output.",
      confirmLabel: "Activate",
    });
    if (!ok) return;
    try {
      await activateModel.mutateAsync(model.id);
      toast.success("Model activated.");
    } catch {
      toast.error("Failed to activate model.");
    }
  }

  async function handleApprove() {
    if (!model) return;
    try {
      await approveModel.mutateAsync({ modelId: model.id, approvedBy: "admin" });
      toast.success("Model approved.");
    } catch {
      toast.error("Failed to approve model.");
    }
  }

  const { getRootProps, getInputProps, isDragActive, open: openFilePicker } =
    useDropzone({
      accept: { "application/pdf": [".pdf"] },
      multiple: true,
      noClick: true,
      maxSize: 50 * 1024 * 1024,
      disabled: !model || uploadToModel.isPending,
      onDrop: (accepted) => handleAddFiles(accepted),
    });

  async function handleRetire() {
    if (!model) return;
    try {
      await retireModel.mutateAsync(model.id);
      toast.success("Model retired.");
    } catch {
      toast.error("Failed to retire model.");
    }
  }

  async function handleDelete() {
    if (!model) return;
    const ok = await confirm({
      title: `Permanently delete model ${model.id}?`,
      description: "This removes it from the database and filesystem and cannot be undone.",
      confirmLabel: "Delete",
      variant: "destructive",
    });
    if (!ok) return;
    try {
      await deleteModel.mutateAsync(model.id);
      toast.success("Model deleted.");
      router.push("/models");
    } catch {
      toast.error("Failed to delete model.");
    }
  }

  if (isLoading) {
    return (
      <div className="space-y-6 max-w-4xl">
        <Skeleton className="h-8 w-64" />
        <Skeleton className="h-48" />
        <Skeleton className="h-64" />
      </div>
    );
  }

  if (isError || !model) {
    return (
      <div className="space-y-6 max-w-4xl">
        <Link href="/models">
          <Button variant="ghost" size="sm">
            <ArrowLeft className="h-4 w-4 mr-2" />
            Back to Models
          </Button>
        </Link>
        <Card className="border-destructive">
          <CardContent className="pt-6">
            <p className="text-sm text-destructive">
              Model not found or failed to load.
            </p>
          </CardContent>
        </Card>
      </div>
    );
  }

  const modelJson = model.model_json as ModelJson;
  const legacySchema = isLegacyModelJson(modelJson);
  const headerFields =
    modelJson?.header_fields ?? modelJson?.header_rules ?? {};
  const totals = modelJson?.totals ?? {};
  const region = modelJson?.region;
  const tableRules = modelJson?.table_rules;
  const rowClassifiers = modelJson?.row_classifiers ?? [];
  const grouping = modelJson?.grouping;
  const itemFields = modelJson?.item_fields ?? [];
  const selfChecks = modelJson?.self_checks ?? [];

  // ── Wizard step model (derived from the cheap training-set view) ──
  const trainCount = trainingSet?.invoices.length ?? 0;
  const holdoutCount = trainingSet?.holdout.length ?? 0;
  const totalInvoices = trainCount + holdoutCount;
  const hasTrained = !!trainingSet?.last_run;
  const reproducesAll = !!trainingSet?.reproduces_all;
  const isLive = model.status === "approved";
  // While a run is active, always focus the Train step (derived, not stateful).
  const effectiveStep: WizardStepKey = trainingActive ? "train" : wizardStep;
  const wizardStatus: Record<WizardStepKey, "done" | "active" | "todo"> = {
    add: totalInvoices > 0 ? "done" : effectiveStep === "add" ? "active" : "todo",
    tag: effectiveStep === "tag" ? "active" : totalInvoices > 0 ? "done" : "todo",
    train: trainingActive
      ? "active"
      : reproducesAll
        ? "done"
        : effectiveStep === "train"
          ? "active"
          : "todo",
    review: trainingSet?.approvable
      ? "done"
      : effectiveStep === "review"
        ? "active"
        : "todo",
  };

  return (
    <div className="space-y-6 max-w-4xl">
      {/* Header */}
      <div className="flex items-start justify-between gap-4 flex-wrap">
        <div className="flex items-center gap-4">
          <Link href="/models">
            <Button variant="ghost" size="sm">
              <ArrowLeft className="h-4 w-4 mr-2" />
              Back
            </Button>
          </Link>
          <div>
            <div className="flex items-center gap-2">
              <h1 className="text-xl font-bold tracking-tight">
                {model.supplier}
              </h1>
              <ModelStatusBadge status={model.status} />
            </div>
            <p className="text-sm text-muted-foreground mt-0.5 font-mono">
              {model.id}
            </p>
          </div>
        </div>
        <div className="flex flex-wrap gap-2">
          {model.status === "candidate" && (
            <Button
              size="sm"
              onClick={handleApprove}
              disabled={approveModel.isPending || (trainingSet ? !trainingSet.approvable : false)}
              title={
                trainingSet && !trainingSet.approvable
                  ? `Not approvable yet: ${trainingSet.reasons.join("; ")}`
                  : undefined
              }
            >
              <CheckCircle2 className="h-4 w-4" />
              Approve
            </Button>
          )}
          {model.status === "failed_validation" && (
            <Button
              size="sm"
              onClick={handleActivate}
              disabled={activateModel.isPending}
            >
              <Rocket className="h-4 w-4" />
              {activateModel.isPending ? "Activating..." : "Approve & Activate"}
            </Button>
          )}
          {model.status === "approved" && (
            <>
              <Button
                size="sm"
                variant="outline"
                onClick={handleClone}
                disabled={cloneModel.isPending}
                title="Create a candidate copy to retrain on more invoices without taking this one out of production"
              >
                <Copy className="h-4 w-4" />
                {cloneModel.isPending ? "Cloning..." : "Clone to retrain"}
              </Button>
              <Button
                size="sm"
                variant="outline"
                onClick={handleRetire}
                disabled={retireModel.isPending}
              >
                <Archive className="h-4 w-4" />
                Retire
              </Button>
            </>
          )}
          <Button
            size="sm"
            variant="destructive"
            onClick={handleDelete}
            disabled={deleteModel.isPending}
          >
            <Trash2 className="h-4 w-4" />
            {deleteModel.isPending ? "Deleting..." : "Delete"}
          </Button>
        </div>
      </div>

      {legacySchema && (
        <Card className="border-warning/30 bg-warning/12">
          <CardContent className="pt-4 pb-4">
            <div className="flex items-start gap-3">
              <AlertTriangle className="h-5 w-5 text-warning shrink-0 mt-0.5" />
              <div>
                <p className="text-sm font-medium text-warning">
                  Legacy model schema
                </p>
                <p className="text-sm text-warning mt-1">
                  This model uses the older header_rules / table_rules format. You can
                  review or delete it here; to build a new model, start training on the
                  profile and upload sample PDFs.
                </p>
              </div>
            </div>
          </CardContent>
        </Card>
      )}

      {/*
        Summary stats. The "Confidence" card is deliberately gone: the default
        extraction path stamps that value as a constant, so it measured nothing
        while looking like a quality score — and at 0.0% it read as broken.
        Validations below is the real signal.
      */}
      <div className="grid gap-4 md:grid-cols-3">
        <Card>
          <CardContent className="pt-4 pb-4">
            <div className="flex items-center gap-3">
              <Hash className="h-5 w-5 text-muted-foreground" />
              <div>
                <p className="text-xs text-muted-foreground">Times Used</p>
                <p className="text-lg font-bold">{model.times_used}</p>
              </div>
            </div>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="pt-4 pb-4">
            <div className="flex items-center gap-3">
              <CheckCircle2 className="h-5 w-5 text-muted-foreground" />
              <div>
                <p className="text-xs text-muted-foreground">Validations</p>
                <p className="text-lg font-bold">
                  {model.validation_success_count}/{model.validation_attempt_count}
                </p>
              </div>
            </div>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="pt-4 pb-4">
            <div className="flex items-center gap-3">
              <Clock className="h-5 w-5 text-muted-foreground" />
              <div>
                <p className="text-xs text-muted-foreground">Last Used</p>
                <p className="text-sm font-medium">
                  {safeFormatDate(model.last_used_at, "MMM d, yyyy", "Never")}
                </p>
              </div>
            </div>
          </CardContent>
        </Card>
      </div>

      {/* Failure banner */}
      {model.status === "failed_validation" && model.last_validation_error && (
        <Card className="border-destructive/30 bg-destructive/12">
          <CardContent className="pt-4 pb-4">
            <div className="flex items-start gap-3">
              <AlertTriangle className="h-5 w-5 text-destructive shrink-0 mt-0.5" />
              <div>
                <p className="text-sm font-medium text-destructive">
                  This model failed automatic validation
                </p>
                <p className="text-sm text-destructive mt-1">{model.last_validation_error}</p>
                <p className="text-xs text-destructive mt-2">
                  Review the AI vs model output below, tweak the rules, re-test, and Approve &amp;
                  Activate if it looks right. Review files are in{" "}
                  <span className="font-mono">
                    /data/completed/{model.created_from_intake ?? "{intake}"}/model_review/
                  </span>
                </p>
              </div>
            </div>
          </CardContent>
        </Card>
      )}

      <Tabs defaultValue={isCandidate ? "training" : "overview"}>
        <TabsList>
          <TabsTrigger value="overview">Overview</TabsTrigger>
          <TabsTrigger value="preview">
            <FileText className="h-4 w-4" />
            Preview Output
          </TabsTrigger>
          <TabsTrigger value="training">
            <FlaskConical className="h-4 w-4" />
            Training
          </TabsTrigger>
          <TabsTrigger value="versions">
            <History className="h-4 w-4" />
            Versions
          </TabsTrigger>
        </TabsList>

        {/* ─────────────── Training workbench ─────────────── */}
        <TabsContent value="training" className="space-y-6">
          {isLive && (
            <Card className="border-primary/30 bg-primary/12">
              <CardContent className="pt-4 pb-4 flex items-start gap-3">
                <GitBranch className="h-5 w-5 text-primary shrink-0 mt-0.5" />
                <div className="text-sm">
                  <p className="font-medium text-primary">
                    This version is live in production
                  </p>
                  <p className="text-primary mt-1">
                    Adding invoices or changing the split starts a new candidate version
                    automatically — production keeps running on the live version until you approve
                    the new one.
                  </p>
                  <Button
                    size="sm"
                    variant="outline"
                    className="mt-2"
                    onClick={handleClone}
                    disabled={cloneModel.isPending}
                  >
                    <Copy className="h-4 w-4" />
                    {cloneModel.isPending ? "Starting…" : "Start new version now"}
                  </Button>
                </div>
              </CardContent>
            </Card>
          )}

          {/* Guided stepper */}
          <Card>
            <CardContent className="py-3">
              <WizardStepper
                current={effectiveStep}
                status={wizardStatus}
                onSelect={setWizardStep}
              />
            </CardContent>
          </Card>

          {/* Step 1 — Add invoices */}
          {effectiveStep === "add" && (
            <Card>
              <CardHeader className="pb-3">
                <CardTitle className="text-sm font-medium">Add sample invoices</CardTitle>
              </CardHeader>
              <CardContent className="space-y-4">
                <p className="text-xs text-muted-foreground">
                  Drop the supplier&apos;s PDFs here. They are staged as training data — extracted
                  for ground truth when you click Train, and never delivered to production. You will
                  split them into Train and Holdout in the next step.
                </p>
                <div
                  {...getRootProps()}
                  className={cn(
                    "flex flex-col items-center justify-center gap-2 rounded-xl border-2 border-dashed px-6 py-10 text-center transition-colors",
                    isDragActive
                      ? "border-primary bg-primary/5"
                      : "border-muted-foreground/25 hover:border-primary/40 hover:bg-muted/30",
                  )}
                >
                  <input {...getInputProps()} />
                  <span className="flex h-12 w-12 items-center justify-center rounded-sm bg-primary/10 text-primary">
                    <Upload className="h-6 w-6" />
                  </span>
                  <p className="text-sm font-medium">
                    {isDragActive ? "Drop to stage these PDFs" : "Drag & drop invoice PDFs"}
                  </p>
                  <p className="text-xs text-muted-foreground">
                    or{" "}
                    <button
                      type="button"
                      className="text-primary underline underline-offset-2"
                      onClick={openFilePicker}
                      disabled={uploadToModel.isPending}
                    >
                      browse your files
                    </button>
                  </p>
                  {uploadToModel.isPending && (
                    <p className="text-xs text-primary inline-flex items-center gap-1">
                      <RefreshCw className="h-3.5 w-3.5 animate-spin" /> Staging…
                    </p>
                  )}
                </div>
                <div className="flex items-center justify-between">
                  <span className="text-xs text-muted-foreground">
                    {totalInvoices > 0
                      ? `${totalInvoices} invoice(s) staged (${trainCount} train · ${holdoutCount} holdout)`
                      : "No invoices yet"}
                  </span>
                  <Button
                    size="sm"
                    onClick={() => setWizardStep("tag")}
                    disabled={totalInvoices === 0}
                  >
                    Continue to tagging
                    <ChevronRight className="h-4 w-4" />
                  </Button>
                </div>
                {trainingSet && totalInvoices > 0 && (
                  <div className="space-y-4">
                    <div>
                      <p className="text-xs font-medium mb-1">
                        Training invoices{" "}
                        <span className="text-muted-foreground">({trainCount})</span>
                      </p>
                      <WorkbenchTable
                        rows={trainingSet.invoices}
                        group="train"
                        onSetRole={handleSetRole}
                        busy={updateTrainingSet.isPending}
                      />
                    </div>
                    <div>
                      <p className="text-xs font-medium mb-1">
                        Holdout invoices{" "}
                        <span className="text-muted-foreground">({holdoutCount})</span>
                      </p>
                      <WorkbenchTable
                        rows={trainingSet.holdout}
                        group="holdout"
                        onSetRole={handleSetRole}
                        busy={updateTrainingSet.isPending}
                      />
                    </div>
                  </div>
                )}
              </CardContent>
            </Card>
          )}

          {/* Step 2 — Tag Train / Holdout */}
          {effectiveStep === "tag" && trainingSet && (
            <Card>
              <CardHeader className="pb-3">
                <CardTitle className="text-sm font-medium">Tag Train / Holdout</CardTitle>
              </CardHeader>
              <CardContent className="space-y-4">
                <p className="text-xs text-muted-foreground">
                  <strong>Train</strong> invoices author the model (it must reproduce them exactly).{" "}
                  <strong>Holdout</strong> invoices are kept back to check the model generalizes to
                  invoices it never learned from. Transfers apply instantly.
                </p>
                <div>
                  <p className="text-xs font-medium mb-1">
                    Training invoices{" "}
                    <span className="text-muted-foreground">({trainCount})</span>
                  </p>
                  <WorkbenchTable
                    rows={trainingSet.invoices}
                    group="train"
                    onSetRole={handleSetRole}
                    busy={updateTrainingSet.isPending}
                  />
                </div>
                <div>
                  <p className="text-xs font-medium mb-1">
                    Holdout invoices{" "}
                    <span className="text-muted-foreground">({holdoutCount})</span>
                  </p>
                  <WorkbenchTable
                    rows={trainingSet.holdout}
                    group="holdout"
                    onSetRole={handleSetRole}
                    busy={updateTrainingSet.isPending}
                  />
                </div>
                <div className="flex items-center justify-between">
                  <Button size="sm" variant="ghost" onClick={() => setWizardStep("add")}>
                    <ArrowLeft className="h-4 w-4" />
                    Add more
                  </Button>
                  <Button
                    size="sm"
                    onClick={() => setWizardStep("train")}
                    disabled={trainCount === 0}
                    title={trainCount === 0 ? "Tag at least one Train invoice" : undefined}
                  >
                    Continue to training
                    <ChevronRight className="h-4 w-4" />
                  </Button>
                </div>
              </CardContent>
            </Card>
          )}

          {/* Step 3 — Train (live progress) */}
          {effectiveStep === "train" && trainingSet && (
            <Card>
              <CardHeader className="pb-3">
                <div className="flex items-center justify-between gap-2 flex-wrap">
                  <CardTitle className="text-sm font-medium">
                    Train the model{" "}
                    <span className="text-muted-foreground font-normal">
                      ({trainCount} training invoice{trainCount === 1 ? "" : "s"}
                      {trainCount < trainingSet.required_count
                        ? `, ${trainingSet.required_count} required`
                        : ""}
                      )
                    </span>
                  </CardTitle>
                  <Button
                    size="sm"
                    onClick={handleTrain}
                    disabled={
                      trainModel.isPending || trainingActive || isLive || trainCount === 0
                    }
                    title={isLive ? "Start a new version first" : undefined}
                  >
                    <Play className={cn("h-4 w-4", trainingActive && "animate-pulse")} />
                    {trainingActive ? "Training…" : hasTrained ? "Re-train" : "Start training"}
                  </Button>
                </div>
              </CardHeader>
              <CardContent className="space-y-4">
                {trainingActive || trainingRun ? (
                  <TrainingProgress
                    run={trainingRun}
                    running={trainingRunning}
                    armed={trainingArmed}
                    invoices={trainingSet.invoices}
                  />
                ) : (
                  <div className="rounded-lg border border-dashed p-6 text-center text-sm text-muted-foreground">
                    <Play className="mx-auto mb-2 h-6 w-6 text-muted-foreground/60" />
                    {trainCount === 0
                      ? "Tag at least one Train invoice, then start training."
                      : isLive
                        ? "This version is live — start a new version to retrain."
                        : `Ready to train on ${trainCount} invoice(s). Extraction, authoring and validation run as one tracked run.`}
                  </div>
                )}
                {!trainingActive && hasTrained && (
                  <div className="flex justify-end">
                    <Button size="sm" variant="outline" onClick={() => setWizardStep("review")}>
                      Review results
                      <ChevronRight className="h-4 w-4" />
                    </Button>
                  </div>
                )}
              </CardContent>
            </Card>
          )}

          {/* Step 4 — Review & Approve */}
          {effectiveStep === "review" && trainingSet && (
            <Card>
              <CardHeader className="pb-3">
                <div className="flex items-center justify-between gap-2 flex-wrap">
                  <CardTitle className="text-sm font-medium">Review &amp; approve</CardTitle>
                  {isCandidate && (
                    <Button
                      size="sm"
                      onClick={handleApprove}
                      disabled={approveModel.isPending || !trainingSet.approvable}
                      title={
                        !trainingSet.approvable
                          ? `Not approvable yet: ${trainingSet.reasons.join("; ")}`
                          : undefined
                      }
                    >
                      <CheckCircle2 className="h-4 w-4" />
                      Approve
                    </Button>
                  )}
                </div>
              </CardHeader>
              <CardContent className="space-y-4">
                {!hasTrained ? (
                  <div className="rounded-md border border-dashed p-3 text-sm flex items-start gap-2 text-muted-foreground">
                    <AlertTriangle className="h-4 w-4 shrink-0 mt-0.5" />
                    <span>Not trained yet — go back to the Train step and start a run.</span>
                  </div>
                ) : (
                  <div
                    className={cn(
                      "rounded-md border p-3 text-sm flex items-start gap-2",
                      trainingSet.approvable
                        ? "border-success/30 bg-success/12"
                        : "border-warning/30 bg-warning/12",
                    )}
                  >
                    {trainingSet.approvable ? (
                      <CheckCircle2 className="h-4 w-4 text-success shrink-0 mt-0.5" />
                    ) : (
                      <AlertTriangle className="h-4 w-4 text-warning shrink-0 mt-0.5" />
                    )}
                    <span>
                      {trainingSet.approvable
                        ? "Reproduces every training invoice and meets the minimum count — ready to approve."
                        : `Not approvable yet: ${trainingSet.reasons.join("; ")}.`}
                    </span>
                  </div>
                )}

                {trainingSet.warnings.length > 0 && (
                  <div className="rounded-md border border-warning/30 bg-warning/12 p-3 text-sm flex items-start gap-2">
                    <AlertTriangle className="h-4 w-4 text-warning shrink-0 mt-0.5" />
                    <span>
                      Holdout warning: {trainingSet.warnings.join("; ")}. You can still approve, but
                      generalization to unseen invoices is unproven.
                    </span>
                  </div>
                )}

                <div>
                  <p className="text-xs font-medium mb-1">
                    Training invoices{" "}
                    <span className="text-muted-foreground">(must all reproduce exactly)</span>
                  </p>
                  <p className="text-[11px] text-muted-foreground mb-1">
                    Download each invoice&apos;s <strong>Model output</strong> and{" "}
                    <strong>AI ground truth</strong> to see exactly where they differ.
                  </p>
                  <WorkbenchTable
                    rows={trainingSet.invoices}
                    group="train"
                    onSetRole={handleSetRole}
                    busy={updateTrainingSet.isPending}
                    reviewModelId={model.id}
                  />
                </div>
                {holdoutCount > 0 && (
                  <div>
                    <p className="text-xs font-medium mb-1">
                      Holdout invoices{" "}
                      <span className="text-muted-foreground">(generalization signal)</span>
                    </p>
                    <WorkbenchTable
                      rows={trainingSet.holdout}
                      group="holdout"
                      onSetRole={handleSetRole}
                      busy={updateTrainingSet.isPending}
                    />
                  </div>
                )}

                <div className="flex justify-start">
                  <Button size="sm" variant="ghost" onClick={() => setWizardStep("train")}>
                    <ArrowLeft className="h-4 w-4" />
                    Back to training
                  </Button>
                </div>
              </CardContent>
            </Card>
          )}

          {/* Advanced: review & edit raw rules (failed/candidate models) */}
          {effectiveStep === "review" &&
            (model.status === "failed_validation" || model.status === "candidate") && (
            <Card>
              <CardHeader className="pb-3">
                <CardTitle className="text-sm font-medium">Advanced: edit rules</CardTitle>
              </CardHeader>
              <CardContent className="space-y-4">
                <div>
                  <p className="text-xs text-muted-foreground mb-1">
                    Model rules (JSON) — edit and Save, then re-test against an invoice.
                  </p>
                  <textarea
                    className="w-full h-72 font-mono text-xs border rounded-md p-3 bg-muted/30"
                    value={rulesText ?? JSON.stringify(model.model_json, null, 2)}
                    onChange={(e) => setRulesText(e.target.value)}
                    spellCheck={false}
                  />
                </div>
                <div className="flex flex-wrap items-center gap-2">
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={handleSaveRules}
                    disabled={updateRules.isPending || rulesText === null}
                  >
                    <Save className="h-4 w-4" />
                    {updateRules.isPending ? "Saving..." : "Save rules"}
                  </Button>
                  <Input
                    className="w-56"
                    placeholder={model.created_from_intake ?? "intake id to test against"}
                    value={testIntakeId}
                    onChange={(e) => setTestIntakeId(e.target.value)}
                  />
                  <Button size="sm" onClick={handleTest} disabled={testModel.isPending}>
                    <Play className="h-4 w-4" />
                    {testModel.isPending ? "Testing..." : "Run test"}
                  </Button>
                </div>

                {testResult && (
                  <div className="rounded-md border p-3 text-sm space-y-2">
                    <div className="flex items-center gap-2">
                      {testResult.is_valid ? (
                        <CheckCircle2 className="h-4 w-4 text-success" />
                      ) : (
                        <AlertTriangle className="h-4 w-4 text-warning" />
                      )}
                      <span className="font-medium">
                        {testResult.is_valid === true
                          ? "Output matches AI baseline"
                          : testResult.is_valid === false
                            ? "Output does not match AI baseline"
                            : "Tested (no baseline to compare)"}
                      </span>
                    </div>
                    <p className="text-xs text-muted-foreground">{testResult.reason}</p>
                    {model.created_from_intake && (
                      <div className="flex gap-3 text-xs">
                        <a
                          className="text-primary underline"
                          href={`/api/v1${modelApiPath(model.id, `/review/${model.created_from_intake}/test_latest.xlsx`)}`}
                        >
                          Download model output (XLSX)
                        </a>
                        <a
                          className="text-primary underline"
                          href={`/api/v1${modelApiPath(model.id, `/review/${model.created_from_intake}/ai_baseline.xlsx`)}`}
                        >
                          Download AI baseline (XLSX)
                        </a>
                      </div>
                    )}
                  </div>
                )}
              </CardContent>
            </Card>
          )}
        </TabsContent>

        {/* ─────────────── Version history ─────────────── */}
        <TabsContent value="versions" className="space-y-6">
          <Card>
            <CardHeader className="pb-3">
              <CardTitle className="text-sm font-medium">Version history</CardTitle>
            </CardHeader>
            <CardContent>
              <p className="text-xs text-muted-foreground mb-3">
                Every version of this model. The live version extracts production invoices; retired
                versions are kept so you can roll back with one click.
              </p>
              <div className="rounded-md border overflow-x-auto">
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Version</TableHead>
                      <TableHead>Status</TableHead>
                      <TableHead>Train / Holdout</TableHead>
                      <TableHead>Validations</TableHead>
                      <TableHead>Approved</TableHead>
                      <TableHead />
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {(versions?.versions ?? []).map((v) => (
                      <TableRow
                        key={v.model_id}
                        className={`border-b ${v.model_id === model.id ? "bg-muted/30" : ""}`}
                      >
                        <TableCell className="font-medium">v{v.version}</TableCell>
                        <TableCell>
                          <div className="flex items-center gap-2">
                            <ModelStatusBadge status={v.status} />
                            {v.is_live && (
                              <Badge className="bg-success/12 text-white text-xs hover:bg-success/12">
                                Live
                              </Badge>
                            )}
                          </div>
                        </TableCell>
                        <TableCell className="text-muted-foreground text-xs">
                          {v.training_invoice_count} / {v.holdout_invoice_count}
                        </TableCell>
                        <TableCell className="text-muted-foreground text-xs">
                          {v.validation_success_count}/{v.validation_attempt_count}
                        </TableCell>
                        <TableCell className="text-muted-foreground text-xs">
                          {v.approved_at
                            ? safeFormatDate(v.approved_at, "MMM d, yyyy")
                            : "—"}
                        </TableCell>
                        <TableCell className="text-right">
                          <div className="flex justify-end gap-2">
                            {v.model_id !== model.id && (
                              <Link href={modelDetailPath(v.model_id)}>
                                <Button size="sm" variant="ghost">
                                  Open
                                </Button>
                              </Link>
                            )}
                            {v.status === "retired" && (
                              <Button
                                size="sm"
                                variant="outline"
                                onClick={() => handleRollback(v.model_id)}
                                disabled={rollbackModel.isPending}
                              >
                                <RotateCcw className="h-4 w-4" />
                                Make live
                              </Button>
                            )}
                          </div>
                        </TableCell>
                      </TableRow>
                    ))}
                    {(!versions || versions.versions.length === 0) && (
                      <TableRow>
                        <TableCell colSpan={6} className="px-4 py-3 text-muted-foreground text-xs">
                          No versions found.
                        </TableCell>
                      </TableRow>
                    )}
                  </TableBody>
                </Table>
              </div>
            </CardContent>
          </Card>
        </TabsContent>

        {/* ─────────────── Preview Output ─────────────── */}
        <TabsContent value="preview" className="space-y-6">
          <Card>
            <CardHeader className="pb-3">
              <CardTitle className="text-sm font-medium">Try an invoice</CardTitle>
            </CardHeader>
            <CardContent>
              <PdfPreviewPanel
                description="Upload a new invoice to see how this model extracts it (deterministic replay, no AI call)."
                buttonLabel="Upload an invoice"
                runningLabel="Running model…"
                onPreview={(file) => modelPreview.mutateAsync(file)}
              />
            </CardContent>
          </Card>
        </TabsContent>

        {/* ─────────────── Overview ─────────────── */}
        <TabsContent value="overview" className="space-y-6">

      {/* Metadata */}
      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-sm font-medium">Model Information</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="grid grid-cols-2 md:grid-cols-3 gap-4 text-sm">
            <div>
              <p className="text-xs text-muted-foreground">Output Type</p>
              <Badge variant="secondary" className="mt-1">{model.output_type}</Badge>
            </div>
            <div>
              <p className="text-xs text-muted-foreground">Version</p>
              <p className="font-medium">v{model.version}</p>
            </div>
            <div>
              <p className="text-xs text-muted-foreground">Created By</p>
              <div className="flex items-center gap-1 mt-0.5">
                <Cpu className="h-3.5 w-3.5 text-muted-foreground" />
                <span>{model.created_by}</span>
              </div>
            </div>
            {model.approved_by && (
              <div>
                <p className="text-xs text-muted-foreground">Approved By</p>
                <div className="flex items-center gap-1 mt-0.5">
                  <User className="h-3.5 w-3.5 text-muted-foreground" />
                  <span>{model.approved_by}</span>
                </div>
              </div>
            )}
            {model.approved_at && (
              <div>
                <p className="text-xs text-muted-foreground">Approved At</p>
                <p>{safeFormatDate(model.approved_at, "MMM d, yyyy HH:mm")}</p>
              </div>
            )}
            {model.updated_by && (
              <div>
                <p className="text-xs text-muted-foreground">Updated By</p>
                <div className="flex items-center gap-1 mt-0.5">
                  <User className="h-3.5 w-3.5 text-muted-foreground" />
                  <span>{model.updated_by}</span>
                </div>
              </div>
            )}
            {model.retired_by && (
              <div>
                <p className="text-xs text-muted-foreground">Retired By</p>
                <div className="flex items-center gap-1 mt-0.5">
                  <User className="h-3.5 w-3.5 text-muted-foreground" />
                  <span>{model.retired_by}</span>
                </div>
              </div>
            )}
            {model.retired_at && (
              <div>
                <p className="text-xs text-muted-foreground">Retired At</p>
                <p>{safeFormatDate(model.retired_at, "MMM d, yyyy HH:mm")}</p>
              </div>
            )}
            <div>
              <p className="text-xs text-muted-foreground">Created</p>
              <p>{safeFormatDate(model.created_at, "MMM d, yyyy HH:mm")}</p>
            </div>
            {model.supplier_profile_id && (
              <div>
                <p className="text-xs text-muted-foreground">Supplier Profile</p>
                <Link
                  href={`/profiles/${model.supplier_profile_id}`}
                  className="font-mono text-xs text-primary hover:underline inline-flex items-center gap-1 mt-0.5"
                >
                  {model.supplier_profile_id}
                  <ExternalLink className="h-3 w-3" />
                </Link>
              </div>
            )}
            <div>
              <p className="text-xs text-muted-foreground">Validation Failures</p>
              <p className="font-medium">{model.validation_failure_count}</p>
            </div>
            <div className="col-span-2 md:col-span-3">
              <p className="text-xs text-muted-foreground">Layout Fingerprint</p>
              <p className="font-mono text-xs mt-0.5 break-all">{model.layout_fingerprint}</p>
            </div>
            {model.layout_family_key && (
              <div className="col-span-2 md:col-span-3">
                <p className="text-xs text-muted-foreground">Layout Family Key</p>
                <p className="font-mono text-xs mt-0.5 break-all">{model.layout_family_key}</p>
              </div>
            )}
            {model.last_validation_error && (
              <div className="col-span-2 md:col-span-3">
                <p className="text-xs text-muted-foreground">Last Validation Error</p>
                <p className="text-sm text-destructive mt-0.5">{model.last_validation_error}</p>
              </div>
            )}
            {model.created_from_intake && (
              <div className="col-span-2 md:col-span-3">
                <p className="text-xs text-muted-foreground">Source Intake</p>
                <Link
                  href={`/invoices/${model.created_from_intake}`}
                  className="font-mono text-xs text-primary hover:underline inline-flex items-center gap-1"
                >
                  {model.created_from_intake}
                  <ExternalLink className="h-3 w-3" />
                </Link>
              </div>
            )}
            {model.notes && (
              <div className="col-span-2 md:col-span-3">
                <p className="text-xs text-muted-foreground">Notes</p>
                <p className="text-sm mt-0.5">{model.notes}</p>
              </div>
            )}
          </div>
        </CardContent>
      </Card>

      {/* Header Fields */}
      {Object.keys(headerFields).length > 0 && (
        <Card>
          <CardHeader className="pb-3">
            <CardTitle className="text-sm font-medium">
              {legacySchema ? "Header Rules" : "Header Fields"}
            </CardTitle>
          </CardHeader>
          <CardContent>
            <HeaderRuleTable rules={headerFields} />
          </CardContent>
        </Card>
      )}

      {/* Legacy table rules */}
      {tableRules && (
        <Card>
          <CardHeader className="pb-3">
            <CardTitle className="text-sm font-medium">Table Rules (legacy)</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="flex flex-wrap gap-4 text-sm">
              {tableRules.start_marker && (
                <div>
                  <span className="text-xs text-muted-foreground">Start marker: </span>
                  <code className="text-xs bg-muted px-1.5 py-0.5 rounded font-mono">
                    {tableRules.start_marker}
                  </code>
                </div>
              )}
              {tableRules.end_marker && (
                <div>
                  <span className="text-xs text-muted-foreground">End marker: </span>
                  <code className="text-xs bg-muted px-1.5 py-0.5 rounded font-mono">
                    {tableRules.end_marker}
                  </code>
                </div>
              )}
            </div>
            {tableRules.columns && tableRules.columns.length > 0 && (
              <div className="rounded-md border overflow-x-auto">
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Column</TableHead>
                      <TableHead>Index</TableHead>
                      <TableHead>Type</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {tableRules.columns.map((col, idx) => (
                      <TableRow key={idx} className="border-b">
                        <TableCell className="font-medium">{col.name ?? "—"}</TableCell>
                        <TableCell className="text-muted-foreground">{col.index ?? "—"}</TableCell>
                        <TableCell>
                          <Badge variant="outline" className="text-xs">
                            {col.data_type ?? "string"}
                          </Badge>
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </div>
            )}
          </CardContent>
        </Card>
      )}

      {/* Line Item Region */}
      {region && (
        <Card>
          <CardHeader className="pb-3">
            <CardTitle className="text-sm font-medium">Line Item Region</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="flex flex-wrap gap-4 text-sm">
              {(region.start_anchors?.length ?? 0) > 0 && (
                <div>
                  <span className="text-xs text-muted-foreground">Starts at: </span>
                  {region.start_anchors!.map((a) => (
                    <code key={a} className="text-xs bg-muted px-1.5 py-0.5 rounded font-mono mr-1">{a}</code>
                  ))}
                </div>
              )}
              {(region.end_anchors?.length ?? 0) > 0 && (
                <div>
                  <span className="text-xs text-muted-foreground">Ends at: </span>
                  {region.end_anchors!.map((a) => (
                    <code key={a} className="text-xs bg-muted px-1.5 py-0.5 rounded font-mono mr-1">{a}</code>
                  ))}
                </div>
              )}
              {region.page_scope && (
                <div>
                  <span className="text-xs text-muted-foreground">Pages: </span>
                  <span className="text-xs">{region.page_scope.join(", ")}</span>
                </div>
              )}
            </div>

            {region.columns && region.columns.length > 0 && (
              <div className="rounded-md border overflow-x-auto">
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Column</TableHead>
                      <TableHead>Header Text</TableHead>
                      <TableHead>X Range (0–1000)</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {region.columns.map((col, idx) => (
                      <TableRow key={idx} className="border-b">
                        <TableCell className="font-medium">{col.name ?? "—"}</TableCell>
                        <TableCell className="text-muted-foreground text-xs">{col.header_text ?? "—"}</TableCell>
                        <TableCell className="font-mono text-xs">
                          {col.x0 != null && col.x1 != null
                            ? `${Math.round(col.x0)} – ${Math.round(col.x1)}`
                            : "—"}
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </div>
            )}
          </CardContent>
        </Card>
      )}

      {/* Row Classifiers + Grouping */}
      {(rowClassifiers.length > 0 || grouping) && (
        <Card>
          <CardHeader className="pb-3">
            <CardTitle className="text-sm font-medium">Row Classification &amp; Grouping</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            {rowClassifiers.length > 0 && (
              <div className="rounded-md border overflow-x-auto">
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Role</TableHead>
                      <TableHead>When</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {rowClassifiers.map((rc, idx) => (
                      <TableRow key={idx} className="border-b">
                        <TableCell>
                          <Badge variant="secondary" className="text-xs">{rc.role}</Badge>
                        </TableCell>
                        <TableCell className="font-mono text-xs text-muted-foreground">
                          {describeRowMatch(rc.where)}
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </div>
            )}
            {grouping && (
              <div className="flex flex-wrap gap-4 text-sm">
                <div>
                  <p className="text-xs text-muted-foreground">Mode</p>
                  <Badge variant="outline" className="text-xs mt-0.5">{grouping.mode}</Badge>
                </div>
                {grouping.item_role && (
                  <div>
                    <p className="text-xs text-muted-foreground">Item Role</p>
                    <p className="font-mono text-xs mt-1">{grouping.item_role}</p>
                  </div>
                )}
                {grouping.start_role && (
                  <div>
                    <p className="text-xs text-muted-foreground">Group Starts At</p>
                    <p className="font-mono text-xs mt-1">{grouping.start_role}</p>
                  </div>
                )}
                {grouping.end_role && (
                  <div>
                    <p className="text-xs text-muted-foreground">Group Ends At</p>
                    <p className="font-mono text-xs mt-1">{grouping.end_role}</p>
                  </div>
                )}
                <div>
                  <p className="text-xs text-muted-foreground">Emit</p>
                  <p className="font-mono text-xs mt-1">
                    {grouping.emit}
                    {grouping.emit_role ? ` (${grouping.emit_role})` : ""}
                  </p>
                </div>
              </div>
            )}
          </CardContent>
        </Card>
      )}

      {/* Item Fields */}
      {itemFields.length > 0 && (
        <Card>
          <CardHeader className="pb-3">
            <CardTitle className="text-sm font-medium">Line Item Fields</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="rounded-md border overflow-x-auto">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Field</TableHead>
                    <TableHead>Source</TableHead>
                    <TableHead>Type</TableHead>
                    <TableHead>Value Map</TableHead>
                    <TableHead>Required</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {itemFields.map((field, idx) => (
                    <TableRow key={idx} className="border-b">
                      <TableCell className="font-medium">{field.name}</TableCell>
                      <TableCell className="font-mono text-xs text-muted-foreground">
                        {describeFieldSource(field)}
                      </TableCell>
                      <TableCell>
                        <Badge variant="outline" className="text-xs">
                          {field.transform?.type ?? "string"}
                        </Badge>
                      </TableCell>
                      <TableCell>
                        {field.transform?.value_map &&
                        Object.keys(field.transform.value_map).length > 0 ? (
                          <div className="flex flex-wrap gap-1">
                            {Object.entries(field.transform.value_map).map(([k, v]) => (
                              <span key={k} className="text-xs bg-muted px-1.5 py-0.5 rounded">
                                {k} → {v}
                              </span>
                            ))}
                          </div>
                        ) : "—"}
                      </TableCell>
                      <TableCell className="text-xs text-muted-foreground">
                        {field.required ? "Yes" : "No"}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
          </CardContent>
        </Card>
      )}

      {/* Totals */}
      {Object.keys(totals).length > 0 && (
        <Card>
          <CardHeader className="pb-3">
            <CardTitle className="text-sm font-medium">Totals</CardTitle>
          </CardHeader>
          <CardContent>
            <HeaderRuleTable rules={totals} />
          </CardContent>
        </Card>
      )}

      {/* Self Checks */}
      {selfChecks.length > 0 && (
        <Card>
          <CardHeader className="pb-3">
            <CardTitle className="text-sm font-medium">Self Checks</CardTitle>
          </CardHeader>
          <CardContent>
            <ul className="space-y-1 text-sm">
              {selfChecks.map((check, idx) => (
                <li key={idx} className="flex items-center gap-2">
                  <CheckCircle2 className="h-3.5 w-3.5 text-muted-foreground" />
                  <span className="font-mono text-xs">
                    {check.check ?? "sum_of_items_equals"} → {check.field ?? "subtotal"}
                    {check.tolerance != null && ` (±${check.tolerance})`}
                  </span>
                </li>
              ))}
            </ul>
          </CardContent>
        </Card>
      )}

      {/* Raw JSON */}
      <Card>
        <CardHeader className="pb-3">
          <button
            className="flex items-center gap-2 w-full text-left"
            onClick={() => setShowRawJson(!showRawJson)}
          >
            <CardTitle className="text-sm font-medium flex items-center gap-2">
              <FileText className="h-4 w-4" />
              Raw Model JSON
              {showRawJson ? (
                <ChevronDown className="h-4 w-4" />
              ) : (
                <ChevronRight className="h-4 w-4" />
              )}
            </CardTitle>
          </button>
        </CardHeader>
        {showRawJson && (
          <CardContent>
            <pre className="text-xs font-mono whitespace-pre-wrap overflow-auto max-h-96 bg-muted p-4 rounded-lg">
              {JSON.stringify(model.model_json, null, 2)}
            </pre>
          </CardContent>
        )}
      </Card>
        </TabsContent>
      </Tabs>
    </div>
  );
}
