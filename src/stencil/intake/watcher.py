"""File watcher for supplier-profile inbound directories.

ACTIVE SupplierProfile JSON files are the watched registry: only profiles with
status == "active" (approved model in production) get their inbound directory
watched. Draft/training/retired profiles are never watched — training invoices
arrive exclusively through the explicit training upload API.

When a PDF lands in a profile directory the supplier is already known, so the
pipeline skips classification and routes by profile + layout fingerprint.

A small global fallback directory is still watched as a catch-all for manual
uploads that do not belong to a configured profile directory.
"""

import time
from pathlib import Path

import structlog
from watchdog.events import FileCreatedEvent, FileSystemEventHandler
from watchdog.observers.polling import PollingObserver

from stencil import runtime_settings
from stencil.config import settings
from stencil.profiles.loader import (
    get_active_profiles,
    load_all_profiles,
    resolve_profile_path,
)
from stencil.profiles.schema import SupplierProfile

logger = structlog.get_logger()


def resolve_profile_watch_dir(profile: SupplierProfile) -> Path | None:
    """Resolve the directory to watch for a profile's inbound PDFs."""
    resolved = resolve_profile_path(profile, "inbound_path")
    if resolved is None:
        return None
    if "{" in str(resolved):
        logger.warning("watcher.profile_path_unresolved", profile_id=profile.profile_id, path=str(resolved))
        return None
    return resolved


class PDFHandler(FileSystemEventHandler):
    """Detects new PDF files and calls back when they're stable."""

    def __init__(self, on_pdf_ready, profile: SupplierProfile | None = None, account=None):
        self._on_pdf_ready = on_pdf_ready
        self._profile = profile
        self._account = account
        self._pending: dict[str, float] = {}

    def on_created(self, event: FileCreatedEvent):
        if event.is_directory:
            return
        path = Path(event.src_path)
        if path.suffix.lower() != ".pdf":
            return
        self._pending[str(path)] = time.time()
        profile_id = self._profile.profile_id if self._profile else "unknown"
        logger.info("watcher.detected", path=str(path), profile_id=profile_id)

    def check_stable_files(self):
        """Check if any pending files have stabilized after being written."""
        now = time.time()
        ready = []
        for path_str, detected_at in list(self._pending.items()):
            path = Path(path_str)
            if not path.exists():
                del self._pending[path_str]
                continue

            if now - detected_at < float(runtime_settings.runtime_value("watcher_stable_seconds")):
                continue

            try:
                current_size = path.stat().st_size
                if current_size == 0:
                    continue
                time.sleep(0.5)
                if path.stat().st_size != current_size:
                    self._pending[path_str] = now
                    continue
            except OSError:
                del self._pending[path_str]
                continue

            ready.append(path)
            del self._pending[path_str]

        for path in ready:
            logger.info("watcher.stable", path=str(path))
            self._on_pdf_ready(path, self._profile, self._account)


def _resolve_dir(raw: str | None) -> Path | None:
    """Resolve a configured literal directory path; skip unresolved templates."""
    if not raw:
        return None
    if "{" in str(raw):
        logger.warning("watcher.path_unresolved", path=str(raw))
        return None
    return Path(raw).expanduser()


def _sync_profile_watches(observer, on_pdf_ready, scheduled: dict, handlers: list,
                          global_dir_str: str) -> tuple[int, int]:
    """Reconcile watched directories with every active profile's account folders.

    One profile can serve many billing accounts, so we watch each account's
    inbound folder and carry the account through to output routing."""
    load_all_profiles()

    desired: dict[str, tuple[SupplierProfile, object]] = {}
    for profile in get_active_profiles():
        for account in profile.effective_accounts:
            inbound = _resolve_dir(account.inbound_path)
            if not inbound:
                logger.warning("watcher.account_missing_inbound",
                               profile_id=profile.profile_id, account=account.label)
                continue
            if not account.output_path:
                logger.warning("watcher.account_missing_output",
                               profile_id=profile.profile_id, account=account.label)
                continue
            try:
                inbound.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                logger.warning("watcher.mkdir_failed", path=str(inbound), error=str(exc))
                continue
            key = str(inbound.resolve())
            existing = desired.get(key)
            if existing is not None:
                # Two active profiles claim the same folder. Save-time validation
                # should prevent this; if it slips through (e.g. a direct DB edit),
                # keep the first deterministically instead of silently dropping one.
                first_profile, _ = existing
                logger.warning(
                    "watcher.account_conflict",
                    path=key,
                    kept_profile_id=first_profile.profile_id,
                    ignored_profile_id=profile.profile_id,
                    account=account.label,
                )
                continue
            desired[key] = (profile, account)

    added = removed = 0

    for dir_str, (profile, account) in desired.items():
        if dir_str == global_dir_str or dir_str in scheduled:
            continue
        handler = PDFHandler(on_pdf_ready, profile=profile, account=account)
        watch = observer.schedule(handler, dir_str, recursive=False)
        scheduled[dir_str] = (handler, watch)
        handlers.append(handler)
        added += 1
        logger.info(
            "watcher.watching",
            path=dir_str,
            recursive=False,
            profile_id=profile.profile_id,
            supplier=profile.identity.canonical_name,
            account=account.label,
        )

    for dir_str in list(scheduled.keys()):
        if dir_str in desired:
            continue
        handler, watch = scheduled.pop(dir_str)
        try:
            observer.unschedule(watch)
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("watcher.unschedule_failed", path=dir_str, error=str(exc))
        if handler in handlers:
            handlers.remove(handler)
        removed += 1
        logger.info("watcher.unwatched", path=dir_str)

    return added, removed


def start_profile_watcher(blocking: bool = True):
    """Watch each ACTIVE supplier profile's inbound directory + global fallback."""
    from stencil.tasks.worker import (
        process_invoice_for_profile_task,
        process_invoice_task,
    )

    def on_pdf_ready(path: Path, profile: SupplierProfile | None, account=None):
        if profile:
            label = account.label if account else None
            logger.info(
                "watcher.queuing",
                path=str(path),
                profile_id=profile.profile_id,
                supplier=profile.identity.canonical_name,
                account=label,
            )
            process_invoice_for_profile_task.delay(str(path), profile.profile_id, label)
        else:
            logger.info("watcher.queuing", path=str(path), profile_id="unknown")
            process_invoice_task.delay(str(path))

    observer = PollingObserver(timeout=float(runtime_settings.runtime_value("watcher_poll_interval")))
    handlers: list[PDFHandler] = []
    scheduled: dict[str, tuple] = {}

    global_inbound = settings.inbound_dir
    global_inbound.mkdir(parents=True, exist_ok=True)
    global_dir_str = str(global_inbound.resolve())
    global_handler = PDFHandler(on_pdf_ready, profile=None)
    observer.schedule(global_handler, global_dir_str, recursive=False)
    handlers.append(global_handler)
    logger.info("watcher.watching", path=global_dir_str, profile_id="global_fallback")

    # Profiles live in MySQL now, so we poll the DB on an interval instead of
    # watching a filesystem registry directory.
    _sync_profile_watches(observer, on_pdf_ready, scheduled, handlers, global_dir_str)

    observer.start()
    logger.info("watcher.profile_started", profile_dirs=len(scheduled), total_dirs=len(handlers))

    if blocking:
        rescan = settings.watcher_registry_rescan_seconds or 15.0
        last_rescan = time.monotonic()
        try:
            while True:
                for handler in list(handlers):
                    handler.check_stable_files()

                if (time.monotonic() - last_rescan) >= rescan:
                    added, removed = _sync_profile_watches(
                        observer, on_pdf_ready, scheduled, handlers, global_dir_str
                    )
                    if added or removed:
                        logger.info(
                            "watcher.profile_registry_resynced",
                            added=added,
                            removed=removed,
                            profile_dirs=len(scheduled),
                        )
                    last_rescan = time.monotonic()
                time.sleep(float(runtime_settings.runtime_value("watcher_poll_interval")))
        except KeyboardInterrupt:
            observer.stop()
        observer.join()
    else:
        return observer, handlers


def start_profile_watcher_sync(blocking: bool = True):
    """Profile watcher that processes PDFs synchronously in dev mode."""
    from stencil.db.session import SessionLocal
    from stencil.intake.service import process_new_pdf
    from stencil.pipeline.events import publish_event
    from stencil.pipeline.processor import continue_pipeline

    def on_pdf_ready(path: Path, profile: SupplierProfile | None, account=None):
        profile_id = profile.profile_id if profile else None
        account_label = account.label if account else None
        print(f"--- New PDF: {path.name} (profile: {profile_id or 'unknown'}) ---")
        db = SessionLocal()
        try:
            intake_id = process_new_pdf(
                db, path, intake_source="watcher", supplier_profile_id=profile_id,
                account_label=account_label,
            )
            publish_event(intake_id, "intake", "completed", "PDF registered and archived")
            continue_pipeline(db, intake_id, profile_id=profile_id)
            print(f"  Completed: {intake_id}")
            print(f"  Output: {settings.completed_dir / intake_id}")
        except Exception as exc:
            print(f"  Failed: {exc}")
            import traceback
            traceback.print_exc()
        finally:
            db.close()
        print()

    observer = PollingObserver(timeout=float(runtime_settings.runtime_value("watcher_poll_interval")))
    handlers: list[PDFHandler] = []
    scheduled: dict[str, tuple] = {}

    global_inbound = settings.inbound_dir
    global_inbound.mkdir(parents=True, exist_ok=True)
    global_dir_str = str(global_inbound.resolve())
    global_handler = PDFHandler(on_pdf_ready, profile=None)
    observer.schedule(global_handler, global_dir_str, recursive=False)
    handlers.append(global_handler)

    _sync_profile_watches(observer, on_pdf_ready, scheduled, handlers, global_dir_str)

    observer.start()
    logger.info("watcher.sync_started", dirs=len(handlers))

    if blocking:
        rescan = settings.watcher_registry_rescan_seconds or 15.0
        last_rescan = time.monotonic()
        try:
            while True:
                for handler in list(handlers):
                    handler.check_stable_files()
                if (time.monotonic() - last_rescan) >= rescan:
                    _sync_profile_watches(observer, on_pdf_ready, scheduled, handlers, global_dir_str)
                    last_rescan = time.monotonic()
                time.sleep(float(runtime_settings.runtime_value("watcher_poll_interval")))
        except KeyboardInterrupt:
            observer.stop()
        observer.join()
    else:
        return observer, handlers


if __name__ == "__main__":
    print("Stencil Profile Watcher")
    print(f"  Global inbound: {settings.inbound_dir}")
    print(f"  Profiles dir:   {settings.supplier_profiles_dir}")
    settings.ensure_directories()
    start_profile_watcher(blocking=True)
