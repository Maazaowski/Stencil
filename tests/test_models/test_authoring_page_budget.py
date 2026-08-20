"""When the authoring prompt must be trimmed, which pages survive?

The final trim used to keep a *prefix* of pages, which is the right answer only
when the interesting part of a document is at its front. A 656-page invoice in
the eval corpus carries its whole deliverable on pages 3-15; a differently
organised one puts it at page 200, and a prefix loses it entirely.
"""

from stencil.models.authoring import _build_and_fit_prompt


def _pages(count: int, size: int = 1000) -> list[str]:
    return [f"=== PAGE {n} ===\n" + ("x" * size) for n in range(1, count + 1)]


def _kept(prompt: str, count: int) -> list[int]:
    return [n for n in range(1, count + 1) if f"=== PAGE {n} ===" in prompt]


def _build(page_texts, budget, priority=None):
    return _build_and_fit_prompt(
        budget=budget,
        page_texts=page_texts,
        additional=[],
        layout_evidence={},
        page_priority=priority,
        target={"header": {}, "totals": {}, "line_items": []},
        supplier_profile={},
        field_schema=None,
        line_amounts_are_net=False,
        feedback=None,
        focus="test",
    )


def test_a_prompt_within_budget_is_sent_unchanged():
    """Corpus parity: normal documents must not be touched."""
    pages = _pages(6)
    prompt = _build(pages, budget=1_000_000)
    assert _kept(prompt, 6) == [1, 2, 3, 4, 5, 6]


def test_priority_pages_survive_the_trim():
    pages = _pages(60)
    # Say the shape lives on pages 40-42 and the header on page 1.
    prompt = _build(pages, budget=4000, priority=[1, 40, 41, 42])
    kept = _kept(prompt, 60)

    assert 1 in kept and 40 in kept and 41 in kept and 42 in kept, kept


def test_a_late_table_is_lost_without_priority_and_kept_with_it():
    """The regression this exists to prevent, stated as a contrast."""
    pages = _pages(60)

    without = _kept(_build(pages, budget=4000), 60)
    with_priority = _kept(_build(pages, budget=4000, priority=[1, 55, 56]), 60)

    assert 55 not in without, "a prefix trim unexpectedly reached page 55"
    assert 55 in with_priority and 56 in with_priority, with_priority


def test_kept_pages_are_emitted_in_document_order():
    pages = _pages(60)
    prompt = _build(pages, budget=4000, priority=[50, 2, 30])
    kept = _kept(prompt, 60)
    assert kept == sorted(kept), kept


def test_an_out_of_range_priority_is_ignored():
    pages = _pages(5)
    prompt = _build(pages, budget=1_000_000, priority=[99, 2, -1])
    assert _kept(prompt, 5) == [1, 2, 3, 4, 5]


def test_at_least_one_page_always_survives():
    pages = _pages(40, size=50_000)
    prompt = _build(pages, budget=1, priority=[7])
    assert _kept(prompt, 40), "trim removed every page"
