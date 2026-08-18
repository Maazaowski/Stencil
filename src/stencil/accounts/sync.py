"""Account discovery: scan the invoices tree in the background, persist a
snapshot, and reconcile it against profile mappings.

Walking ``/data/Invoices`` inside a request hangs prod — it's a Windows→Linux
bind mount where every dir op crosses the host boundary, and there are ~2,600
accounts. So discovery runs as a Celery task (``accounts_sync_task``) that writes
``work_dir/accounts/snapshot.json``; the API reads that snapshot (instant) and
computes each account's state against the *live* profiles. Mirrors the evals
``report.py`` file-status pattern, and survives container rebuilds because the
snapshot lives on the persistent ``/work`` volume.

The snapshot stores **disk discovery only**; the mapped/unmapped/conflict/missing
state is recomputed on read, so assigning or unassigning a profile reflects
immediately without a re-scan.
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import structlog
from pydantic import BaseModel, Field

from stencil.config import settings
from stencil.profiles.loader import (
    account_owner_index,
    load_all_profiles,
    normalize_account_path,
)

logger = structlog.get_logger()

# Only descend this many levels below the scan base looking for `pdf` folders,
# so a mis-pointed base can't trigger a walk of an enormous unrelated tree.
_MAX_SCAN_DEPTH = 4

# A running sync whose status file hasn't been touched in this long is assumed
# dead (worker restarted mid-scan) and may be superseded by a new one.
_STALE_SYNC_SECONDS = 180.0


# --- response models (kept here, FastAPI-free, so the worker can import them) ---


class AccountRow(BaseModel):
    customer: str
    account: str
    inbound_path: str
    output_path: str
    pdf_count: int
    state: str  # unmapped | mapped | conflict | missing
    profile_ids: list[str] = Field(default_factory=list)


class AccountGroup(BaseModel):
    customer: str
    accounts: list[AccountRow]
    total: int
    unmapped: int
    conflict: int
    missing: int


class AccountsResponse(BaseModel):
    scan_base: str
    groups: list[AccountGroup]
    total_accounts: int
    unmapped: int
    conflict: int
    missing: int
    synced_at: str | None = None        # when the snapshot was last written
    syncing: bool = False               # a sync is queued/running right now
    never_synced: bool = False          # no snapshot exists yet (fresh deploy)


# --- paths ----------------------------------------------------------------


def scan_base() -> Path:
    base = settings.accounts_scan_dir or (settings.data_dir / "Invoices")
    return Path(base).expanduser()


def _accounts_dir() -> Path:
    return settings.work_dir / "accounts"


def snapshot_path() -> Path:
    return _accounts_dir() / "snapshot.json"


def sync_status_path() -> Path:
    return _accounts_dir() / "sync_status.json"


# --- disk walk (os.scandir: one read per dir, no per-entry stat) ----------


def _iter_subdirs(directory: str):
    """Yield the immediate subdirectory DirEntries of ``directory``, cheaply."""
    try:
        with os.scandir(directory) as it:
            for entry in it:
                try:
                    if entry.is_dir(follow_symlinks=False):
                        yield entry
                except OSError:
                    continue
    except OSError:
        return


def _collect_pdf_dirs(directory: str, depth: int, out: list[str]) -> None:
    """Append every ``pdf`` directory under ``directory`` (bounded by depth)."""
    if depth > _MAX_SCAN_DEPTH:
        return
    for entry in _iter_subdirs(directory):
        if entry.name.lower() == "pdf":
            out.append(entry.path)
            continue  # never recurse into a pdf folder
        _collect_pdf_dirs(entry.path, depth + 1, out)


def _pdf_count(pdf_dir: str) -> int:
    """Entry count of a pdf folder in one read — no per-file ``stat`` (paying
    ~36k cross-mount stats just to show a count is what hung prod)."""
    try:
        return len(os.listdir(pdf_dir))
    except OSError:
        return 0


def _folder_entry(pdf_dir: str) -> dict:
    account_dir = Path(pdf_dir).parent
    return {
        "customer": account_dir.parent.name or "(root)",
        "account": account_dir.name,
        "inbound_path": str(Path(pdf_dir)),
        "output_path": str(account_dir / "xls"),
        "pdf_count": _pdf_count(pdf_dir),
    }


# --- snapshot + status i/o ------------------------------------------------


def _write_json_atomic(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, default=str), encoding="utf-8")
    os.replace(tmp, path)  # atomic swap: readers never see a partial snapshot


_memo: dict = {"mtime": None, "value": None}


def read_snapshot() -> dict | None:
    """Parsed snapshot, memoized by file mtime so we don't re-parse every request."""
    path = snapshot_path()
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return None
    if _memo["mtime"] == mtime and _memo["value"] is not None:
        return _memo["value"]
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    _memo["mtime"] = mtime
    _memo["value"] = value
    return value


def write_sync_status(fields: dict) -> None:
    path = sync_status_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    existing: dict = {}
    if path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            existing = {}
    existing.update(fields)
    path.write_text(json.dumps(existing, default=str), encoding="utf-8")


def read_sync_status() -> dict | None:
    path = sync_status_path()
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def sync_in_progress() -> bool:
    """True if a sync is queued/running and its status file is being kept fresh.
    A stale 'running' status (dead worker) reads as not-in-progress so a new sync
    can supersede it."""
    status = read_sync_status()
    if not status or status.get("status") not in ("queued", "running"):
        return False
    try:
        age = time.time() - sync_status_path().stat().st_mtime
    except OSError:
        return True
    return age < _STALE_SYNC_SECONDS


# --- scan -----------------------------------------------------------------


def scan_to_snapshot(progress_cb: Callable[[int, int, int], None] | None = None) -> dict:
    """Walk the scan base and write a fresh snapshot atomically.

    ``progress_cb(customers_done, customers_total, accounts_found)`` fires once up
    front (so the bar is determinate immediately) and after each customer.
    """
    base = scan_base()
    customers = list(_iter_subdirs(str(base)))
    total = len(customers)
    folders: list[dict] = []
    if progress_cb:
        progress_cb(0, total, 0)
    for done, customer in enumerate(customers, start=1):
        pdf_dirs: list[str] = []
        _collect_pdf_dirs(customer.path, depth=2, out=pdf_dirs)  # customer is depth 1
        folders.extend(_folder_entry(p) for p in pdf_dirs)
        if progress_cb:
            progress_cb(done, total, len(folders))
    snapshot = {
        "scanned_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "scan_base": str(base),
        "customers": total,
        "folders": folders,
    }
    _write_json_atomic(snapshot_path(), snapshot)
    _memo["mtime"] = None  # force re-read on next reconcile
    return snapshot


def run_sync() -> dict:
    """Full scan → snapshot, streaming progress to the status file. Called by the
    Celery task (and the dev sync path). Records status=running → done/error."""
    started = time.monotonic()
    write_sync_status({
        "status": "running",
        "customers_done": 0,
        "customers_total": None,
        "accounts_found": 0,
        "started_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "error": None,
    })

    def cb(done: int, total: int, found: int) -> None:
        write_sync_status({
            "status": "running",
            "customers_done": done,
            "customers_total": total,
            "accounts_found": found,
        })

    try:
        snapshot = scan_to_snapshot(progress_cb=cb)
    except Exception as exc:
        write_sync_status({"status": "error", "error": str(exc)})
        raise
    write_sync_status({
        "status": "done",
        "customers_done": snapshot["customers"],
        "customers_total": snapshot["customers"],
        "accounts_found": len(snapshot["folders"]),
        "scanned_at": snapshot["scanned_at"],
        "finished_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "duration_seconds": round(time.monotonic() - started, 1),
        "error": None,
    })
    logger.info(
        "accounts.sync_done",
        accounts=len(snapshot["folders"]),
        customers=snapshot["customers"],
        seconds=round(time.monotonic() - started, 1),
    )
    return snapshot


def upsert_folder(pdf_dir: str) -> None:
    """Add one pdf dir to the snapshot immediately (e.g. after create-account), so
    it appears without waiting for a full re-scan. No-op if there's no snapshot."""
    snapshot = read_snapshot()
    if snapshot is None:
        return
    key = normalize_account_path(pdf_dir)
    folders = snapshot.get("folders", [])
    if any(normalize_account_path(f["inbound_path"]) == key for f in folders):
        return
    folders.append(_folder_entry(pdf_dir))
    snapshot["folders"] = folders
    _write_json_atomic(snapshot_path(), snapshot)
    _memo["mtime"] = None


# --- reconcile snapshot vs profiles --------------------------------------


def reconcile() -> AccountsResponse:
    """Build the grouped accounts view from the persisted snapshot + live profile
    mappings — no disk walk. Instant, and always returns."""
    base = scan_base()
    snapshot = read_snapshot()
    status = read_sync_status() or {}
    syncing = status.get("status") in ("queued", "running")

    if snapshot is None:
        return AccountsResponse(
            scan_base=str(base), groups=[], total_accounts=0,
            unmapped=0, conflict=0, missing=0,
            synced_at=None, syncing=syncing, never_synced=True,
        )

    owners = account_owner_index()  # normalized inbound path -> [profile_id, ...]
    groups: dict[str, list[AccountRow]] = {}
    seen_keys: set[str] = set()

    for folder in snapshot.get("folders", []):
        key = normalize_account_path(folder["inbound_path"])
        claimants = owners.get(key or "", [])
        if key:
            seen_keys.add(key)
        state = "unmapped" if not claimants else ("mapped" if len(claimants) == 1 else "conflict")
        groups.setdefault(folder["customer"], []).append(
            AccountRow(
                customer=folder["customer"],
                account=folder["account"],
                inbound_path=folder["inbound_path"],
                output_path=folder["output_path"],
                pdf_count=folder.get("pdf_count", 0),
                state=state,
                profile_ids=sorted(claimants),
            )
        )

    # Accounts a profile maps but whose folder is absent from the snapshot -> "missing".
    for profile in load_all_profiles().values():
        for account in profile.effective_accounts:
            key = normalize_account_path(account.inbound_path)
            if not key or key in seen_keys:
                continue
            seen_keys.add(key)
            inbound = Path(account.inbound_path)
            customer = inbound.parent.parent.name or "(unknown)"
            groups.setdefault(customer, []).append(
                AccountRow(
                    customer=customer,
                    account=inbound.parent.name or account.label,
                    inbound_path=account.inbound_path,
                    output_path=account.output_path or "",
                    pdf_count=0,
                    state="missing",
                    profile_ids=[profile.profile_id],
                )
            )

    group_models: list[AccountGroup] = []
    for customer in sorted(groups):
        rows = sorted(groups[customer], key=lambda r: r.account.lower())
        group_models.append(
            AccountGroup(
                customer=customer,
                accounts=rows,
                total=len(rows),
                unmapped=sum(1 for r in rows if r.state == "unmapped"),
                conflict=sum(1 for r in rows if r.state == "conflict"),
                missing=sum(1 for r in rows if r.state == "missing"),
            )
        )

    all_rows = [r for g in group_models for r in g.accounts]
    return AccountsResponse(
        scan_base=str(base),
        groups=group_models,
        total_accounts=len(all_rows),
        unmapped=sum(1 for r in all_rows if r.state == "unmapped"),
        conflict=sum(1 for r in all_rows if r.state == "conflict"),
        missing=sum(1 for r in all_rows if r.state == "missing"),
        synced_at=snapshot.get("scanned_at"),
        syncing=syncing,
        never_synced=False,
    )


def snapshot_is_stale(ttl_hours: float) -> bool:
    """True if there's no snapshot or it's older than ``ttl_hours`` — used to
    decide whether to kick a background sync on startup."""
    snapshot = read_snapshot()
    if snapshot is None:
        return True
    scanned_at = snapshot.get("scanned_at")
    if not scanned_at:
        return True
    try:
        when = datetime.fromisoformat(scanned_at)
    except ValueError:
        return True
    age_hours = (datetime.now(UTC) - when).total_seconds() / 3600.0
    return age_hours >= ttl_hours
