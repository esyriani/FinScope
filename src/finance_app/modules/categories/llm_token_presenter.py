"""Presentation helpers for LLM token-estimate JSON responses.

The estimator returns provider-neutral dictionaries. Controllers pass their
translation function here before returning browser-facing JSON.
"""

from collections.abc import Callable, Mapping
from typing import Any


def localize_token_estimate_result(
    result: Mapping[str, Any],
    translate: Callable[[str], str],
) -> dict[str, Any]:
    """Return an estimate result with browser-facing messages localized."""
    payload = dict(result)
    message = payload.get("message")
    if message:
        payload["message"] = translate(str(message))

    estimate = payload.get("estimate")
    if isinstance(estimate, Mapping):
        payload["estimate"] = localize_token_estimate(estimate, translate)

    return payload


def localize_token_estimate(
    estimate: Mapping[str, Any],
    translate: Callable[[str], str],
) -> dict[str, Any]:
    """Return token-estimate fields with warning text localized."""
    localized = dict(estimate)
    warning = localized.get("warning")
    if warning:
        localized["warning"] = translate(str(warning))

    batches = localized.get("batches")
    if isinstance(batches, list):
        localized["batches"] = [
            localize_token_estimate_batch(batch, translate) if isinstance(batch, Mapping) else batch
            for batch in batches
        ]

    return localized


def localize_token_estimate_batch(
    batch: Mapping[str, Any],
    translate: Callable[[str], str],
) -> dict[str, Any]:
    """Return one token-estimate batch with warning text localized."""
    localized = dict(batch)
    warning = localized.get("warning")
    if warning:
        localized["warning"] = translate(str(warning))
    return localized
