"""Start the Celery worker with runtime-configured process concurrency."""

from __future__ import annotations

import subprocess
import sys

import structlog

from stencil import runtime_settings

logger = structlog.get_logger()


def main() -> int:
    try:
        concurrency = max(1, runtime_settings.worker_concurrency())
    except Exception as exc:  # pragma: no cover - startup fallback
        logger.warning("worker.concurrency_setting_failed", error=str(exc))
        concurrency = 2

    logger.info("worker.starting", concurrency=concurrency)
    return subprocess.call([
        sys.executable,
        "-m",
        "celery",
        "-A",
        "stencil.tasks.worker",
        "worker",
        "--loglevel=info",
        f"--concurrency={concurrency}",
    ])


if __name__ == "__main__":
    raise SystemExit(main())
