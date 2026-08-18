"""Dashboard counts treat 'completed_with_warnings' as delivered."""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from stencil.db import crud
from stencil.db.models import Base, IntakeRecord


@pytest.fixture()
def db() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()


def _intake(session, status):
    rec = IntakeRecord(original_filename="x.pdf", original_pdf_path="/x.pdf", status=status)
    session.add(rec)
    session.commit()


def test_completed_with_warnings_counts_as_completed(db):
    _intake(db, "completed")
    _intake(db, "completed_with_warnings")
    _intake(db, "completed_with_warnings")
    _intake(db, "failed")

    stats = crud.get_dashboard_stats(db)

    # both clean and warning completions count as completed/delivered
    assert stats["completed_invoices"] == 3
    assert stats["completed_with_warnings"] == 2
    assert stats["failed_invoices"] == 1
    assert stats["total_invoices"] == 4
