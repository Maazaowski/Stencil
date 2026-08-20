# Stencil

**AI-powered invoice extraction that learns.** Drop a PDF, get structured data. The first invoice uses AI. Every invoice after that is free.

Stencil replaces brittle, per-supplier report models (Astera ReportMiner) with a hybrid system where AI builds reusable extraction models for new layouts, and known layouts use those models directly -- keeping costs near zero while handling format diversity.

---

## How It Works

```
                    +------------------+
                    |   Invoice PDF    |
                    |   (dropped in    |
                    |  inbound folder) |
                    +--------+---------+
                             |
                    +--------v---------+
                    |  1. INTAKE        |  Register, archive, move to processing
                    +--------+---------+
                             |
                    +--------v---------+
                    |  2. FINGERPRINT   |  Hash the PDF layout (no AI, pymupdf)
                    +--------+---------+
                             |
                 +-----------+-----------+
                 |                       |
          Known layout?            New layout?
                 |                       |
        +--------v---------+   +--------v---------+
        |  FAST PATH        |   |  SLOW PATH        |
        |  (model, $0)      |   |  (AI, ~$0.10-0.50)|
        |                   |   |                   |
        |  Apply saved      |   |  Classify supplier|
        |  extraction model |   |  Extract via AI   |
        |  (interpreter)    |   |  Author rules     |
        |  No API calls     |   |  Save candidate   |
        +--------+---------+   +--------+---------+
                 |                       |
                 +-----------+-----------+
                             |
                    +--------v---------+
                    |  3. RECONCILE     |  Verify math (deterministic, no AI)
                    +--------+---------+
                             |
                    +--------v---------+
                    |  4. OUTPUT        |  XLSX + JSON + manifest
                    +--------+---------+
                             |
                    +--------v---------+
                    |  5. READY         |  manifest.json = pickup signal
                    |  for Temforce     |  for Temforce Bot Agent
                    +------------------+
```

**The key insight:** AI is the model *author*, not the model *runner*. For a new layout, AI extracts ground truth once, then authors a declarative `ExtractionModel` (layout rules in JSON). A generic **interpreter** executes that document on every subsequent invoice with the same profile + fingerprint — no supplier-specific Python and no per-invoice AI cost.

### Architecture (current)

| Layer | Role |
|---|---|
| **Supplier profiles** (`supplier_profiles/*.json`) | Routing, header hints, training config — supplier knowledge lives here only |
| **ExtractionModel** (JSON + DB) | Declarative rules: region anchors, row classifiers, column bands, transforms, self-checks |
| **`interpreter.py`** | Generic executor — knows layout primitives, not any supplier |
| **`authoring.py` + `training.py`** | Grounded loop: AI authors rules → execute → diff deliverable rows → refine |
| **`diff.py`** | Compares Temforce 8-column output (the delivery contract) |
| **Profiles UI** | Training uploads, candidate approval, model review downloads |

Legacy per-account templates and the old regex **executor** have been removed. Models are keyed by **`supplier_profile_id` + `layout_fingerprint`**.

### Useful API endpoints

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/v1/invoices` | List/filter intakes (`suppliers`, `extraction_path`, `status`, …) |
| `GET` | `/api/v1/invoices/facets/suppliers` | Distinct supplier names for list filters |
| `GET` | `/api/v1/invoices/{id}` | Detail + jobs + processing logs |
| `POST` | `/api/v1/invoices/{id}/cancel` | Cancel in-flight processing |
| `GET` | `/api/v1/invoices/{id}/model-review/{file}` | Training comparison artifacts |
| `DELETE` | `/api/v1/invoices/purge` | **Debug only** — wipe all intakes + work dirs |
| `WS` | `/api/v1/pipeline/live/{id}` | Live pipeline events (Redis pub/sub) |

Interactive docs: http://localhost:8000/docs

---

## Tech Stack

| Layer | Technology |
|---|---|
| **Frontend** | Next.js 16 (App Router), React 19, TypeScript |
| **UI Components** | shadcn/ui v4 (base-ui), Tailwind CSS v4 |
| **Data Fetching** | TanStack Query v5 (React Query) |
| **Tables** | TanStack Table v8 |
| **Charts** | Recharts (bar, line, pie) |
| **Backend** | Python 3.12+, FastAPI |
| **AI** | OpenAI Responses API (gpt-5.5, Structured Outputs) |
| **PDF Processing** | pymupdf (fingerprinting, page rendering) |
| **Database** | MySQL (PyMySQL) + SQLAlchemy + Alembic |
| **Task Queue** | Celery + Redis |
| **Excel Output** | openpyxl |
| **Validation** | Pydantic v2 |
| **File Watching** | watchdog |
| **Logging** | structlog (JSON) |

---

## Prerequisites

Before you begin, make sure you have these installed:

| Requirement | Version | Check Command |
|---|---|---|
| Python | 3.12+ | `python --version` |
| Node.js | 20+ | `node --version` |
| MySQL | 8+ | `mysql --version` |
| Redis | 7+ | `redis-cli ping` |
| pip | latest | `pip --version` |

You will also need an **OpenAI API key** with access to the configured models (default: `gpt-5.5`).

---

## Quick Start (Docker)

The fastest way to run Stencil. Requires only [Docker Desktop](https://www.docker.com/products/docker-desktop/).

```bash
# 1. Clone the repo
git clone https://github.com/your-org/Stencil.git
cd Stencil

# 2. Configure your OpenAI key
cp .env.docker .env
# Edit .env and set ST_OPENAI_API_KEY=sk-your-key-here

# 3. Start everything
docker compose up -d

# 4. Open the app
# Frontend:  http://localhost:3000
# API docs:  http://localhost:8000/docs
```

To update to a new version:
```bash
git pull
docker compose up -d --build
```

To stop:
```bash
docker compose down
```

Data is persisted in Docker volumes (`mysql_data`) and host-mounted folders (`ST_HOST_DATA_DIR`, `ST_HOST_WORK_DIR`). See [Folder Structure](#folder-structure) for the data vs work split.

---

## Manual Setup (Step by Step)

### Step 1: Clone and Install

```bash
# Clone the repository
git clone https://github.com/your-org/Stencil.git
cd Stencil

# Create a virtual environment
python -m venv .venv

# Activate it
# Windows:
.venv\Scripts\activate
# macOS/Linux:
source .venv/bin/activate

# Install the backend with all dependencies
pip install -e ".[dev]"

# Install the frontend
cd frontend
npm install
cd ..
```

### Step 2: Configure Environment

```bash
# Copy the example env file
cp .env.example .env
```

Open `.env` and fill in your values:

```env
# REQUIRED - your OpenAI API key
ST_OPENAI_API_KEY=sk-your-actual-key-here

# REQUIRED - MySQL connection
ST_DATABASE_URL=mysql+pymysql://stencil:stencil@localhost:3306/stencil

# REQUIRED - Redis connection
ST_REDIS_URL=redis://localhost:6379/0

# Dev-only features (purge all invoices, danger zone in Settings)
ST_DEBUG=false

# Grounded model authoring retries (author → execute → diff → refine)
ST_MODEL_AUTHORING_MAX_ATTEMPTS=3
```

All other settings have sensible defaults. See `.env.example` for the full list.

> **Note:** `.env.example` may still list the legacy name `ST_MODEL_GENERATION_VALIDATION_ATTEMPTS` — the live setting is **`ST_MODEL_AUTHORING_MAX_ATTEMPTS`** (`config.py`).

### Step 3: Set Up the Database

```bash
# Create the MySQL database
mysql -u root -p -e "CREATE DATABASE stencil;"
mysql -u root -p -e "CREATE USER 'stencil'@'localhost' IDENTIFIED BY 'stencil';"
mysql -u root -p -e "GRANT ALL PRIVILEGES ON stencil.* TO 'stencil'@'localhost';"

# Run migrations to create tables
alembic upgrade head
```

This creates tables for: `intake_records`, `extraction_jobs`, `extraction_models`, `processing_logs`, `ai_cost_logs`. Supplier profiles live as JSON under `supplier_profiles/` (not in the DB).

### Step 4: Verify Installation

```bash
# Start the API server
uvicorn stencil.main:app --reload

# In another terminal, check health
curl http://localhost:8000/health
# Expected: {"status":"ok","app":"Stencil"}

# Start the frontend (in another terminal)
cd frontend
npm run dev
# Open http://localhost:3000
```

---

## Usage

There are **three ways** to process invoices:

### Option A: Drop a PDF (File Watcher)

The file watcher monitors the `inbound/` folder and automatically processes any PDF dropped in.

**Terminal 1 -- Start the API:**
```bash
uvicorn stencil.main:app --reload
```

**Terminal 2 -- Start the Celery worker:**
```bash
celery -A stencil.tasks.worker worker --loglevel=info
```

**Terminal 3 -- Start the file watcher:**
```bash
python -c "
from stencil.tasks.worker import process_invoice_task
from stencil.intake.watcher import start_watcher

def on_pdf(path):
    print(f'Processing: {path}')
    process_invoice_task.delay(str(path))

start_watcher(on_pdf)
"
```

Now drop a PDF into a supplier profile's inbound folder (configured in each profile's `inbound_path` under `ST_DATA_DIR`) or use the **Upload** page / watcher on operational `inbound/`.

### Option B: Python Script (Direct Pipeline)

For testing or scripting, call the pipeline directly:

```python
from pathlib import Path
from stencil.db.session import SessionLocal
from stencil.pipeline.processor import process_invoice

db = SessionLocal()
intake_id = process_invoice(db, Path("path/to/invoice.pdf"))
print(f"Done! intake_id: {intake_id}")
db.close()
```

### Option C: Celery Task (Async)

Queue an invoice for background processing:

```python
from stencil.tasks.worker import process_invoice_task

result = process_invoice_task.delay("path/to/invoice.pdf")
print(f"Task ID: {result.id}")
print(f"Result: {result.get(timeout=120)}")
```

---

## What Happens When You Process an Invoice

Here is exactly what happens when a PDF enters the system:

```
 1. INTAKE          PDF registered, archived, moved to processing/
                    DB record created with unique intake_id

 2. FINGERPRINT     pymupdf extracts text layout features from page 1-2
                    SHA-256 hash computed from block positions, labels, fonts
                    Hash compared against approved extraction models in DB

 3. ROUTING         Profile + fingerprint routing:
                    Approved model for (profile, fingerprint) → model path ($0)
                    Candidate model → AI + side-by-side validation
                    Unknown fingerprint on active profile → AI + layout-drift exception
                    No profile → AI only

 4a. FAST PATH      Generic interpreter executes declarative ExtractionModel JSON
                    (region anchors, row classifiers, column bands, transforms)
                    Header/totals via label-relative rules; line items from layout geometry
                    If execution fails or confidence low → fallback to AI path

 4b. SLOW PATH      OpenAI classifies supplier (when needed)
                    OpenAI extracts full invoice (ground truth)
                    For training uploads: grounded authoring loop
                      author rules → execute → diff vs AI → refine (configurable attempts)
                    Candidate saved only on exact deliverable match + passing self-checks

 4c. TRAINING       Explicit training PDF for a profile in `training` status
                    AI output always delivered; model training may warn without failing intake
                    Review artifacts: ai_output.xlsx, model_output.xlsx, training_report.json

 5. RECONCILE       Line items summed, compared to stated totals
                    Variance checked against threshold (default 1%)
                    Warnings generated for any mismatches

 6. OUTPUT          Files written to `{work_dir}/completed/{intake_id}/`:
                    - {original_pdf_name}.xlsx  (Temforce 8-column format, same stem as the PDF)
                    - canonical_invoice.json (full structured data)
                    - extraction_log.json    (metadata, cost, confidence)
                    - manifest.json          (WRITTEN LAST = ready signal)
                    - model_review/          (training/validation comparison artifacts, when applicable)

 7. DONE            Status: `completed`, `completed_with_warnings`, or `failed`
                    Training failures still deliver AI output but mark warnings when model did not converge
                    Temforce Bot Agent picks up manifest.json when ready
```

---

## Output Files

After processing, each invoice gets a package in `{work_dir}/completed/{intake_id}/` (default work root: `stencil_work/`):

| File | Purpose |
|---|---|
| `{original_pdf_name}.xlsx` | Temforce-ready **8-column** spreadsheet (`EXT_SERVICEID`, `EXT_AMOUNT`, `EXT_TAX`, etc.); stem matches the source PDF (e.g. `Colt_Invoice.pdf` → `Colt_Invoice.xlsx`) |
| `canonical_invoice.json` | Full structured extraction (header, line items, totals, metadata) |
| `extraction_log.json` | Processing metadata (path used, tokens, cost, confidence, warnings) |
| `manifest.json` | Ready signal for Temforce Bot Agent (written last) |

The XLSX is also copied into the profile's configured `output_path` under **the same
name**, so one account's invoices no longer collide there. If two different invoices
would land on the same filename the copy is delivered as `name (2).xlsx` and a
`pipeline.output.name_collision` warning is logged — a deliverable is never silently
replaced.
| `model_review/` | Optional comparison files when training or validating models (`ai_output.xlsx`, `model_output.xlsx`, `training_report.json`, `execution_error.txt`) |

Failed or uncertain invoices go to `{work_dir}/exceptions/{intake_id}/` with an `error_log.json` explaining why.

Operational lifecycle folders (`inbound`, `processing`, `completed`, `exceptions`, `archive`) live under **`ST_WORK_DIR`** (default `stencil_work/`). Supplier PDF/Excel folders under **`ST_DATA_DIR`** are separate and are not purged by the dev reset tool.

---

## Extraction Model Lifecycle

This is how the system gets smarter over time:

```
  FIRST invoice from a layout         SUBSEQUENT invoices, same profile + fingerprint
  ~~~~~~~~~~~~~~~~~~~~~~~~~~~         ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

  1. No approved model yet            1. Fingerprint matches approved/candidate model
  2. AI extracts data ($0.10-0.50)    2. Interpreter executes ExtractionModel JSON
  3. AI authors declarative rules       3. No AI call needed (model path)
  4. Candidate saved on exact match     4. Same deliverable XLSX contract
  5. Human approves in UI               5. Cost: $0.00
```

### Grounded authoring loop (training uploads)

When a supplier profile is in **`training`** status and you upload a training PDF:

1. AI extraction produces **ground truth** for that invoice.
2. AI **authors** an `ExtractionModel` JSON using layout evidence + profile hints.
3. The **interpreter executes** the model; output is **diffed** against AI at the Temforce row level.
4. On mismatch, structured feedback is sent back to the AI (up to `ST_MODEL_AUTHORING_MAX_ATTEMPTS`).
5. A candidate is **saved only** if deliverable rows match exactly and self-checks pass.
6. If training fails, the intake still completes with **`completed_with_warnings`** — AI XLSX/JSON are delivered; comparison files land in `model_review/`.

### Managing Models

**List models in the database:**
```python
from stencil.db.session import SessionLocal
from stencil.db.crud import find_model_by_fingerprint
from sqlalchemy import select
from stencil.db.models import ExtractionModelRecord

db = SessionLocal()
models = db.execute(select(ExtractionModelRecord)).scalars().all()
for m in models:
    print(f"{m.id} | {m.supplier} | {m.status} | used {m.times_used}x | confidence {m.confidence}")
db.close()
```

**Approve a candidate model (enables it for production use):**
```python
from stencil.db.session import SessionLocal
from stencil.models.registry import approve_model

db = SessionLocal()
approve_model(db, model_id="gtt-standard-v1", approved_by="your_name")
db.close()
```

**Retire an old model:**
```python
from stencil.db.session import SessionLocal
from stencil.models.registry import retire_model

db = SessionLocal()
retire_model(db, model_id="gtt-standard-v1")
db.close()
```

---

## Testing

### Run the Test Suite

```bash
# Default: unit + deterministic corpus tests (no OpenAI calls)
pytest

# With coverage
pytest --cov=stencil --cov-report=term-missing

# AI-dependent model authoring e2e (requires ST_OPENAI_API_KEY)
pytest -m authoring

# Specific areas
pytest tests/test_validation/
pytest tests/test_fingerprint/
pytest tests/test_models/test_corpus_extraction.py
pytest tests/test_models/test_corpus_authoring.py -m authoring
```

The **golden corpus** under `tests/fixtures/corpus/` holds real PDFs (e.g. Colt, euNetworks) plus expected outputs for fingerprint, extraction, and authoring regression.

### Continuous integration

Every pull request and push to `main` runs [`.github/workflows/ci.yml`](.github/workflows/ci.yml) with four parallel jobs (no OpenAI key, no registry push):

| Job | What it checks |
| --- | --- |
| `backend-lint` | `ruff check src tests` |
| `backend-test` | `pytest` (default excludes `-m authoring`) |
| `frontend` | `npm run lint` and `npm run build` |
| `docker-build` | Docker Level 1: `docker compose build backend worker frontend` (images discarded) |

Configure **branch protection** on `main` to require all four checks before merge.

**AI authoring (Suite 3)** is manual only — [`.github/workflows/authoring-manual.yml`](.github/workflows/authoring-manual.yml). Run it from the GitHub Actions tab when you need to refresh `model.json` snapshots:

1. Add repo secret `ST_OPENAI_API_KEY`
2. Run **Authoring (manual)** with layout `all`, `colt.standard`, or `eunetworks.standard`
3. Download the `corpus-model-snapshots-*` artifact, review diffs, and commit updated `tests/fixtures/corpus/*/model.json`

There is no scheduled/nightly authoring run.

See [`tests/fixtures/corpus/README.md`](tests/fixtures/corpus/README.md) for which layouts are fully gated in CI today.

### Quick Smoke Test (No AI Needed)

Test the core pipeline components without making any OpenAI API calls:

```python
"""smoke_test.py -- Run this to verify your installation works."""
from pathlib import Path
from decimal import Decimal

# 1. Test config loads
from stencil.config import settings
settings.ensure_directories()
print(f"[OK] Config loaded. Data dir: {settings.data_dir}")
print(f"[OK] Directories created: inbound, processing, completed, exceptions, archive")

# 2. Test schemas validate
from stencil.validation.schema import (
    CanonicalInvoice, InvoiceHeader, LineItem,
    ExtractionMetadata, ExtractionPath, OutputType,
)
invoice = CanonicalInvoice(
    intake_id="test-123",
    output_type=OutputType.STANDARD,
    header=InvoiceHeader(
        supplier_name="Test Supplier",
        invoice_number="INV-001",
        invoice_date="2026-01-15",
        currency="USD",
    ),
    line_items=[
        LineItem(line_number=1, description="Monthly Service", charge_type="recurring",
                 amount=Decimal("100.00")),
        LineItem(line_number=2, description="Tax", charge_type="tax",
                 amount=Decimal("8.25")),
    ],
    subtotal=Decimal("100.00"),
    tax=Decimal("8.25"),
    total_due=Decimal("108.25"),
    metadata=ExtractionMetadata(extraction_path=ExtractionPath.AI),
)
print(f"[OK] Canonical schema validated: {len(invoice.line_items)} line items")

# 3. Test reconciliation
from stencil.validation.reconciler import reconcile
result = reconcile(invoice)
print(f"[OK] Reconciliation: reconciled={result.is_reconciled}, variance={result.variance}")

# 4. Test XLSX generation
output_dir = settings.data_dir / "test_output"
output_dir.mkdir(parents=True, exist_ok=True)
from stencil.output.xlsx_writer import write_xlsx
xlsx_path = write_xlsx(invoice, output_dir / "test_output.xlsx")
print(f"[OK] XLSX written: {xlsx_path}")

# 5. Test JSON output
from stencil.output.json_writer import write_canonical_json, write_extraction_log
json_path = write_canonical_json(invoice, output_dir / "test_canonical.json")
log_path = write_extraction_log(invoice, output_dir / "test_log.json")
print(f"[OK] JSON written: {json_path}")
print(f"[OK] Log written: {log_path}")

# 6. Test supplier profiles load
from stencil.profiles.loader import load_all_profiles
profiles = load_all_profiles()
print(f"[OK] Loaded {len(profiles)} supplier profiles: {', '.join(profiles.keys())}")

print("\n--- ALL SMOKE TESTS PASSED ---")
```

Run it:
```bash
python smoke_test.py
```

### End-to-End Test With a Real PDF (Requires OpenAI Key)

```python
"""e2e_test.py -- Full pipeline test with a real invoice PDF."""
from pathlib import Path
from stencil.db.session import SessionLocal
from stencil.pipeline.processor import process_invoice

# Point this to a real invoice PDF
PDF_PATH = Path("path/to/your/test-invoice.pdf")

assert PDF_PATH.exists(), f"PDF not found: {PDF_PATH}"

db = SessionLocal()
try:
    intake_id = process_invoice(db, PDF_PATH)
    print(f"\n[SUCCESS] Invoice processed!")
    print(f"  Intake ID: {intake_id}")

    # Check what was created
    from stencil.db.crud import get_intake, get_jobs_for_intake, get_processing_logs
    intake = get_intake(db, intake_id)
    jobs = get_jobs_for_intake(db, intake_id)
    logs = get_processing_logs(db, intake_id)

    print(f"  Status: {intake.status}")
    print(f"  Fingerprint: {intake.layout_fingerprint[:40]}...")
    print(f"  Extraction jobs: {len(jobs)}")
    for job in jobs:
        print(f"    - Path: {job.extraction_path} | Status: {job.status} | "
              f"Items: {job.line_item_count} | Confidence: {job.overall_confidence}")
    print(f"  Processing log entries: {len(logs)}")
    for log in logs:
        print(f"    [{log.step}] {log.status}: {log.message}")

    # Check output files
    from stencil.config import settings
    output_dir = settings.completed_dir / intake_id
    if output_dir.exists():
        print(f"\n  Output files in {output_dir}:")
        for f in sorted(output_dir.iterdir()):
            print(f"    - {f.name} ({f.stat().st_size:,} bytes)")

finally:
    db.close()
```

Run it:
```bash
python e2e_test.py
```

### Test the Hybrid Model Reuse

This is the most important test -- proving that the second invoice is free:

```python
"""hybrid_test.py -- Prove that AI runs once, model runs forever."""
from pathlib import Path
from stencil.db.session import SessionLocal
from stencil.db.crud import get_jobs_for_intake, get_processing_logs
from stencil.models.registry import approve_model
from stencil.pipeline.processor import process_invoice
from sqlalchemy import select
from stencil.db.models import ExtractionModelRecord

# You need TWO invoices from the SAME supplier with the SAME layout
PDF_1 = Path("path/to/supplier-invoice-january.pdf")
PDF_2 = Path("path/to/supplier-invoice-february.pdf")

db = SessionLocal()

# --- FIRST INVOICE: should use AI path ---
print("=" * 60)
print("FIRST INVOICE (expect: AI path)")
print("=" * 60)
id1 = process_invoice(db, PDF_1)
jobs1 = get_jobs_for_intake(db, id1)
print(f"  Path used: {jobs1[0].extraction_path}")
print(f"  AI tokens: {jobs1[0].tokens_input + jobs1[0].tokens_output}")
print(f"  Cost: ${jobs1[0].estimated_cost_usd}")
assert jobs1[0].extraction_path == "ai", "Expected AI path for first invoice!"

# --- CHECK: model should have been generated ---
models = db.execute(select(ExtractionModelRecord).where(
    ExtractionModelRecord.status == "candidate")).scalars().all()
print(f"\n  Candidate models: {len(models)}")
for m in models:
    print(f"    {m.id} (confidence: {m.confidence})")

# --- APPROVE THE MODEL ---
if models:
    approve_model(db, models[0].id, approved_by="test_script")
    print(f"\n  Approved model: {models[0].id}")

# --- SECOND INVOICE: should use MODEL path (free!) ---
print("\n" + "=" * 60)
print("SECOND INVOICE (expect: MODEL path, $0 cost)")
print("=" * 60)
id2 = process_invoice(db, PDF_2)
jobs2 = get_jobs_for_intake(db, id2)
print(f"  Path used: {jobs2[0].extraction_path}")
print(f"  AI tokens: {jobs2[0].tokens_input + jobs2[0].tokens_output}")
print(f"  Cost: ${jobs2[0].estimated_cost_usd}")
assert jobs2[0].extraction_path == "model", "Expected MODEL path for second invoice!"
assert jobs2[0].tokens_input == 0, "Expected zero tokens for model path!"

print("\n" + "=" * 60)
print("HYBRID TEST PASSED!")
print(f"  Invoice 1: AI path (${jobs1[0].estimated_cost_usd})")
print(f"  Invoice 2: Model path ($0.00)")
print("=" * 60)

db.close()
```

---

## Frontend

The frontend is a Next.js 16 application with App Router, served at http://localhost:3000 in development. It proxies all `/api/v1/*` requests to the FastAPI backend.

### Pages

| Route | Page | Description |
|---|---|---|
| `/` | Dashboard | KPI stat cards, volume/cost charts; auto-refreshes while invoices are processing |
| `/invoices` | Invoice List | Sortable table with search, **multi-supplier filter**, status chips, extraction path, intake source, date range |
| `/invoices/[intakeId]` | Invoice Detail | Unified **processing timeline** (live via polling + WebSocket), downloads, cancel, reject |
| `/upload` | Upload | PDF drag-and-drop with per-file progress cards |
| `/profiles` | Supplier Profiles | Profile CRUD, training uploads, model approval workflow |
| `/models` | Extraction Models | Browse candidates and approved models by profile + fingerprint |
| `/exceptions` | Exception Queue | Review and retry failed invoices |
| `/settings` | Settings | Runtime config; **Danger zone** (purge all invoice data when debug enabled) |

### Live updates

The UI polls every **3 seconds** while any invoice is `processing` or `received` (list, detail, upload). The dashboard refreshes every **5 seconds** during active processing. The processing timeline also subscribes to **`/api/v1/pipeline/live/{intake_id}`** WebSocket events (no connection indicator in the UI — polling is the fallback).

### Intake statuses

| Status | Meaning |
|---|---|
| `received` | Queued, not yet processing |
| `processing` | Pipeline running |
| `completed` | Delivered successfully |
| `completed_with_warnings` | Delivered but needs review (e.g. reconciliation variance, **training did not converge**) |
| `failed` | Pipeline error or user cancel |

### Running the Frontend

```bash
cd frontend

# Development (with hot reload)
npm run dev

# Production build
npm run build
npm start
```

The frontend expects the FastAPI backend at `http://localhost:8000` by default. Override with `NEXT_PUBLIC_API_URL` in `frontend/.env.local`.

### Key Components

- **Sidebar** — Navigation (dashboard, invoices, upload, profiles, models, exceptions, settings)
- **DataTable** — Reusable TanStack Table with sorting, pagination, row click navigation
- **StatusBadge** — Intake status, extraction path (`ai` / `model` / `model_fallback_ai`), confidence, charge type, model status
- **ProcessingTimeline** — Single unified pipeline view: structured step checklist, elapsed time, per-step durations, expandable full audit log, model-review downloads inline
- **PipelineProgress** — Thin card wrapper around `ProcessingTimeline` on the upload page
- **Charts** — Recharts bar (volume) and line (cost trend) on the dashboard

---

## Folder Structure

```
Stencil/
|-- frontend/                  # Next.js frontend application
|   |-- src/
|   |   |-- app/               # App Router pages (dashboard, invoices, upload, etc.)
|   |   |-- components/        # Shared components (sidebar, header, data-table, badges)
|   |   |   `-- ui/            # shadcn/ui primitives (button, card, table, etc.)
|   |   |-- hooks/             # TanStack Query hooks (use-invoices, use-dashboard, etc.)
|   |   |-- lib/               # API client, query provider, utilities
|   |   `-- types/             # TypeScript type definitions matching backend schemas
|   |-- next.config.ts         # API proxy rewrites
|   |-- tailwind.config.ts     # Tailwind v4 config
|   `-- package.json
|
|-- src/stencil/          # Application source code
|   |-- main.py                # FastAPI entry point
|   |-- config.py              # Settings (Pydantic BaseSettings, ST_ prefix)
|   |-- intake/                # PDF intake, file watcher, cleanup helpers
|   |-- fingerprint/           # Layout fingerprinting (pymupdf)
|   |-- classification/        # AI supplier classification
|   |-- extraction/            # AI extraction, layout text, evidence alignment
|   |-- models/                # ExtractionModel schema, interpreter, authoring, training, diff, registry
|   |-- validation/            # Canonical schema + reconciliation
|   |-- output/                # XLSX writer (8-col Temforce), JSON, review artifacts
|   |-- exceptions/            # Exception handler
|   |-- profiles/              # Supplier profile loader (JSON)
|   |-- pipeline/              # Orchestrator, events, cancellation, timing
|   |-- db/                    # SQLAlchemy models, session, CRUD
|   `-- tasks/                 # Celery async tasks
|
|-- supplier_profiles/         # Versioned supplier config JSONs (routing, header hints, training)
|-- extraction_models/         # Persisted ExtractionModel JSON (runtime + DB)
|-- tests/fixtures/corpus/     # Golden PDFs + expected outputs for regression
|-- migrations/                # Alembic database migrations
|-- tests/                     # Test suite (pytest; `-m authoring` for OpenAI e2e)
|
|-- stencil_data/         # ST_DATA_DIR — supplier folders (PDFs, delivered Excel)
|-- stencil_work/         # ST_WORK_DIR — operational lifecycle (default)
|   |-- inbound/               # Staging for watcher uploads
|   |-- processing/            # Active extractions
|   |-- completed/             # Output packages + model_review/
|   |-- exceptions/            # Failed/uncertain invoices
|   `-- archive/               # Original PDF archive
|
|-- pyproject.toml
|-- alembic.ini
|-- .env.example
`-- README.md
```

---

## Supported Suppliers (Pilot)

| Supplier | Profile | Output Type | Status |
|---|---|---|---|
| GTT | `gtt.standard.v1` | Standard | Active |
| Colt | `colt.standard.v1` | Standard | Active |
| Zayo | `zayo.standard.v1` | Standard | Active |
| euNetworks | `eunetworks.standard.v2` | Standard | Active |
| AT&T Wireless | `att.wireless.v1` | Wireless | Draft (post-pilot) |

New suppliers are automatically handled by the AI path. Once the AI generates a model and it's approved, that supplier's invoices become free to process.

---

## Configuration Reference

All settings use the `ST_` prefix and can be set via environment variables or `.env` file.

| Variable | Default | Description |
|---|---|---|
| `ST_DEBUG` | `false` | Dev mode: enables purge API and Settings **Danger zone** |
| `ST_LOG_LEVEL` | `INFO` | Logging level (`DEBUG`, `INFO`, `WARNING`, `ERROR`) |
| `ST_DATABASE_URL` | `mysql+pymysql://...localhost/stencil` | MySQL connection string |
| `ST_REDIS_URL` | `redis://localhost:6379/0` | Redis URL for Celery |
| `ST_OPENAI_API_KEY` | *(required)* | OpenAI API key |
| `ST_OPENAI_MODEL_EXTRACTION` | `gpt-5.5` | Model for invoice extraction |
| `ST_OPENAI_MODEL_CLASSIFICATION` | `gpt-5.5` | Model for supplier classification |
| `ST_OPENAI_MODEL_MODEL_GENERATION` | `gpt-5.5` | Model for extraction-model authoring |
| `ST_MODEL_AUTHORING_MAX_ATTEMPTS` | `1` | Author → execute → diff retries per training invoice |
| `ST_CONFIDENCE_THRESHOLD` | `0.75` | Min confidence for AI extraction |
| `ST_MODEL_CONFIDENCE_THRESHOLD` | `0.80` | Min confidence for model path (below = fallback to AI) |
| `ST_RECONCILIATION_VARIANCE_THRESHOLD` | `0.01` | Max acceptable variance (1%) |
| `ST_DATA_DIR` | `stencil_data` | Supplier data root (PDFs, profile output paths) |
| `ST_WORK_DIR` | `stencil_work` | Operational lifecycle root (inbound → completed) |
| `ST_WATCHER_POLL_INTERVAL` | `1.0` | File watcher poll interval (seconds) |
| `ST_WATCHER_STABLE_SECONDS` | `3.0` | Wait time before processing a new file |

### Debug mode vs production

There is no separate “production mode” flag — **production is the default** (`ST_DEBUG=false`).

| | Production (default) | Debug / dev |
|---|---|---|
| `ST_DEBUG` | `false` | `true` |
| Purge all invoices (`DELETE /api/v1/invoices/purge`) | Blocked (403) | Allowed |
| Settings **Danger zone** | Hidden | Visible |

**Recommended setup:**

```env
# Dev sandbox
ST_DEBUG=true
ST_LOG_LEVEL=DEBUG

# Production-like
ST_DEBUG=false
ST_LOG_LEVEL=INFO
```

After changing `.env`, restart **backend**, **Celery worker**, and **watcher** (each process reads settings at startup). The Settings UI toggle for debug applies **in-memory to the API process only** and does not persist to `.env` or propagate to workers.

**Dev reset:** With `ST_DEBUG=true`, open **Settings → Danger zone → Clear all invoice data**, type `DELETE ALL`. This removes all intake DB rows and deletes subfolders under `ST_WORK_DIR` lifecycle directories (not supplier PDFs in `ST_DATA_DIR`).

---

## Cost Model

```
WITHOUT model caching (every invoice hits AI):
  100 invoices/month x ~$0.10-0.50/invoice = $10-50/month

WITH model caching (Stencil approach):
  First invoice per layout:  $0.10-0.50 (AI)
  All subsequent same-layout: $0.00     (model)

  Typical: 5-10 unique layouts x $0.50 = $2.50-5.00 one-time
  Monthly recurring: ~$0 (only new layouts trigger AI)
```

---

## Troubleshooting

| Problem | Solution |
|---|---|
| `ModuleNotFoundError: stencil` | Run `pip install -e .` from the project root |
| `No module named uvicorn` | Ensure venv is activated and run `pip install -e ".[dev]"` |
| Database connection refused | Ensure MySQL is running: `mysql -u root -e "SELECT 1"` |
| Redis connection refused | Ensure Redis is running: `redis-cli ping` |
| OpenAI API errors | Check `ST_OPENAI_API_KEY` in `.env` is valid |
| No output generated | Check `stencil_data/exceptions/` for error logs |
| Model path not used | Models must be "approved" first. Check model status in DB |
| Low confidence fallback | Increase `ST_MODEL_CONFIDENCE_THRESHOLD` or improve the model |
| Reconciliation failures | Check `extraction_log.json` for warnings. Adjust `ST_RECONCILIATION_VARIANCE_THRESHOLD` |
| Frontend can't reach backend | Ensure FastAPI is on port 8000 and check `frontend/.env.local` |
| Frontend build errors | Run `cd frontend && npm install`. Clear `.next/` if routes reference deleted pages |
| Training shows “Completed” but model failed | Check for `completed_with_warnings` and `model_review/` artifacts; training delivers AI output even when authoring fails |
| Purge returns 403 | Set `ST_DEBUG=true` in `.env` and restart the backend |
| Settings debug toggle has no effect on worker | Debug is per-process; use `.env` + restart worker for pipeline behavior |

---

## License

Proprietary. Internal use only.
