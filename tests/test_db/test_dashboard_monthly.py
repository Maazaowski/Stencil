"""Monthly cost aggregations: window scoping, continuous trend, model/supplier grouping."""

from datetime import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from stencil.db import crud
from stencil.db.crud import _recent_month_keys
from stencil.db.models import AICostLog, Base, ExtractionJob, IntakeRecord


@pytest.fixture()
def db() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()


def _cost(session, *, when, call_type, cost, model="gpt-5.5", intake_id=None,
          tokens_in=100, tokens_out=50):
    session.add(AICostLog(
        intake_id=intake_id, call_type=call_type, ai_model_name=model,
        tokens_input=tokens_in, tokens_output=tokens_out,
        estimated_cost_usd=cost, created_at=when,
    ))


def _intake(session, *, intake_id, when, source=None, supplier=None, account=None, status="completed"):
    session.add(IntakeRecord(
        id=intake_id, original_filename="x.pdf", original_pdf_path="/x.pdf",
        status=status, intake_source=source, supplier_profile_id=supplier,
        account_label=account, created_at=when,
    ))


def _job(session, *, when, path):
    session.add(ExtractionJob(intake_id="i", extraction_path=path, created_at=when))


def test_monthly_stats_scopes_to_window_and_splits_by_purpose(db):
    jun = datetime(2026, 6, 15, 10)
    jul = datetime(2026, 7, 10, 9)

    # June: 1.00 extraction (normal intake), 0.50 classification, 2.00 model authoring.
    _intake(db, intake_id="inv-jun", when=jun, supplier="mindtree", account="MindTree")
    _cost(db, when=jun, call_type="extraction", cost=1.00, intake_id="inv-jun")
    _cost(db, when=jun, call_type="classification", cost=0.50, intake_id="inv-jun")
    _cost(db, when=jun, call_type="model_authoring", cost=2.00, intake_id="authoring-abc")

    # July: 3.00 + 1.00 extraction, 0.25 classification. One training-sample extraction (0.10).
    _intake(db, intake_id="inv-jul", when=jul, supplier="att", account="ATT VPN")
    _intake(db, intake_id="train-jul", when=jul, source="training")
    _cost(db, when=jul, call_type="extraction", cost=3.00, model="gpt-5.5", intake_id="inv-jul")
    _cost(db, when=jul, call_type="extraction", cost=1.00, model="gpt-5.6-sol", intake_id="inv-jul")
    _cost(db, when=jul, call_type="classification", cost=0.25, intake_id="inv-jul")
    _cost(db, when=jul, call_type="extraction", cost=0.10, intake_id="train-jul")  # training sample
    _job(db, when=jul, path="ai")
    _job(db, when=jul, path="model")
    _job(db, when=jul, path="model")
    db.commit()

    jul_stats = crud.get_monthly_stats(db, "2026-07")
    assert jul_stats["extraction_cost"] == pytest.approx(4.00)
    assert jul_stats["classification_cost"] == pytest.approx(0.25)
    assert jul_stats["training_cost"] == pytest.approx(0.10)  # training-sample extraction
    assert jul_stats["total_cost"] == pytest.approx(4.35)
    assert jul_stats["ai_path_count"] == 1
    assert jul_stats["model_path_count"] == 2
    assert jul_stats["invoices_processed"] == 1  # inv-jul (training intake excluded)

    jun_stats = crud.get_monthly_stats(db, "2026-06")
    assert jun_stats["extraction_cost"] == pytest.approx(1.00)
    assert jun_stats["classification_cost"] == pytest.approx(0.50)
    assert jun_stats["training_cost"] == pytest.approx(2.00)  # model authoring
    assert jun_stats["total_cost"] == pytest.approx(3.50)
    assert jun_stats["ai_path_count"] == 0


def test_recent_month_keys_are_continuous_and_wrap_years():
    keys = _recent_month_keys(12, now=datetime(2026, 2, 5))
    assert len(keys) == 12
    assert keys[-1] == "2026-02"
    assert keys[0] == "2025-03"  # 11 months earlier, across the year boundary
    # strictly continuous, oldest first
    assert keys == sorted(keys)


def test_cost_by_month_is_continuous_with_zero_fill(db):
    now = datetime.now()
    _cost(db, when=now, call_type="extraction", cost=5.00, intake_id="a")
    db.commit()

    series = crud.get_cost_by_month(db, months=12)
    assert len(series) == 12
    assert [p["month"] for p in series] == _recent_month_keys(12, now=now)
    assert series[-1]["month"] == now.strftime("%Y-%m")
    assert series[-1]["cost"] == pytest.approx(5.00)
    # every other month is zero-filled, so the whole series sums to just our one row
    assert sum(p["cost"] for p in series) == pytest.approx(5.00)


def test_cost_by_model_grouped_and_sorted_desc(db):
    jul = datetime(2026, 7, 10)
    _cost(db, when=jul, call_type="extraction", cost=1.00, model="gpt-5.6-sol", intake_id="a")
    _cost(db, when=jul, call_type="extraction", cost=3.00, model="gpt-5.5", intake_id="b")
    _cost(db, when=jul, call_type="extraction", cost=0.50, model="gpt-5.5", intake_id="c")
    _cost(db, when=datetime(2026, 6, 1), call_type="extraction", cost=9.00, model="gpt-5.5", intake_id="d")
    db.commit()

    rows = crud.get_cost_by_model(db, "2026-07")
    assert [r["model"] for r in rows] == ["gpt-5.5", "gpt-5.6-sol"]  # sorted by cost desc
    assert rows[0]["cost"] == pytest.approx(3.50)  # 3.00 + 0.50, June row excluded
    assert rows[0]["calls"] == 2


def test_cost_by_supplier_includes_unattributed_authoring_rows(db):
    jul = datetime(2026, 7, 12)
    _intake(db, intake_id="inv-jul", when=jul, supplier="mindtree", account="MindTree")
    _cost(db, when=jul, call_type="extraction", cost=4.00, intake_id="inv-jul")
    _cost(db, when=jul, call_type="model_authoring", cost=1.50, intake_id="authoring-xyz")  # no join
    db.commit()

    rows = crud.get_cost_by_supplier(db, "2026-07")
    assert rows[0]["supplier_profile_id"] == "mindtree"
    assert rows[0]["cost"] == pytest.approx(4.00)
    # authoring row has no joinable intake → null supplier, surfaced (not dropped)
    unattributed = [r for r in rows if r["supplier_profile_id"] is None]
    assert len(unattributed) == 1
    assert unattributed[0]["cost"] == pytest.approx(1.50)
