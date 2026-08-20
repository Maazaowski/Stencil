"""Deterministic page classification — what each page of a document *is*.

No AI, no DB, no settings: a pure function of a ``LayoutDocument``.  Two pages
whose cell x-positions agree are the same table continuing; that is the core
signal, and it is geometric rather than semantic, so it holds for a French
telecom bill and a shipping manifest alike.

Why this exists
---------------
Extraction currently decides which pages matter with two hardcoded
"first five and last two" heuristics (``compact_chunked`` and
``profiles/authoring_runtime``) and, until recently, a silent 30-page cap.  All
three are "take some pages" with no theory, and on a long document they take the
wrong ones: a 656-page invoice in the eval corpus carries its entire 384-row
deliverable on pages 3-15, and nothing in the system knew that.

Classifying pages up front turns that into a fact the sampler, the model author
and the operator can all read.

Roles
-----
``header``               leading non-tabular pages (cover, remittance, notices)
``detail``               the first page of a table run
``detail_continuation``  a page continuing the run above it
``summary``              trailing non-tabular pages, or a page dominated by
                         total-style rows
``noise``                effectively empty — no usable text

A *run* is a maximal group of consecutive pages sharing a column signature.  A
document may hold several: the same 656-page invoice has one long run for the
billing summary and hundreds of short ones for the per-account detail that never
reaches the deliverable.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from stencil.extraction.layout import LayoutDocument, LayoutPage, VisualRow

# Normalized page space is 0-1000; bucketing at 20 tolerates the sub-pixel drift
# between two renderings of the same table without merging adjacent columns,
# which are rarely closer than 30 units in a real layout.
_COLUMN_BUCKET = 20

# A row needs this many cells before it counts as evidence of a table. Two cells
# is a label/value pair, which every cover page has.
_MIN_TABULAR_CELLS = 3

# A page needs this many tabular rows before it is a table rather than a stray
# aligned pair of lines.
_MIN_TABULAR_ROWS = 3

# Share of a run's column signature a page must reproduce to be the same table.
# Deliberately generous: a continuation page legitimately drops a column when
# every value in it happens to be blank.
_RUN_MATCH_RATIO = 0.6

# Row roles that indicate a page is closing something out rather than listing it.
_TOTAL_ROLES = frozenset({"group_total", "section_total", "tax_total"})

# Layout roles that carry a delivered record. ``_infer_row_role`` assigns these.
_RECORD_ROLES = frozenset({"detail", "service_start", "period_amount"})

_MONEY = re.compile(r"[$€£]?\s*\(?-?\d[\d,]*\.\d{2}\)?")


@dataclass(frozen=True)
class PageClassification:
    """What one page is, and why."""

    page_number: int
    role: str
    column_signature: tuple[int, ...]
    tabular_rows: int
    record_rows: int
    total_rows: int
    text_rows: int
    run_id: int | None = None

    @property
    def is_detail(self) -> bool:
        return self.role in ("detail", "detail_continuation")


@dataclass(frozen=True)
class TableRun:
    """A maximal group of consecutive pages carrying the same table."""

    run_id: int
    pages: tuple[int, ...]
    column_signature: tuple[int, ...]
    record_rows: int

    @property
    def first_page(self) -> int:
        return self.pages[0]

    @property
    def last_page(self) -> int:
        return self.pages[-1]

    def __len__(self) -> int:
        return len(self.pages)


@dataclass
class DocumentPageMap:
    """Every page classified, plus the table runs they form."""

    pages: list[PageClassification] = field(default_factory=list)
    runs: list[TableRun] = field(default_factory=list)

    def pages_with_role(self, *roles: str) -> list[int]:
        return [page.page_number for page in self.pages if page.role in roles]

    @property
    def largest_run(self) -> TableRun | None:
        """The run holding the most records.

        On a document whose deliverable is a summary table followed by hundreds
        of pages of per-account backup, this is the table that matters.
        """
        return max(self.runs, key=lambda run: (run.record_rows, len(run)), default=None)

    def context_pages(self, *, max_pages: int = 8) -> list[int]:
        """Pages likely to carry document-level fields.

        Different question from :meth:`sample_pages`, which asks "show me every
        distinct shape". This asks "where do the invoice number, the account and
        the stated totals live", and the answer is structural rather than
        positional: identifiers sit before the table that produces the
        deliverable, totals sit at the end of it or after it.

        Replaces the ``page <= 5 or page >= last - 1`` rule, which finds the
        right pages only when the header happens to be in the first five.
        """
        if not self.pages:
            return []

        anchor = self.largest_run.first_page if self.largest_run else None
        last_page = self.pages[-1].page_number

        chosen: list[int] = []
        seen: set[int] = set()

        def take(page: int | None) -> None:
            if page is not None and page not in seen:
                seen.add(page)
                chosen.append(page)

        for page in self.pages:
            if page.role == "noise":
                continue
            if anchor is not None and page.page_number >= anchor:
                break
            take(page.page_number)

        if self.largest_run is not None:
            take(self.largest_run.last_page)
            # Totals follow their table: the subtotal for a run that ends on
            # page 14 is routinely printed at the top of page 15.
            if self.largest_run.last_page < last_page:
                take(self.largest_run.last_page + 1)
        for page in self.pages_with_role("summary"):
            take(page)
        take(last_page)

        return sorted(chosen[:max_pages])

    def sample_pages(self, *, max_pages: int = 12, interior_per_run: int = 2) -> list[int]:
        """Pages that between them show every distinct shape in the document.

        Ranked by what each page can teach, not by where it sits, because a
        document can hold hundreds of runs and an even spread across them shows
        the same shape hundreds of times while missing the one table that
        produces the deliverable.  Order of value:

        1. the leading pages before any table -- where header fields live
        2. the first and last page of each run, largest run first, since a
           table's first page carries its column headers and its last carries
           the totals that a check will be written against
        3. interior pages of those runs, spread out, so continuation quirks show
        4. trailing summary pages

        This is what replaces "first five and last two".
        """
        return sorted(self.ranked_pages(interior_per_run=interior_per_run)[:max_pages])

    def ranked_pages(self, *, interior_per_run: int = 2) -> list[int]:
        """Every page, most informative first.

        Same ordering as :meth:`sample_pages` but unbounded and un-sorted, so a
        caller working to a token budget can keep taking pages until it runs out
        rather than being handed a fixed-size sample.
        """
        ranked: list[int] = []
        seen: set[int] = set()

        def take(page: int) -> None:
            if page not in seen:
                seen.add(page)
                ranked.append(page)

        # Anchor on the largest run, not the first. Header fields live before the
        # table that produces the deliverable, and those pages are not reliably
        # classified "header" -- a cover page carrying a summary box is itself a
        # small run. What matters is that they come first, not what they are.
        anchor = self.largest_run.first_page if self.largest_run else None
        leading = [
            page.page_number for page in self.pages
            if page.role != "noise" and (anchor is None or page.page_number < anchor)
        ]
        for page in leading[:3]:
            take(page)

        by_value = sorted(self.runs, key=lambda run: (-run.record_rows, -len(run), run.run_id))
        for run in by_value:
            take(run.first_page)
            take(run.last_page)
        for run in by_value:
            interior = run.pages[1:-1]
            if not interior or interior_per_run <= 0:
                continue
            step = max(1, len(interior) // (interior_per_run + 1))
            for page in interior[step::step][:interior_per_run]:
                take(page)

        for page in self.pages_with_role("summary"):
            take(page)

        # Everything else, in document order, so the list is a total ranking.
        for page in self.pages:
            if page.role != "noise":
                take(page.page_number)

        return ranked


def classify_pages(document: LayoutDocument) -> DocumentPageMap:
    """Classify every page of ``document`` and group its table runs."""
    profiles = [_profile_page(page) for page in document.pages]
    runs = _build_runs(profiles)

    run_of_page: dict[int, int] = {}
    first_page_of_run: dict[int, int] = {}
    for run in runs:
        first_page_of_run[run.run_id] = run.first_page
        for page_number in run.pages:
            run_of_page[page_number] = run.run_id

    first_detail = min(run_of_page, default=None)
    last_detail = max(run_of_page, default=None)

    classified: list[PageClassification] = []
    for profile in profiles:
        number = profile["page_number"]
        run_id = run_of_page.get(number)
        if profile["text_rows"] == 0:
            role = "noise"
        elif run_id is not None:
            role = "detail" if first_page_of_run[run_id] == number else "detail_continuation"
        elif _looks_like_summary(profile, number, first_detail, last_detail):
            role = "summary"
        else:
            role = "header"
        classified.append(
            PageClassification(
                page_number=number,
                role=role,
                column_signature=profile["signature"],
                tabular_rows=profile["tabular_rows"],
                record_rows=profile["record_rows"],
                total_rows=profile["total_rows"],
                text_rows=profile["text_rows"],
                run_id=run_id,
            )
        )
    return DocumentPageMap(pages=classified, runs=runs)


# ---------------------------------------------------------------------------
# internals
# ---------------------------------------------------------------------------


def _profile_page(page: LayoutPage) -> dict:
    rows = page.visual_rows
    tabular = [row for row in rows if len(row.cells) >= _MIN_TABULAR_CELLS]

    # The signature is the set of column starts shared by tabular rows. Counting
    # occurrences and keeping the common ones drops the one-off indent that a
    # wrapped description introduces.
    counts: dict[int, int] = {}
    for row in tabular:
        for bucket in _row_buckets(row):
            counts[bucket] = counts.get(bucket, 0) + 1
    floor = max(2, len(tabular) // 4)
    signature = tuple(sorted(bucket for bucket, n in counts.items() if n >= floor))

    return {
        "page_number": page.page_number,
        "signature": signature,
        "tabular_rows": len(tabular),
        "record_rows": sum(1 for row in rows if row.row_role in _RECORD_ROLES and len(row.cells) >= 2),
        "total_rows": sum(1 for row in rows if row.row_role in _TOTAL_ROLES),
        "text_rows": sum(1 for row in rows if row.text.strip()),
        "money_rows": sum(1 for row in rows if _MONEY.search(row.text)),
    }


def _row_buckets(row: VisualRow) -> set[int]:
    buckets = set()
    for cell in row.cells:
        box = cell.normalized_bbox or cell.bbox
        buckets.add(int(box.x0) // _COLUMN_BUCKET)
    return buckets


def _is_tabular(profile: dict) -> bool:
    return (
        profile["tabular_rows"] >= _MIN_TABULAR_ROWS
        and len(profile["signature"]) >= _MIN_TABULAR_CELLS
        and profile["money_rows"] > 0
    )


def _signatures_match(left: tuple[int, ...], right: tuple[int, ...]) -> bool:
    """Do two pages carry the same table?

    Asymmetric on purpose: the comparison is against the *smaller* signature, so
    a continuation page that happens to leave one column blank still matches the
    page above it, while a genuinely different table does not.
    """
    if not left or not right:
        return False
    shared = len(set(left) & set(right))
    return shared >= _RUN_MATCH_RATIO * min(len(left), len(right))


def _build_runs(profiles: list[dict]) -> list[TableRun]:
    runs: list[TableRun] = []
    current: list[dict] = []

    def close() -> None:
        if not current:
            return
        merged: set[int] = set()
        for profile in current:
            merged |= set(profile["signature"])
        runs.append(
            TableRun(
                run_id=len(runs),
                pages=tuple(profile["page_number"] for profile in current),
                column_signature=tuple(sorted(merged)),
                record_rows=sum(profile["record_rows"] for profile in current),
            )
        )
        current.clear()

    for profile in profiles:
        if not _is_tabular(profile):
            close()
            continue
        if current:
            contiguous = profile["page_number"] == current[-1]["page_number"] + 1
            if contiguous and _signatures_match(current[-1]["signature"], profile["signature"]):
                current.append(profile)
                continue
            close()
        current.append(profile)
    close()
    return runs


def _looks_like_summary(
    profile: dict, number: int, first_detail: int | None, last_detail: int | None
) -> bool:
    if first_detail is None or last_detail is None:
        return False
    if number > last_detail:
        return True
    # A page wedged between runs that is mostly totals is closing a section, not
    # introducing one.
    return number > first_detail and profile["total_rows"] > profile["record_rows"]
