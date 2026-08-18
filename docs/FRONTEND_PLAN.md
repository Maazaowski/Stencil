# Stencil Frontend Plan

## Overview

A web application that provides a management interface for Stencil's invoice extraction pipeline. Users can upload invoices, create/edit supplier profiles, manage extraction models, monitor pipeline activity, review exceptions, and track system health — all connected to the FastAPI backend.

---

## Tech Stack Decision

| Layer | Technology | Rationale |
|-------|-----------|-----------|
| Framework | **Next.js 15 (App Router)** | Server components for data-heavy pages, file-based routing, built-in API route proxying |
| Language | **TypeScript** | Type safety across the full frontend, matches Pydantic backend contracts |
| UI Library | **shadcn/ui + Tailwind CSS v4** | Production-grade components, fully customizable, no runtime dependency |
| State Management | **TanStack Query (React Query)** | Server state caching, background refetching, optimistic updates — ideal for API-driven app |
| Forms | **React Hook Form + Zod** | Performant forms with schema-based validation matching backend Pydantic models |
| Tables | **TanStack Table** | Headless table logic for sortable, filterable, paginated data grids |
| Charts | **Recharts** | Lightweight charting for dashboard metrics (cost trends, volume, confidence) |
| File Upload | **react-dropzone** | Drag-and-drop PDF upload with progress |
| HTTP Client | **ky** (or native fetch) | Lightweight, works with Next.js server/client boundary |
| Icons | **Lucide React** | Consistent icon set, tree-shakeable |
| Notifications | **Sonner** | Toast notifications for pipeline events |
| Date handling | **date-fns** | Lightweight date formatting/manipulation |

### Why Next.js over Vite + React SPA?

1. **Server components** — Heavy data tables (intake records, processing logs) render on the server, reducing client bundle
2. **API route proxying** — Next.js API routes can proxy to FastAPI, avoiding CORS complexity in development
3. **File-based routing** — Natural page structure for a multi-page management app
4. **Streaming** — Server-sent events for live pipeline status work naturally with Next.js
5. **Deployment flexibility** — Can deploy as static export if needed, or full SSR

---

## Backend API Layer (To Be Built)

The existing FastAPI backend has only a `/health` endpoint. The frontend requires a REST API layer. These endpoints need to be added to the backend as **FastAPI routers**.

### API Design Principles

- RESTful resource-based URLs
- JSON request/response bodies
- Pydantic response models (reuse existing schemas where possible)
- Pagination via `?page=1&per_page=25` query params
- Filtering via query params (e.g., `?status=completed&supplier=GTT`)
- Standard error responses: `{ "detail": "message" }`
- File uploads via `multipart/form-data`
- WebSocket or SSE for live pipeline updates

### Required API Endpoints

#### Intake / Invoice Upload

```
POST   /api/v1/invoices/upload          Upload one or more PDFs, triggers pipeline
GET    /api/v1/invoices                 List all intake records (paginated, filterable)
GET    /api/v1/invoices/{intake_id}     Get single intake record with full details
GET    /api/v1/invoices/{intake_id}/jobs       Get extraction jobs for an intake
GET    /api/v1/invoices/{intake_id}/logs       Get processing logs for an intake
GET    /api/v1/invoices/{intake_id}/output     Download output files (XLSX, JSON)
DELETE /api/v1/invoices/{intake_id}     Delete an intake record (soft delete)
```

#### Supplier Profiles

```
GET    /api/v1/profiles                 List all supplier profiles
GET    /api/v1/profiles/{profile_id}    Get single profile
POST   /api/v1/profiles                 Create new profile
PUT    /api/v1/profiles/{profile_id}    Update profile
DELETE /api/v1/profiles/{profile_id}    Delete/retire profile
POST   /api/v1/profiles/validate        Validate profile JSON without saving
```

#### Extraction Models

```
GET    /api/v1/models                   List all extraction models (paginated, filterable)
GET    /api/v1/models/{model_id}        Get single model with rules JSON
PUT    /api/v1/models/{model_id}/approve    Approve a draft model
PUT    /api/v1/models/{model_id}/retire     Retire an approved model
GET    /api/v1/models/{model_id}/usage      Get usage statistics
DELETE /api/v1/models/{model_id}        Delete a model
```

#### Exceptions

```
GET    /api/v1/exceptions               List all exception packages (paginated)
GET    /api/v1/exceptions/{intake_id}   Get exception details + files
PUT    /api/v1/exceptions/{intake_id}/resolve   Mark exception as resolved
POST   /api/v1/exceptions/{intake_id}/retry     Re-run pipeline for this invoice
```

#### Dashboard / Analytics

```
GET    /api/v1/dashboard/stats          Summary stats (total invoices, success rate, etc.)
GET    /api/v1/dashboard/volume         Invoice volume over time
GET    /api/v1/dashboard/costs          AI cost breakdown over time
GET    /api/v1/dashboard/models         Model usage and hit-rate stats
GET    /api/v1/dashboard/exceptions     Exception rate and common reasons
```

#### Pipeline Control

```
GET    /api/v1/pipeline/status          Current pipeline health (watcher status, queue depth)
POST   /api/v1/pipeline/process         Manually trigger processing for a specific PDF
WS     /api/v1/pipeline/live            WebSocket for real-time pipeline events
```

#### System

```
GET    /api/v1/system/health            Health check (DB, Redis, filesystem)
GET    /api/v1/system/config            Non-sensitive configuration values
```

### Connection Architecture

```
┌─────────────────────┐     ┌─────────────────────────────┐
│   Next.js Frontend  │     │     FastAPI Backend          │
│                     │     │                              │
│  Browser            │     │  /api/v1/invoices/*          │
│    |                │     │  /api/v1/profiles/*          │
│    v                │     │  /api/v1/models/*            │
│  Next.js API Routes │────>│  /api/v1/exceptions/*        │
│  /api/proxy/*       │     │  /api/v1/dashboard/*         │
│  (dev only)         │     │  /api/v1/pipeline/*          │
│                     │     │                              │
│  In production:     │     │  PostgreSQL                  │
│  Direct fetch to    │     │  Redis/Celery                │
│  FastAPI URL        │     │  File System                 │
└─────────────────────┘     └─────────────────────────────┘
```

**Development:** Next.js `rewrites` in `next.config.ts` proxy `/api/v1/*` to `http://localhost:8000/api/v1/*`
**Production:** Environment variable `NEXT_PUBLIC_API_URL` points to the FastAPI server. CORS configured on FastAPI.

---

## Page Structure & Features

### 1. Dashboard (`/`)

The landing page showing system health at a glance.

**Components:**
- **Stats Cards** — Total invoices processed, success rate, exceptions pending, AI cost this month
- **Volume Chart** — Line/bar chart: invoices per day/week over last 30 days
- **Cost Chart** — AI cost over time, trending down as models accumulate
- **Recent Activity Feed** — Last 10 pipeline events (intake, extraction, completion, exception)
- **Model Coverage** — Pie chart: % invoices using model path vs. AI path
- **Exception Alerts** — Unresolved exceptions requiring attention

### 2. Invoice Upload (`/upload`)

Drag-and-drop interface for submitting invoices.

**Components:**
- **Dropzone** — Drag PDFs or click to browse. Accept `.pdf` only, max 50MB per file
- **Upload Queue** — Shows files pending upload with progress bars
- **Upload History** — Recent uploads with status badges (processing, completed, exception)
- **Bulk Upload** — Support for uploading multiple PDFs at once
- **Live Status** — After upload, show real-time pipeline progress via WebSocket

**Flow:**
1. User drops PDF(s) into dropzone
2. Files uploaded to `POST /api/v1/invoices/upload`
3. Backend returns intake_id(s)
4. UI subscribes to WebSocket for live status updates
5. Shows step-by-step progress: Intake -> Fingerprint -> Classification/Model -> Extraction -> Reconciliation -> Output
6. On completion: download link for XLSX + JSON

### 3. Invoice List (`/invoices`)

Searchable, filterable table of all processed invoices.

**Components:**
- **Data Table** — Columns: intake_id, filename, supplier, status, extraction path, confidence, created_at
- **Filters** — Status (received, processing, completed, failed), supplier, date range, extraction path (AI/model)
- **Search** — Full-text search by filename, invoice number, supplier
- **Bulk Actions** — Select multiple, re-process, download
- **Status Badges** — Color-coded: green (completed), yellow (processing), red (failed/exception)

### 4. Invoice Detail (`/invoices/[intake_id]`)

Full details for a single invoice.

**Components:**
- **Header Info** — Supplier, invoice number, date, total, status
- **Extraction Summary** — Path used (AI/model), model ID, confidence, duration, token count, cost
- **Line Items Table** — All extracted line items with amounts, charge types, confidence scores
- **Reconciliation Card** — Computed vs. stated totals, variance, reconciled status
- **Processing Timeline** — Step-by-step log with timestamps (expandable details)
- **Output Files** — Download buttons for XLSX, canonical JSON, extraction log, manifest
- **PDF Preview** — Embedded PDF viewer showing the original invoice (optional, Phase 2)
- **Actions** — Re-process, send to exception queue

### 5. Supplier Profiles (`/profiles`)

CRUD management for supplier profiles.

**Components:**
- **Profile List** — Cards or table: profile_id, supplier name, output type, status, last updated
- **Profile Editor** — Full form for creating/editing profiles:

**Profile Editor Form Sections:**

| Section | Fields |
|---------|--------|
| **Identity** | Canonical name, aliases (tag input), domains, invoice number patterns (regex), account number patterns |
| **Classification** | Output type (dropdown: standard/wireless/T&M), keywords (tag input), page count hint |
| **Directory Paths** | Customer ID, account number, inbound path override (e.g., `D:\Astera\Invoices\{customer_id}\{account_number}\pdf`), output path override (e.g., `...\xls`). Preview of resolved paths. |
| **Document Structure** | Header pages (number list), detail start/end markers, ignored sections (tag input), table header keywords |
| **Header Mapping** | Label fields for: invoice number, invoice date, due date, account number, total due, billing period, currency |
| **Line Item Hints** | Service ID patterns (regex, editable list), charge type map (key-value editor), subtotal/tax/fee keywords |
| **Reconciliation** | Variance threshold (slider + number), expect tax line (toggle), expect subtotal line (toggle) |
| **Notes** | Extraction notes (textarea) |

**Features:**
- **Live Validation** — Zod schema validates as user types, shows errors inline
- **JSON Preview** — Toggle to see the raw JSON that will be saved
- **Test Profile** — Upload a sample PDF to test profile against (optional, Phase 2)
- **Version History** — See previous versions of the profile
- **Import/Export** — Download profile as JSON, upload JSON to create profile

### 6. Extraction Models (`/models`)

Management interface for AI-generated extraction models.

**Components:**
- **Model List** — Table: model_id, supplier, layout fingerprint, status, confidence, times_used, last_used, created_at
- **Status Filters** — draft, approved, retired
- **Model Detail** — Expandable view showing:
  - Header rules (table of field -> regex pattern -> page region)
  - Table rules (start/end markers, column definitions)
  - Reconciliation rules
  - Usage stats (times used, success rate, last used date)
  - Source intake (link to the invoice that generated this model)
- **Approval Workflow** — "Approve" button on draft models (with confirmation dialog)
- **Retire** — "Retire" button on approved models
- **Comparison View** — Compare two model versions side by side (optional, Phase 2)

### 7. Exceptions (`/exceptions`)

Exception queue for failed or uncertain invoices requiring human review.

**Components:**
- **Exception List** — Table: intake_id, reason, supplier, message, created_at, resolved status
- **Reason Filters** — unknown_supplier, low_confidence, reconciliation_failure, etc.
- **Exception Detail** — Shows:
  - Error log (reason, message, timestamp)
  - Partial extraction data (if available)
  - Original PDF download
  - Processing timeline showing where it failed
- **Actions:**
  - **Retry** — Re-process the invoice through the pipeline
  - **Resolve** — Mark as resolved with notes
  - **Create Profile** — Quick link to create a new supplier profile for unknown_supplier exceptions
  - **Adjust Model** — Quick link to the model management page for model-related exceptions

### 8. Audit & Logging (`/logs`)

Comprehensive logging and audit trail for all pipeline activity.

**Components:**
- **Processing Log Table** — Full audit log: intake_id, step, status, message, timestamp. Filterable by intake_id, step, status, date range
- **Token Usage Report** — Table showing per-invoice AI token consumption: intake_id, supplier, model used, tokens_input, tokens_output, estimated cost. Aggregated totals by day/week/month
- **Cost Dashboard** — AI cost over time chart, cost per supplier breakdown, cumulative spend, projected monthly cost
- **Error Log** — Filterable view of all errors and warnings across the system
- **Export** — Download log data as CSV for external reporting

### 9. Settings (`/settings`)

Application configuration (non-sensitive).

**Components:**
- **General** — App name, debug mode, log level
- **Thresholds** — Confidence threshold, model confidence threshold, reconciliation variance threshold
- **Directories** — Display current data directory paths (read-only), per-supplier path template
- **AI Configuration** — Model selection (display only for security), max retries, timeout
- **File Watcher** — Poll interval, stability seconds, enable/disable, list of watched directories
- **Database** — Connection status, database type (MySQL/PostgreSQL), migration status

---

## Frontend Project Structure

```
frontend/
  package.json
  next.config.ts
  tsconfig.json
  tailwind.config.ts
  postcss.config.js

  src/
    app/
      layout.tsx                    # Root layout with sidebar navigation
      page.tsx                      # Dashboard
      loading.tsx                   # Global loading skeleton

      upload/
        page.tsx                    # Invoice upload dropzone

      invoices/
        page.tsx                    # Invoice list
        [intakeId]/
          page.tsx                  # Invoice detail

      profiles/
        page.tsx                    # Supplier profile list
        new/
          page.tsx                  # Create new profile
        [profileId]/
          page.tsx                  # Edit profile

      models/
        page.tsx                    # Extraction model list
        [modelId]/
          page.tsx                  # Model detail

      exceptions/
        page.tsx                    # Exception queue
        [intakeId]/
          page.tsx                  # Exception detail

      logs/
        page.tsx                    # Audit log & token usage
        costs/
          page.tsx                  # AI cost reporting

      settings/
        page.tsx                    # Application settings

    components/
      layout/
        sidebar.tsx                 # Main navigation sidebar
        header.tsx                  # Top bar with breadcrumbs, user info
        page-header.tsx             # Page title + description + actions

      ui/                           # shadcn/ui components (auto-generated)
        button.tsx
        card.tsx
        dialog.tsx
        table.tsx
        badge.tsx
        input.tsx
        select.tsx
        tabs.tsx
        toast.tsx
        ... (generated via shadcn CLI)

      dashboard/
        stats-cards.tsx             # KPI cards row
        volume-chart.tsx            # Invoice volume over time
        cost-chart.tsx              # AI cost trend chart
        activity-feed.tsx           # Recent pipeline events
        model-coverage-chart.tsx    # AI vs. model path pie chart

      invoices/
        invoice-table.tsx           # Sortable, filterable invoice data table
        invoice-filters.tsx         # Filter bar (status, supplier, date)
        invoice-status-badge.tsx    # Color-coded status badges
        line-items-table.tsx        # Line items sub-table in detail view
        processing-timeline.tsx     # Step-by-step processing log
        reconciliation-card.tsx     # Reconciliation summary card

      upload/
        dropzone.tsx                # PDF drag-and-drop upload area
        upload-queue.tsx            # File upload progress list
        pipeline-progress.tsx       # Live pipeline step tracker

      profiles/
        profile-form.tsx            # Full profile editor form
        profile-card.tsx            # Profile summary card for list view
        identity-section.tsx        # Identity sub-form
        classification-section.tsx  # Classification sub-form
        structure-section.tsx       # Document structure sub-form
        header-mapping-section.tsx  # Header mapping sub-form
        line-item-section.tsx       # Line item hints sub-form
        reconciliation-section.tsx  # Reconciliation config sub-form
        tag-input.tsx               # Reusable tag/chip input for arrays
        key-value-editor.tsx        # Key-value pair editor for maps
        regex-pattern-list.tsx      # Editable regex pattern list

      models/
        model-table.tsx             # Model list data table
        model-detail-panel.tsx      # Expandable model detail view
        model-rules-viewer.tsx      # JSON rules display (formatted)
        approval-dialog.tsx         # Confirm model approval
        usage-stats.tsx             # Model usage statistics

      exceptions/
        exception-table.tsx         # Exception list data table
        exception-detail.tsx        # Exception detail view
        resolve-dialog.tsx          # Mark exception as resolved

      shared/
        data-table.tsx              # Generic TanStack Table wrapper
        pagination.tsx              # Pagination controls
        search-input.tsx            # Debounced search input
        date-range-picker.tsx       # Date range filter
        json-viewer.tsx             # Syntax-highlighted JSON viewer
        file-download-button.tsx    # Download button with loading state
        confirm-dialog.tsx          # Reusable confirmation dialog
        empty-state.tsx             # Empty state placeholder
        error-boundary.tsx          # Error boundary with retry

    lib/
      api/
        client.ts                   # Base API client (fetch wrapper with auth, error handling)
        invoices.ts                 # Invoice API functions
        profiles.ts                 # Profile API functions
        models.ts                   # Extraction model API functions
        exceptions.ts               # Exception API functions
        dashboard.ts                # Dashboard/analytics API functions
        pipeline.ts                 # Pipeline control API functions
        types.ts                    # TypeScript types matching backend Pydantic models

      hooks/
        use-invoices.ts             # TanStack Query hooks for invoice data
        use-profiles.ts             # TanStack Query hooks for profiles
        use-models.ts               # TanStack Query hooks for models
        use-exceptions.ts           # TanStack Query hooks for exceptions
        use-dashboard.ts            # TanStack Query hooks for dashboard stats
        use-pipeline-status.ts      # WebSocket hook for live pipeline events
        use-debounce.ts             # Debounce hook for search

      utils/
        formatters.ts               # Date, currency, percentage formatters
        validators.ts               # Zod schemas for form validation
        constants.ts                # Status values, charge types, exception reasons

    types/
      api.ts                        # API response types
      invoice.ts                    # Invoice-related types
      profile.ts                    # SupplierProfile type (mirrors backend)
      model.ts                      # ExtractionModel type (mirrors backend)
```

---

## Type Safety: Backend-Frontend Contract

TypeScript types mirror the backend Pydantic models exactly. This ensures the frontend never drifts from the API contract.

### Key Type Definitions

```typescript
// types/invoice.ts — mirrors stencil.validation.schema

type ChargeType = 'recurring' | 'one_time' | 'tax' | 'fee' | 'credit'
                | 'adjustment' | 'surcharge' | 'usage' | 'unknown';

type OutputType = 'standard' | 'wireless' | 'time_and_material';

type ExtractionPath = 'ai' | 'model' | 'model_fallback_ai';

interface LineItem {
  line_number: number;
  service_id: string | null;
  description: string;
  charge_type: ChargeType;
  amount: number;
  currency: string;
  billing_period_start: string | null;
  billing_period_end: string | null;
  quantity: number | null;
  unit_rate: number | null;
  source_page: number | null;
  confidence: number;
}

interface InvoiceHeader {
  supplier_name: string;
  invoice_number: string;
  invoice_date: string;
  due_date: string | null;
  account_number: string | null;
  currency: string;
  // ... other fields
}

interface IntakeRecord {
  id: string;
  original_filename: string;
  status: 'received' | 'processing' | 'completed' | 'failed';
  layout_fingerprint: string | null;
  page_count: number;
  file_size_bytes: number;
  created_at: string;
  updated_at: string;
  error_message: string | null;
}

interface ExtractionJob {
  id: string;
  intake_id: string;
  extraction_path: ExtractionPath;
  extraction_model_id: string | null;
  supplier_name: string | null;
  status: string;
  overall_confidence: number;
  line_item_count: number;
  tokens_input: number;
  tokens_output: number;
  estimated_cost_usd: number;
  extraction_duration_ms: number;
  is_reconciled: boolean | null;
  reconciliation_variance: number | null;
  started_at: string | null;
  completed_at: string | null;
}
```

```typescript
// types/profile.ts — mirrors stencil.profiles.schema

interface SupplierIdentity {
  canonical_name: string;
  aliases: string[];
  domains: string[];
  invoice_number_patterns: string[];
  account_number_patterns: string[];
}

interface ClassificationSignals {
  output_type: OutputType;
  keywords: string[];
  page_count_hint: string | null;
}

interface DocumentStructure {
  header_pages: number[];
  detail_start_marker: string | null;
  detail_end_marker: string | null;
  ignored_sections: string[];
  table_header_keywords: string[];
}

interface HeaderMapping {
  invoice_number_label: string;
  invoice_date_label: string;
  due_date_label: string | null;
  account_number_label: string | null;
  total_due_label: string;
  billing_period_label: string | null;
  currency_label: string | null;
}

interface LineItemHints {
  service_id_patterns: string[];
  charge_type_map: Record<string, string>;
  subtotal_keywords: string[];
  tax_keywords: string[];
  fee_keywords: string[];
}

interface ReconciliationConfig {
  variance_threshold: number;
  expect_tax_line: boolean;
  expect_subtotal_line: boolean;
}

interface SupplierProfile {
  profile_id: string;
  version: number;
  status: 'active' | 'deprecated' | 'retired';
  owner: string | null;
  created_date: string | null;
  last_updated_date: string | null;
  identity: SupplierIdentity;
  classification: ClassificationSignals;
  document_structure: DocumentStructure;
  header_mapping: HeaderMapping;
  line_item_hints: LineItemHints;
  reconciliation: ReconciliationConfig;
  extraction_notes: string | null;
}
```

---

## UI Design System

### Layout
- **Sidebar navigation** — Fixed left sidebar (collapsible), icons + labels for each page
- **Content area** — Main content with page header (title, description, action buttons)
- **Responsive** — Sidebar collapses to icon-only on smaller screens, mobile-friendly tables

### Color Palette (Dark/Light mode support)
- **Primary** — Blue (#4472C4) — matches the XLSX header color already in use
- **Success** — Green — completed, reconciled, approved
- **Warning** — Amber — low confidence, draft models, reconciliation warnings
- **Danger** — Red — failed, exception, reconciliation failure
- **Neutral** — Gray scale for backgrounds, borders, secondary text

### Status Badge Colors
| Status | Color | Context |
|--------|-------|---------|
| received | Gray | Intake just created |
| processing | Blue | Pipeline running |
| completed | Green | Successfully extracted |
| failed | Red | Pipeline error |
| draft | Amber | Model awaiting approval |
| approved | Green | Model in production |
| retired | Gray | Model no longer used |

---

## Real-Time Updates

### WebSocket Connection

```typescript
// Live pipeline events via WebSocket
const ws = new WebSocket(`${API_URL}/api/v1/pipeline/live`);

ws.onmessage = (event) => {
  const data = JSON.parse(event.data);
  // data.type: 'intake' | 'fingerprint' | 'classification' | 'extraction' |
  //            'reconciliation' | 'output' | 'exception' | 'completed'
  // data.intake_id: string
  // data.step: string
  // data.status: string
  // data.message: string
};
```

**Backend implementation:** FastAPI WebSocket endpoint that subscribes to Redis pub/sub channel. Pipeline processor publishes events to Redis at each step.

---

## Implementation Phases

### Phase F1: Project Setup & Core Layout (Week 1)
**Tasks:**
- Initialize Next.js 15 project with TypeScript
- Configure Tailwind CSS v4 + shadcn/ui
- Set up TanStack Query provider
- Build sidebar layout with navigation
- Create page header component
- Set up API client with base URL configuration
- Configure Next.js rewrites for API proxying
- Create TypeScript type definitions matching backend schemas

**Backend work needed:**
- Add CORS middleware to FastAPI app
- No new endpoints yet (use mock data on frontend)

### Phase F2: Backend API Endpoints (Week 2)
**Tasks (backend):**
- Create FastAPI routers for all resource endpoints
- Add Pydantic response schemas for list/detail views
- Implement pagination helper
- Add file upload endpoint (multipart/form-data)
- Add file download endpoints
- Write API tests

**Tasks (frontend):**
- Build API client functions for each endpoint
- Create TanStack Query hooks
- Build reusable data table component (TanStack Table)

### Phase F3: Dashboard & Invoice Pages (Week 2-3)
**Tasks:**
- Dashboard stats cards (fetching from `/api/v1/dashboard/stats`)
- Volume and cost charts (Recharts)
- Activity feed component
- Invoice list page with data table, filters, search
- Invoice detail page with line items, reconciliation, timeline
- Status badge component
- File download buttons

### Phase F4: Invoice Upload (Week 3)
**Tasks:**
- Dropzone component with react-dropzone
- Upload queue with progress indicators
- WebSocket integration for live pipeline status
- Pipeline progress stepper component
- Post-upload result display (download links)

**Backend work needed:**
- WebSocket endpoint on FastAPI
- Redis pub/sub for pipeline events
- Pipeline processor publishes events at each step

### Phase F5: Supplier Profile Management (Week 4)
**Tasks:**
- Profile list page (cards view)
- Full profile editor form with all sections
- Tag input component (for aliases, keywords, patterns)
- Key-value editor (for charge_type_map)
- Regex pattern list editor
- Form validation with Zod (matching backend schema)
- JSON preview toggle
- Import/export functionality

**Backend work needed:**
- Profile CRUD endpoints (currently profiles are JSON files, need API layer)

### Phase F6: Extraction Model Management (Week 5)
**Tasks:**
- Model list page with data table
- Model detail view with rules viewer
- Approval workflow (button -> confirmation dialog -> API call)
- Retire workflow
- Usage statistics display
- Model status filters (draft/approved/retired)

### Phase F7: Exception Management (Week 5)
**Tasks:**
- Exception list page with data table
- Exception detail view with error log
- Resolve dialog (mark resolved with notes)
- Retry button (re-process through pipeline)
- Quick-link to create supplier profile for unknown_supplier exceptions
- Reason code filters

### Phase F8: Settings & Polish (Week 6)
**Tasks:**
- Settings page (read/write for non-sensitive config)
- Dark mode toggle
- Loading skeletons for all pages
- Error boundaries with retry
- Empty states for all list pages
- Keyboard shortcuts (/, Ctrl+K for search)
- Responsive design pass
- Accessibility audit (ARIA labels, focus management)

### Phase F9: Integration Testing & Deployment (Week 7)
**Tasks:**
- End-to-end testing (upload PDF -> see result in UI)
- Cross-browser testing
- Performance profiling (Lighthouse)
- Docker setup (Next.js + FastAPI in docker-compose)
- Environment-based configuration
- Production build optimization

---

## Backend Changes Required (Summary)

The frontend plan requires these additions to the existing backend:

### New Files

```
src/stencil/
  api/
    __init__.py
    router.py              # Main API router, includes all sub-routers
    invoices.py            # Invoice/intake endpoints
    profiles.py            # Supplier profile CRUD endpoints
    models.py              # Extraction model endpoints
    exceptions.py          # Exception queue endpoints
    dashboard.py           # Analytics/stats endpoints
    pipeline.py            # Pipeline control + WebSocket
    schemas.py             # Pydantic response schemas (pagination, lists)
    deps.py                # Shared dependencies (DB session, pagination params)
```

### Changes to Existing Files

| File | Change |
|------|--------|
| `main.py` | Add CORS middleware, mount API router, add WebSocket endpoint |
| `config.py` | Add `cors_origins`, `frontend_url`, `database_url` (MySQL), per-supplier path template settings |
| `pipeline/processor.py` | Publish events to Redis at each pipeline step |
| `profiles/loader.py` | Add write/delete operations (currently read-only) |
| `profiles/schema.py` | Add `customer_id`, `account_number`, `inbound_path`, `output_path` fields |
| `db/session.py` | MySQL connection string support (pymysql driver) |
| `db/crud.py` | Add list/filter/paginate queries, dashboard aggregation queries, token usage reports |
| `intake/watcher.py` | Support watching multiple directories (one per supplier/customer) |
| `intake/service.py` | Route output to per-supplier output directories |

### New Database Migration

```
002_add_profile_table.py    # If profiles move from JSON files to DB (recommended for UI)
003_mysql_compatibility.py  # Any MySQL-specific adjustments (JSON column type, etc.)
```

### New Dependencies

```
pymysql                     # MySQL driver for SQLAlchemy
pywin32 or nssm             # Windows Service support
```

---

## Key Design Decisions

1. **Next.js App Router** — Server components for data-heavy pages, natural routing structure
2. **shadcn/ui** — No vendor lock-in, components live in the project, fully customizable
3. **TanStack Query** — Server state management is the right model for this API-driven app (not Redux/Zustand)
4. **API proxy in dev** — Avoids CORS complexity during development, clean separation in production
5. **TypeScript types mirror Pydantic** — Single source of truth on the backend, frontend stays in sync
6. **WebSocket for live updates** — Real-time pipeline progress is critical UX for invoice upload
7. **Profile editor as forms, not raw JSON** — Users should not need to write JSON to create profiles
8. **Dashboard first** — The landing page should immediately show system health and recent activity
9. **Supplier profiles may move to DB** — Currently JSON files; a DB-backed approach enables version history, audit trail, and multi-user access from the UI
10. **MySQL over PostgreSQL** — Reuse existing Temforce MySQL instance rather than managing a separate database. SQLAlchemy makes this a connection string change
11. **Per-supplier directory paths** — Match existing Astera folder structure (`D:\Astera\Invoices\{customer_id}\{account_number}\pdf`) so the AP bot requires zero changes
12. **Windows Service deployment** — Production runs on existing Windows Server 2025 via NSSM, auto-starts on boot, restarts on failure
13. **Full audit logging with cost tracking** — Every invoice tracked with token usage, estimated cost, extraction path, timestamps. Dedicated logging/audit page in UI for reporting

---

## Deployment Architecture

### Production: Windows Server 2025

The primary deployment target is an existing Windows Server 2025 machine with an existing MySQL instance.

```
┌──────────────────────────────────────────────────────────────────┐
│                 Windows Server 2025                               │
│                                                                  │
│  Windows Services (via NSSM):                                    │
│  ┌─────────────┐  ┌──────────────┐  ┌───────────────┐          │
│  │  Next.js     │  │  FastAPI     │  │  Celery       │          │
│  │  Frontend    │  │  Backend     │  │  Worker       │          │
│  │  :3000       │  │  :8000       │  │               │          │
│  └──────┬──────┘  └──────┬───────┘  └───────┬───────┘          │
│         │                │                   │                   │
│         │                ▼                   ▼                   │
│         │         ┌──────────────┐  ┌───────────────┐           │
│         └────────>│  MySQL       │  │    Redis      │           │
│                   │  (existing)  │  │    :6379      │           │
│                   └──────────────┘  └───────────────┘           │
│                                                                  │
│  File system:                                                    │
│    D:\Astera\Invoices\{customer_id}\{account_number}\pdf  (in)  │
│    D:\Astera\Invoices\{customer_id}\{account_number}\xls  (out) │
│    D:\Stencil\archive\                                      │
│    D:\Stencil\exceptions\                                   │
│    D:\Stencil\supplier_profiles\                            │
│    D:\Stencil\extraction_models\                            │
└──────────────────────────────────────────────────────────────────┘

Services auto-start on boot and restart on failure.
Logs written to Windows Event Log + structured JSON files.
```

### Development: Docker Compose (optional)

For local development, a Docker Compose setup is also available:

```
┌──────────────────────────────────────────────────────────┐
│                    Docker Compose                         │
│                                                          │
│  ┌─────────────┐  ┌──────────────┐  ┌───────────────┐  │
│  │  Next.js     │  │  FastAPI     │  │  Celery       │  │
│  │  Frontend    │  │  Backend     │  │  Worker       │  │
│  │  :3000       │  │  :8000       │  │               │  │
│  └──────┬──────┘  └──────┬───────┘  └───────┬───────┘  │
│         │                │                   │           │
│         │                ▼                   ▼           │
│         │         ┌──────────────┐  ┌───────────────┐   │
│         └────────>│  MySQL       │  │    Redis      │   │
│                   │  :3306       │  │    :6379      │   │
│                   └──────────────┘  └───────────────┘   │
│                                                          │
│  Shared volumes:                                         │
│    - stencil_data/                                  │
│    - supplier_profiles/                                  │
│    - extraction_models/                                  │
└──────────────────────────────────────────────────────────┘
```
