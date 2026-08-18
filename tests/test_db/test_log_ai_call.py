"""log_ai_call is non-critical bookkeeping: a DB failure must not poison the
caller's transaction or propagate (regression for the authoring-<uuid> overflow
that aborted an otherwise-successful extraction)."""

from sqlalchemy.exc import SQLAlchemyError

from stencil.db import crud
from stencil.db.session import SessionLocal


def test_log_ai_call_swallows_db_error_and_keeps_session_usable(isolated_db):
    db = SessionLocal()
    try:
        original_commit = db.commit
        state = {"calls": 0}

        def flaky_commit():
            state["calls"] += 1
            if state["calls"] == 1:
                raise SQLAlchemyError("simulated data-too-long")
            return original_commit()

        db.commit = flaky_commit  # type: ignore[method-assign]

        # First call fails inside commit: must return None, not raise.
        result = crud.log_ai_call(
            db,
            intake_id="authoring-" + "x" * 36,  # 46 chars, the original culprit shape
            call_type="profile_authoring_extract",
            ai_model_name="gpt-5.5",
            tokens_input=1,
            tokens_output=1,
        )
        assert result is None

        # Session was rolled back and is usable again: a valid log persists.
        ok = crud.log_ai_call(
            db,
            intake_id="real-intake",
            call_type="extraction",
            ai_model_name="gpt-5.5",
        )
        assert ok is not None
        assert ok.id is not None
    finally:
        db.close()
