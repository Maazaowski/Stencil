"""Stage timing helpers for pipeline observability."""

import time
from collections.abc import Iterator
from contextlib import contextmanager


@contextmanager
def stage_timer() -> Iterator[dict[str, int]]:
    """Yield a mutable dict; after the block, dict['duration_ms'] is set."""
    timing: dict[str, int] = {"duration_ms": 0}
    started = time.monotonic()
    try:
        yield timing
    finally:
        timing["duration_ms"] = int((time.monotonic() - started) * 1000)
