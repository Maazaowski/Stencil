"""Shared pytest configuration."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool


def _assert_testing_this_checkout() -> None:
    """Fail loudly if `import stencil` resolves outside this repo.

    This project has been renamed twice (ExtractForce -> DatWick -> Stencil).
    Stale `pip install -e .` entries for the OLD package names stay in the venv
    and sort ahead of `stencil` on sys.path, so `import stencil` can silently
    resolve to a sibling checkout — the whole suite then green-lights code that
    is not the code under review. Cheap check, expensive bug.
    """
    import stencil

    repo_src = (Path(__file__).resolve().parent.parent / "src").resolve()
    actual = Path(stencil.__file__).resolve().parent.parent
    if actual != repo_src:
        raise RuntimeError(
            f"`import stencil` resolves to {actual}, "
            f"not this checkout's {repo_src}. "
            "A stale editable install is shadowing it. Fix with: "
            "`pip uninstall -y datwick extractforce && pip install -e .`"
        )


# Must run before anything imports stencil modules for real.
_assert_testing_this_checkout()


def _setup_registry_db() -> None:
    """Point the suite's DB session at an in-memory SQLite with all tables created
    and the disk registries seeded, so the DB-backed loaders work without MySQL.

    Runs at import time (before collection) because module-level code such as
    ``extractor.EXTRACTION_JSON_SCHEMA = default_field_schema()`` reads the DB.
    """
    from stencil.db import session as db_session
    from stencil.db.models import Base

    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    # Loaders/seed import SessionLocal lazily, so reassigning the attribute here
    # makes every later access use this SQLite engine.
    db_session.engine = engine
    db_session.SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)

    from stencil.db.seed import seed_registries_from_disk

    seed_registries_from_disk()


_setup_registry_db()


import pytest  # noqa: E402


@pytest.fixture()
def isolated_db(monkeypatch):
    """A clean, empty in-memory profile/registry DB for a single test (no seeded
    profiles), so registry-driven tests can assert exact state."""
    from stencil.db import session as db_session
    from stencil.db.models import Base
    from stencil.profiles import loader as ploader

    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    monkeypatch.setattr(db_session, "engine", engine)
    monkeypatch.setattr(db_session, "SessionLocal", sessionmaker(bind=engine, expire_on_commit=False))
    monkeypatch.setattr(ploader, "_profiles_cache", {})
    yield


@pytest.fixture()
def corpus_profile():
    """Loader for a committed corpus profile snapshot, keyed by layout folder name.

    Tests use this instead of ``get_profile(...)`` because the live
    ``supplier_profiles/`` registry holds only client data (not committed), so the
    registry seeds zero profiles in CI.
    """
    from tests.corpus_utils import load_corpus_profile

    return load_corpus_profile


def pytest_configure(config) -> None:
    """Use a repo-local temp dir on Windows where system temp may be locked."""
    if config.option.basetemp is None:
        basetemp = Path(__file__).resolve().parent.parent / ".pytest_tmp"
        basetemp.mkdir(exist_ok=True)
        config.option.basetemp = str(basetemp)
