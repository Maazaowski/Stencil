"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { toast } from "sonner";
import { useDeleteOutputSpec, useOutputSpecs } from "@/hooks/use-output-specs";
import { PageHeader } from "@/components/page-header";
import { ErrorState, LoadingState } from "@/components/states";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { useConfirm } from "@/components/ui/confirm-dialog";
import { Plus, Search, Edit, Trash2, Table } from "lucide-react";

export default function OutputSpecsPage() {
  const router = useRouter();
  const { data: specs, isLoading, isError, refetch } = useOutputSpecs();
  const deleteSpec = useDeleteOutputSpec();
  const confirm = useConfirm();
  const [search, setSearch] = useState("");

  const filtered = (specs ?? []).filter((s) => {
    if (!search) return true;
    const q = search.toLowerCase();
    return s.spec_id.toLowerCase().includes(q) || s.name.toLowerCase().includes(q);
  });

  async function handleDelete(specId: string) {
    const ok = await confirm({
      title: `Delete output spec "${specId}"?`,
      confirmLabel: "Delete",
      variant: "destructive",
    });
    if (!ok) return;
    try {
      await deleteSpec.mutateAsync(specId);
      toast.success("Output spec deleted.");
    } catch {
      toast.error("Failed to delete output spec.");
    }
  }

  return (
    <div className="space-y-6">
      <PageHeader
        title="Output Specs"
        description="Define delivered output columns and how they map to extracted fields."
        actions={
          <Button onClick={() => router.push("/output-specs/new")}>
            <Plus className="h-4 w-4" />
            New Output Spec
          </Button>
        }
      />

      <div className="relative max-w-sm">
        <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
        <Input
          className="pl-9"
          placeholder="Search output specs..."
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
      </div>

      {isLoading ? (
        <LoadingState rows={4} />
      ) : isError ? (
        <ErrorState what="output specs" onRetry={() => refetch()} />
      ) : (
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
          {filtered.map((spec) => (
            <Card key={spec.spec_id}>
              <CardHeader className="pb-2">
                <CardTitle className="flex items-center gap-2 text-base">
                  <Table className="h-4 w-4 text-muted-foreground" />
                  {spec.name || spec.spec_id}
                </CardTitle>
                <p className="text-sm text-muted-foreground font-mono">{spec.spec_id}</p>
              </CardHeader>
              <CardContent className="flex items-center justify-between">
                <span className="text-sm text-muted-foreground">
                  {spec.column_count} column{spec.column_count === 1 ? "" : "s"}
                </span>
                <div className="flex gap-2">
                  <Link href={`/output-specs/${spec.spec_id}`}>
                    <Button variant="outline" size="sm">
                      <Edit className="h-3.5 w-3.5" />
                      Edit
                    </Button>
                  </Link>
                  {spec.spec_id !== "temforce.standard" && (
                    <Button variant="ghost" size="sm" onClick={() => handleDelete(spec.spec_id)}>
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
