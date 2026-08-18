"""Evidence grading for discovery-authored profile drafts."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any, Literal

from stencil.profiles.schema import AuthoringEvidence


def grade_authoring_evidence(
    *,
    evidence_level: Literal["paired_blueprint", "historical_blueprint", "invoice_only"],
    sample_results: list[dict[str, Any]],
    category_confidence: float,
    engine_version: str,
    historical_coverage_threshold: float = 0.90,
) -> AuthoringEvidence:
    """Apply the locked confidence gates to deterministic validation results.

    Each sample result may contain ``output_diff_count``, ``identifier_coverage``,
    ``reconciliation_variance``, ``required_fields_missing`` and ``reconciled``.
    Missing proof is deliberately treated as review-required, never verified.
    """
    hard_blockers: list[str] = []
    review_warnings: list[str] = []
    failed = False
    exact_diffs = True
    all_reconciled = bool(sample_results)
    coverages: list[float] = []
    max_variance = Decimal("0")

    for index, result in enumerate(sample_results, start=1):
        missing = list(result.get("required_fields_missing") or [])
        if missing:
            failed = True
            hard_blockers.append(
                f"Sample {index} is missing required fields: {', '.join(missing)}"
            )
        diff_count = result.get("output_diff_count")
        if diff_count is None:
            exact_diffs = False
        elif int(diff_count) != 0:
            exact_diffs = False
            message = f"Sample {index} has {int(diff_count)} output difference(s)."
            if evidence_level == "paired_blueprint":
                failed = True
                hard_blockers.append(message)
            else:
                review_warnings.append(message)
        reconciled = bool(result.get("reconciled", False))
        all_reconciled = all_reconciled and reconciled
        if not reconciled:
            failed = True
            hard_blockers.append(f"Sample {index} did not reconcile against printed totals.")
        try:
            variance = abs(Decimal(str(result.get("reconciliation_variance") or 0)))
        except (InvalidOperation, ValueError):
            variance = Decimal("Infinity")
        max_variance = max(max_variance, variance)
        if variance > Decimal("0.01"):
            failed = True
            hard_blockers.append(f"Sample {index} reconciliation variance is {variance}.")
        if result.get("identifier_coverage") is not None:
            coverages.append(float(result["identifier_coverage"]))

    if failed:
        status = "failed"
    elif evidence_level == "invoice_only":
        status = "review_required"
        review_warnings.append("Invoice-only profiles require manual review.")
    elif evidence_level == "paired_blueprint":
        status = "verified" if exact_diffs and all_reconciled and sample_results else "review_required"
    else:
        latest_coverage = coverages[-1] if coverages else 0.0
        status = (
            "verified"
            if all_reconciled and latest_coverage >= historical_coverage_threshold and sample_results
            else "review_required"
        )
        if latest_coverage < historical_coverage_threshold:
            failed = True
            hard_blockers.append(
                f"Latest-sample identifier coverage {latest_coverage:.1%} is below "
                f"{historical_coverage_threshold:.1%}."
            )

    if hard_blockers:
        status = "failed"
    risks = [*hard_blockers, *review_warnings]

    metrics = {
        "sample_count": len(sample_results),
        "exact_output_diff": exact_diffs,
        "all_samples_reconciled": all_reconciled,
        "latest_identifier_coverage": coverages[-1] if coverages else None,
        "max_reconciliation_variance": str(max_variance),
    }
    return AuthoringEvidence(
        evidence_level=evidence_level,
        status=status,
        category_confidence=category_confidence,
        metrics=metrics,
        unresolved_risks=list(dict.fromkeys(risks)),
        hard_blockers=list(dict.fromkeys(hard_blockers)),
        review_warnings=list(dict.fromkeys(review_warnings)),
        engine_version=engine_version,
    )
