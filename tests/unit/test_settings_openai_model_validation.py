"""Tests for Settings OpenAI model validation boundary."""

from types import SimpleNamespace
from typing import Any

from finance_app.modules.settings.openai_model_validation import (
    MODEL_VALIDATION_AVAILABLE,
    MODEL_VALIDATION_MODEL_NOT_FOUND,
    MODEL_VALIDATION_REQUEST_ERROR,
    OPENAI_MODEL_VALIDATION_TIMEOUT_SECONDS,
    OpenAIModelValidationResult,
    validate_openai_model_availability,
)
from finance_app.modules.settings.service import validate_openai_model_from_form


class FakeOpenAIModelsClientFactory:
    """OpenAI-compatible fake for model-list validation tests."""

    def __init__(self, model_ids: tuple[str, ...] = (), error: BaseException | None = None) -> None:
        """Store the fake model-list scenario and captured constructor calls."""
        self.model_ids = tuple(model_ids)
        self.error = error
        self.constructor_calls: list[dict[str, Any]] = []
        self.list_calls = 0

    def __call__(self, api_key: str, timeout: int) -> SimpleNamespace:
        """Return a fake client and capture construction arguments."""
        self.constructor_calls.append({"api_key": api_key, "timeout": timeout})
        return SimpleNamespace(models=SimpleNamespace(list=self.list_models))

    def list_models(self) -> SimpleNamespace:
        """Return or raise the configured model-list response."""
        self.list_calls += 1
        if self.error:
            raise self.error
        return SimpleNamespace(data=[SimpleNamespace(id=model_id) for model_id in self.model_ids])


def test_validate_openai_model_availability_uses_injected_client_factory() -> None:
    """Verify model validation calls the injected provider client."""
    fake_client = FakeOpenAIModelsClientFactory(("gpt-test", "gpt-other"))

    result = validate_openai_model_availability(
        "gpt-test",
        api_key="sk-test",
        client_factory=fake_client,
    )

    assert result == OpenAIModelValidationResult(
        True,
        MODEL_VALIDATION_AVAILABLE,
        "Model is available to this API key: gpt-test",
        "gpt-test",
    )
    assert fake_client.constructor_calls == [{"api_key": "sk-test", "timeout": OPENAI_MODEL_VALIDATION_TIMEOUT_SECONDS}]
    assert fake_client.list_calls == 1


def test_validate_openai_model_availability_reports_missing_model() -> None:
    """Verify unavailable models return a typed not-found result."""
    fake_client = FakeOpenAIModelsClientFactory(("gpt-other",))

    result = validate_openai_model_availability(
        "gpt-test",
        api_key="sk-test",
        client_factory=fake_client,
    )

    assert result.available is False
    assert result.code == MODEL_VALIDATION_MODEL_NOT_FOUND
    assert result.message == "Model was not returned by the OpenAI models API: gpt-test"


def test_validate_openai_model_availability_sanitizes_provider_errors() -> None:
    """Verify provider errors preserve type while hiding API key material."""
    fake_client = FakeOpenAIModelsClientFactory(error=TimeoutError("request timed out for sk-secret123"))

    result = validate_openai_model_availability(
        "gpt-test",
        api_key="sk-test",
        client_factory=fake_client,
    )

    assert result.available is False
    assert result.code == MODEL_VALIDATION_REQUEST_ERROR
    assert result.error_type == "TimeoutError"
    assert result.message == "Could not load models for the configured OpenAI API key."
    assert "sk-secret123" not in result.detail
    assert "sk-***" in result.detail


def test_validate_openai_model_from_form_uses_injected_validator() -> None:
    """Verify Settings service depends on an injected model validator boundary."""
    calls: list[str] = []

    def validator(model_name: str) -> OpenAIModelValidationResult:
        """Capture the validated model name and return a successful result."""
        calls.append(model_name)
        return OpenAIModelValidationResult(
            True,
            MODEL_VALIDATION_AVAILABLE,
            f"Model is available to this API key: {model_name}",
            model_name,
        )

    message = validate_openai_model_from_form({"openai_model": "gpt-unit"}, model_validator=validator)

    assert message == "Model is available to this API key: gpt-unit"
    assert calls == ["gpt-unit"]
