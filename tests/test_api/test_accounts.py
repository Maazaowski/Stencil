"""Accounts snapshot + sync + assign/create/unassign API.

Discovery now runs as a background sync that writes a snapshot to work_dir; the
API reads that snapshot (no live disk walk). Tests populate the snapshot by
calling ``sync.run_sync()`` synchronously instead of going through Celery.
"""

import pytest
from fastapi.testclient import TestClient

from stencil.accounts import sync
from stencil.config import settings
from stencil.main import app
from stencil.profiles.loader import get_profile, save_profile
from stencil.profiles.schema import SupplierProfile


@pytest.fixture
def client(isolated_db, tmp_path, monkeypatch):
    base = tmp_path / "Invoices"
    # Two discoverable accounts under one customer, plus a legacy (no-pdf) folder.
    (base / "82824706" / "acctA" / "pdf").mkdir(parents=True)
    (base / "82824706" / "acctB" / "pdf").mkdir(parents=True)
    (base / "82824706" / "acctA" / "pdf" / "inv1.pdf").write_text("x")
    (base / "82824706" / "Colt").mkdir(parents=True)
    (base / "82824706" / "Colt" / "model.rmd").write_text("legacy")
    monkeypatch.setattr(settings, "accounts_scan_dir", base)
    monkeypatch.setattr(settings, "work_dir", tmp_path / "work")
    sync._memo["mtime"] = None  # snapshot memo is module-global; reset per test
    sync._memo["value"] = None
    return TestClient(app)


def _profile(profile_id: str, *, status: str = "active", accounts=None) -> SupplierProfile:
    return SupplierProfile.model_validate({
        "profile_id": profile_id,
        "status": status,
        "identity": {"canonical_name": profile_id, "aliases": []},
        "classification": {"output_type": "standard"},
        "output_spec_id": "temforce.standard",
        "field_schema_id": "invoice.standard",
        "delivery": {"accounts": accounts or []},
    })


def _acct_path(customer, account):
    return str(settings.accounts_scan_dir / customer / account / "pdf")


def _one_account(path):
    return [{"label": "a", "inbound_path": path, "output_path": path.replace("pdf", "xls")}]


# --- snapshot + sync ------------------------------------------------------


def test_never_synced_before_first_scan(client):
    data = client.get("/api/v1/accounts").json()
    assert data["never_synced"] is True
    assert data["groups"] == [] and data["synced_at"] is None


def test_sync_populates_snapshot_grouped_and_ignores_legacy(client):
    snap = sync.run_sync()  # synchronous scan → snapshot on disk
    assert len(snap["folders"]) == 2

    data = client.get("/api/v1/accounts").json()
    assert data["never_synced"] is False and data["synced_at"]
    accts = {a["account"]: a for g in data["groups"] for a in g["accounts"]}
    assert set(accts) == {"acctA", "acctB"}  # the .rmd/Colt folder is ignored
    assert accts["acctA"]["state"] == "unmapped"
    assert accts["acctA"]["pdf_count"] == 1
    assert accts["acctA"]["output_path"].endswith("xls")


def test_post_sync_enqueues_task_and_status_reports_progress(client):
    from stencil.tasks import worker

    queued = []
    monkeypatch_delay = lambda *a, **k: queued.append((a, k))  # noqa: E731
    worker.accounts_sync_task.delay = monkeypatch_delay  # type: ignore[method-assign]

    r = client.post("/api/v1/accounts/sync")
    assert r.status_code == 200 and r.json()["status"] == "queued"
    assert queued  # the Celery task was dispatched

    # The status file the worker would write; simulate by running the sync body.
    sync.run_sync()
    status = client.get("/api/v1/accounts/sync/status").json()
    assert status["status"] == "done"
    assert status["accounts_found"] == 2 and status["customers_total"] == 1


def test_reconcile_reflects_profiles_without_rescan(client):
    """Assigning changes state on the SAME snapshot — no re-scan needed."""
    sync.run_sync()
    save_profile(_profile("colt.v1"))
    path = _acct_path("82824706", "acctA")
    client.post("/api/v1/accounts/assign", json={"profile_id": "colt.v1", "inbound_paths": [path]})

    row = next(a for g in client.get("/api/v1/accounts").json()["groups"]
               for a in g["accounts"] if a["account"] == "acctA")
    assert row["state"] == "mapped" and row["profile_ids"] == ["colt.v1"]


def test_conflict_state_surfaces_double_mapped_folders(client):
    sync.run_sync()
    path = _acct_path("82824706", "acctA")
    save_profile(_profile("owner.one", accounts=_one_account(path)))
    save_profile(_profile("owner.two", accounts=_one_account(path)))

    row = next(a for g in client.get("/api/v1/accounts").json()["groups"]
               for a in g["accounts"] if a["account"] == "acctA")
    assert row["state"] == "conflict"
    assert sorted(row["profile_ids"]) == ["owner.one", "owner.two"]


def test_missing_state_for_mapped_folder_absent_from_snapshot(client):
    sync.run_sync()
    ghost = str(settings.accounts_scan_dir / "99999999" / "gone" / "pdf")
    save_profile(_profile("stale.v1", accounts=_one_account(ghost)))

    row = next(a for g in client.get("/api/v1/accounts").json()["groups"]
               for a in g["accounts"] if a["account"] == "gone")
    assert row["state"] == "missing"


def test_snapshot_survives_and_is_read_without_disk_walk(client, monkeypatch):
    """Once synced, a reload reads the snapshot even if the disk walk would find
    nothing — proving the page no longer depends on scanning the mount."""
    sync.run_sync()
    # Make the live walk a no-op; the snapshot must still serve the accounts.
    monkeypatch.setattr(sync, "_collect_pdf_dirs", lambda *a, **k: None)
    accts = {a["account"] for g in client.get("/api/v1/accounts").json()["groups"]
             for a in g["accounts"]}
    assert {"acctA", "acctB"} <= accts


def test_pdf_count_counts_files_without_stat_walk(client):
    pdf = settings.accounts_scan_dir / "82824706" / "acctA" / "pdf"
    (pdf / "inv2.pdf").write_text("y")
    assert sync._pdf_count(str(pdf)) == 2  # inv1.pdf (fixture) + inv2.pdf


# --- assign / create / unassign -------------------------------------------


def test_assign_maps_and_creates_xls(client):
    sync.run_sync()
    save_profile(_profile("colt.v1"))
    path = _acct_path("82824706", "acctA")

    r = client.post("/api/v1/accounts/assign", json={"profile_id": "colt.v1", "inbound_paths": [path]})
    assert r.status_code == 200, r.text
    assert r.json()["assigned"] == 1

    accts = get_profile("colt.v1").effective_accounts
    assert [a.inbound_path for a in accts] == [path]
    assert (settings.accounts_scan_dir / "82824706" / "acctA" / "xls").is_dir()


def test_reassign_detaches_from_previous_profile(client):
    save_profile(_profile("p.first"))
    save_profile(_profile("p.second"))
    path = _acct_path("82824706", "acctA")
    client.post("/api/v1/accounts/assign", json={"profile_id": "p.first", "inbound_paths": [path]})

    r = client.post("/api/v1/accounts/assign", json={"profile_id": "p.second", "inbound_paths": [path]})
    assert r.status_code == 200, r.text
    assert r.json()["detached_from"] == ["p.first"]

    assert [a.inbound_path for a in get_profile("p.first").effective_accounts] == []
    assert [a.inbound_path for a in get_profile("p.second").effective_accounts] == [path]


def test_create_makes_folders_assigns_and_shows_immediately(client):
    sync.run_sync()  # snapshot exists; create should upsert into it
    save_profile(_profile("newco.v1"))
    r = client.post("/api/v1/accounts/create",
                    json={"profile_id": "newco.v1", "customer": "13405598", "account": "brand-new"})
    assert r.status_code == 200, r.text

    base = settings.accounts_scan_dir / "13405598" / "brand-new"
    assert (base / "pdf").is_dir() and (base / "xls").is_dir()
    assert [a.inbound_path for a in get_profile("newco.v1").effective_accounts] == [str(base / "pdf")]

    # Appears in the view without a full re-scan (upserted into the snapshot).
    row = next(a for g in client.get("/api/v1/accounts").json()["groups"]
               for a in g["accounts"] if a["account"] == "brand-new")
    assert row["state"] == "mapped"


def test_unassign_keeps_the_folder_and_files_on_disk(client):
    sync.run_sync()
    save_profile(_profile("colt.v1"))
    path = _acct_path("82824706", "acctA")
    client.post("/api/v1/accounts/assign", json={"profile_id": "colt.v1", "inbound_paths": [path]})

    r = client.post("/api/v1/accounts/unassign", json={"inbound_paths": [path]})
    assert r.status_code == 200 and r.json()["unassigned"] == 1

    # Mapping gone, but the folder and its PDF are untouched.
    assert get_profile("colt.v1").effective_accounts == []
    assert (settings.accounts_scan_dir / "82824706" / "acctA" / "pdf" / "inv1.pdf").exists()

    row = next(a for g in client.get("/api/v1/accounts").json()["groups"]
               for a in g["accounts"] if a["account"] == "acctA")
    assert row["state"] == "unmapped"


def test_create_rejects_path_traversal(client):
    save_profile(_profile("newco.v1"))
    r = client.post("/api/v1/accounts/create",
                    json={"profile_id": "newco.v1", "customer": "..", "account": "x"})
    assert r.status_code == 400
