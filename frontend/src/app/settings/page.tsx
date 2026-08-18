"use client";

import { useState, useEffect } from "react";
import {
  useSettings,
  useUpdateSettings,
  useSetProviderKey,
  useClearProviderKey,
  type SettingsUpdate,
} from "@/hooks/use-settings";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { PageHeader } from "@/components/page-header";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { toast } from "sonner";
import {
  Brain,
  Gauge,
  FolderOpen,
  Eye,
  Save,
  RotateCcw,
  CheckCircle2,
  XCircle,
  Trash2,
  KeyRound,
} from "lucide-react";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { usePurgeAllInvoices } from "@/hooks/use-invoices";

const PROVIDER_LABELS: Record<string, string> = {
  openai: "OpenAI (GPT)",
  anthropic: "Anthropic (Claude)",
};

function ProviderKeyRow({
  provider,
  label,
  isSet,
  enabled,
}: {
  provider: string;
  label: string;
  isSet: boolean;
  enabled: boolean;
}) {
  const [value, setValue] = useState("");
  const setKey = useSetProviderKey();
  const clearKey = useClearProviderKey();

  return (
    <div className="flex flex-wrap items-center gap-2">
      <Label className="flex w-24 items-center gap-1 text-sm">
        <KeyRound className="h-3 w-3" />
        {label}
      </Label>
      {isSet ? (
        <Badge variant="outline" className="border-success/30 bg-success/12 text-success">
          <CheckCircle2 className="mr-1 h-3 w-3" />
          Configured
        </Badge>
      ) : (
        <Badge variant="outline" className="border-destructive/30 bg-destructive/12 text-destructive">
          <XCircle className="mr-1 h-3 w-3" />
          Not Set
        </Badge>
      )}
      {enabled && (
        <>
          <Input
            type="password"
            value={value}
            onChange={(e) => setValue(e.target.value)}
            placeholder={`${label} API key`}
            className="h-8 w-64"
            autoComplete="off"
          />
          <Button
            size="sm"
            disabled={!value.trim() || setKey.isPending}
            onClick={() =>
              setKey.mutate(
                { provider, api_key: value },
                {
                  onSuccess: () => {
                    setValue("");
                    toast.success(`${label} key saved.`);
                  },
                  onError: (err) =>
                    toast.error(err instanceof Error ? err.message : "Failed to save key."),
                },
              )
            }
          >
            Save
          </Button>
          {isSet && (
            <Button
              size="sm"
              variant="outline"
              disabled={clearKey.isPending}
              onClick={() =>
                clearKey.mutate(provider, {
                  onSuccess: () => toast.success(`${label} key cleared.`),
                })
              }
            >
              Clear
            </Button>
          )}
        </>
      )}
    </div>
  );
}

export default function SettingsPage() {
  const { data: settings, isLoading, isError } = useSettings();
  const updateSettings = useUpdateSettings();
  const purgeAll = usePurgeAllInvoices();
  const [form, setForm] = useState<SettingsUpdate>({});
  const [dirty, setDirty] = useState(false);
  const [purgeOpen, setPurgeOpen] = useState(false);
  const [purgeConfirm, setPurgeConfirm] = useState("");

  // Populate form when settings load
  useEffect(() => {
    let cancelled = false;
    if (settings) {
      queueMicrotask(() => {
        if (cancelled) return;
        setForm({
          llm_provider: settings.llm_provider,
          openai_model_classification: settings.openai_model_classification,
          openai_model_extraction: settings.openai_model_extraction,
          openai_model_model_generation: settings.openai_model_model_generation,
          openai_max_retries: settings.openai_max_retries,
          openai_timeout: settings.openai_timeout,
          worker_concurrency: settings.worker_concurrency,
          confidence_threshold: settings.confidence_threshold,
          model_confidence_threshold: settings.model_confidence_threshold,
          reconciliation_variance_threshold:
            settings.reconciliation_variance_threshold,
          watcher_poll_interval: settings.watcher_poll_interval,
          watcher_stable_seconds: settings.watcher_stable_seconds,
          log_level: settings.log_level,
          debug: settings.debug,
        });
        setDirty(false);
      });
    }
    return () => {
      cancelled = true;
    };
  }, [settings]);

  function updateField<K extends keyof SettingsUpdate>(
    key: K,
    value: SettingsUpdate[K]
  ) {
    setForm((prev) => ({ ...prev, [key]: value }));
    setDirty(true);
  }

  // Switching provider repoints the three model pickers to that provider's models,
  // keeping any current selection that is still valid for the new provider.
  function changeProvider(next: string | null) {
    if (!next) return;
    const models = settings?.provider_model_options?.[next] ?? [];
    const first = models[0] ?? "";
    const keep = (m?: string) => (m && models.includes(m) ? m : first);
    setForm((prev) => ({
      ...prev,
      llm_provider: next,
      openai_model_classification: keep(prev.openai_model_classification),
      openai_model_extraction: keep(prev.openai_model_extraction),
      openai_model_model_generation: keep(prev.openai_model_model_generation),
    }));
    setDirty(true);
  }

  function handleReset() {
    if (settings) {
      setForm({
        llm_provider: settings.llm_provider,
        openai_model_classification: settings.openai_model_classification,
        openai_model_extraction: settings.openai_model_extraction,
        openai_model_model_generation: settings.openai_model_model_generation,
        openai_max_retries: settings.openai_max_retries,
        openai_timeout: settings.openai_timeout,
        worker_concurrency: settings.worker_concurrency,
        confidence_threshold: settings.confidence_threshold,
        model_confidence_threshold: settings.model_confidence_threshold,
        reconciliation_variance_threshold:
          settings.reconciliation_variance_threshold,
        watcher_poll_interval: settings.watcher_poll_interval,
        watcher_stable_seconds: settings.watcher_stable_seconds,
        log_level: settings.log_level,
        debug: settings.debug,
      });
      setDirty(false);
    }
  }

  async function handleSave() {
    try {
      await updateSettings.mutateAsync(form);
      toast.success("Settings saved successfully.");
      setDirty(false);
    } catch {
      toast.error("Failed to save settings.");
    }
  }

  if (isLoading) {
    return (
      <div className="space-y-6">
        <PageHeader
          title="Settings"
          description="Application configuration and thresholds."
        />
        <div className="space-y-4">
          {Array.from({ length: 4 }).map((_, i) => (
            <Skeleton key={i} className="h-48" />
          ))}
        </div>
      </div>
    );
  }

  if (isError || !settings) {
    return (
      <div className="space-y-6">
        <PageHeader
          title="Settings"
          description="Application configuration and thresholds."
        />
        <Card className="border-destructive">
          <CardContent className="pt-6">
            <p className="text-sm text-destructive">
              Failed to load settings. Is the backend running?
            </p>
          </CardContent>
        </Card>
      </div>
    );
  }

  const activeProvider = form.llm_provider ?? settings.llm_provider;
  const modelOptions =
    settings.provider_model_options?.[activeProvider] ??
    (settings.openai_model_options?.length ? settings.openai_model_options : ["gpt-5.5"]);

  return (
    <div className="space-y-6">
      <PageHeader
        title="Settings"
        description="Application configuration and thresholds."
        actions={
          <>
            <Button
              variant="outline"
              size="sm"
              onClick={handleReset}
              disabled={!dirty}
            >
              <RotateCcw className="h-4 w-4" />
              Reset
            </Button>
            <Button
              size="sm"
              onClick={handleSave}
              disabled={!dirty || updateSettings.isPending}
            >
              <Save className="h-4 w-4" />
              {updateSettings.isPending ? "Saving..." : "Save Changes"}
            </Button>
          </>
        }
      />


      {/* Folder Paths (read-only) */}
      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="flex items-center gap-2 text-base">
            <FolderOpen className="h-4 w-4" />
            Folder Paths
          </CardTitle>
          <p className="text-xs text-muted-foreground">
            Configured via environment variables. Restart required to change.
          </p>
        </CardHeader>
        <CardContent>
          <div className="grid gap-3 sm:grid-cols-2">
            {[
              { label: "Data Directory", value: settings.data_dir },
              { label: "Inbound", value: settings.inbound_dir },
              { label: "Processing", value: settings.processing_dir },
              { label: "Completed", value: settings.completed_dir },
              { label: "Exceptions", value: settings.exceptions_dir },
              { label: "Archive", value: settings.archive_dir },
              { label: "Supplier Profiles", value: settings.supplier_profiles_dir },
            ].map(({ label, value }) => (
              <div key={label}>
                <Label className="text-xs text-muted-foreground">{label}</Label>
                <p className="font-mono text-sm mt-0.5 truncate" title={value}>
                  {value}
                </p>
              </div>
            ))}
          </div>
        </CardContent>
      </Card>

      {/* AI / OpenAI Settings */}
      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="flex items-center gap-2 text-base">
            <Brain className="h-4 w-4" />
            AI Configuration
          </CardTitle>
          <p className="text-xs text-muted-foreground">
            Choose a provider, enter its API key, and pick a model for each task.
          </p>
        </CardHeader>
        <CardContent className="space-y-4">
          {/* Provider */}
          <div className="flex flex-wrap items-center gap-3">
            <Label className="w-24 text-sm">Provider</Label>
            <Select
              value={activeProvider}
              onValueChange={changeProvider}
              // Without `items` the trigger renders the raw value ("openai")
              // instead of the display label ("OpenAI").
              items={Object.fromEntries(
                settings.providers.map((p) => [p, PROVIDER_LABELS[p] ?? p]),
              )}
            >
              <SelectTrigger className="w-48">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {settings.providers.map((p) => (
                  <SelectItem key={p} value={p}>
                    {PROVIDER_LABELS[p] ?? p}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          {/* API keys (per provider) */}
          <div className="space-y-2">
            <ProviderKeyRow
              provider="openai"
              label="OpenAI"
              isSet={settings.openai_api_key_set}
              enabled={settings.secret_storage_enabled}
            />
            <ProviderKeyRow
              provider="anthropic"
              label="Anthropic"
              isSet={settings.anthropic_api_key_set}
              enabled={settings.secret_storage_enabled}
            />
            {!settings.secret_storage_enabled && (
              <p className="text-xs text-muted-foreground">
                To store API keys in the app, set{" "}
                <span className="font-mono">ST_SECRET_KEY</span>. Without it, keys come from the{" "}
                <span className="font-mono">ST_OPENAI_API_KEY</span> /{" "}
                <span className="font-mono">ST_ANTHROPIC_API_KEY</span> environment variables.
              </p>
            )}
          </div>

          <div className="grid gap-4 sm:grid-cols-3">
            <div>
              <Label htmlFor="model-classification" className="text-sm">
                Classification Model
              </Label>
              <Select
                value={form.openai_model_classification ?? ""}
                onValueChange={(value) =>
                  updateField("openai_model_classification", value ?? undefined)
                }
              >
                <SelectTrigger id="model-classification" className="mt-1">
                  <SelectValue placeholder="Select model" />
                </SelectTrigger>
                <SelectContent>
                  {modelOptions.map((model) => (
                    <SelectItem key={model} value={model}>
                      {model}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <p className="text-xs text-muted-foreground mt-1">
                Used for supplier classification
              </p>
            </div>
            <div>
              <Label htmlFor="model-extraction" className="text-sm">
                Extraction Model
              </Label>
              <Select
                value={form.openai_model_extraction ?? ""}
                onValueChange={(value) =>
                  updateField("openai_model_extraction", value ?? undefined)
                }
              >
                <SelectTrigger id="model-extraction" className="mt-1">
                  <SelectValue placeholder="Select model" />
                </SelectTrigger>
                <SelectContent>
                  {modelOptions.map((model) => (
                    <SelectItem key={model} value={model}>
                      {model}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <p className="text-xs text-muted-foreground mt-1">
                Used for data extraction from PDFs
              </p>
            </div>
            <div>
              <Label htmlFor="model-generation" className="text-sm">
                Model Generation
              </Label>
              <Select
                value={form.openai_model_model_generation ?? ""}
                onValueChange={(value) =>
                  updateField("openai_model_model_generation", value ?? undefined)
                }
              >
                <SelectTrigger id="model-generation" className="mt-1">
                  <SelectValue placeholder="Select model" />
                </SelectTrigger>
                <SelectContent>
                  {modelOptions.map((model) => (
                    <SelectItem key={model} value={model}>
                      {model}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <p className="text-xs text-muted-foreground mt-1">
                Used for training rule authoring
              </p>
            </div>
          </div>

          <div className="grid gap-4 sm:grid-cols-2">
            <div>
              <Label htmlFor="max-retries" className="text-sm">
                Max Retries
              </Label>
              <Input
                id="max-retries"
                type="number"
                min={1}
                max={10}
                value={form.openai_max_retries ?? 3}
                onChange={(e) =>
                  updateField("openai_max_retries", parseInt(e.target.value) || 3)
                }
                className="mt-1"
              />
            </div>
            <div>
              <Label htmlFor="timeout" className="text-sm">
                Timeout (seconds)
              </Label>
              <Input
                id="timeout"
                type="number"
                min={10}
                max={600}
                value={form.openai_timeout ?? 120}
                onChange={(e) =>
                  updateField("openai_timeout", parseInt(e.target.value) || 120)
                }
                className="mt-1"
              />
            </div>
          </div>
        </CardContent>
      </Card>

      {/* Extraction Thresholds */}
      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="flex items-center gap-2 text-base">
            <Gauge className="h-4 w-4" />
            Extraction Thresholds
          </CardTitle>
          <p className="text-xs text-muted-foreground">
            Confidence and reconciliation thresholds for quality control.
          </p>
        </CardHeader>
        <CardContent>
          <div className="grid gap-4 sm:grid-cols-2">
            <div>
              <Label htmlFor="confidence-threshold" className="text-sm">
                Classification Confidence Threshold
              </Label>
              <div className="flex items-center gap-2 mt-1">
                <Input
                  id="confidence-threshold"
                  type="number"
                  min={0}
                  max={1}
                  step={0.05}
                  value={form.confidence_threshold ?? 0.75}
                  onChange={(e) =>
                    updateField(
                      "confidence_threshold",
                      parseFloat(e.target.value) || 0.75
                    )
                  }
                />
                <span className="text-sm text-muted-foreground whitespace-nowrap">
                  {((form.confidence_threshold ?? 0.75) * 100).toFixed(0)}%
                </span>
              </div>
              <p className="text-xs text-muted-foreground mt-1">
                Minimum AI confidence when identifying which supplier an
                uploaded invoice belongs to. Does not affect extraction.
              </p>
            </div>
            <div>
              <Label htmlFor="reconciliation-variance" className="text-sm">
                Reconciliation Variance
              </Label>
              <div className="flex items-center gap-2 mt-1">
                <Input
                  id="reconciliation-variance"
                  type="number"
                  min={0}
                  max={1}
                  step={0.005}
                  value={form.reconciliation_variance_threshold ?? 0.01}
                  onChange={(e) =>
                    updateField(
                      "reconciliation_variance_threshold",
                      parseFloat(e.target.value) || 0.01
                    )
                  }
                />
                <span className="text-sm text-muted-foreground whitespace-nowrap">
                  {(
                    (form.reconciliation_variance_threshold ?? 0.01) * 100
                  ).toFixed(1)}
                  %
                </span>
              </div>
              <p className="text-xs text-muted-foreground mt-1">
                Max allowed variance between line items and total
              </p>
            </div>
          </div>
        </CardContent>
      </Card>

      {/* File Watcher & System */}
      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="flex items-center gap-2 text-base">
            <Eye className="h-4 w-4" />
            File Watcher & System
          </CardTitle>
          <p className="text-xs text-muted-foreground">
            File watcher timing and system-level options.
          </p>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid gap-4 sm:grid-cols-2">
            <div>
              <Label htmlFor="worker-concurrency" className="text-sm">
                Concurrent Jobs
              </Label>
              <Input
                id="worker-concurrency"
                type="number"
                min={1}
                max={16}
                value={form.worker_concurrency ?? 2}
                onChange={(e) =>
                  updateField(
                    "worker_concurrency",
                    parseInt(e.target.value) || 2
                  )
                }
                className="mt-1"
              />
              <p className="text-xs text-muted-foreground mt-1">
                Maximum invoice jobs a worker runs at once. Applies live to running workers.
              </p>
            </div>
            <div>
              <Label htmlFor="poll-interval" className="text-sm">
                Poll Interval (seconds)
              </Label>
              <Input
                id="poll-interval"
                type="number"
                min={0.5}
                max={30}
                step={0.5}
                value={form.watcher_poll_interval ?? 1.0}
                onChange={(e) =>
                  updateField(
                    "watcher_poll_interval",
                    parseFloat(e.target.value) || 1.0
                  )
                }
                className="mt-1"
              />
              <p className="text-xs text-muted-foreground mt-1">
                How often the file watcher checks for new PDFs
              </p>
            </div>
            <div>
              <Label htmlFor="stable-seconds" className="text-sm">
                Stable Seconds
              </Label>
              <Input
                id="stable-seconds"
                type="number"
                min={1}
                max={60}
                step={0.5}
                value={form.watcher_stable_seconds ?? 3.0}
                onChange={(e) =>
                  updateField(
                    "watcher_stable_seconds",
                    parseFloat(e.target.value) || 3.0
                  )
                }
                className="mt-1"
              />
              <p className="text-xs text-muted-foreground mt-1">
                Wait time after last file change before processing
              </p>
            </div>
          </div>

          <div className="grid gap-4 sm:grid-cols-2">
            <div>
              <Label htmlFor="log-level" className="text-sm">
                Log Level
              </Label>
              <Select
                value={form.log_level ?? "INFO"}
                onValueChange={(v) => updateField("log_level", v ?? "INFO")}
              >
                <SelectTrigger className="mt-1">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="DEBUG">DEBUG</SelectItem>
                  <SelectItem value="INFO">INFO</SelectItem>
                  <SelectItem value="WARNING">WARNING</SelectItem>
                  <SelectItem value="ERROR">ERROR</SelectItem>
                </SelectContent>
              </Select>
            </div>
            <div>
              <Label className="text-sm" htmlFor="debug-mode">
                Debug Mode
              </Label>
              <div className="flex items-center gap-3 mt-2">
                <Switch
                  id="debug-mode"
                  // Unlabelled, this reads as a bare "switch" — and it is the
                  // control that reveals the destructive purge.
                  aria-label="Debug mode"
                  aria-describedby="debug-mode-help"
                  checked={form.debug ?? false}
                  onCheckedChange={(checked) => updateField("debug", checked)}
                />
                <span className="text-sm text-muted-foreground">
                  {form.debug ? "Enabled" : "Disabled"}
                </span>
              </div>
              <p id="debug-mode-help" className="text-xs text-muted-foreground mt-1">
                Enable verbose logging and debug endpoints. Admin-only, and
                required before the data-clearing action below becomes available.
              </p>
            </div>
          </div>
        </CardContent>
      </Card>

      {(settings?.debug || form.debug) && (
        <Card className="border-destructive/50">
          <CardHeader>
            <CardTitle className="text-base text-destructive flex items-center gap-2">
              <Trash2 className="h-4 w-4" />
              Danger zone
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            <p className="text-sm text-muted-foreground">
              Delete all invoice intake records, processing logs, extraction jobs,
              and work-directory artifacts. Supplier PDF folders in data_dir are not
              removed. Requires debug mode.
            </p>
            <Dialog open={purgeOpen} onOpenChange={setPurgeOpen}>
              <DialogTrigger render={<Button variant="destructive" size="sm" />}>
                Clear all invoice data
              </DialogTrigger>
              <DialogContent>
                <DialogHeader>
                  <DialogTitle>Clear all invoice data?</DialogTitle>
                  <DialogDescription>
                    This permanently deletes every intake, job, log, and completed
                    output folder. Type <strong>DELETE ALL</strong> to confirm.
                  </DialogDescription>
                </DialogHeader>
                <Input
                  value={purgeConfirm}
                  onChange={(e) => setPurgeConfirm(e.target.value)}
                  placeholder="DELETE ALL"
                  className="font-mono"
                />
                <DialogFooter>
                  <Button variant="outline" onClick={() => setPurgeOpen(false)}>
                    Cancel
                  </Button>
                  <Button
                    variant="destructive"
                    disabled={purgeConfirm !== "DELETE ALL" || purgeAll.isPending}
                    onClick={() => {
                      purgeAll.mutate(undefined, {
                        onSuccess: (res) => {
                          toast.success(res.message);
                          setPurgeOpen(false);
                          setPurgeConfirm("");
                        },
                        onError: (err) => {
                          toast.error(
                            err instanceof Error ? err.message : "Purge failed",
                          );
                        },
                      });
                    }}
                  >
                    {purgeAll.isPending ? "Deleting…" : "Delete everything"}
                  </Button>
                </DialogFooter>
              </DialogContent>
            </Dialog>
          </CardContent>
        </Card>
      )}

      {/* Sticky save bar when dirty */}
      {dirty && (
        <div className="fixed bottom-0 left-0 right-0 bg-background border-t p-3 flex items-center justify-center gap-3 z-50">
          <span className="text-sm text-muted-foreground">
            You have unsaved changes
          </span>
          <Button variant="outline" size="sm" onClick={handleReset}>
            Discard
          </Button>
          <Button
            size="sm"
            onClick={handleSave}
            disabled={updateSettings.isPending}
          >
            <Save className="h-4 w-4" />
            Save Changes
          </Button>
        </div>
      )}
    </div>
  );
}
