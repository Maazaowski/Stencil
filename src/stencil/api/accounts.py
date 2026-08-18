"""Accounts view API: serve the persisted folder snapshot reconciled against
profile mappings, trigger a background re-sync, and assign/create/unassign
account→profile mappings.

Discovery (the slow disk walk over the ``/data/Invoices`` bind mount) lives in
``stencil.accounts.sync`` and runs as a Celery task — this module never
walks the mount inside a request, so the page always loads fast.

Accounts still live inside ``profile.delivery.accounts``; assign/unassign edit
those, and the reconciled state (unmapped/mapped/conflict/missing) is recomputed
on read, so mapping changes reflect immediately without a re-scan.
"""

from __future__ import annotations

from pathlib import Path

import structlog
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from stencil.accounts import sync
from stencil.accounts.sync import AccountsResponse
from stencil.api.deps import CurrentUser
from stencil.profiles.loader import (
    get_profile,
    load_all_profiles,
    normalize_account_path,
    save_profile,
)
from stencil.profiles.schema import DeliveryAccount, SupplierProfile

logger = structlog.get_logger()

router = APIRouter(prefix="/accounts", tags=["accounts"])


# --- read: snapshot reconciled against profiles ---------------------------


@router.get("", response_model=AccountsResponse)
def list_accounts():
    """Grouped accounts from the last snapshot, reconciled against live profiles.
    Reads a file (no disk walk), so it always returns fast. Use POST /sync to
    refresh the snapshot from disk."""
    return sync.reconcile()


class SyncStatus(BaseModel):
    status: str | None = None
    customers_done: int | None = None
    customers_total: int | None = None
    accounts_found: int | None = None
    scanned_at: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    duration_seconds: float | None = None
    error: str | None = None


class SyncResponse(BaseModel):
    status: str  # queued | already_running


@router.post("/sync", response_model=SyncResponse)
def sync_accounts(current_user: CurrentUser):
    """Kick a background re-scan of the invoices tree (Celery task). No-op if one
    is already running. Poll GET /accounts/sync/status for progress."""
    from stencil.tasks.worker import accounts_sync_task

    if sync.sync_in_progress():
        return SyncResponse(status="already_running")
    sync.write_sync_status({
        "status": "queued", "customers_done": 0, "customers_total": None,
        "accounts_found": 0, "error": None,
    })
    accounts_sync_task.delay()
    logger.info("accounts.sync_queued")
    return SyncResponse(status="queued")


@router.get("/sync/status", response_model=SyncStatus)
def sync_status():
    """Progress of the current/last sync, for the page's progress bar."""
    return SyncStatus(**(sync.read_sync_status() or {}))


# --- assign / create / unassign -------------------------------------------


class AssignRequest(BaseModel):
    profile_id: str
    inbound_paths: list[str]


class CreateAccountRequest(BaseModel):
    profile_id: str
    customer: str
    account: str


class UnassignRequest(BaseModel):
    inbound_paths: list[str]


class MutationResponse(BaseModel):
    assigned: int = 0
    detached_from: list[str] = Field(default_factory=list)
    unassigned: int = 0


def _write_accounts(profile: SupplierProfile, accounts: list[DeliveryAccount]) -> None:
    """Persist an account list on a profile, clearing the legacy single-path form."""
    profile.delivery.accounts = accounts
    profile.delivery.inbound_path = None
    profile.delivery.output_path = None
    save_profile(profile)


def _detach_folder(key: str, *, keep_profile_id: str) -> list[str]:
    """Remove an account folder from every profile except ``keep_profile_id``.

    Persists each detached profile immediately so the DB reflects one owner
    before the target is validated. Never touches the filesystem.
    """
    detached: list[str] = []
    for profile in list(load_all_profiles().values()):
        if profile.profile_id == keep_profile_id:
            continue
        remaining = [
            a for a in profile.effective_accounts
            if normalize_account_path(a.inbound_path) != key
        ]
        if len(remaining) != len(profile.effective_accounts):
            _write_accounts(profile, remaining)
            detached.append(profile.profile_id)
    return detached


def _assign_paths(profile_id: str, inbound_paths: list[str]) -> MutationResponse:
    target = get_profile(profile_id)
    if target is None:
        raise HTTPException(status_code=404, detail=f"Profile '{profile_id}' not found")

    from stencil.api.profiles import _profile_readiness_issues

    accounts = list(target.effective_accounts)
    existing_keys = {normalize_account_path(a.inbound_path) for a in accounts}
    labels = {a.label for a in accounts}
    detached_all: list[str] = []
    assigned = 0

    for raw in inbound_paths:
        key = normalize_account_path(raw)
        if not key:
            continue
        # Detach from any other profile first, so the target won't self-conflict.
        detached_all.extend(_detach_folder(key, keep_profile_id=profile_id))
        if key in existing_keys:
            continue  # already on the target

        inbound = Path(raw)
        output = inbound.parent / "xls"
        for d in (inbound, output):
            try:
                d.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                logger.warning("accounts.mkdir_failed", path=str(d), error=str(exc))

        account_name = inbound.parent.name or inbound.name
        customer = inbound.parent.parent.name
        label = account_name if account_name not in labels else f"{customer}/{account_name}"
        labels.add(label)
        accounts.append(DeliveryAccount(label=label, inbound_path=raw, output_path=str(output)))
        existing_keys.add(key)
        assigned += 1

    _write_accounts(target, accounts)

    issues = [i for i in _profile_readiness_issues(target) if "already mapped" not in i]
    if issues:
        raise HTTPException(status_code=422, detail={"issues": issues})

    return MutationResponse(assigned=assigned, detached_from=sorted(set(detached_all)))


@router.post("/assign", response_model=MutationResponse)
def assign_accounts(body: AssignRequest, current_user: CurrentUser):
    """Map account folders to a profile, detaching each from any other profile
    first (enforcing one-account-one-profile). Ensures the pdf + xls folders
    exist; never deletes anything."""
    return _assign_paths(body.profile_id, body.inbound_paths)


@router.post("/create", response_model=MutationResponse)
def create_account(body: CreateAccountRequest, current_user: CurrentUser):
    """Onboard an account whose folder doesn't exist yet: create
    ``<scan_base>/<customer>/<account>/{pdf,xls}`` on disk, then assign it."""
    for part in (body.customer, body.account):
        if not part or part in (".", "..") or "/" in part or "\\" in part:
            raise HTTPException(status_code=400, detail="Invalid customer/account name")
    account_dir = sync.scan_base() / body.customer / body.account
    pdf_dir = account_dir / "pdf"
    result = _assign_paths(body.profile_id, [str(pdf_dir)])
    # Show the new folder immediately, without waiting for a full re-scan.
    sync.upsert_folder(str(pdf_dir))
    return result


@router.post("/unassign", response_model=MutationResponse)
def unassign_accounts(body: UnassignRequest, current_user: CurrentUser):
    """Remove account folders from whatever profile(s) claim them. Mapping only —
    the folders and their PDFs stay on disk."""
    keys = {normalize_account_path(p) for p in body.inbound_paths} - {None}
    count = 0
    for profile in list(load_all_profiles().values()):
        remaining = [
            a for a in profile.effective_accounts
            if normalize_account_path(a.inbound_path) not in keys
        ]
        if len(remaining) != len(profile.effective_accounts):
            count += len(profile.effective_accounts) - len(remaining)
            _write_accounts(profile, remaining)
    return MutationResponse(unassigned=count)
