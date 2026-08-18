# Building a model in the visual Model Builder — worked example (Orange)

This is a step-by-step walkthrough of building an extraction model **by hand** in
the new Model Builder, using a real 23‑page Orange invoice
(`NSHERTZ17_202606_FRX_FRX02606002976.pdf`, 126 line items) as the example. It
covers the **thinking process** as much as the clicks, so you can repeat it for
any supplier.

The result: a hand‑built model that reproduces the AI's output **exactly** on the
three per‑line‑item fields (service id, description, amount) across all 126 rows —
at **$0** (no AI at run time).

---

## 0. The mental model — what a "model" actually is

A model is five decisions, in order. Each builder tab is one decision:

| Tab | Question it answers | Produces |
|-----|--------------------|----------|
| **Region** | *Where on the page do the line items live?* | start/end anchors |
| **Columns** | *Where are the vertical columns?* | named x‑bands |
| **Rows** | *What kind of row is each line?* | roles (via patterns) |
| **Groups** | *How do rows combine into one line item?* | grouping rule |
| **Fields** | *Where does each output value come from?* | field → column/role/regex |

Everything is **deterministic and layout‑based** — no AI. If you can describe the
*shape* of the invoice with these five decisions, the interpreter replays them on
every future invoice of that layout for free.

**Golden rule:** describe *shapes and positions*, never the literal values of this
one invoice. "A row whose first column matches `^\d{6,}`" is good; "the row that
says 12‑8276680" is not.

---

## 1. Read the invoice first (before touching the builder)

Don't start clicking. First understand the document's structure. I opened the
sample and looked at how the line items are laid out. The relevant part repeats
per **service group**:

```
Internet Essential - Country : FRA - ... - Installed Offer ID : 12-8276680   ← group start
  SITE : FRAGO90-C1
  RECURRING CHARGES
    access                     ... 93.00 EUR ...  93.00   ← a charge row
    Monthly Service Management ... 10.00 EUR ...  10.00   ← a charge row
  TOTAL                        ...  20.00        103.00   ← group total
```

Then I compared it to the **target output** (what the AI produced). Each output
row is **one service group**:

| EXT_SERVICEID | EXT_BILLINGREFERENCE | EXT_AMOUNT | EXT_TAX |
|---|---|---|---|
| 12-8276680 | access | 103 | 20.6 |
| 12-6842892 | Disconnection fee | 1500 | 300 |

The three key realisations:

1. **One output row = one service group**, and each group is announced by an
   `Installed Offer ID :` line. I counted them: **`Installed Offer ID` appears
   exactly 126 times** — the same as the number of output rows. That's the
   grouping anchor.
2. **`amount` is the group `TOTAL`** (103 = 93 + 10), not the individual charges.
3. **`service_id` = the Installed Offer ID** (`12-8276680`); **`billing_reference`
   = the first charge description** (`access`); **tax = 20 %** of the amount.
4. The other columns (`EXT_DATE`, `EXT_ACCOUNT`, `EXT_INVOICENUMBER`, `EXT_TAX`)
   are **document‑level** values (invoice date, account no., invoice no., tax
   rate) that the output spec stamps onto every row — they are *not* per‑line.

That analysis **is** the model. The builder is just how you type it in.

> Tip: the "Show rows" toggle colours every reconstructed row; the coloured role
> overlay (Rows tab) and the item bands (Groups tab) let you confirm each of these
> realisations visually before you trust them.

---

## 2. Step‑by‑step in the builder

Launch: **Models → Build model** (or **Profiles → a profile → Build model
manually**), then **upload the sample PDF**. The canvas reconstructs the page from
its real text — no image, so it's crisp and the coordinates are exact.

I moved the page selector to **page 6** (the first page with the charge table) so
the columns and rows I was reasoning about were visible.

### Region — *where the items live*
On the **Region** tab:
- Start anchor: `Breakdown Of Charges`
- End anchor: `TOTAL AMOUNT DUE`

The region tints green from the start anchor down. Anchors are matched as
case‑insensitive substrings and the region carries across all 23 pages until the
end anchor, so items on every page are in scope.

### Columns — *where the values sit*
On the **Columns** tab, **Draw column**, then drag a vertical band over each
column and name it:
- **`amount`** — the right‑most "Amount" column (the group total lands here).
- **`desc`** — the left "Description" column (the charge names live here).

Cells captured by a band are tinted in the band's colour, so you can see instantly
whether the band is catching the right text. Resize with the edge handles until it
does.

### Rows — *what each row is*
On the **Rows** tab, give each *kind* of row a role. Pick the role, click a
matching row on the page to seed a rule, then tighten its regex:

| Role | Pattern (regex on the row text) | Matches |
|------|-------------------|---------|
| `offer` | `Installed Offer ID` | the group‑start line (126 of them) |
| `item_total` | `^TOTAL\s+[0-9]` | the per‑group `TOTAL 20.00 103.00` row (not "TOTAL AMOUNT DUE") |
| `charge` | `EUR` | the individual charge rows (they print `… EUR`) |

Order matters — first match wins. The overlay recolours the page live so you can
confirm each pattern hits exactly the rows you intend (here: 6 offers, 5 totals, 9
charges on page 6).

> Why `EUR` for `charge`? The charge rows are the only ones printing an amount
> "…EUR"; totals and offer lines don't. Picking a stable token the target rows
> share (and the others don't) is the whole game.

### Groups — *how rows become one item*
On the **Groups** tab:
- Mode: **By start** (role‑transition)
- Start role: **`offer`**

This starts a new line item at every `offer` row and runs it until the next one —
so each Installed Offer ID becomes exactly one item. The panel confirmed **6 line
items on page 6** (126 across the document).

### Fields — *where each value comes from*
On the **Fields** tab, add the three per‑line fields and point each at the right
source (open **Advanced** for the row selectors):

| Field | Column | Rows | Row role | Regex / notes |
|-------|--------|------|----------|---------------|
| `service_id` | (none) | role | `offer` | `Installed Offer ID\s*:\s*([0-9-]+)` — capture the id out of the offer line |
| `billing_reference` | `desc` | role | `charge` | first charge row's description (`access`) |
| `amount` | `amount` | role | `item_total` | the group total's amount; transform **currency**; **Required** |

`amount` is marked Required, so any group that fails to yield an amount is dropped
— exactly what you want (it keeps noise out).

### Preview — *prove it*
**Run all** in the Preview panel runs the model against the full 23‑page PDF at
$0 and shows the deliverable it produces:

```
126 rows
EXT_SERVICEID   EXT_BILLINGREFERENCE   EXT_AMOUNT
12-8276680      access                 103
12-6842892      Disconnection fee      1500
12-4128720      Access Speed           80
12-4979742      access                 103
12-10788936     Access                 81
...
```

That's an **exact match** to the AI output on service id, description, and amount,
for all 126 rows.

To check it **generalises**, use **Add invoice** to upload another Orange PDF and
Run all — you get a per‑file row count and a pass/fail against that file's AI
baseline, side by side.

### Save
**Save as candidate** (choosing the owning profile, or pre‑bound when launched
from a profile) persists it. Routing keys (layout fingerprint + family) are
computed server‑side from the sample, and it enters the normal candidate →
approval lifecycle.

---

## 3. How to think about it (the reusable recipe)

1. **Find the repeating unit** and a stable text marker that starts it. That marker
   is your grouping anchor (`Installed Offer ID` here). Count its occurrences — it
   should equal your expected row count.
2. **Bound the section** with a start and end anchor (Region).
3. **Name the columns** you'll pull values from (Columns).
4. **Classify the row types** you care about with shape patterns, using a token the
   target rows share and others don't (Rows).
5. **Group** by the repeating unit (Groups).
6. **Map each field** to a (role, column, regex) (Fields).
7. **Preview, then add more invoices** and preview again until it's stable.

Iterate on the **Preview**, not in your head — wrong row count usually means the
grouping/region is off; wrong values mean a column band or a field's role is off.

---

## 4. What matched, and the honest gaps

**Matched exactly (the hard part — 126 grouped rows):** `EXT_SERVICEID`,
`EXT_BILLINGREFERENCE`, `EXT_AMOUNT`.

**Not populated by the builder yet** (they were blank/`UNKNOWN` in the preview):
`EXT_DATE`, `formula`, `EXT_ACCOUNT`, `EXT_INVOICENUMBER`, `EXT_TAX`. These are
**document‑level header fields + the tax rate**, and the current builder only edits
the line‑item side (region / columns / rows / groups / fields). They're the clear
next features:

- **Header fields editor** — label‑anchored locators for invoice number, account
  number, invoice date, due date (the invoice literally prints `Number :`,
  `Your Account Number :`, `Date :`, `PAYMENT DUE DATE`).
- **Totals / tax‑rate** — a per‑line tax rate (the `20.00` "Tax %" column) or a
  document tax rate, so `EXT_TAX` computes (20 % → 20.6).

With those two additions the hand‑built model would reproduce the AI's output on
**every** column, not just the line‑item core.

A couple of smaller builder papercuts noticed while doing this:
- The **Rows** classifier editor only writes *text* patterns; a "row has a number
  in column X" predicate would help for tables where the target rows share no text
  token.
- The **Fields** advanced panel has no "occurrence (first/last)" control (it
  defaults to first). It didn't matter here (one total per group) but would for
  groups with multiple totals.
