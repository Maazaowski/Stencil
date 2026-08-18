# Building the AT&T VPN model in the visual builder

A start-to-finish walkthrough of hand-authoring an extraction model for the
**AT&T VPN** layout (`eval_cases/att.vpn`) — the two-column "newspaper" invoice
that Phase 1.6 made modelable. It also records, honestly, where the builder
fought back: see **Friction & refinements** at the end.

Result of this walkthrough (smallest invoice, 5 expected rows), built entirely
through the UI:

| Column | Built via UI | Matches expected |
|---|---|---|
| EXT_SERVICEID | `DHEC.047001..ATI` … `.L4YS.595327..ATI.` | ✅ 5/5 |
| EXT_BILLINGREFERENCE | `AT&T VPN Service` | ✅ |
| EXT_AMOUNT | 181.07 / 441.5 / 184.47 / 292.56 / 6952.36 | ✅ |
| EXT_TAX | 10.86 / 45.92 / 23.9 / 29.26 / 347.62 | ✅ |
| EXT_DATE, EXT_ACCOUNT, EXT_INVOICENUMBER | header fields | ❌ (see friction #1–#3) |

So the **hard part — the two independent columns of service blocks — is fully
authorable in the builder** and reproduces the deliverable's line items exactly.
The document-level header columns are where the builder currently needs work.

---

## The invoice, in one paragraph

Each page prints **two side-by-side columns** of independent service blocks. A
block starts at `Charges for <n>`, contains `Circuit #: <id>` and
`AT&T VPN Service`, and ends at `Total AT&T VPN Service <amount>` followed by
`Total Taxes` (and sometimes `Total Surcharges and Other Fees`). One delivered
row = one block: `service_id` from the Circuit #, `amount` from the block total,
`tax` = Taxes + Surcharges. The bold header text is drawn twice
(`Invoice Invoice 3658496118 3658496118`), which matters for the header fields.

## Thinking process (what to decide before clicking)

1. **Is it multi-column?** Yes — so the very first move is the **column
   divider**. Without it the two columns merge into shared rows and nothing
   downstream can separate them.
2. **What marks one item?** `Charges for` opens each block; `Total AT&T VPN
   Service` carries the amount. Those two become row roles.
3. **Where do the numbers live?** All values sit in a right-hand band. Because
   the page is split, one wide amount band works for both columns.
4. **What's the grouping?** A new item at every `Charges for` → `role_transition`
   (labelled **By start** in the UI).

---

## Step by step

### 0. Upload
Models → **Build model** → upload a sample PDF (start with the smallest,
`…_20260701_…pdf`, 5 rows).

### 1. Region (tab: Region)
- **Start anchors:** type `Charges for`, Enter.
- **End anchors:** type `Total Current Charges`, Enter.
- **Turn ON “Start anchor row is also data.”** `Charges for` is both the region
  opener *and* the item marker; without this toggle the anchor rows are dropped
  and you get **zero items** (the builder warns if a start anchor equals a
  classifier). This is Phase 1.6's anchor-inclusion fix.
- **Column divider:** switch the page selector to a page whose blocks fill both
  columns; a dashed **`gutter?`** hint appears. Click **Use detected gutter**
  (or drag the divider). The canvas re-reads the page as two reading columns —
  the row count jumps (e.g. 79 → 109) because left and right now cluster
  separately instead of merging.

### 2. Columns (tab: Columns)
- **Draw column** → drag one wide band across the right-hand value area (roughly
  the right 55% of the page). Name it `amount`. Because rows are now
  column-pure, this single band captures the left column's value *and* the right
  column's value (each row only has one).

### 3. Rows (tab: Rows) — four roles
Pick the role name, click **Assign by clicking rows**, click a matching row, then
**refine the auto-suggested regex** (it guesses e.g. `^Charges`; make it a clean
substring):

| Role | Pattern |
|---|---|
| `item_start` | `Charges for` |
| `item_total` | `Total AT&T VPN Service` |
| `tax_taxes` | `Total Taxes` |
| `tax_surcharges` | `Total Surcharges and Other Fees` |

Keep `item_total` specific (**not** `^Total`) or it will also grab `Total Taxes`,
`Total Group`, etc.

### 4. Groups (tab: Groups)
- Mode: **By start** (= `role_transition`).
- Start role: **item_start**.

### 5. Fields (tab: Fields)
- **service_id** → Advanced → capture regex `Circuit #:\s*([A-Z0-9.]+)`
  (rows = `all_in_group`, no column).
- **billing_reference** → Advanced → capture regex `(AT&T VPN Service)`
  (no “literal value” control exists — you capture the constant with a pattern;
  friction #4).
- **amount** → Column `amount`, transform `currency`, Advanced → rows `role`,
  Role `item_total`. (`amount` is auto-marked Required.)
- **tax_amount** → Column `amount`, Advanced → rows `role`, Role `tax_taxes`.
  *This is the single-source approximation* — see friction #1 for the exact
  `Taxes + Surcharges` sum.

### 6. Tax (tab: Tax)
- **Per-line amount** (there is no “extract per-row exact” mode — friction #2).

### 7. Verify
- **Run all** → the primary sample shows **5 rows** with correct service_id,
  billing_reference, amount and tax.
- Add the other three invoices (**Add invoice**) or use **Score against eval
  folder** → type `att.vpn` → per-file `matched/expected`. With header fields
  corrected the full model scores **2/4 files exact (10/10, 5/5)** and 25/30,
  16/18 on the larger two.

---

## Friction & refinements (flagged, not yet fixed)

Verified against source while doing this walkthrough. Ordered by impact.

1. **No summed / multi-source field in the UI.** `tax = Total Taxes + Total
   Surcharges` needs `ItemFieldRule.sources[] + combine:"sum"` — the interpreter
   supports it (Phase 1.6D), but `field-editor.tsx` exposes only one source. So
   the exact AT&T tax can only be authored by editing model JSON. *Refine:* add
   an “+ add source (sum)” affordance to the field card.
2. **Tax tab has no “extract per-row exact” mode.** Modes are none / flat_rate /
   per_line / subtotal_tax (`tax-editor.tsx`); none maps to
   `tax_output_mode="extract_exact"`, which is what a layout with *varying*
   per-row tax rates needs. Per-line happened to round-trip correctly here, but
   it's the wrong tool for mixed-rate invoices. *Refine:* add an “extract the
   printed per-row tax” mode.
3. **Header editor can't clean a value.** `header-editor.tsx` uses
   `value_pattern` when computing the live sample (line 58) but renders **no
   input to set it** — only Label text + value position + date format. On AT&T
   the bold header is doubled (`3658496118 3658496118`), so the value can't be
   de-duplicated, and the account number can't be captured. The header lookup
   also matched a wrong occurrence across pages (`EXT_INVOICENUMBER` came back as
   “Previous Bill”), so the live **Sample ≠ the value the backend extracts**.
   *Refine:* expose `value_pattern` (and `occurrence`) in the header panel and
   make the sample use the same all-pages resolution the interpreter does.
4. **No “literal value” field control.** `ItemFieldRule.literal` exists in the
   schema but not in `field-editor.tsx`; a constant like `AT&T VPN Service` has
   to be captured with a `(…)` regex instead.
5. **Row hitboxes are full-width on a split page.** The role-classifier overlay
   draws each row's click target as `absolute left-0 right-0`
   (`classifier-editor.tsx:84`), so on a two-column page the left- and
   right-column rows overlap and the dense marketing column intercepts clicks —
   many left-column rows become unclickable. *Refine:* scope row hitboxes to
   their `reading_column` x-range. (The header overlay is already per-cell and
   doesn't have this problem.)
6. **Columns tab is draw-only.** No numeric x0/x1 entry, so a precise band (e.g.
   420–1000) is hard to place by dragging. *Refine:* allow typing the band
   bounds.
7. **On-page grouping counter is misleading.** The Groups tab showed “0 line
   items on this page” while the backend produced the items correctly — the
   frontend `model-logic` approximation doesn't model `include_anchor_row` or the
   column split. *Refine:* either make the estimate split-aware or label it
   clearly as approximate.
8. **Preview result is collapsed by default.** The deliverable table and the
   near-miss “dropped rows” only appear after you click the file row to expand —
   easy to miss the very feedback you ran the preview for. *Refine:* auto-expand
   the primary result.
9. **Auto-suggested classifier regex needs hand-editing.** Clicking a row
   suggests e.g. `^Charges` / `^Total`; usable as a start but you must refine it
   (e.g. `^Total` is far too broad). Expected, but worth a hint that the suggestion
   is a starting point.

None of these block the **line-item** half of an AT&T-class model — that now
works end to end. #1–#3 are what stop the *document-level* columns from being
authored purely in the UI today.
