"""Dev-only API to browse captured AI-call prompt dumps.

Reads the Markdown files ``ai_debug.traced_chat_completion`` writes under
``work_dir/ai_debug``. Every endpoint is gated on the live ``debug`` runtime
setting (``ST_DEBUG``) — returns 403 in production.
"""

from __future__ import annotations

import json
import re

import structlog
from fastapi import APIRouter, HTTPException

from stencil import runtime_settings
from stencil.ai_debug import ai_debug_dir
from stencil.api.deps import AdminUser

logger = structlog.get_logger()

router = APIRouter(prefix="/debug/ai-calls", tags=["debug"])

_NAME_PATTERN = re.compile(r"^[0-9A-Za-z._-]+\.md$")
_META_BLOCK = re.compile(r"```json\s*(\{.*?\})\s*```", re.DOTALL)


def _require_debug() -> None:
    if not bool(runtime_settings.runtime_value("debug")):
        raise HTTPException(status_code=403, detail="AI-call capture is only available in debug mode")


def _parse_meta(text: str) -> dict:
    match = _META_BLOCK.search(text)
    if not match:
        return {}
    try:
        return json.loads(match.group(1))
    except Exception:
        return {}


@router.get("")
def list_ai_calls(_admin: AdminUser):
    """Recent captured AI calls, newest first (filenames are timestamp-prefixed)."""
    _require_debug()
    directory = ai_debug_dir()
    if not directory.exists():
        return {"calls": []}
    calls = []
    for path in sorted(directory.glob("*.md"), reverse=True):
        meta = _parse_meta(path.read_text(encoding="utf-8"))
        calls.append({"name": path.name, **meta})
    return {"calls": calls}


@router.get("/{name}")
def get_ai_call(name: str, _admin: AdminUser):
    """Full Markdown dump (prompt + schema + response) for one captured call."""
    _require_debug()
    if not _NAME_PATTERN.match(name):
        raise HTTPException(status_code=400, detail="Invalid name")
    path = (ai_debug_dir() / name).resolve()
    base = ai_debug_dir().resolve()
    if not str(path).startswith(str(base)) or not path.exists():
        raise HTTPException(status_code=404, detail="Not found")
    return {"name": name, "markdown": path.read_text(encoding="utf-8")}


@router.delete("")
def clear_ai_calls(_admin: AdminUser):
    """Delete all captured AI-call dumps (convenience during prompt iteration)."""
    _require_debug()
    directory = ai_debug_dir()
    removed = 0
    if directory.exists():
        for path in directory.glob("*.md"):
            path.unlink(missing_ok=True)
            removed += 1
    return {"removed": removed}
