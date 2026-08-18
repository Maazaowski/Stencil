# UI verification + Profile Editor review

Verified against the running Docker stack (frontend :3000, backend :8000, MySQL, Redis) on 2026-07-10.
Backend container confirmed to contain the new code (`instructions.py`, `allocate_invoice_tax`, `tax_method`).

---

## Part 1 — UI changes from Auto Tax Resolver + Instruction Compiler

### What I changed

| Feature | File | Change |
|---|---|---|
| Auto Tax Resolver | `frontend/src/lib/profile-options.ts` | Added `auto` → **"Auto (recommended)"** to `TAX_OUTPUT_MODE_OPTIONS` |
| Auto Tax Resolver | `frontend/src/app/profiles/[profileId]/page.tsx` | `emptyAdvanced()` defaults `tax_output_mode: "auto"`; unset label → "Default (Auto)"; hint fallback → `auto` |
| Instruction Compiler | `frontend/src/types/index.ts` | Added `SetupConflict`, `TaxMethod`; `ExtractionPreview` gains `tax_method`, `conflicts`, `ignored_notes` |
| Both | `frontend/src/components/extraction-preview.tsx` | Added a **"Tax method"** line and a **"Setup conflicts"** panel |

### Verified working ✅

**`verify-1-tax-mode-dropdown-open.png`** — Tax output mode dropdown now lists:
`Default (Auto)` · `Auto (recommended)` · `Extract exact row tax` · `Calculate missing row tax` · `No row tax`

**`verify-2-tax-mode-auto-selected.png`** — Selecting **Auto** correctly:
- updates the hint to *"Resolve tax automatically: printed row tax if present, else allocate an invoice-level tax total across rows, else a resolved rate, else blank."*
- disables + clears **Tax rate source** (the existing `updateTaxOutputMode` coupling behaves correctly with `auto`)

No console errors or warnings on any page.

### NOT working ❌ — a real defect I introduced

The **"Tax method" line and "Setup conflicts" panel never render** on the invoice page or the model workbench.

Cause: `GET /api/v1/invoices/{id}/preview` and `POST /api/v1/models/{id}/preview` both declare
`response_model=ExtractionPreviewResponse` (`src/stencil/api/schemas.py:33`). **FastAPI silently strips any key
not declared on the response model**, so `tax_method`, `conflicts`, and `ignored_notes` are dropped before reaching
the browser. Confirmed live — the endpoint returns only:

```
columns, rows, row_count, line_item_count, header_fields, totals, reconciliation, extraction_path, warnings
```

The profile-preview endpoints (`/profiles/{id}/preview`, `/profiles/preview/{job_id}`) have **no** `response_model`,
so they *would* pass the new fields through — but exercising that path requires a billed AI extraction.

**Fix (not applied, per "no code changes"):** add `tax_method: str | None`, `conflicts: list[dict]`,
`ignored_notes: list[str]` to `ExtractionPreviewResponse`.

### A UX flaw I introduced

The dropdown now offers **both** `Default (Auto)` (the `__unset__` sentinel) and `Auto (recommended)` — two options
that mean the same thing. One should go. Recommendation: drop `allowUnset` for this field and keep the explicit
`auto` option, since `auto` is now the backend default anyway.

### Backend compiler verified on real data (no AI, no cost)

Ran `compile_instructions` over all 22 live DB profiles. **4 have genuine conflicts:**

| Profile | Conflict |
|---|---|
| `crowncastle.ai.v3` | `tax_source = none` but a note prescribes per-line tax; **and** a note says "one row per service group" while granularity is `per_charge_row` |
| `eunetworks.standard.v3` | note prescribes a tax rule, `tax_output_mode` unset/auto |
| `Granite_82824706` | same |
| `lumen.standard.v1` | same |

These are real misconfigurations on live profiles — exactly what the compiler is for.

Note: `eunetworks.standard.v2` (DB) already has `tax_output_mode=calculate` + `tax_rate_source=invoice_tax_rate`,
whereas `eval_cases/eunetworks.standard/profile.json` still has them `null`. **The eval file is stale relative to
the DB** — worth reconciling (CLAUDE.md warns about exactly this).

---

## Part 2 — Profile Editor review

Screenshots: `review-1`/`review-2` (Setup tab), `review-3`..`review-6` (Advanced tab).

Scale: **Setup = 5 sections. Advanced = 8 sections, 37 form fields, 4213px of scroll.**

### A. Same question asked twice (worst problem)

Setup **§3 "What each output field looks like on the invoice"** and Advanced **"Grouped row policy"** ask for the
*same printed column names*, stored in different places (`field_overrides.label_hint` vs `line_item_hints.*_column_label`):

| Setup §3 | Advanced | euNetworks value (both) |
|---|---|---|
| Service ID | Service ID column label | `Service` |
| Billing Reference | Billing reference column label | `Description` |
| Amount | Amount column label | `Net Value` |
| Tax Amount | Tax amount column label | (`Tax Rate` in Advanced) |

Nothing tells the user which wins. **Fix:** show it once. If both must exist, the Advanced field should render
read-only and say "set in Setup §3".

Also duplicated:
- **Notes** appears twice — Setup §5 *and* the bottom of Advanced. Same field, two locations.
- **Amount total label** ("Net Total") vs **Subtotal Keywords** (`Net Total`, `Subtotal`) — overlapping intent.
- **Tax total label** ("Tax Total") vs **Tax Keywords** (`VAT`, `MwSt`) — overlapping intent.

### B. Bugs / wrong data visible right now

1. **`__unset__` sentinel leaks into the UI.** Four selects display the literal string `__unset__` instead of
   their unset label ("Auto (AI decides)", etc.): *Amount source*, *Tax source*, *Tax rate source*, and others.
   See `review-4-advanced-b.png`. This is a pre-existing `EnumSelectField` bug, not mine.
2. **Select triggers show the raw enum value, not the label.** The trigger reads `calculate`, `invoice_tax_rate`,
   `per_charge_row`, `parent_identifier` — while the dropdown list shows friendly labels ("Calculate missing row
   tax"). The user picks a nice label and then sees a snake_case token.
3. **Contradictory config is accepted silently.** On euNetworks, *Service ID (primary row identifier)* **and**
   *Billing reference (secondary identifier)* are **both** `parent_identifier` — yet the help text says they are
   opposites ("When service_id is the child row ID, put the parent section ID here, and vice versa"). No validation.
4. **`Tax amount column label` = "Tax Rate"** on euNetworks. That field means "printed header for per-line tax",
   but euNetworks prints no per-line tax — someone put the *rate* label there. The UI gave no signal it was wrong.
5. **Setup §3 "Feeds:" captions are wrong.** Both *Service ID* and *Billing Reference* say
   `Feeds: EXT_SERVICEID, EXT_BILLINGREFERENCE`. They should feed one column each.
6. **Skip Row Keywords is empty in the DB profile** but heavily populated in the eval file — more DB/file drift.

### C. Too complicated / should be hidden

The Advanced tab exposes engine internals with no progressive disclosure. Candidates to collapse behind
"Show engine internals" or remove from the UI entirely:

- **Executable policy** selects — *Amount source*, *Tax source*. Labeled "(executable policy)", values like
  `group_total_minus_amount`, `label_amount_minus_tax`. Unintelligible to a new user; these are authored by the
  AI/model path anyway.
- **Regex fields** — *Service ID value pattern*, *Billing reference value pattern*, *Ignore Label Patterns*,
  *Optional Column Patterns*, *Exclude Span Patterns*. Five regex inputs with placeholders like
  `(?i)^dia\s+(actual|commit)\s+usage$`. No validation, no tester, no example match.
- **Layout Fingerprint** (whole section: Summary Anchors, Fingerprint Currency Codes, and the 3 regex fields).
  This is routing-engine tuning, not supplier configuration.
- **Training gates** (Minimum validation successes, Require reconciliation) — belongs on the Training tab, not
  in supplier config.
- **Profile Metadata** (Version, Owner, Created/Updated) — read-only trivia occupying the top of Advanced.

### D. Confusing labels / copy

- "**Document Category**" has two competing hints: *"Default invoice / statement layout category"* and
  *"Layout category label; salts the layout fingerprint."* The second is engine-speak.
- "**Detail Start Marker** / **Detail End Marker**" — users won't know "marker" means a printed heading string.
  Call them "Line items begin after this heading".
- "**Grouped row policy**" section title doesn't say what it's for; its explanatory example ("Example — child in
  EXT_SERVICEID, parent in EXT_BILLINGREFERENCE…") is a wall of text below the fields rather than above them.
- The Notes textarea is **3 rows tall** for euNetworks' ~6-paragraph note. It's the single most important
  free-text field and it's the smallest input on the page.

### E. What I'd actually do

1. **Delete the duplication.** One place per question. Setup §3 owns the printed column names; Advanced shows them
   read-only. One Notes field, not two.
2. **Fix the `__unset__` leak and the raw-value triggers.** Two small `EnumSelectField` bugs that make every
   dropdown on the page look broken.
3. **Validate contradictions on save** — e.g. service_id and billing_reference preference can't both be
   `parent_identifier`. The Instruction Compiler already has the machinery for exactly this; wire its conflict
   output into the editor (this was the planned `/profiles/{id}/lint` endpoint).
4. **Collapse Advanced into "Layout hints" (visible) + "Engine internals" (collapsed)**: move regexes, executable
   policies, fingerprint tuning, and training gates behind the fold. That takes the visible field count from 37 to
   roughly 12.
5. **Grow the Notes box** and, once the lint endpoint exists, show setup conflicts inline next to it — the notes
   field is where the conflicts originate.
