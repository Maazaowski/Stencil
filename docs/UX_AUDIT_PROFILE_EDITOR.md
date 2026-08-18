# Profile Editor — UX Audit (2026-07-06)

Live Playwright walkthrough of the profile editor against the running stack,
plus a code cross-check of every field the editor asks for. Screenshots in
`docs/ux-audit/` (`profile-setup-tab.png`, `profile-advanced-tab.png`,
`profile-grouped-tax.png`, `profile-assistant.png`).

The editor is `frontend/src/app/profiles/[profileId]/page.tsx` (~1960 lines). It
has four tabs: **Setup** (numbered 1–5 flow), **Advanced** (6 dense sections),
**Preview Output**, **Training**. The AI-assistant create flow
(`/profiles/new/assistant`) is a clean 3-column layout and needs no change.

---

## A. Duplicate fields — the same thing asked twice (highest priority)

The same "what is X labeled on this invoice?" question is asked in **two tabs**,
writing **two different backend fields**, both of which are injected into the AI
prompt. On the live Rogers profile they have already **drifted apart**:

| Concept | Setup §3 "What each output field looks like" (writes `field_overrides.label_hint`) | Advanced "Grouped row policy / Tax policy" (writes `line_item_hints.*_column_label`) | Live Rogers values |
|---|---|---|---|
| service_id | **Service ID** | **Service ID column label** | both = `Wireless, Phone` (identical) |
| billing_reference | **Billing Reference** | **Billing reference column label** | `Monthly charges` vs `Monthly Charges` (case drift) |
| amount | **Amount** | **Amount column label** | `Total monthly charges, Total one-time charges…` **vs** `Total before taxes` ← **conflict** |
| tax | **Tax** | **Tax amount column label** | duplicate ask |

**Why this matters beyond clutter:** `build_extraction_user_prompt`
(`src/stencil/extraction/prompts.py`) emits BOTH — the field-override label
hint *and* the line-item column label — so for `amount` the model receives two
contradictory instructions in the same prompt. This is the same class of
conflicting-hint problem behind the Rogers net-vs-gross incident. The user has no
way to know which wins.

**Other repeated inputs:**
- **Profile ID** appears in Setup §1 *and* Advanced "Profile Metadata" (both
  read-only once the profile exists).
- **Notes** appears in Setup §5 *and* Advanced "Notes" — the same `profile.notes`
  textarea rendered in two tabs.
- **Amount is configurable 3 ways**: Setup §3 label + Advanced "Amount column
  label" + Advanced "Amount source (executable policy)".
- **Tax is configurable 5 ways**: Setup §3 label + "Tax amount column label" +
  "Tax source" + "Tax output mode" + "Tax rate source".
- **Reconciliation "Amount total label / Tax total label"** overlaps with
  **Extraction-keywords "Subtotal Keywords / Tax Keywords"** — both locate the
  totals to reconcile against.
- **"Detail Table Anchors" vs "Table Column Labels"** sit adjacent under one
  section and are easily confused (section-heading anchors vs column headers).

---

## B. Example values shown *inside* the input box (user-flagged)

Many inputs use `placeholder="e.g. …"`, so a blank field shows grey example text
that reads like a value. On a brand-new profile **every** field shows an `e.g.`
placeholder. The `Field` component already renders a hint line *below* the input,
which is the correct home for examples. Representative offenders:

- Profile ID → `e.g. gtt.standard.v1`
- Source name → `e.g. GTT Communications`
- Layout Description → `e.g. Standard monthly invoice, grouped by service`
- Detail Start Marker → `e.g. INDEX OF CURRENT CHARGES BILLED`
- **Service ID value pattern (regex)** → `e.g. PDFSPLITSTART-[^_]+_(.+?)-PDFSPLITEND`
  (the exact "sample regex in the box" the user called out)
- Amount column label → `e.g. SUB-TOT, Current Charges`
- Tax amount column label → `e.g. Taxes, TAX / SURCHRG`

Several of these fields **also** carry a hint line, so the example is duplicated.

**Fix:** drop `e.g. …` from placeholders (leave blank or a neutral noun like
"Regex pattern"), and move any genuinely useful example into the hint line below.

---

## C. Structure / clutter

- **Two overlapping editing surfaces.** Setup §3 and Advanced "Grouped row
  policy" both configure service_id / billing / amount / tax. Setup should be the
  friendly path; Advanced should hold only what Setup does *not* — the
  deterministic knobs (granularity, id/billing preferences, value patterns,
  amount/tax sources, markers, fingerprint). The per-field **label** should be
  asked exactly once.
- **Three stacked "Service ID …" fields** ("Service ID (primary row identifier)"
  dropdown, "Service ID column label", "Service ID value pattern") are hard to
  distinguish at a glance; same for Billing reference.
- **Advanced dumps ~25 inputs across 6 sections** with dense descriptions —
  intimidating. Rarely-used sections (Layout Fingerprint, Extraction keywords)
  could be collapsed by default.
- **"Profile Metadata"** (read-only ID/version/owner/created/updated) is the
  first thing in Advanced; it's low-value and belongs at the bottom.

---

## Prioritized change list

### Urgent (small, safe, directly addresses the two flagged issues)
1. **Placeholder cleanup** — remove `e.g. …` example text from all inputs; move
   examples to the hint line. (Pure presentation; no behavior change.)
2. **Collapse the per-field-label duplication** — stop asking for service_id /
   billing_reference / amount / tax labels in two places. Recommended: keep the
   friendly Setup §3 ask, and in Advanced remove the four plain "* column label"
   inputs, driving `line_item_hints.*_column_label` from the §3 value on save (or
   vice-versa). Removes the conflicting-prompt risk. *(Needs a small decision —
   see report.)*
3. **De-dupe Profile ID and Notes** across tabs (show ID once; single Notes).

### Medium (clarity, no data risk)
4. Regroup Advanced so each field appears once and related knobs cluster
   (Region markers · Row grouping · Amount · Tax · Reconciliation · Fingerprint ·
   Training). Rename the stacked "Service ID …" fields for distinctness.
5. Collapse rarely-used Advanced sections by default; move Profile Metadata down.

### Later (full redesign — out of scope now)
6. Merge Setup + Advanced into one progressive form (basic → reveal advanced per
   field) so there's a single place per concept.

---

## Housekeeping note
A throwaway admin `reviewer@temforce.com` (password `review-pass-123`) was created
to drive the Playwright login and could not be auto-removed (Docker stopped before
cleanup). Delete it on next stack start:
`DELETE FROM users WHERE email='reviewer@temforce.com';`
