"""Uploads are streamed with a size cap.

Every upload endpoint used to do `await file.read()`, buffering the entire body
in memory with no ceiling — a single large POST could exhaust the API process.
"""

import io

import pytest
from fastapi import HTTPException, UploadFile

from stencil.api.uploads import (
    stream_upload_to,
    validate_upload_filename,
)
from stencil.config import settings


def _upload(name: str, payload: bytes) -> UploadFile:
    return UploadFile(filename=name, file=io.BytesIO(payload))


async def test_upload_under_the_cap_is_written(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "max_upload_bytes", 1024)
    dest = tmp_path / "small.pdf"
    written = await stream_upload_to(_upload("small.pdf", b"x" * 512), dest)
    assert written == 512
    assert dest.read_bytes() == b"x" * 512


async def test_upload_over_the_cap_is_rejected_and_leaves_no_file(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "max_upload_bytes", 1024)
    dest = tmp_path / "big.pdf"
    with pytest.raises(HTTPException) as exc:
        await stream_upload_to(_upload("big.pdf", b"x" * 5000), dest)
    assert exc.value.status_code == 413
    # The partial write must not survive the rejection.
    assert not dest.exists()


async def test_zero_disables_the_cap(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "max_upload_bytes", 0)
    dest = tmp_path / "unbounded.pdf"
    assert await stream_upload_to(_upload("unbounded.pdf", b"x" * 5000), dest) == 5000


@pytest.mark.parametrize(
    "filename",
    ["../escape.pdf", "dir/nested.pdf", "back\\slash.pdf", "", ".", "..", "notes.txt"],
)
def test_dangerous_filenames_are_rejected(filename):
    with pytest.raises(HTTPException) as exc:
        validate_upload_filename(_upload(filename, b""), allowed_suffixes=(".pdf",))
    assert exc.value.status_code == 400


def test_ordinary_filename_is_accepted():
    assert (
        validate_upload_filename(_upload("Invoice 2026-08.pdf", b""), allowed_suffixes=(".pdf",))
        == "Invoice 2026-08.pdf"
    )
