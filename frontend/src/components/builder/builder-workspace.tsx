"use client";

import { useRef, useState } from "react";
import { ZoomIn, ZoomOut, Eye, EyeOff, AlertTriangle, FileText } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
} from "@/components/ui/select";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { LayoutCanvas } from "./layout-canvas";
import { ColumnBandsOverlay, ColumnEditorPanel } from "./column-editor";
import { RegionOverlay, RegionEditorPanel, type PickMode } from "./region-editor";
import { ColumnSplitOverlay, ColumnSplitPanel } from "./column-split-editor";
import { RoleOverlay, ClassifierEditorPanel } from "./classifier-editor";
import { GroupsOverlay, GroupingEditorPanel } from "./grouping-editor";
import { FieldsOverlay, FieldEditorPanel } from "./field-editor";
import { HeaderOverlay, HeaderEditorPanel } from "./header-editor";
import { TaxEditorPanel } from "./tax-editor";
import { PreviewPanel } from "./preview-panel";
import { SaveControl } from "./save-control";
import {
  suggestRowPattern,
  distinctRoles,
  computeGroups,
  inRegionRowIds,
  classifyRowRole,
} from "./model-logic";
import {
  useIntakeLayout,
  useDraftModel,
  type LayoutRow,
  type LayoutCell,
  type BuilderTarget,
} from "@/hooks/use-builder";
import { formatApiError } from "@/lib/api/errors";

const ZOOM_STEP = 0.15;
const ZOOM_MIN = 0.5;
const ZOOM_MAX = 2.5;

const DATE_FIELDS = new Set(["invoice_date", "due_date"]);

type EditorTab = "region" | "columns" | "rows" | "groups" | "fields" | "header" | "tax";

export interface BuilderWorkspaceProps {
  /** The sample invoice being authored against (a builder sample_id). */
  sampleId: string;
  /** Filename of the primary sample, for display in the preview. */
  sampleName: string;
  /** What the model is for: a supplier profile, or a document type + deliverable
   * chosen directly. Decides the columns the preview renders. */
  target: BuilderTarget;
}

export function BuilderWorkspace({ sampleId, sampleName, target }: BuilderWorkspaceProps) {
  const profileId = target.profileId;
  const {
    draft,
    updateRegion,
    setColumns,
    setClassifiers,
    updateGrouping,
    setFields,
    setHeaderField,
    updateTax,
  } = useDraftModel();

  // The canvas re-reads the page through the model's own column split, so the
  // rows shown are the rows the interpreter will execute against.
  const { data, isLoading, error } = useIntakeLayout(sampleId, draft.region.column_split_x);

  const [pageIndex, setPageIndex] = useState(0);
  const [zoom, setZoom] = useState(1);
  const [showOutlines, setShowOutlines] = useState(true);

  const [showOriginal, setShowOriginal] = useState(false);
  // Served inline by GET /intakes/samples/{id}/pdf (same-origin, mirrors the
  // exceptions page's PDF link). A diagnostic reference for the reconstruction.
  const originalPdfUrl = `/api/v1/intakes/samples/${sampleId}/pdf`;

  const [tab, setTab] = useState<EditorTab>("region");
  const [pickMode, setPickMode] = useState<PickMode>("none");
  const [drawMode, setDrawMode] = useState(false);
  const [selectedCol, setSelectedCol] = useState<number | null>(null);
  const [rolePick, setRolePick] = useState(false);
  const [pendingRole, setPendingRole] = useState("item_start");
  const [selectedField, setSelectedField] = useState<string | null>(null);
  const [headerPick, setHeaderPick] = useState<string | null>(null);

  const pages = data?.pages ?? [];
  const totalRows = pages.reduce((n, p) => n + p.rows.length, 0);
  // Representative column-split suggestion for the sidebar hint (first page that has one).
  const suggestedSplit = pages.find((p) => p.suggested_column_split_x != null)?.suggested_column_split_x ?? null;

  // Per-page canvas refs so the "jump to page" dropdown can scroll a page into view.
  const pageRefs = useRef<Array<HTMLDivElement | null>>([]);
  const jumpToPage = (i: number) => {
    setPageIndex(i);
    pageRefs.current[i]?.scrollIntoView({ behavior: "smooth", block: "start" });
  };

  // Live grouping preview across ALL pages (mirrors the interpreter).
  const availableRoles = distinctRoles(draft.row_classifiers);
  const groupItemCount = (() => {
    const roleOf = (row: LayoutRow) => classifyRowRole(row, draft.row_classifiers);
    let total = 0;
    for (const p of pages) {
      const inReg = inRegionRowIds(p.rows, draft.region);
      const domain = inReg.size > 0 ? p.rows.filter((r) => inReg.has(r.row_id)) : p.rows;
      const groups = computeGroups(domain, roleOf, draft.grouping);
      if (draft.grouping.emit === "one_item_per_role_row" && draft.grouping.emit_role) {
        const emitRole = draft.grouping.emit_role;
        total += groups.reduce(
          (n, grp) =>
            n +
            grp.rowIds.filter((id) => {
              const r = domain.find((x) => x.row_id === id);
              return r && roleOf(r) === emitRole;
            }).length,
          0,
        );
      } else {
        total += groups.length;
      }
    }
    return total;
  })();

  const changeTab = (v: string) => {
    setTab(v as EditorTab);
    setPickMode("none");
    setDrawMode(false);
    setRolePick(false);
    setHeaderPick(null);
  };

  const pickHeaderCell = (cell: LayoutCell) => {
    if (!headerPick) return;
    const text = cell.text.trim();
    if (!text) return;
    setHeaderField(headerPick, {
      label: text,
      value_position: "right",
      date_format: DATE_FIELDS.has(headerPick) ? "%d-%b-%Y" : null,
      required: false,
    });
    setHeaderPick(null);
  };

  const pickAnchor = (row: LayoutRow) => {
    const text = row.text.trim();
    if (!text) return;
    if (pickMode === "start" && !draft.region.start_anchors.includes(text)) {
      updateRegion({ start_anchors: [...draft.region.start_anchors, text] });
    } else if (pickMode === "end" && !draft.region.end_anchors.includes(text)) {
      updateRegion({ end_anchors: [...draft.region.end_anchors, text] });
    }
  };

  const addClassifier = (row: LayoutRow) => {
    const pattern = suggestRowPattern(row.text);
    if (!pattern) return;
    setClassifiers([
      ...draft.row_classifiers,
      { role: pendingRole.trim() || "item_start", where: { row_text: pattern } },
    ]);
  };

  // The active-tab overlays, drawn on top of every page's canvas. Each page
  // passes its own suggested column split; all edited values are global
  // normalized model props, so editing from any page stays consistent.
  const renderOverlays = (p: (typeof pages)[number]) => (
    <>
      {tab === "columns" && (
        <ColumnBandsOverlay
          columns={draft.region.columns}
          onChange={setColumns}
          drawMode={drawMode}
          selectedIndex={selectedCol}
          onSelect={setSelectedCol}
        />
      )}
      {tab === "region" && (
        <>
          <RegionOverlay region={draft.region} pickMode={pickMode} onPick={pickAnchor} />
          <ColumnSplitOverlay
            region={draft.region}
            suggested={p.suggested_column_split_x}
            onChange={updateRegion}
          />
        </>
      )}
      {tab === "rows" && (
        <RoleOverlay
          classifiers={draft.row_classifiers}
          picking={rolePick}
          onPick={addClassifier}
        />
      )}
      {tab === "groups" && (
        <GroupsOverlay
          region={draft.region}
          classifiers={draft.row_classifiers}
          grouping={draft.grouping}
        />
      )}
      {tab === "fields" && (
        <FieldsOverlay
          fields={draft.item_fields}
          columns={draft.region.columns}
          selectedField={selectedField}
        />
      )}
      {tab === "header" && (
        <HeaderOverlay
          headerFields={draft.header_fields}
          pickField={headerPick}
          onPickCell={pickHeaderCell}
        />
      )}
    </>
  );

  return (
    <div className="space-y-4">
      <div className="sticky top-0 z-20 -mx-1 flex items-center justify-end bg-background/80 px-1 py-2 backdrop-blur supports-[backdrop-filter]:bg-background/60">
        <SaveControl draft={draft} sampleId={sampleId} profileId={profileId} target={target} />
      </div>

      {isLoading && <Skeleton className="h-[600px] w-full max-w-5xl" />}

      {error && (
        <div className="flex items-center gap-2 rounded-md border border-destructive/40 bg-destructive/5 p-4 text-sm text-destructive">
          <AlertTriangle className="h-4 w-4 shrink-0" />
          <span>Could not load the sample layout: {formatApiError(error)}</span>
        </div>
      )}

      {data && pages.length > 0 && (
        <div className="flex flex-col gap-4 lg:flex-row lg:items-start">
          {/* Canvas + toolbar */}
          <div className="min-w-0 flex-1 space-y-3">
            <div className="flex flex-wrap items-center gap-3">
              {data.page_count > 1 && (
                <Select value={String(pageIndex)} onValueChange={(v) => jumpToPage(Number(v))}>
                  <SelectTrigger className="w-36">
                    <span>Jump to page…</span>
                  </SelectTrigger>
                  <SelectContent>
                    {pages.map((p, i) => (
                      <SelectItem key={p.page_id} value={String(i)}>
                        Page {p.page_number}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              )}

              <div className="flex items-center gap-1">
                <Button
                  variant="outline"
                  size="icon-sm"
                  onClick={() => setZoom((z) => Math.max(ZOOM_MIN, +(z - ZOOM_STEP).toFixed(2)))}
                  disabled={zoom <= ZOOM_MIN}
                  title="Zoom out"
                >
                  <ZoomOut className="h-4 w-4" />
                </Button>
                <button
                  className="w-12 text-center text-sm tabular-nums text-muted-foreground hover:text-foreground"
                  onClick={() => setZoom(1)}
                  title="Reset zoom"
                >
                  {Math.round(zoom * 100)}%
                </button>
                <Button
                  variant="outline"
                  size="icon-sm"
                  onClick={() => setZoom((z) => Math.min(ZOOM_MAX, +(z + ZOOM_STEP).toFixed(2)))}
                  disabled={zoom >= ZOOM_MAX}
                  title="Zoom in"
                >
                  <ZoomIn className="h-4 w-4" />
                </Button>
              </div>

              <Button variant="outline" size="sm" onClick={() => setShowOutlines((s) => !s)}>
                {showOutlines ? <EyeOff className="mr-2 h-4 w-4" /> : <Eye className="mr-2 h-4 w-4" />}
                {showOutlines ? "Hide rows" : "Show rows"}
              </Button>

              <Button
                variant={showOriginal ? "default" : "outline"}
                size="sm"
                onClick={() => setShowOriginal((s) => !s)}
                title="Show the original PDF next to the reconstruction"
              >
                <FileText className="mr-2 h-4 w-4" />
                {showOriginal ? "Hide original" : "Original PDF"}
              </Button>

              <span className="ml-auto text-sm text-muted-foreground">
                {totalRows} rows · {data.page_count} page{data.page_count === 1 ? "" : "s"}
              </span>
            </div>

            {data.warnings.length > 0 && (
              <div className="rounded-md border border-warning/30 bg-warning/12 p-2 text-xs text-warning">
                {data.warnings.join(" · ")}
              </div>
            )}

            {/* Bounded viewer window: all pages stacked and centered, so scrolling
                stays inside the viewer instead of moving the whole app. When the
                original PDF is toggled on, it sits side-by-side for comparison. */}
            <div className="flex gap-3">
              <div className="min-w-0 flex-1 max-h-[calc(100vh-13rem)] overflow-auto rounded-lg border bg-muted/30 p-6">
                <div className="flex flex-col items-center gap-8">
                  {pages.map((p, i) => (
                    <div
                      key={p.page_id}
                      ref={(el) => {
                        pageRefs.current[i] = el;
                      }}
                      className="w-fit space-y-1 scroll-mt-6"
                    >
                      <div className="text-xs font-medium text-muted-foreground">
                        Page {p.page_number}
                        <span className="text-muted-foreground/60"> · {p.rows.length} rows</span>
                      </div>
                      <LayoutCanvas page={p} zoom={zoom} showRowOutlines={showOutlines}>
                        {renderOverlays(p)}
                      </LayoutCanvas>
                    </div>
                  ))}
                </div>
              </div>

              {showOriginal && (
                <div className="min-w-0 flex-1 h-[calc(100vh-13rem)] overflow-hidden rounded-lg border bg-white">
                  <iframe
                    src={originalPdfUrl}
                    title="Original invoice PDF"
                    className="h-full w-full"
                  />
                </div>
              )}
            </div>

            <PreviewPanel
              draft={draft}
              primarySampleId={sampleId}
              primaryName={sampleName}
              target={target}
              defaultEvalLayoutId={profileId}
            />
          </div>

          {/* Editor panel */}
          <Card className="w-full shrink-0 lg:w-96">
            <CardContent className="pt-6">
              <Tabs value={tab} onValueChange={changeTab}>
                <TabsList className="grid h-auto w-full grid-cols-4 gap-1">
                  <TabsTrigger value="region">Region</TabsTrigger>
                  <TabsTrigger value="columns">Columns</TabsTrigger>
                  <TabsTrigger value="rows">Rows</TabsTrigger>
                  <TabsTrigger value="groups">Groups</TabsTrigger>
                  <TabsTrigger value="fields">Fields</TabsTrigger>
                  <TabsTrigger value="header">Header</TabsTrigger>
                  <TabsTrigger value="tax">Tax</TabsTrigger>
                </TabsList>
                <TabsContent value="region" className="mt-4 space-y-4">
                  <RegionEditorPanel
                    region={draft.region}
                    onChange={updateRegion}
                    pickMode={pickMode}
                    onSetPickMode={setPickMode}
                    classifiers={draft.row_classifiers}
                  />
                  <ColumnSplitPanel
                    region={draft.region}
                    suggested={suggestedSplit}
                    onChange={updateRegion}
                  />
                </TabsContent>
                <TabsContent value="columns" className="mt-4">
                  <ColumnEditorPanel
                    columns={draft.region.columns}
                    onChange={setColumns}
                    drawMode={drawMode}
                    onToggleDraw={() => setDrawMode((d) => !d)}
                    selectedIndex={selectedCol}
                    onSelect={setSelectedCol}
                  />
                </TabsContent>
                <TabsContent value="rows" className="mt-4">
                  <ClassifierEditorPanel
                    classifiers={draft.row_classifiers}
                    onChange={setClassifiers}
                    picking={rolePick}
                    onTogglePick={() => setRolePick((p) => !p)}
                    pendingRole={pendingRole}
                    onPendingRoleChange={setPendingRole}
                    columns={draft.region.columns}
                  />
                </TabsContent>
                <TabsContent value="groups" className="mt-4">
                  <GroupingEditorPanel
                    grouping={draft.grouping}
                    onChange={updateGrouping}
                    roles={availableRoles}
                    itemCount={groupItemCount}
                  />
                </TabsContent>
                <TabsContent value="fields" className="mt-4">
                  <FieldEditorPanel
                    fields={draft.item_fields}
                    onChange={setFields}
                    columns={draft.region.columns}
                    roles={availableRoles}
                    selectedField={selectedField}
                    onSelectField={setSelectedField}
                  />
                </TabsContent>
                <TabsContent value="header" className="mt-4">
                  <HeaderEditorPanel
                    headerFields={draft.header_fields}
                    onChange={setHeaderField}
                    pickField={headerPick}
                    onSetPickField={setHeaderPick}
                    pages={data.pages}
                  />
                </TabsContent>
                <TabsContent value="tax" className="mt-4">
                  <TaxEditorPanel tax={draft.tax} onChange={updateTax} />
                </TabsContent>
              </Tabs>
            </CardContent>
          </Card>
        </div>
      )}
    </div>
  );
}
