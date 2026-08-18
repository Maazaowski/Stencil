"""Deterministic grouped line-item strategy + worker leak fixes."""


import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from stencil.db import crud
from stencil.db.models import Base
from stencil.extraction.normalization import build_grouped_line_items, has_grouping_policy
from stencil.models.schema import ExtractionModel, LineItemStrategy
from stencil.profiles.schema import LineItemHints


@pytest.fixture()
def db() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()


# ── grouping policy detection ───────────────────────────────────────────


def test_has_grouping_policy():
    assert has_grouping_policy(LineItemHints(
        line_item_granularity="per_charge_row", service_id_preference="first_identifier")) is True
    assert has_grouping_policy(LineItemHints(
        line_item_granularity="per_total_group", service_id_preference="total_row_identifier")) is True
    # No granularity / no id preference => not a declared policy.
    assert has_grouping_policy(LineItemHints()) is False
    assert has_grouping_policy(LineItemHints(line_item_granularity="per_charge_row")) is False
    assert has_grouping_policy(None) is False


def test_build_grouped_line_items_returns_empty_without_policy():
    # Incomplete policy => no deterministic items (AI authoring handles it).
    items = build_grouped_line_items(
        document=_empty_doc(), granularity="", service_id_preference="",
    )
    assert items == []


def _empty_doc():
    from stencil.extraction.layout import LayoutDocument
    return LayoutDocument(pages=[])


# ── strategy round-trips on the model ───────────────────────────────────


def test_model_carries_line_item_strategy():
    model = ExtractionModel(
        model_id="colt.standard.v1", supplier_profile_id="colt.standard.v1", supplier="Colt",
        line_item_strategy=LineItemStrategy(
            granularity="per_charge_row", service_id_preference="first_identifier",
            billing_reference_preference="same_as_service_id",
            anchors=["SERVICE LEVEL ACTIVITY"], skip_keywords=["Total Amount Due"],
        ),
    )
    dumped = model.model_dump(mode="json")
    assert dumped["line_item_strategy"]["granularity"] == "per_charge_row"
    restored = ExtractionModel.model_validate(dumped)
    assert restored.line_item_strategy.service_id_preference == "first_identifier"


# ── authoring synthesizes a strategy for declared-policy profiles ────────


def test_authoring_attaches_strategy_for_grouped_profile():
    from stencil.models.authoring import _apply_grouping_strategy
    from stencil.profiles.schema import (
        AdvancedHints,
        ClassificationSignals,
        DocumentStructure,
        SupplierIdentity,
        SupplierProfile,
    )

    profile = SupplierProfile(
        profile_id="colt.standard.v1",
        identity=SupplierIdentity(canonical_name="Colt"),
        classification=ClassificationSignals(output_type="standard"),
        advanced=AdvancedHints(
            document_structure=DocumentStructure(detail_start_marker="Service Level Activity"),
            line_item_hints=LineItemHints(
                line_item_granularity="per_charge_row",
                service_id_preference="first_identifier",
                billing_reference_preference="same_as_service_id",
                detail_table_anchors=["SERVICE LEVEL ACTIVITY"],
                skip_row_keywords=["Total Amount Due"],
            ),
        ),
    )
    model = ExtractionModel(model_id="colt.standard.v1", supplier_profile_id="colt.standard.v1",
                            supplier="Colt")
    _apply_grouping_strategy(model, profile)
    assert model.line_item_strategy is not None
    assert model.line_item_strategy.granularity == "per_charge_row"
    assert "SERVICE LEVEL ACTIVITY" in model.line_item_strategy.anchors
    assert "Service Level Activity" in model.line_item_strategy.anchors


def test_authoring_skips_strategy_without_policy():
    from stencil.models.authoring import _apply_grouping_strategy
    from stencil.profiles.schema import (
        ClassificationSignals,
        SupplierIdentity,
        SupplierProfile,
    )

    profile = SupplierProfile(
        profile_id="x.standard.v1",
        identity=SupplierIdentity(canonical_name="X"),
        classification=ClassificationSignals(output_type="standard"),
        line_item_hints=LineItemHints(),
    )
    model = ExtractionModel(model_id="x.standard.v1", supplier_profile_id="x.standard.v1", supplier="X")
    _apply_grouping_strategy(model, profile)
    assert model.line_item_strategy is None


# ── leak fixes ──────────────────────────────────────────────────────────


def test_fail_stale_training_runs(db):
    run = crud.create_training_run(db, model_id="m", supplier_profile_id="m", total_steps=3)
    assert run.state == "running"
    swept = crud.fail_stale_training_runs(db)
    assert swept == 1
    again = crud.get_latest_training_run(db, "m")
    assert again.state == "failed"
    assert again.finished_at is not None
    # Idempotent: nothing left to sweep.
    assert crud.fail_stale_training_runs(db) == 0
