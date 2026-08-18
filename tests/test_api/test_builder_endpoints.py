"""Visual-builder backend endpoints: intake layout, page image, draft dry-run,
and manual model create."""

import shutil

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from stencil.config import settings
from stencil.db import crud
from stencil.db.models import Base, IntakeRecord
from stencil.db.session import get_db
from stencil.main import app
from stencil.models import registry
from stencil.models.schema import ExtractionModel
from stencil.profiles.loader import load_all_profiles
from tests.corpus_utils import CORPUS_ROOT, load_corpus_profile

PROFILE_ID = "colt.standard.v1"
_LAYOUT = "colt.standard"


def _corpus_pdf():
    return sorted((CORPUS_ROOT / _LAYOUT / "invoices").glob("*.pdf"))[0]


def _corpus_model_json() -> dict:
    import json

    return json.loads((CORPUS_ROOT / _LAYOUT / "model.json").read_text(encoding="utf-8"))


@pytest.fixture()
def client(tmp_path, monkeypatch):
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine)

    def override_get_db():
        session = session_factory()
        try:
            yield session
        finally:
            session.close()

    monkeypatch.setattr(settings, "work_dir", tmp_path)
    monkeypatch.setattr(settings, "archive_dir", tmp_path / "archive")
    monkeypatch.setattr(settings, "processing_dir", tmp_path / "processing")
    monkeypatch.setattr(settings, "completed_dir", tmp_path / "completed")
    monkeypatch.setattr(registry.settings, "extraction_models_dir", tmp_path / "models")

    from stencil.db import session as db_session
    from stencil.profiles import loader as ploader

    monkeypatch.setattr(db_session, "engine", engine)
    monkeypatch.setattr(db_session, "SessionLocal", session_factory)
    monkeypatch.setattr(ploader, "_profiles_cache", {})

    app.dependency_overrides[get_db] = override_get_db
    ploader.save_profile(load_corpus_profile(_LAYOUT))
    load_all_profiles()
    try:
        yield TestClient(app), session_factory, tmp_path
    finally:
        app.dependency_overrides.clear()


def _seed_intake(session_factory, tmp_path, *, with_pdf=True) -> str:
    session = session_factory()
    record = IntakeRecord(
        original_filename="colt.pdf", original_pdf_path="/in/colt.pdf", status="completed",
    )
    session.add(record)
    session.commit()
    intake_id = record.id
    session.close()
    if with_pdf:
        dest = tmp_path / "archive" / intake_id / "original.pdf"
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(_corpus_pdf(), dest)
    return intake_id


# --- layout ---------------------------------------------------------------

def test_layout_returns_pages_with_normalized_bboxes(client):
    tc, sf, tmp_path = client
    intake_id = _seed_intake(sf, tmp_path)

    resp = tc.get(f"/api/v1/intakes/{intake_id}/layout")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["intake_id"] == intake_id
    assert body["page_count"] >= 1
    first_page = body["pages"][0]
    assert "size" in first_page and first_page["size"]["width"] > 0
    # At least one row carries a normalized bbox to drive the overlay.
    rows = first_page["rows"]
    assert rows, "expected visual rows"
    assert any(r.get("normalized_bbox") for r in rows)


def test_layout_reports_reading_column_and_divider_hint(client):
    tc, sf, tmp_path = client
    intake_id = _seed_intake(sf, tmp_path)

    body = tc.get(f"/api/v1/intakes/{intake_id}/layout").json()
    page = body["pages"][0]
    # Unsplit: every row is one reading column, and the hint key is present so
    # the builder can offer (or withhold) the divider.
    assert body["column_split_x"] == []
    assert all(r.get("reading_column", 0) == 0 for r in page["rows"])
    assert "suggested_column_split_x" in page


def test_layout_applies_requested_column_split(client):
    tc, sf, tmp_path = client
    intake_id = _seed_intake(sf, tmp_path)

    body = tc.get(f"/api/v1/intakes/{intake_id}/layout?column_split_x=500").json()
    assert body["column_split_x"] == [500.0]
    cols = {r.get("reading_column", 0) for p in body["pages"] for r in p["rows"]}
    # A split page reads as more than one column (the corpus page has content on
    # both sides of the mid-line).
    assert cols == {0, 1}, cols


def test_layout_404_when_pdf_missing(client):
    tc, sf, tmp_path = client
    intake_id = _seed_intake(sf, tmp_path, with_pdf=False)
    resp = tc.get(f"/api/v1/intakes/{intake_id}/layout")
    assert resp.status_code == 404


def test_layout_404_when_intake_missing(client):
    tc, _, _ = client
    resp = tc.get("/api/v1/intakes/does-not-exist/layout")
    assert resp.status_code == 404


# --- sample store ---------------------------------------------------------

def _upload_sample(tc) -> str:
    with _corpus_pdf().open("rb") as fh:
        resp = tc.post(
            "/api/v1/intakes/samples",
            files={"file": ("colt.pdf", fh.read(), "application/pdf")},
        )
    assert resp.status_code == 200, resp.text
    return resp.json()["sample_id"]


def test_sample_upload_then_layout(client):
    tc, _, _ = client
    sample_id = _upload_sample(tc)
    resp = tc.get(f"/api/v1/intakes/{sample_id}/layout")
    assert resp.status_code == 200, resp.text
    assert resp.json()["page_count"] >= 1


def test_sample_rejects_non_pdf(client):
    tc, _, _ = client
    resp = tc.post(
        "/api/v1/intakes/samples",
        files={"file": ("notes.txt", b"hello", "text/plain")},
    )
    assert resp.status_code == 400


def test_sample_pdf_served_inline(client):
    tc, _, _ = client
    sample_id = _upload_sample(tc)
    resp = tc.get(f"/api/v1/intakes/samples/{sample_id}/pdf")
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"] == "application/pdf"
    assert resp.headers["content-disposition"].startswith("inline")
    assert resp.content[:4] == b"%PDF"


def test_sample_pdf_404_when_missing(client):
    tc, _, _ = client
    resp = tc.get("/api/v1/intakes/samples/does-not-exist/pdf")
    assert resp.status_code == 404


def test_sample_pdf_rejects_path_traversal(client):
    tc, _, _ = client
    # A traversal id must never resolve outside the sample store.
    resp = tc.get("/api/v1/intakes/samples/..%2f..%2fetc/pdf")
    assert resp.status_code in (400, 404)


def test_draft_test_accepts_sample_id(client):
    tc, _, _ = client
    sample_id = _upload_sample(tc)
    resp = tc.post(
        "/api/v1/models/draft/test",
        json={"intake_id": sample_id, "model_json": _corpus_model_json()},
    )
    assert resp.status_code == 200, resp.text
    assert "trace" in resp.json()


# --- draft dry-run --------------------------------------------------------

def test_draft_test_returns_trace_and_output(client):
    tc, sf, tmp_path = client
    intake_id = _seed_intake(sf, tmp_path)

    resp = tc.post(
        "/api/v1/models/draft/test",
        json={"intake_id": intake_id, "model_json": _corpus_model_json()},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # Trace structure is always present (region_rows may be empty for models that
    # use the deterministic line_item_strategy path, as the corpus model does).
    assert "trace" in body and "region_rows" in body["trace"]
    # The corpus model should still produce a non-empty deliverable.
    assert body["output"]["row_count"] >= 1


def test_draft_test_previews_through_the_requested_output_spec(client):
    """A model authored before any profile exists must preview against the
    deliverable the author picked, not the system default — otherwise the whole
    session is spent checking output against the wrong columns."""
    tc, sf, tmp_path = client
    intake_id = _seed_intake(sf, tmp_path)

    created = tc.post(
        "/api/v1/output-specs",
        json={
            "spec_id": "builder.twocol",
            "name": "Two column",
            "columns": [
                {"header": "SERVICE", "source": "line_item.service_id", "width": 20},
                {"header": "AMOUNT", "source": "line_item.amount", "width": 14},
            ],
        },
    )
    assert created.status_code == 201, created.text

    # What the builder sends before a profile is chosen: the placeholder id,
    # which resolves to no profile.
    unbound = {**_corpus_model_json(), "supplier_profile_id": "draft"}
    resp = tc.post(
        "/api/v1/models/draft/test",
        json={
            "intake_id": intake_id,
            "model_json": unbound,
            "output_spec_id": "builder.twocol",
        },
    )
    assert resp.status_code == 200, resp.text
    headers = [c["header"] for c in resp.json()["output"]["columns"]]
    assert headers == ["SERVICE", "AMOUNT"]


def test_draft_test_defaults_to_the_standard_spec_without_an_override(client):
    tc, sf, tmp_path = client
    intake_id = _seed_intake(sf, tmp_path)

    unbound = {**_corpus_model_json(), "supplier_profile_id": "draft"}
    resp = tc.post(
        "/api/v1/models/draft/test",
        json={"intake_id": intake_id, "model_json": unbound},
    )
    assert resp.status_code == 200, resp.text
    headers = [c["header"] for c in resp.json()["output"]["columns"]]
    assert headers[0] == "EXT_SERVICEID"


def test_draft_test_profile_binding_wins_over_a_spec_override(client):
    """Once the draft names a real profile, that profile's deliverable is the
    authority — the preview must never disagree with what it will ship."""
    tc, sf, tmp_path = client
    intake_id = _seed_intake(sf, tmp_path)

    created = tc.post(
        "/api/v1/output-specs",
        json={
            "spec_id": "builder.ignored",
            "name": "Ignored",
            "columns": [{"header": "SERVICE", "source": "line_item.service_id", "width": 20}],
        },
    )
    assert created.status_code == 201, created.text

    resp = tc.post(
        "/api/v1/models/draft/test",
        json={
            "intake_id": intake_id,
            "model_json": _corpus_model_json(),  # carries a resolvable profile id
            "output_spec_id": "builder.ignored",
        },
    )
    assert resp.status_code == 200, resp.text
    headers = [c["header"] for c in resp.json()["output"]["columns"]]
    assert headers[0] == "EXT_SERVICEID"


def test_draft_test_reports_dropped_rows_instead_of_failing(client):
    """A model that matches nothing must still return its trace + why it dropped.

    This is exactly when the author needs the feedback, so the builder path asks
    for allow_empty rather than surfacing a bare 422.
    """
    tc, sf, tmp_path = client
    intake_id = _seed_intake(sf, tmp_path)

    # Region + grouping find candidate items, but the amount column is a band
    # where no amount lives, so every candidate is dropped for a missing amount.
    model_json = {
        "model_id": "draft",
        "supplier_profile_id": "draft",
        "supplier": "draft",
        "region": {"columns": [{"name": "amount", "x0": 0, "x1": 5}]},
        "row_classifiers": [{"role": "item", "where": {"row_text": "."}}],
        "grouping": {"mode": "single_row", "item_role": "item"},
        "item_fields": [
            {
                "name": "amount",
                "source": {"rows": "role", "row_role": "item", "column": "amount"},
                "transform": {"type": "currency"},
                "required": True,
            }
        ],
    }
    resp = tc.post(
        "/api/v1/models/draft/test", json={"intake_id": intake_id, "model_json": model_json}
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["output"]["row_count"] == 0
    dropped = body["trace"]["dropped_items"]
    assert dropped, "expected dropped rows to explain the empty output"
    assert "amount" in dropped[0]["reason"]
    assert dropped[0]["text"]


def _write_expected_xlsx(path, headers, rows):
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.append(list(headers))
    for row in rows:
        ws.append(["" if c is None else c for c in row])
    wb.save(path)
    wb.close()


def _build_eval_folder(tmp_path, layout):
    """A one-case eval folder whose expected XLSX is exactly what the corpus
    model produces, so a correct model scores a full match."""
    from stencil.evals.dataset import discover_cases
    from stencil.models.interpreter import execute_model
    from stencil.output.preview import preview_for_profile

    root = tmp_path / "evalroot"
    case_dir = root / layout
    (case_dir / "invoices").mkdir(parents=True)
    (case_dir / "expected").mkdir(parents=True)
    shutil.copyfile(CORPUS_ROOT / _LAYOUT / "profile.json", case_dir / "profile.json")
    pdf = _corpus_pdf()
    shutil.copyfile(pdf, case_dir / "invoices" / pdf.name)

    case = next(c for c in discover_cases(root) if c.layout_id == layout)
    profile = case.load_profile()
    invoice = execute_model(
        ExtractionModel.model_validate(_corpus_model_json()), pdf, "gen", skip_self_checks=True
    )
    preview = preview_for_profile(invoice, profile, extraction_path="model")
    _write_expected_xlsx(
        case_dir / "expected" / f"{pdf.stem}.xlsx",
        [c["header"] for c in preview["columns"]],
        preview["rows"],
    )
    return root


def test_eval_folder_scores_model_against_expected(client, tmp_path, monkeypatch):
    tc, _, _ = client
    root = _build_eval_folder(tmp_path, "mylayout")
    monkeypatch.setattr(settings, "debug", True)
    monkeypatch.setattr(settings, "eval_corpus_dir", str(root))

    resp = tc.post(
        "/api/v1/models/draft/eval-folder",
        json={"model_json": _corpus_model_json(), "layout_id": "mylayout"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["files_total"] == 1
    f = body["files"][0]
    # Expected XLSX == the model's own output, so every row matches.
    assert f["expected_rows"] > 0
    assert f["matched_rows"] == f["expected_rows"]
    assert f["is_match"] is True
    assert body["files_matched"] == 1


def test_eval_folder_requires_debug(client, tmp_path, monkeypatch):
    tc, _, _ = client
    monkeypatch.setattr(settings, "debug", False)
    resp = tc.post(
        "/api/v1/models/draft/eval-folder",
        json={"model_json": _corpus_model_json(), "layout_id": "mylayout"},
    )
    assert resp.status_code == 403


def test_eval_folder_404_for_unknown_layout(client, tmp_path, monkeypatch):
    tc, _, _ = client
    monkeypatch.setattr(settings, "debug", True)
    monkeypatch.setattr(settings, "eval_corpus_dir", str(tmp_path / "empty"))
    resp = tc.post(
        "/api/v1/models/draft/eval-folder",
        json={"model_json": _corpus_model_json(), "layout_id": "nope"},
    )
    assert resp.status_code == 404


def test_draft_test_model_level_tax_and_header_populate(client):
    """A model that carries header_fields + a flat tax rate fills the
    document-level columns and EXT_TAX = rate × amount, with no profile."""
    tc, sf, tmp_path = client
    intake_id = _seed_intake(sf, tmp_path)

    model = _corpus_model_json()  # corpus model already has header_fields
    model["tax_output_mode"] = "calculate"
    model["tax_rate_source"] = "invoice_tax_rate"
    model.setdefault("totals", {})["tax_rate"] = {"label": "", "literal": "20"}

    resp = tc.post(
        "/api/v1/models/draft/test",
        json={"intake_id": intake_id, "model_json": model},
    )
    assert resp.status_code == 200, resp.text
    out = resp.json()["output"]
    cols = [c["header"] for c in out["columns"]]
    ai, ti, ii = cols.index("EXT_AMOUNT"), cols.index("EXT_TAX"), cols.index("EXT_INVOICENUMBER")

    taxed = 0
    for row in out["rows"]:
        amt, tax = row[ai], row[ti]
        if amt and tax is not None:
            assert abs(float(tax) - 0.2 * float(amt)) < 0.01
            taxed += 1
    assert taxed > 0, "expected EXT_TAX computed as 20% of amount"
    # header field flows into the delivered column (corpus has invoice_number)
    assert any(row[ii] and row[ii] != "UNKNOWN" for row in out["rows"])


def test_draft_test_rejects_bad_model_json(client):
    tc, sf, tmp_path = client
    intake_id = _seed_intake(sf, tmp_path)
    resp = tc.post(
        "/api/v1/models/draft/test",
        json={"intake_id": intake_id, "model_json": {"not": "a model"}},
    )
    assert resp.status_code == 400


# --- manual create --------------------------------------------------------

def test_manual_create_persists_candidate(client):
    tc, sf, tmp_path = client
    intake_id = _seed_intake(sf, tmp_path)

    resp = tc.post(
        f"/api/v1/profiles/{PROFILE_ID}/models",
        json={"sample_intake_id": intake_id, "model_json": _corpus_model_json()},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["id"] == PROFILE_ID
    assert body["status"] == "candidate"
    assert body["model_json"]["created_by"] == "manual"
    # Routing keys are derived server-side, not taken from the posted rules.
    assert body["layout_fingerprint"]
    assert body["layout_family_key"]


def test_manual_create_409_when_approved(client):
    tc, sf, tmp_path = client
    intake_id = _seed_intake(sf, tmp_path)

    session = sf()
    registry.save_model(session, ExtractionModel(
        model_id=PROFILE_ID, supplier_profile_id=PROFILE_ID, supplier="Colt",
        layout_fingerprint="sha256:fp",
    ))
    crud.update_model_status(session, PROFILE_ID, "approved", approved_by="me")
    session.commit()
    session.close()

    resp = tc.post(
        f"/api/v1/profiles/{PROFILE_ID}/models",
        json={"sample_intake_id": intake_id, "model_json": _corpus_model_json()},
    )
    assert resp.status_code == 409


def test_manual_create_rejects_bad_model_json(client):
    tc, sf, tmp_path = client
    intake_id = _seed_intake(sf, tmp_path)
    resp = tc.post(
        f"/api/v1/profiles/{PROFILE_ID}/models",
        json={"sample_intake_id": intake_id, "model_json": {"nope": 1}},
    )
    assert resp.status_code == 400


def test_manual_create_404_when_profile_missing(client):
    tc, sf, tmp_path = client
    intake_id = _seed_intake(sf, tmp_path)
    resp = tc.post(
        "/api/v1/profiles/ghost.profile/models",
        json={"sample_intake_id": intake_id, "model_json": _corpus_model_json()},
    )
    assert resp.status_code == 404
