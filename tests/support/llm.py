"""Shared LLM test helpers.

Provides deterministic payload builders and request stubs for tests that
exercise LLM categorization behavior without calling external services.
"""

import json
from types import SimpleNamespace


AUTO_REQUEST_ID = object()


def unknown_transaction(description, merchant_key, amount):
    """Build an unknown transaction payload for LLM categorization tests.

    Args:
        description: Raw transaction description.
        merchant_key: Normalized merchant key used by categorization.
        amount: Transaction amount.

    Returns:
        A transaction dictionary in the shape expected by LLM helpers.
    """
    return {
        "description": description,
        "merchant_key": merchant_key,
        "amount": amount,
        "category": "UNKNOWN",
        "tags": [],
    }


def taxonomy_id(rows, name):
    """Return the taxonomy id for a category or tag name.

    Args:
        rows: Iterable taxonomy rows with ``name`` and ``id`` keys.
        name: Name to locate.

    Returns:
        The matching row id.

    Raises:
        AssertionError: If the requested taxonomy name is absent.
    """
    for row in rows:
        if row["name"] == name:
            return row["id"]
    raise AssertionError(f"Missing taxonomy row for {name}")


def result_payload(category_rows, tag_rows, request_id, category, confidence, tags=None, **extra):
    """Build a strict ID-based mocked LLM result payload.

    Args:
        category_rows: Available category taxonomy rows.
        tag_rows: Available tag taxonomy rows.
        request_id: Optional LLM request id to include.
        category: Category name to encode as an id.
        confidence: Mock confidence value.
        tags: Optional tag names to encode as ids.
        extra: Additional payload fields.

    Returns:
        A mocked LLM response dictionary.
    """
    payload = {
        "category_id": taxonomy_id(category_rows, category),
        "tag_ids": [taxonomy_id(tag_rows, tag) for tag in (tags or [])],
        "confidence": confidence,
    }
    if request_id is not None:
        payload["request_id"] = request_id
    payload.update(extra)
    return payload


def llm_result(category, confidence, tags=None, request_id=AUTO_REQUEST_ID, **extra):
    """Build a named LLM result scenario specification.

    Args:
        category: Taxonomy category name to return.
        confidence: Confidence score to encode.
        tags: Optional tag names to return.
        request_id: ``AUTO_REQUEST_ID`` uses the matching chunk request id,
            ``None`` omits the field, and any other value is used directly.
        extra: Additional LLM result fields.

    Returns:
        A scenario specification consumed by ``llm_response_scenario``.
    """
    return {
        "category": category,
        "confidence": confidence,
        "tags": list(tags or []),
        "request_id": request_id,
        "extra": dict(extra),
    }


def invalid_category_result(confidence=0.99, tags=None, request_id=AUTO_REQUEST_ID, **extra):
    """Build a scenario result with an invalid category id.

    Args:
        confidence: Confidence score to encode.
        tags: Optional valid tags to include.
        request_id: Request id behavior; see ``llm_result``.
        extra: Additional LLM result fields.

    Returns:
        A scenario specification consumed by ``llm_response_scenario``.
    """
    return {
        "invalid_category": True,
        "confidence": confidence,
        "tags": list(tags or []),
        "request_id": request_id,
        "extra": dict(extra),
    }


def invalid_tag_result(category, confidence=0.99, invalid_tag_id=999999, request_id=AUTO_REQUEST_ID, **extra):
    """Build a scenario result with a valid category and invalid tag id."""
    return {
        "category": category,
        "confidence": confidence,
        "invalid_tag_id": invalid_tag_id,
        "request_id": request_id,
        "extra": dict(extra),
    }


def llm_response_scenario(*specs):
    """Return a callable LLM response factory for named result specifications.

    Args:
        specs: Result specifications from ``llm_result``,
            ``invalid_category_result``, or ``invalid_tag_result``.

    Returns:
        A callable with the same shape as ``request_llm_categories``.
    """

    def scenario(unknown_chunk, *args):
        """Build deterministic result dictionaries for one LLM request."""
        category_rows = args[3]
        tag_rows = args[4]
        results = []
        for index, spec in enumerate(specs):
            request_id = _scenario_request_id(spec.get("request_id"), unknown_chunk, index)
            if spec.get("invalid_category"):
                payload = {
                    "category_id": 999999,
                    "confidence": spec["confidence"],
                    "needs_review": spec.get("extra", {}).get("needs_review", False),
                    "tag_ids": [taxonomy_id(tag_rows, tag) for tag in spec.get("tags", [])],
                }
                if request_id is not None:
                    payload["request_id"] = request_id
                payload.update(spec.get("extra", {}))
                results.append(payload)
                continue
            if "invalid_tag_id" in spec:
                payload = {
                    "category_id": taxonomy_id(category_rows, spec["category"]),
                    "confidence": spec["confidence"],
                    "needs_review": spec.get("extra", {}).get("needs_review", False),
                    "tag_ids": [spec["invalid_tag_id"]],
                }
                if request_id is not None:
                    payload["request_id"] = request_id
                payload.update(spec.get("extra", {}))
                results.append(payload)
                continue
            results.append(
                result_payload(
                    category_rows,
                    tag_rows,
                    request_id,
                    spec["category"],
                    spec["confidence"],
                    tags=spec.get("tags", []),
                    **spec.get("extra", {}),
                )
            )
        return results

    return scenario


def _scenario_request_id(spec_request_id, unknown_chunk, index):
    """Resolve an LLM scenario request id against a requested chunk."""
    if spec_request_id is AUTO_REQUEST_ID:
        return unknown_chunk[index]["llm_request_id"]
    return spec_request_id


def compact_candidates_for_test(conn, unknown_items, category_options, tag_options, unknown_category, *args):
    """Attach narrow candidate taxonomies for fallback-policy tests.

    Args:
        conn: Active database connection, unused by this deterministic helper.
        unknown_items: Transactions being prepared for LLM prompting.
        category_options: Available category names, unused.
        tag_options: Available tag names, unused.
        unknown_category: Configured unknown category label.
        args: Additional application arguments ignored by the helper.
    """
    del conn, category_options, tag_options, args
    for tx in unknown_items:
        tx["llm_candidate_categories"] = ["Food", unknown_category]
        tx["llm_candidate_tags"] = ["Tax"]


class LLMRequestStub:
    """Callable deterministic replacement for ``request_llm_categories``.

    Args:
        factory: Callable receiving the same arguments as the LLM requester and
            returning mocked result dictionaries.

    Attributes:
        calls: Captured call records in order.
    """

    def __init__(self, factory):
        """Store the result factory and initialize the call log."""
        self.factory = factory
        self.calls = []

    def __call__(self, unknown_chunk, *args):
        """Capture one request and return the factory's mocked response."""
        self.calls.append({"unknown_chunk": list(unknown_chunk), "args": args})
        return self.factory(unknown_chunk, *args)


class FakeOpenAIClientFactory:
    """OpenAI-compatible fake client constructor for request adapter tests.

    Args:
        payload: JSON-serializable response payload.
        raw_content: Optional raw response content, used for malformed JSON.
        error: Optional exception raised when chat completions are requested.

    Attributes:
        constructor_calls: Captured client construction arguments.
        created_calls: Captured chat completion request arguments.
    """

    def __init__(self, payload=None, raw_content=None, error=None):
        """Store the provider response scenario and initialize call logs."""
        self.payload = payload if payload is not None else {"results": []}
        self.raw_content = raw_content
        self.error = error
        self.constructor_calls = []
        self.created_calls = []

    def __call__(self, api_key, timeout):
        """Return an OpenAI-shaped fake client and capture construction args."""
        self.constructor_calls.append({"api_key": api_key, "timeout": timeout})
        return SimpleNamespace(
            chat=SimpleNamespace(
                completions=SimpleNamespace(create=self.create),
            )
        )

    def create(self, **kwargs):
        """Return or raise the configured fake chat completion response."""
        self.created_calls.append(kwargs)
        if self.error:
            raise self.error
        content = self.raw_content if self.raw_content is not None else json.dumps(self.payload)
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content=content),
                )
            ]
        )


def openai_json_response(payload):
    """Return a fake OpenAI client factory that emits a JSON payload."""
    return FakeOpenAIClientFactory(payload=payload)


def openai_invalid_json_response(raw_content="{not-json"):
    """Return a fake OpenAI client factory that emits malformed JSON."""
    return FakeOpenAIClientFactory(raw_content=raw_content)


def openai_error_response(error):
    """Return a fake OpenAI client factory that raises a provider-style error."""
    return FakeOpenAIClientFactory(error=error)
