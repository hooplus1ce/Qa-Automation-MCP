"""Explicit coverage metadata for bounded tool responses."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any


def completeness_report(
    *,
    scope: Any,
    returned_count: int | None = None,
    total_count: int | None = None,
    limit: int | None = None,
    truncated: bool | None = False,
    has_more: bool | None = None,
    complete_for_scope: bool | None = None,
    unit: str = "items",
    reasons: Iterable[str] = (),
) -> dict[str, Any]:
    """Describe whether a tool returned all data for its explicitly stated scope.

    ``complete_for_scope`` never means "the entire page was inspected" unless the
    scope itself says that. Unknown totals remain unknown instead of being guessed.
    """
    if has_more is None and returned_count is not None and total_count is not None:
        has_more = returned_count < total_count
    if truncated is True and has_more is None:
        has_more = True

    if complete_for_scope is None:
        if truncated is True or has_more is True:
            complete_for_scope = False
        elif truncated is False and has_more is False:
            complete_for_scope = True

    status = (
        "complete"
        if complete_for_scope is True
        else "partial"
        if complete_for_scope is False
        else "unknown"
    )
    normalized_reasons = list(dict.fromkeys(str(reason) for reason in reasons if reason))
    if truncated is True and not normalized_reasons:
        normalized_reasons.append("response_limit")

    return {
        "status": status,
        "complete_for_scope": complete_for_scope,
        "scope": scope,
        "unit": unit,
        "returned_count": returned_count,
        "total_count": total_count,
        "limit": limit,
        "has_more": has_more,
        "truncated": truncated,
        "reasons": normalized_reasons,
    }


__all__ = ["completeness_report"]
