# Stencil Backend Plan

## Overview

Stencil is a hybrid AI invoice extraction system. AI generates reusable extraction models for new PDF layouts; known layouts use those models directly with zero AI cost. This document covers the backend services, pipeline, and data layer.

**Target milestone:** June 30, 2026 — Standard invoice production POC pilot.

---

## Core Architecture: Hybrid Model-First + AI-Fallback

```
Two paths through the system:

FAST PATH (no AI cost):
  Known layout -> Load extraction model -> Rule-based extraction -> Output

SLOW PATH (AI cost, but creates a model for next time):
  Unknown layout -> AI classifies + extracts -> Output
                                             -> ALSO generates an extraction model
                                             -> Model saved for future use
```

### What is an "Extraction Model"?

A JSON definition that encodes HOW to extract data from a specific supplier's invoice layout. It captures what the AI learned so it doesn't need to learn again:

- `header_rules` — regex patterns + page regions for header fields (invoice number, date, account, etc.)
- `table_rules` — start/end markers, column definitions, value mappings
- `reconciliation_rules` — labels for subtotal, tax, total

### Layout Fingerprinting

SHA-256 hash of PDF structural features (text block positions, labels, table headers, font sizes) via pymupdf. Determines whether to use an existing model or call AI.

---

## System Flow

```
Invoice PDF (Inbound Folder)
  |
  v
1. INTAKE SERVICE — File watcher detects PDF, archives original, creates DB record
  |
  v
2. LAYOUT FINGERPRINT — pymupdf extracts structure, computes SHA-256 hash
  |
  +-- Model FOUND (known layout) --------+
  |                                       |
  v                                       v
3a. AI PATH (new layout)           3b. MODEL PATH (known layout)
  - Classify via gpt-4o-mini         - Rule-based extraction
  - Extract via gpt-4o               - Zero AI cost
  - Generate extraction model         - Falls back to AI if low confidence
  - Save model as "draft"
  |                                       |
  v<--------------------------------------+
4. SCHEMA VALIDATION — Pydantic v2 canonical invoice schema
  |
  v
5. DETERMINISTIC RECONCILIATION — Sum line items vs. stated totals
  |
  v
6. OUTPUT GENERATION — XLSX + canonical JSON + extraction log
  |
  v
7. PACKAGE & MANIFEST — manifest.json written LAST (ready signal for Temforce Bot)
  |
  v
8. TEMFORCE BOT AGENT (External) — Validates and posts to Temforce
```

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Backend service | Python 3.12+ with FastAPI |
| File watcher | Python watchdog |
| AI API | OpenAI Responses API (gpt-4o, Structured Outputs) |
| Schema validation | Pydantic v2 |
| Database | MySQL (existing Temforce instance) via SQLAlchemy + Alembic |
| Task queue | Celery + Redis |
| Excel generation | openpyxl |
| PDF utilities | pymupdf |
| Logging | structlog (structured JSON) |
| Deployment | Windows Service (via NSSM or pywin32) on Windows Server 2025 |

> **Database note:** SQLAlchemy is database-agnostic. The codebase was initially developed against PostgreSQL but will target the existing MySQL instance in production to avoid managing a separate database server. Connection string change + minor dialect adjustments only.

---

## Per-Supplier Directory Structure

Production environment uses per-customer/supplier directory paths matching the existing Astera layout:

```
D:\Astera\Invoices\
  {customer_id}\
    {account_number}\
      pdf\                    # Inbound PDFs (Stencil watches these)
      xls\                    # Output XLSX + JSON (AP bot picks up from here)
```

Each supplier profile includes `inbound_path` and `output_path` overrides. The file watcher monitors multiple directories simultaneously. Output is written to the supplier-specific output folder so the existing AP bot can pick up files without any changes to its configuration.

When no per-supplier paths are configured, the system falls back to the global `inbound_dir` / `completed_dir` from settings.

---

## Windows Service Deployment

The application runs as a Windows Service on Windows Server 2025:

- **NSSM (Non-Sucking Service Manager)** wraps the Python process as a Windows Service
- Starts automatically on boot, restarts on failure
- File watcher + Celery worker run as separate services
- FastAPI web UI runs as a third service (or behind IIS reverse proxy)
- Logs written to Windows Event Log + structured JSON files

Alternative: `pywin32` for native Python Windows Service integration.

---

## Project Structure (Backend)

```
src/stencil/
  __init__.py
  main.py                     # FastAPI app entry point
  config.py                   # Settings (Pydantic BaseSettings)

  intake/
    watcher.py                # File watcher (watchdog), supports multiple directories
    service.py                # Intake record creation, file moves

  fingerprint/
    fingerprinter.py          # PDF structure analysis + SHA-256 hash

  models/
    schema.py                 # ExtractionModel Pydantic schema
    registry.py               # Model CRUD, lookup by fingerprint
    executor.py               # Rule-based extraction using a model
    generator.py              # AI -> extraction model conversion

  classification/
    classifier.py             # OpenAI classification call

  extraction/
    extractor.py              # OpenAI extraction call
    prompts.py                # System/user prompt templates

  validation/
    schema.py                 # Canonical invoice schema (Pydantic)
    reconciler.py             # Deterministic total/tax checks

  output/
    xlsx_writer.py            # XLSX generation (openpyxl)
    json_writer.py            # Canonical JSON + manifest writer
    mapper.py                 # Canonical JSON -> XLSX column mapping

  exceptions/
    handler.py                # Exception package creation

  profiles/
    schema.py                 # SupplierProfile Pydantic schema
    loader.py                 # Load/validate supplier profile JSONs

  pipeline/
    processor.py              # End-to-end orchestrator with routing

  db/
    models.py                 # SQLAlchemy models
    session.py                # DB session management
    crud.py                   # Database operations

  tasks/
    worker.py                 # Celery async tasks
```

---

## Database Tables

| Table | Purpose |
|-------|---------|
| `intake_records` | Tracks every PDF received: filename, paths, status, fingerprint |
| `extraction_jobs` | One per extraction attempt: path (AI/model), tokens, cost, confidence |
| `extraction_models` | AI-generated extraction models: rules JSON, lifecycle status, usage stats |
| `processing_logs` | Append-only audit log for every pipeline step |

---

## Implementation Phases

### Phase 1: Design Lock — COMPLETED
- pyproject.toml, config.py, canonical schema, output column mapping
- Supplier profile format and initial profiles (GTT, Colt, Zayo, euNetworks, AT&T)
- Extraction model JSON schema

### Phase 2: Foundation Build — COMPLETED
- FastAPI app with health endpoint
- Intake service with watchdog file watcher
- PDF layout fingerprinting via pymupdf
- SQLAlchemy models + Alembic migration (001_initial_schema)
- Database CRUD layer
- Pipeline orchestrator skeleton with routing logic

### Phase 3: AI Extraction + Model Generation — COMPLETED
- OpenAI classification (gpt-4o-mini, first 1-2 pages as PNG)
- OpenAI extraction (gpt-4o, all pages, Structured Outputs)
- Model generation from AI extraction results
- Model registry (save, find by fingerprint, approve/retire)
- Prompt templates with supplier profile injection

### Phase 4: Model-Based Extraction + Output — COMPLETED
- Rule-based extraction engine (header regex, table parsing, value mapping)
- Full routing logic (fingerprint match -> model; no match -> AI)
- Model confidence fallback to AI path
- XLSX generation (openpyxl, Temforce-ready format)
- Canonical JSON + extraction log + manifest writer

### Phase 5: Reconciliation & Exceptions — COMPLETED
- Deterministic reconciliation (line item sums vs. stated totals)
- Variance threshold checks with configurable tolerance
- Exception package creation with reason codes
- Zero-amount row detection
- Warning aggregation

### Phase 6: Integration Testing — TODO
- End-to-end pipeline tests with sample PDFs
- Model reuse verification (second PDF same layout = no AI)
- Model fallback test (corrupt model -> AI path)
- Layout drift test (changed PDF -> new model)
- Reconciliation failure test
- Exception flow test

### Phase 7: UAT & Production Hardening — TODO
- Test with real pilot supplier invoices
- Deployment scripts and runbook
- Monitoring and alerting
- AI cost dashboard
- Model approval workflow
- MySQL compatibility testing and migration
- Per-supplier directory path configuration
- Windows Service setup (NSSM / pywin32) for Windows Server 2025
- Multi-directory file watcher for per-customer inbound folders

---

## Key Design Decisions

1. **AI is the model builder, not the model runner** — AI runs once per layout, generates a reusable model
2. **Layout fingerprinting as the router** — PDF structure hash determines path, no human decision needed
3. **Model fallback to AI** — low confidence triggers AI re-extraction, handles layout drift
4. **Extraction models are versioned** — layout changes create new versions, old ones retire
5. **Human approval gate** — AI-generated models start as "draft", require approval before production use
6. **AI is extraction only** — never approves invoices. Temforce is the truth layer
7. **Deterministic code for all math** — reconciliation, tax allocation, XLSX generation are NOT AI
8. **Canonical JSON as intermediate** — both paths produce the same schema, downstream is path-agnostic
9. **Manifest-last write** — manifest.json is the "ready" signal to Temforce Bot Agent
10. **Exception-first** — uncertain results route to exception queue, never silently succeed
11. **Database-agnostic** — SQLAlchemy ORM allows switching between PostgreSQL and MySQL via connection string
12. **Per-supplier directory paths** — matches existing Astera folder structure (`{customer_id}/{account_number}/pdf`) so the AP bot requires no changes
13. **Windows Service deployment** — runs unattended on Windows Server 2025, auto-starts on boot
14. **Full audit logging** — every invoice tracked with timestamps, extraction path, token usage, cost, reconciliation results, errors

---

## Supplier Profiles

Pre-configured JSON files that guide extraction for known suppliers:

| Profile | Supplier | Output Type |
|---------|----------|-------------|
| gtt.standard.v1 | GTT | Standard |
| colt.standard.v1 | Colt | Standard |
| zayo.standard.v1 | Zayo | Standard |
| eunetworks.standard.v1 | euNetworks | Standard |
| att.wireless.v1 | AT&T | Wireless |

Profiles contain: identity/aliases, classification signals, document structure hints, header label mappings, line item patterns, reconciliation config.
