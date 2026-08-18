# Golden corpus

Real invoices + their correct outputs. Three regression suites run against
this corpus to keep the model generation engine honest:

| Suite | File | When | What it proves |
| --- | --- | --- | --- |
| 1 — Fingerprints | `tests/test_fingerprint/test_corpus_fingerprints.py` | every commit | same layout ⇒ same fingerprint; different layouts never collide |
| 2 — Extraction | `tests/test_models/test_corpus_extraction.py` | every commit | the committed `model.json` reproduces the correct output for every PDF |
| 3 — Authoring | `tests/test_models/test_corpus_authoring.py` | `pytest -m authoring` (needs OpenAI key) | AI can author a model from one invoice that generalizes to all of them |

## Adding a supplier layout

Create one folder per `{supplier}.{layout}` variant:

```
tests/fixtures/corpus/
  acme.standard/
    profile.json                supplier profile (hints) used for this layout
    invoices/
      invoice_001.pdf           2+ PDFs sharing the SAME layout
      invoice_002.pdf
    expected/
      invoice_001.xlsx          the CORRECT delivered output for each PDF
      invoice_002.xlsx          (same file stem as the PDF)
    expected_line_items.json    optional summary expectations (see below)
    model.json                  authored model snapshot (written by Suite 3)
```

- `expected/*.xlsx` enables exact cell-by-cell comparison (preferred).
- `expected_line_items.json` is a lighter alternative when no XLSX is
  available yet — per PDF: `{"count": N, "total": "123.45", "service_ids": [...]}`
  under an `"invoices"` key.
- `model.json` is produced by running `pytest -m authoring` for the layout
  (or by the training workflow in the app); review and commit it so Suite 2
  locks in the behaviour on every subsequent commit.

A folder missing `model.json` or expectations is skipped by the suites it
can't serve — fixtures can be added incrementally.

## CI gating status

| Layout | PDFs in git | `model.json` | Expected outputs | Suites active in CI |
| --- | --- | --- | --- | --- |
| `eunetworks.standard` | Yes (8 invoices) | Not yet | Not yet | Suite 1 (fingerprints) only |
| `colt.standard` | No (`invoices/` missing) | Yes | Not yet | Skipped until PDFs are committed |

To gate extraction (Suite 2) for a layout, commit `expected/*.xlsx` or
`expected_line_items.json`, run the manual authoring workflow (or local
`pytest -m authoring`), then commit the refreshed `model.json`.

Colt PDFs are not in the repository; add them under
`colt.standard/invoices/` when available if Colt regressions should block merge.

