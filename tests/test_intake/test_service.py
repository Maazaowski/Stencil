from stencil.config import settings
from stencil.db import session as db_session
from stencil.intake.service import process_new_pdf


def test_supplier_watcher_intake_preserves_original_pdf(isolated_db, tmp_path, monkeypatch):
    for attr in ("archive_dir", "processing_dir"):
        directory = tmp_path / attr
        directory.mkdir()
        monkeypatch.setattr(settings, attr, directory)

    source_dir = tmp_path / "Supplier" / "pdf"
    source_dir.mkdir(parents=True)
    source_pdf = source_dir / "U0143096.pdf"
    source_pdf.write_bytes(b"%PDF-1.4\n")

    db = db_session.SessionLocal()
    try:
        intake_id = process_new_pdf(
            db,
            source_pdf,
            intake_source="watcher",
            supplier_profile_id="supplier.standard.v1",
        )
    finally:
        db.close()

    assert source_pdf.exists()
    assert (settings.archive_dir / intake_id / "original.pdf").read_bytes() == b"%PDF-1.4\n"
    assert (settings.processing_dir / intake_id / "original.pdf").read_bytes() == b"%PDF-1.4\n"


def test_generic_intake_still_moves_source_pdf(isolated_db, tmp_path, monkeypatch):
    for attr in ("archive_dir", "processing_dir"):
        directory = tmp_path / attr
        directory.mkdir()
        monkeypatch.setattr(settings, attr, directory)

    source_pdf = tmp_path / "global-inbound.pdf"
    source_pdf.write_bytes(b"%PDF-1.4\n")

    db = db_session.SessionLocal()
    try:
        intake_id = process_new_pdf(db, source_pdf, intake_source="watcher")
    finally:
        db.close()

    assert not source_pdf.exists()
    assert (settings.archive_dir / intake_id / "original.pdf").exists()
    assert (settings.processing_dir / intake_id / "original.pdf").exists()
