"""Rules-first handling of long documents.

The AI half of this path cannot run offline, so what is covered here is the part
that decides *whether* to trust it: page selection, the arithmetic verification
that stands in for a ground truth, and the guarantee that every failure falls
back to ordinary extraction rather than losing a document.
"""

from decimal import Decimal

import fitz
import pytest

from stencil.config import settings
from stencil.models.sample_authoring import (
    plan_sample,
    replay_and_verify,
    should_author_from_sample,
    verify_against_stated_totals,
)
from stencil.models.schema import (
    ExtractionModel,
    FieldSource,
    FieldTransform,
    GroupingRule,
    HeaderFieldRule,
    ItemFieldRule,
    RegionRule,
    RowClassifier,
    RowMatch,
)
from stencil.profiles.schema import (
    ClassificationSignals,
    SupplierIdentity,
    SupplierProfile,
)
from stencil.validation.schema import (
    CanonicalInvoice,
    ExtractionMetadata,
    ExtractionPath,
)

PAGES = 12
ROWS_PER_PAGE = 10
ROW_AMOUNT = Decimal("70.80")
STATED_TOTAL = ROW_AMOUNT * PAGES * ROWS_PER_PAGE


def _profile() -> SupplierProfile:
    return SupplierProfile(
        profile_id="p.v1",
        identity=SupplierIdentity(canonical_name="Test"),
        classification=ClassificationSignals(output_type="standard"),
    )


@pytest.fixture(scope="module")
def long_pdf(tmp_path_factory):
    """A document long enough to qualify, with a total it states about itself."""
    path = tmp_path_factory.mktemp("sample") / "long.pdf"
    doc = fitz.open()
    cover = doc.new_page(width=612, height=792)
    cover.insert_text((60, 100), "Invoice Number:", fontsize=9)
    cover.insert_text((300, 100), "INV-0001", fontsize=9)
    cover.insert_text((60, 120), "Total Due:", fontsize=9)
    cover.insert_text((300, 120), f"{STATED_TOTAL:,.2f}", fontsize=9)

    for n in range(2, PAGES + 2):
        page = doc.new_page(width=612, height=792)
        page.insert_text((60, 90), "SERVICE", fontsize=8)
        page.insert_text((520, 90), "AMOUNT", fontsize=8)
        for i in range(ROWS_PER_PAGE):
            y = 110 + i * 16
            page.insert_text((60, y), f"SVC{n:02d}{i:03d}", fontsize=8)
            page.insert_text((520, y), f"{ROW_AMOUNT}", fontsize=8)
    doc.save(str(path))
    doc.close()
    return path


def _model() -> ExtractionModel:
    return ExtractionModel(
        model_id="m", supplier_profile_id="p.v1", supplier="Test",
        region=RegionRule(columns=[
            {"name": "service_id", "x0": 0, "x1": 400},
            {"name": "amount", "x0": 700, "x1": 1000},
        ]),
        row_classifiers=[
            RowClassifier(role="skip", where=RowMatch(row_text=r"^SERVICE")),
            RowClassifier(role="detail", where=RowMatch(
                column="service_id", pattern=r"^SVC\d{5}$")),
        ],
        grouping=GroupingRule(mode="single_row", item_role="detail"),
        item_fields=[
            ItemFieldRule(name="service_id",
                          source=FieldSource(rows="first", column="service_id")),
            ItemFieldRule(name="amount", required=True,
                          source=FieldSource(rows="first", column="amount"),
                          transform=FieldTransform(type="currency")),
        ],
        # Without this the replayed document states no total, and verification
        # correctly refuses to trust rows it cannot check.
        totals={
            "total_due": HeaderFieldRule(page=1, label="Total Due:", value_position="right"),
        },
        header_fields={
            "invoice_number": HeaderFieldRule(
                page=1, label="Invoice Number:", value_position="right"),
        },
    )


def _invoice(rows: int, *, stated_total: Decimal | None, amount=ROW_AMOUNT) -> CanonicalInvoice:
    fields = {} if stated_total is None else {"total_due": stated_total}
    return CanonicalInvoice(
        intake_id="t",
        fields=fields,
        rows=[{"line_number": i + 1, "description": "x", "amount": amount}
              for i in range(rows)],
        metadata=ExtractionMetadata(extraction_path=ExtractionPath.MODEL),
    )


class TestGate:
    def test_it_is_off_unless_explicitly_enabled(self):
        """An unvalidated path must not change behaviour on its own."""
        assert settings.sample_authoring_enabled is False
        assert should_author_from_sample(1000, _profile()) is False

    def test_a_long_document_with_a_profile_qualifies(self):
        assert should_author_from_sample(656, _profile(), enabled=True, min_pages=100)

    def test_a_short_document_does_not(self):
        assert not should_author_from_sample(8, _profile(), enabled=True, min_pages=100)

    def test_no_profile_means_no_home_for_a_model(self):
        assert not should_author_from_sample(656, None, enabled=True, min_pages=100)

    def test_a_zero_threshold_disables_the_gate(self):
        assert not should_author_from_sample(656, _profile(), enabled=True, min_pages=0)


class TestVerification:
    """The arithmetic that stands in for a ground truth."""

    def test_rows_matching_the_stated_total_are_accepted(self):
        ok, reason, metrics = verify_against_stated_totals(
            _invoice(10, stated_total=ROW_AMOUNT * 10)
        )
        assert ok, reason
        assert metrics["row_count"] == 10

    def test_a_dropped_page_is_caught(self):
        """The failure mode that makes long documents dangerous."""
        ok, reason, _ = verify_against_stated_totals(
            _invoice(90, stated_total=ROW_AMOUNT * 100)
        )
        assert not ok
        assert "off by" in reason

    def test_an_empty_replay_is_rejected(self):
        ok, reason, _ = verify_against_stated_totals(_invoice(0, stated_total=STATED_TOTAL))
        assert not ok
        assert "no rows" in reason

    def test_a_document_stating_no_total_is_not_trusted(self):
        """Unverifiable is not verified — here it decides whether to skip reading."""
        ok, reason, _ = verify_against_stated_totals(_invoice(10, stated_total=None))
        assert not ok
        assert "no total" in reason

    def test_the_tolerance_is_tighter_than_ordinary_reconciliation(self):
        assert settings.sample_authoring_max_variance < settings.reconciliation_variance_threshold

    def test_the_tolerance_is_configurable(self):
        invoice = _invoice(99, stated_total=ROW_AMOUNT * 100)
        assert not verify_against_stated_totals(invoice, max_variance=0.001)[0]
        assert verify_against_stated_totals(invoice, max_variance=0.05)[0]


class TestPlanning:
    def test_the_sample_is_bounded_and_deterministic(self, long_pdf):
        first = plan_sample(long_pdf, max_sample_pages=6)[1]
        second = plan_sample(long_pdf, max_sample_pages=6)[1]

        assert first == second
        assert len(first) <= 6
        assert first == sorted(first)

    def test_the_sample_is_a_small_fraction_of_the_document(self, long_pdf):
        _page_map, sample = plan_sample(long_pdf, max_sample_pages=6)
        assert len(sample) < PAGES + 1

    def test_the_cover_page_is_sampled(self, long_pdf):
        """Header fields live there; missing it means no invoice number."""
        _page_map, sample = plan_sample(long_pdf, max_sample_pages=6)
        assert 1 in sample, sample


class TestReplay:
    def test_a_correct_rule_set_reconciles_across_every_page(self, long_pdf):
        outcome = replay_and_verify(_model(), long_pdf, "t")

        assert outcome.usable, outcome.reason
        assert len(outcome.invoice.rows) == PAGES * ROWS_PER_PAGE

    def test_replay_is_identical_across_runs(self, long_pdf):
        digests = {
            tuple(sorted(str(row["amount"]) for row in replay_and_verify(
                _model(), long_pdf, "t").invoice.rows))
            for _ in range(3)
        }
        assert len(digests) == 1

    def test_a_rule_set_that_reads_the_wrong_column_is_rejected(self, long_pdf):
        model = _model()
        # Point the amount band at empty space: rows lose their amounts.
        model.region.columns[1].x0 = 100
        model.region.columns[1].x1 = 200

        outcome = replay_and_verify(model, long_pdf, "t")
        assert not outcome.usable
        assert outcome.invoice is None

    def test_a_replay_that_produces_nothing_is_rejected_not_raised(self, long_pdf):
        """A broken rule set must return an outcome, never take the pipeline down."""
        model = _model()
        # No row carries this role, so no group is ever emitted.
        model.grouping.item_role = "role_no_row_has"

        outcome = replay_and_verify(model, long_pdf, "t")

        assert outcome.status == "rejected"
        assert outcome.invoice is None
        assert "replay failed" in outcome.reason
