"use client";

import { use, useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { toast } from "sonner";
import { toastErrorMessage } from "@/lib/api/errors";
import {
  useCloneOutputSpec,
  useCreateOutputSpec,
  useOutputSpec,
  useUpdateOutputSpec,
} from "@/hooks/use-output-specs";
import { useFieldSchemas, useFieldSchema } from "@/hooks/use-field-schemas";
import { PageHeader } from "@/components/page-header";
import { ErrorState, LoadingState } from "@/components/states";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { ArrowLeft, Loader2, Plus, Save, Trash2 } from "lucide-react";
import type { OutputColumn, OutputSpec } from "@/types";

function emptyColumn(): OutputColumn {
  return { header: "", source: "", fallback: null, width: 15, number_format: null };
}

function emptySpec(): OutputSpec {
  return { spec_id: "", name: "", columns: [emptyColumn()] };
}

function buildSourceOptions(schema: ReturnType<typeof useFieldSchema>["data"]) {
  const options = ["computed.line_tax"];
  if (!schema) return options;
  for (const field of schema.fields) {
    const prefix = field.scope === "document" ? "field." : "row.";
    options.push(`${prefix}${field.name}`);
  }
  return options;
}

export default function OutputSpecEditorPage({
  params,
}: {
  params: Promise<{ specId: string }>;
}) {
  const { specId } = use(params);
  const isNew = specId === "new";
  const router = useRouter();
  const { data: existing, isLoading, isError, refetch } = useOutputSpec(isNew ? "" : specId);
  const { data: schemaList } = useFieldSchemas();
  const createSpec = useCreateOutputSpec();
  const updateSpec = useUpdateOutputSpec();
  const cloneSpec = useCloneOutputSpec();
  const [spec, setSpec] = useState<OutputSpec>(emptySpec());
  const [referenceSchemaId, setReferenceSchemaId] = useState("invoice.standard");
  const [startMode, setStartMode] = useState<"blank" | "clone_temforce">("blank");
  const { data: referenceSchema } = useFieldSchema(referenceSchemaId);
  const sourceOptions = useMemo(() => buildSourceOptions(referenceSchema), [referenceSchema]);

  useEffect(() => {
    let cancelled = false;
    if (existing) {
      queueMicrotask(() => {
        if (!cancelled) {
          setSpec(existing);
        }
      });
    }
    return () => {
      cancelled = true;
    };
  }, [existing]);

  function updateColumn(index: number, patch: Partial<OutputColumn>) {
    setSpec((prev) => ({
      ...prev,
      columns: prev.columns.map((c, i) => (i === index ? { ...c, ...patch } : c)),
    }));
  }

  async function handleSave() {
    if (!spec.spec_id.trim()) {
      toast.error("Spec ID is required.");
      return;
    }
    try {
      if (isNew) {
        if (startMode === "clone_temforce") {
          await cloneSpec.mutateAsync({
            specId: "temforce.standard",
            newSpecId: spec.spec_id,
            name: spec.name || undefined,
          });
        } else {
          await createSpec.mutateAsync(spec);
        }
        toast.success("Output spec created.");
        router.push(`/output-specs/${spec.spec_id}`);
      } else {
        await updateSpec.mutateAsync({ specId, spec });
        toast.success("Output spec saved.");
      }
    } catch (err) {
      toast.error(toastErrorMessage(err, "Failed to save output spec."));
    }
  }

  if (!isNew && isLoading) {
    return <LoadingState rows={5} />;
  }

  if (!isNew && isError) {
    return <ErrorState what="this output spec" onRetry={() => refetch()} />;
  }

  return (
    <div className="space-y-6">
      <PageHeader
        title={isNew ? "New Output Spec" : spec.name || specId}
        description="Map delivered columns to extracted field paths."
        actions={
          <div className="flex gap-2">
            <Link href="/output-specs">
              <Button variant="outline">
                <ArrowLeft className="h-4 w-4" />
                Back
              </Button>
            </Link>
            <Button onClick={handleSave} disabled={createSpec.isPending || updateSpec.isPending}>
              {(createSpec.isPending || updateSpec.isPending) && (
                <Loader2 className="h-4 w-4 animate-spin" />
              )}
              <Save className="h-4 w-4" />
              Save
            </Button>
          </div>
        }
      />

      <Card>
        <CardHeader><CardTitle>Spec identity</CardTitle></CardHeader>
        <CardContent className="grid gap-4 md:grid-cols-2">
          <div>
            <label className="text-sm font-medium">Spec ID</label>
            <Input
              value={spec.spec_id}
              disabled={!isNew}
              onChange={(e) => setSpec((p) => ({ ...p, spec_id: e.target.value }))}
              placeholder="e.g. lab_report.standard"
            />
          </div>
          <div>
            <label className="text-sm font-medium">Display name</label>
            <Input
              value={spec.name}
              onChange={(e) => setSpec((p) => ({ ...p, name: e.target.value }))}
            />
          </div>
          {isNew && (
            <div className="md:col-span-2">
              <label className="text-sm font-medium">Start from</label>
              <Select value={startMode} onValueChange={(v) => setStartMode(v as "blank" | "clone_temforce")}>
                <SelectTrigger><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="blank">Blank columns</SelectItem>
                  <SelectItem value="clone_temforce">Clone temforce.standard</SelectItem>
                </SelectContent>
              </Select>
            </div>
          )}
          <div className="md:col-span-2">
            <label className="text-sm font-medium">Reference field schema (picker aid)</label>
            <Select value={referenceSchemaId} onValueChange={(v) => setReferenceSchemaId(v ?? "invoice.standard")}>
              <SelectTrigger><SelectValue /></SelectTrigger>
              <SelectContent>
                {(schemaList ?? []).map((s) => (
                  <SelectItem key={s.schema_id} value={s.schema_id}>{s.schema_id}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        </CardContent>
      </Card>

      {!(isNew && startMode === "clone_temforce") && (
        <Card>
          <CardHeader className="flex flex-row items-center justify-between">
            <CardTitle>Columns</CardTitle>
            <Button
              variant="outline"
              size="sm"
              onClick={() => setSpec((p) => ({ ...p, columns: [...p.columns, emptyColumn()] }))}
            >
              <Plus className="h-4 w-4" />
              Add column
            </Button>
          </CardHeader>
          <CardContent className="space-y-4">
            {spec.columns.map((column, index) => (
              <div key={index} className="grid gap-3 rounded-md border p-4 md:grid-cols-2">
                <Input
                  value={column.header}
                  onChange={(e) => updateColumn(index, { header: e.target.value })}
                  placeholder="Delivered header"
                />
                <Select
                  value={column.source || undefined}
                  onValueChange={(v) => updateColumn(index, { source: v ?? "" })}
                >
                  <SelectTrigger><SelectValue placeholder="Source path" /></SelectTrigger>
                  <SelectContent>
                    {sourceOptions.map((opt) => (
                      <SelectItem key={opt} value={opt}>{opt}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                <Select
                  value={column.fallback || "__none__"}
                  onValueChange={(v) => updateColumn(index, { fallback: !v || v === "__none__" ? undefined : v })}
                >
                  <SelectTrigger><SelectValue placeholder="Fallback (optional)" /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value="__none__">None</SelectItem>
                    {sourceOptions.map((opt) => (
                      <SelectItem key={`fb-${opt}`} value={opt}>{opt}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                <div className="flex gap-2">
                  <Input
                    type="number"
                    value={column.width ?? 15}
                    onChange={(e) => updateColumn(index, { width: Number(e.target.value) || 15 })}
                    placeholder="Width"
                  />
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() =>
                      setSpec((p) => ({
                        ...p,
                        columns: p.columns.filter((_, i) => i !== index),
                      }))
                    }
                  >
                    <Trash2 className="h-4 w-4" />
                  </Button>
                </div>
              </div>
            ))}
          </CardContent>
        </Card>
      )}
    </div>
  );
}
