"""Shared helpers for profile marker matching."""

from __future__ import annotations


def marker_terms(marker: str | None) -> list[str]:
    """Return lowercase marker alternatives from a backward-compatible string.

    Profiles historically stored one marker string. Some supplier profiles use a
    comma-separated list to mean "any of these closes the region"; keep that
    shape while making matching explicit and reusable.
    """
    return [
        part.strip().lower()
        for part in str(marker or "").split(",")
        if part.strip()
    ]


def marker_in_text(markers: list[str], text: str) -> bool:
    text_low = text.lower()
    return any(marker and marker in text_low for marker in markers)
