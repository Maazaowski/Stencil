"""Context budgeting for model authoring.

Large, many-line-item documents (e.g. wireless bills with hundreds of charges)
blow past the model's input limit when the authoring prompt carries the full
ground truth + per-line evidence for every training invoice. The AI only needs
to learn the REPEATING row pattern, not every row, so we send a representative
sample. The authored model is still validated against the FULL invoice
downstream, so sampling never weakens correctness — it only bounds the prompt.
"""

from __future__ import annotations

from stencil.validation.schema import CanonicalInvoice, LineItem


def _row_signature(item: LineItem) -> tuple:
    """Coarse 'row shape' key: same charge type + same set of populated optional
    fields counts as the same KIND of row for authoring purposes."""
    return (
        str(item.charge_type),
        item.service_id is not None,
        item.billing_reference is not None,
        item.tax_amount is not None,
        item.quantity is not None,
        item.unit_rate is not None,
        item.billing_period_start is not None,
    )


def sample_representative_rows(invoice: CanonicalInvoice, max_rows: int) -> list[int]:
    """Return indices of a representative subset of the invoice's line items.

    Groups rows by shape and returns exemplars covering every distinct shape,
    up to ``max_rows`` (round-robin so each shape is represented before any shape
    gets a second exemplar). Deterministic: the same invoice + cap always yields
    the same indices, so the prompt's target and evidence stay consistent.
    """
    items = invoice.line_items
    if max_rows <= 0 or len(items) <= max_rows:
        return list(range(len(items)))

    groups: dict[tuple, list[int]] = {}
    for idx, item in enumerate(items):
        groups.setdefault(_row_signature(item), []).append(idx)

    queues = [list(idxs) for idxs in groups.values()]
    selected: list[int] = []
    while len(selected) < max_rows and any(queues):
        for queue in queues:
            if not queue:
                continue
            selected.append(queue.pop(0))
            if len(selected) >= max_rows:
                break
    return sorted(selected)
