"use client";

import { use, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { toast } from "sonner";
import { formatApiError } from "@/lib/api/errors";
import {
  useCloneFieldSchema,
  useCreateFieldSchema,
  useFieldSchema,
  useUpdateFieldSchema,
} from "@/hooks/use-field-schemas";
import { PageHeader } from "@/components/page-header";
import { ErrorState, InlineError, LoadingState } from "@/components/states";
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
import { Switch } from "@/components/ui/switch";
import { ArrowLeft, Loader2, Plus, Save, Trash2 } from "lucide-react";
import type { FieldDef, FieldSchema } from "@/types";

const SCOPES = ["document", "row"] as const;
const TYPES = ["string", "date", "number", "currency", "integer", "enum"] as const;
const ROLES = ["none", "identifier", "amount", "tax", "subtotal", "total", "tax_rate"] as const;

function emptyField(): FieldDef {
  return {
    name: "",
    scope: "document",
    type: "string",
    role: "none",
    label_hint: null,
    required: false,
    enum_values: [],
    description: "",
  };
}

function emptySchema(): FieldSchema {
  return { schema_id: "", name: "", fields: [emptyField()] };
}

export default function FieldSchemaEditorPage({
  params,
}: {
  params: Promise<{ schemaId: string }>;
}) {
  const { schemaId } = use(params);
  const isNew = schemaId === "new";
  const router = useRouter();
  const { data: existing, isLoading, isError, refetch } = useFieldSchema(isNew ? "" : schemaId);
  const createSchema = useCreateFieldSchema();
  const updateSchema = useUpdateFieldSchema();
  const cloneSchema = useCloneFieldSchema();
  const [schema, setSchema] = useState<FieldSchema>(emptySchema());
  const [saveError, setSaveError] = useState<string | null>(null);
  const [template, setTemplate] = useState<string>("blank_document");

  useEffect(() => {
    let cancelled = false;
    if (existing) {
      queueMicrotask(() => {
        if (!cancelled) {
          setSchema(existing);
        }
      });
    }
    return () => {
      cancelled = true;
    };
  }, [existing]);

  function updateField(index: number, patch: Partial<FieldDef>) {
    setSchema((prev) => ({
      ...prev,
      fields: prev.fields.map((f, i) => (i === index ? { ...f, ...patch } : f)),
    }));
  }

  async function handleSave() {
    setSaveError(null);
    if (!schema.schema_id.trim()) {
      setSaveError("Schema ID is required.");
      return;
    }
    try {
      if (isNew) {
        if (template === "clone_invoice") {
          await cloneSchema.mutateAsync({
            schemaId: "invoice.standard",
            newSchemaId: schema.schema_id,
            name: schema.name || undefined,
          });
        } else {
          await createSchema.mutateAsync({
            schema_id: schema.schema_id,
            name: schema.name,
            template,
          });
        }
        toast.success("Field schema created.");
        router.push(`/field-schemas/${schema.schema_id}`);
      } else {
        await updateSchema.mutateAsync({ schemaId, schema });
        toast.success("Field schema saved.");
      }
    } catch (err) {
      setSaveError(formatApiError(err, "Field schema could not be saved."));
    }
  }

  if (!isNew && isLoading) {
    return <LoadingState rows={5} />;
  }

  if (!isNew && isError) {
    return <ErrorState what="this field schema" onRetry={() => refetch()} />;
  }

  return (
    <div className="space-y-6">
      <PageHeader
        title={isNew ? "New Field Schema" : schema.name || schemaId}
        description="Define document-level and row-level extraction fields."
        actions={
          <div className="flex gap-2">
            <Link href="/field-schemas">
              <Button variant="outline">
                <ArrowLeft className="h-4 w-4" />
                Back
              </Button>
            </Link>
            <Button onClick={handleSave} disabled={createSchema.isPending || updateSchema.isPending}>
              {(createSchema.isPending || updateSchema.isPending) && (
                <Loader2 className="h-4 w-4 animate-spin" />
              )}
              <Save className="h-4 w-4" />
              Save
            </Button>
          </div>
        }
      />

      {/* Stays put next to Save — a vanished failure reads as success. */}
      <InlineError message={saveError} onDismiss={() => setSaveError(null)} />

      <Card>
        <CardHeader>
          <CardTitle>Schema identity</CardTitle>
        </CardHeader>
        <CardContent className="grid gap-4 md:grid-cols-2">
          <div>
            <label className="text-sm font-medium">Schema ID</label>
            <Input
              value={schema.schema_id}
              disabled={!isNew}
              onChange={(e) => setSchema((p) => ({ ...p, schema_id: e.target.value }))}
              placeholder="e.g. lab_report.standard"
            />
          </div>
          <div>
            <label className="text-sm font-medium">Display name</label>
            <Input
              value={schema.name}
              onChange={(e) => setSchema((p) => ({ ...p, name: e.target.value }))}
              placeholder="Lab Report"
            />
          </div>
          {isNew && (
            <div className="md:col-span-2">
              <label className="text-sm font-medium">Start from</label>
              <Select value={template} onValueChange={(v) => setTemplate(v ?? "blank_document")}>
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="blank_document">Blank (document fields only)</SelectItem>
                  <SelectItem value="blank_tabular">Blank tabular (document + row amount)</SelectItem>
                  <SelectItem value="clone_invoice">Clone invoice.standard</SelectItem>
                </SelectContent>
              </Select>
            </div>
          )}
        </CardContent>
      </Card>

      {!(isNew && template === "clone_invoice") && (
        <Card>
          <CardHeader className="flex flex-row items-center justify-between">
            <CardTitle>Fields</CardTitle>
            <Button
              variant="outline"
              size="sm"
              onClick={() => setSchema((p) => ({ ...p, fields: [...p.fields, emptyField()] }))}
            >
              <Plus className="h-4 w-4" />
              Add field
            </Button>
          </CardHeader>
          <CardContent className="space-y-4">
            {schema.fields.map((field, index) => (
              <div key={index} className="grid gap-3 rounded-md border p-4 md:grid-cols-3">
                <Input
                  value={field.name}
                  onChange={(e) => updateField(index, { name: e.target.value })}
                  placeholder="Field name"
                />
                <Input
                  value={field.label_hint ?? ""}
                  onChange={(e) => updateField(index, { label_hint: e.target.value || undefined })}
                  placeholder="Default label hint"
                />
                <Select
                  value={field.scope}
                  onValueChange={(v) => updateField(index, { scope: (v ?? "document") as FieldDef["scope"] })}
                >
                  <SelectTrigger><SelectValue /></SelectTrigger>
                  <SelectContent>
                    {SCOPES.map((s) => (
                      <SelectItem key={s} value={s}>{s}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                <Select
                  value={field.type}
                  onValueChange={(v) => updateField(index, { type: v ?? "string" })}
                >
                  <SelectTrigger><SelectValue /></SelectTrigger>
                  <SelectContent>
                    {TYPES.map((t) => (
                      <SelectItem key={t} value={t}>{t}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                <Select
                  value={field.role}
                  onValueChange={(v) => updateField(index, { role: v ?? "none" })}
                >
                  <SelectTrigger><SelectValue /></SelectTrigger>
                  <SelectContent>
                    {ROLES.map((r) => (
                      <SelectItem key={r} value={r}>{r}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                <div className="flex items-center gap-2">
                  <Switch
                    checked={field.required ?? false}
                    onCheckedChange={(checked) => updateField(index, { required: checked })}
                  />
                  <span className="text-sm">Required</span>
                  <Button
                    variant="ghost"
                    size="sm"
                    className="ml-auto"
                    onClick={() =>
                      setSchema((p) => ({
                        ...p,
                        fields: p.fields.filter((_, i) => i !== index),
                      }))
                    }
                  >
                    <Trash2 className="h-4 w-4" />
                  </Button>
                </div>
                {field.type === "enum" && (
                  <Input
                    className="md:col-span-3"
                    value={(field.enum_values ?? []).join(", ")}
                    onChange={(e) =>
                      updateField(index, {
                        enum_values: e.target.value.split(",").map((v) => v.trim()).filter(Boolean),
                      })
                    }
                    placeholder="Enum values (comma-separated)"
                  />
                )}
              </div>
            ))}
          </CardContent>
        </Card>
      )}
    </div>
  );
}
