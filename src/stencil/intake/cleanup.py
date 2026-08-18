"""Filesystem cleanup for intake lifecycle directories."""

from __future__ import annotations

import shutil
from pathlib import Path

import structlog

from stencil.config import settings

logger = structlog.get_logger()

_LIFECYCLE_DIRS = (
    "inbound_dir",
    "processing_dir",
    "completed_dir",
    "exceptions_dir",
    "archive_dir",
)


def purge_work_dir_artifacts() -> int:
    """Remove all intake subdirectories under operational work_dir folders."""
    removed = 0
    for attr in _LIFECYCLE_DIRS:
        root: Path | None = getattr(settings, attr, None)
        if root is None or not root.exists():
            continue
        for child in root.iterdir():
            if not child.is_dir():
                continue
            try:
                shutil.rmtree(child)
                removed += 1
            except OSError as exc:
                logger.warning("purge.dir_remove_failed", path=str(child), error=str(exc))
    return removed
