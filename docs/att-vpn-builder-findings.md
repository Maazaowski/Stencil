# Model Builder findings — AT&T VPN two-column layout (`eval_cases/att.vpn`)

Status: **NOT resolvable with the current builder/interpreter.** Notes only — no code changed.

## The case
- 4 invoices, deliverable = one row per **Circuit #** service block.
  Expected row counts: 30 / 18 / 10 / 5.
- Per row: `service_id` = text after `Circuit #:`, `billing_reference` = literal
  `AT&T VPN Service`, `amount` = the `Total AT&T VPN Service` value,
  `tax` = `Total Taxes` + `Total Surcharges and Other Fees` (missing = 0).
- Header: `EXT_DATE`/`formula` = Billing Date, `EXT_ACCOUNT` = `831-000-9099 383`,
  `EXT_INVOICENUMBER` varies per file.

## The blocker: two-column "newspaper" layout
Each page prints service blocks in **two independent side-by-side columns**
(left x≈29–256, right x≈317–546), each a full top-to-bottom stack of blocks.

The layout engine builds **one visual row per y-band across the whole page width**,
so left- and right-column text at the same y is **merged into a single row**. Real
examples from the 5-row invoice:

- `Total Group #000001 8,509.52  Charges for 90741800` — a left block's *total* row
  merged with a right block's *start* row.
- `Charges for 90414789  Gross: 221.00` — a left block *start* merged with a right
  component line.
- `Customer Location:  Total AT&T VPN Service 184.47` — left filler merged with the
  right column's **amount** row.

Consequences for the model primitives:
1. **One role per row** can't represent a row that is simultaneously left-filler and
   a right-column total.
2. **`role_transition` grouping** orders by a single y-sequence, so it interleaves the
   two columns' blocks — it cannot form "left blocks" vs "right blocks".
3. A single **amount column band** can only sit over one column's value x (left≈nx441,
   right≈nx911); the other column's amount falls outside it.

## Best achievable result (measured)
A hand-tuned model (region `Group #`→`Total Current Charges`, `item_start`=`Charges for`,
`item_total`=`Total AT&T VPN Service`, left amount band nx430–480, `role_transition`)
on the 5-row invoice produced **3 of 5 rows** — exactly the three **left-column** blocks
(181.07, 292.56, 6952.36). The two **right-column** blocks (441.50, 184.47) were dropped:
their amounts are outside the left band and their block boundaries are scrambled by the
merged rows. No band/classifier tuning fixes this, because the merge happens in the
layout engine *before* the model sees any rows.

(By contrast, `header_fields` + `extract_exact` tax would work fine here — the blocker
is purely line-item grouping.)

## What would be needed (do NOT build yet — captured for planning)
- **Column-aware layout / region.** Let a region declare a *reading column* (an x-band
  that segments the page into an independent row sequence), so grouping runs per column.
  Equivalent to giving the layout engine a "split into N text columns, order within each"
  mode and exposing it as a region option in the builder.
- Until then, this layout can only be served by the AI path + profile `line_item_hints`
  (which is what generated the expected output), not by a builder model.

## Smaller builder friction noticed while attempting this (polish backlog)
1. **Region start-anchor rows are excluded from the region.** Using the same text as
   both the region `start_anchor` and the `item_start` role silently yields **0 groups**
   (the anchor row is consumed as a delimiter). Non-obvious; the builder should warn, or
   offer an "include anchor row" toggle.
2. **`required` amount drops whole items silently.** When the amount band misses, the
   item vanishes with only a generic "model produced no line items" — no per-row "amount
   not found in column X" hint. The preview should show near-miss rows and *why* they
   were dropped.
3. **No summed-field transform.** `tax = Total Taxes + Total Surcharges and Other Fees`
   can't be expressed (a field reads one row/column). Need an additive/multi-source field.
4. **Multi-line / merged cell text is confusing to author against.** The reconstructed
   canvas shows the merged row text; there's no way in the builder to see or pick "the
   left cell only" vs "the right cell only" of a merged row.
5. **No way to preview against the eval `expected/*.xlsx` directly.** Had to compare by
   hand. A "diff against expected" drop for a folder of PDF+XLSX pairs would make
   authoring these measurable.
