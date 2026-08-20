"""OpenAI model availability validation for Settings.

This adapter owns the provider-specific client construction used by the
Settings page model check. Callers can inject an OpenAI-compatible
``client_factory`` so tests and future providers do not need route-level
monkeypatches or network access.
"""

import logging
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from finance_app.core.config import settings as app_settings

logger = logging.getLogger(__name__)

OPENAI_MODEL_VALIDATION_TIMEOUT_SECONDS = 5

MODEL_VALIDATION_AVAILABLE = "available"
MODEL_VALIDATION_CONFIGURATION_MISSING = "configuration_missing"
MODEL_VALIDATION_DEPENDENCY_MISSING = "dependency_missing"
MODEL_VALIDATION_REQUEST_ERROR = "request_error"
MODEL_VALIDATION_MODEL_NOT_FOUND = "model_not_found"


@dataclass(frozen=True)
class OpenAIModelValidationResult:
    """Represent the result of checking a model against the provider."""

    available: bool
    code: str
    message: str
    model_name: str
    error_type: str = ""
    detail: str = ""


def validate_openai_model_availability(
    model_name: str,
    *,
    api_key: str | None = None,
    client_factory: Any = None,
    timeout: int = OPENAI_MODEL_VALIDATION_TIMEOUT_SECONDS,
) -> OpenAIModelValidationResult:
    """Return a typed provider validation result for an OpenAI model name."""
    model_name = str(model_name or "").strip()
    effective_api_key = app_settings.openai_api_key if api_key is None else api_key
    if not effective_api_key:
        return OpenAIModelValidationResult(
            False,
            MODEL_VALIDATION_CONFIGURATION_MISSING,
            "Configure an OpenAI API key first.",
            model_name,
        )

    if client_factory is None:
        try:
            from openai import OpenAI
        except ImportError:
            return OpenAIModelValidationResult(
                False,
                MODEL_VALIDATION_DEPENDENCY_MISSING,
                "Install the OpenAI Python package first.",
                model_name,
                error_type="ImportError",
            )
        client_factory = OpenAI

    try:
        client = client_factory(api_key=effective_api_key, timeout=timeout)
        models = client.models.list()
    except Exception as exc:
        detail = sanitize_provider_error(exc)
        logger.warning(
            "OpenAI model validation failed: %s: %s",
            type(exc).__name__,
            detail,
        )
        return OpenAIModelValidationResult(
            False,
            MODEL_VALIDATION_REQUEST_ERROR,
            "Could not load models for the configured OpenAI API key.",
            model_name,
            error_type=type(exc).__name__,
            detail=detail,
        )

    model_ids = openai_model_ids(models)
    if model_name in model_ids:
        return OpenAIModelValidationResult(
            True,
            MODEL_VALIDATION_AVAILABLE,
            f"Model is available to this API key: {model_name}",
            model_name,
        )

    return OpenAIModelValidationResult(
        False,
        MODEL_VALIDATION_MODEL_NOT_FOUND,
        f"Model was not returned by the OpenAI models API: {model_name}",
        model_name,
    )


def openai_model_ids(models: Any) -> set[str]:
    """Return model ids from an OpenAI ``models.list`` response."""
    ids: set[str] = set()
    for model in getattr(models, "data", []):
        model_id = model.get("id") if isinstance(model, Mapping) else getattr(model, "id", "")
        if model_id:
            ids.add(str(model_id))
    return ids


def sanitize_provider_error(exc: BaseException) -> str:
    """Return a bounded provider error message without API-key material."""
    message = str(exc)
    message = re.sub(r"sk-[A-Za-z0-9_-]+", "sk-***", message)
    return message[:500]
