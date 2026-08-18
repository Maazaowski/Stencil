"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { toast } from "sonner";
import { useFieldSchemas, useDeleteFieldSchema } from "@/hooks/use-field-schemas";
import { PageHeader } from "@/components/page-header";
import { ErrorState, LoadingState } from "@/components/states";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { useConfirm } from "@/components/ui/confirm-dialog";
import { Plus, Search, Edit, Trash2, Database } from "lucide-react";

export default function FieldSchemasPage() {
  const router = useRouter();
  const { data: schemas, isLoading, isError, refetch } = useFieldSchemas();
  const deleteSchema = useDeleteFieldSchema();
  const confirm = useConfirm();
  const [search, setSearch] = useState("");

  const filtered = (schemas ?? []).filter((s) => {
    if (!search) return true;
    const q = search.toLowerCase();
    return s.schema_id.toLowerCase().includes(q) || s.name.toLowerCase().includes(q);
  });

  async function handleDelete(schemaId: string) {
    const ok = await confirm({
      title: `Delete field schema "${schemaId}"?`,
      confirmLabel: "Delete",
      variant: "destructive",
    });
    if (!ok) return;
    try {
      await deleteSchema.mutateAsync(schemaId);
      toast.success("Field schema deleted.");
    } catch {
      toast.error("Failed to delete field schema.");
    }
  }

  return (
    <div className="space-y-6">
      <PageHeader
        title="Field Schemas"
        description="Define what fields to extract from documents (document fields and row fields)."
        actions={
          <Button onClick={() => router.push("/field-schemas/new")}>
            <Plus className="h-4 w-4" />
            New Schema
          </Button>
        }
      />

      <div className="relative max-w-sm">
        <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
        <Input
          className="pl-9"
          placeholder="Search schemas..."
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
      </div>

      {isLoading ? (
        <LoadingState rows={4} />
      ) : isError ? (
        <ErrorState what="field schemas" onRetry={() => refetch()} />
      ) : (
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
          {filtered.map((schema) => (
            <Card key={schema.schema_id}>
              <CardHeader className="pb-2">
                <CardTitle className="flex items-center gap-2 text-base">
                  <Database className="h-4 w-4 text-muted-foreground" />
                  {schema.name || schema.schema_id}
                </CardTitle>
                <p className="text-sm text-muted-foreground font-mono">{schema.schema_id}</p>
              </CardHeader>
              <CardContent className="flex items-center justify-between">
                <span className="text-sm text-muted-foreground">
                  {schema.field_count} field{schema.field_count === 1 ? "" : "s"}
                </span>
                <div className="flex gap-2">
                    <Link href={`/field-schemas/${schema.schema_id}`}>
                    <Button variant="outline" size="sm">
                      <Edit className="h-3.5 w-3.5" />
                      Edit
                    </Button>
                  </Link>
                  {schema.schema_id !== "invoice.standard" && (
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => handleDelete(schema.schema_id)}
                    >
                      <Trash2 className="h-3.5 w-3.5" />
                    </Button>
                  )}
                </div>
              </CardContent>
            </Card>
          ))}
        </div>
      )}
    </div>
  );
}
