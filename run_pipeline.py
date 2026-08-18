"""Run the Stencil pipeline end-to-end without Celery.

Usage:
    python run_pipeline.py                     # Start file watcher (drop PDFs into stencil_data/inbound/)
    python run_pipeline.py path/to/invoice.pdf # Process a single PDF directly
"""

import sys
from pathlib import Path

# Ensure src is on the path
sys.path.insert(0, str(Path(__file__).parent / "src"))

from stencil.config import settings
from stencil.db.session import SessionLocal
from stencil.pipeline.processor import process_invoice


def process_single(pdf_path: Path):
    """Process a single PDF through the full pipeline."""
    if not pdf_path.exists():
        print(f"ERROR: File not found: {pdf_path}")
        sys.exit(1)
    if not pdf_path.suffix.lower() == ".pdf":
        print(f"ERROR: Not a PDF file: {pdf_path}")
        sys.exit(1)

    print(f"Processing: {pdf_path.name}")
    print(f"  Data dir: {settings.data_dir}")
    print(f"  Database: {settings.database_url.split('@')[-1] if '@' in settings.database_url else 'configured'}")
    print()

    db = SessionLocal()
    try:
        intake_id = process_invoice(db, pdf_path)
        print()
        print("  SUCCESS!")
        print(f"  Intake ID: {intake_id}")
        print(f"  Output:    {settings.completed_dir / intake_id}")
        print("  Files:")
        output_dir = settings.completed_dir / intake_id
        if output_dir.exists():
            for f in sorted(output_dir.iterdir()):
                print(f"    - {f.name} ({f.stat().st_size:,} bytes)")
    except Exception as e:
        print(f"  FAILED: {e}")
        import traceback
        traceback.print_exc()
    finally:
        db.close()


def start_watcher():
    """Start the supplier-profile file watcher (active profiles + global fallback).

    In dev mode (no Celery), processes PDFs synchronously in the watcher thread.
    """
    from stencil.intake.watcher import resolve_profile_watch_dir, start_profile_watcher_sync
    from stencil.profiles.loader import get_active_profiles

    settings.ensure_directories()

    profiles = get_active_profiles()
    watched = []
    for p in profiles:
        inbound = resolve_profile_watch_dir(p)
        if inbound:
            watched.append(f"  {p.profile_id}: {inbound}")

    print("Stencil Profile Watcher (dev mode, sync)")
    print(f"  Global inbound: {settings.inbound_dir.resolve()}")
    if watched:
        print(f"  Active profile directories ({len(watched)}):")
        for w in watched:
            print(w)
    print(f"  Output: {settings.completed_dir.resolve()}")
    print(f"  Database: {settings.database_url.split('@')[-1] if '@' in settings.database_url else 'configured'}")
    print()
    print("Drop PDF files into any watched folder to process them.")
    print("Press Ctrl+C to stop.")
    print()

    start_profile_watcher_sync(blocking=True)


if __name__ == "__main__":
    if len(sys.argv) > 1:
        process_single(Path(sys.argv[1]))
    else:
        start_watcher()
