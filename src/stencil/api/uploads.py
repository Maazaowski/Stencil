"""Shared helpers for handling uploaded PDFs in API endpoints."""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from fastapi import HTTPException, UploadFile

from stencil.config import settings

# Uploads are streamed in chunks rather than via `await file.read()`, which
# buffers the WHOLE body in memory — one oversized upload could OOM the API.
_CHUNK_BYTES = 1024 * 1024


def validate_upload_filename(file: UploadFile, *, allowed_suffixes: tuple[str, ...]) -> str:
    """Return the safe basename, rejecting traversal and disallowed file types."""
    if not file.filename or not file.filename.lower().endswith(allowed_suffixes):
        raise HTTPException(
            status_code=400,
            detail=f"Only {', '.join(allowed_suffixes)} files are accepted",
        )
    safe_name = Path(file.filename).name
    if (
        safe_name != file.filename
        or safe_name in {"", ".", ".."}
        or any(sep in file.filename for sep in ("/", "\\", "\x00"))
    ):
        raise HTTPException(status_code=400, detail="Invalid filename")
    return safe_name


async def stream_upload_to(file: UploadFile, dest: Path) -> int:
    """Stream an upload to ``dest``, enforcing ``ST_MAX_UPLOAD_BYTES``.

    Returns the byte count written. On overflow, removes the partial file and
    raises 413 — so an oversized upload costs bounded memory AND bounded disk.
    """
    limit = settings.max_upload_bytes
    written = 0
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        with dest.open("wb") as out:
            while chunk := await file.read(_CHUNK_BYTES):
                written += len(chunk)
                if limit and written > limit:
                    raise HTTPException(
                        status_code=413,
                        detail=f"File exceeds the {limit // (1024 * 1024)} MB upload limit",
                    )
                out.write(chunk)
    except HTTPException:
        dest.unlink(missing_ok=True)
        raise
    return written


async def save_uploaded_pdf(file: UploadFile, *, prefix: str) -> Path:
    """Validate and persist an uploaded PDF to a temp previews dir.

    The caller is responsible for deleting the returned file when done.
    """
    return await save_uploaded_file(file, prefix=prefix, allowed_suffixes=(".pdf",))


async def save_uploaded_file(
    file: UploadFile, *, prefix: str, allowed_suffixes: tuple[str, ...]
) -> Path:
    """Validate and persist an arbitrary uploaded file to the temp previews dir.

    The caller is responsible for deleting the returned file when done.
    """
    safe_name = validate_upload_filename(file, allowed_suffixes=allowed_suffixes)
    dest = settings.work_dir / "previews" / f"{prefix}_{uuid4().hex}_{safe_name}"
    await stream_upload_to(file, dest)
    return dest
