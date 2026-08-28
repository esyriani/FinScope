"""Tests for server-side transaction AI payload storage."""

from finance_app.modules.transactions import ai_payloads


def test_transaction_ai_payload_store_pops_once_and_returns_copies():
    """Verify transaction AI payloads are one-time and not exposed by reference."""
    store = ai_payloads.TransactionAiPayloadStore()
    reference = store.store({"description": "TVA SPORTS DIRECT", "nested": {"category": "Entertainment"}})

    first = store.get(reference)
    assert first == {"description": "TVA SPORTS DIRECT", "nested": {"category": "Entertainment"}}
    assert first is not None
    first["nested"]["category"] = "Changed"

    assert store.get(reference) == {
        "description": "TVA SPORTS DIRECT",
        "nested": {"category": "Entertainment"},
    }
    assert store.pop(reference) == {
        "description": "TVA SPORTS DIRECT",
        "nested": {"category": "Entertainment"},
    }
    assert store.pop(reference) is None


def test_transaction_ai_payload_store_expires_payloads(monkeypatch):
    """Verify stale transaction AI payloads are not returned."""
    now = 100.0
    monkeypatch.setattr(ai_payloads.time, "monotonic", lambda: now)
    store = ai_payloads.TransactionAiPayloadStore()
    reference = store.store({"category": "Entertainment"})

    now += ai_payloads.TRANSACTION_AI_PAYLOAD_TTL_SECONDS + 1

    assert store.get(reference) is None
    assert store.pop(reference) is None
