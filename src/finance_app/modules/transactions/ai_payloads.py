"""Server-side one-time storage for single-transaction AI payloads.

Keeps financial AI modal data and apply payloads out of Flask's signed
client-side session cookie while preserving the redirect-based Transactions
workflow. Payloads are process-local, short-lived, and addressed only by opaque
references stored in the browser session.
"""

import copy
import secrets
import time
from collections.abc import Mapping
from dataclasses import dataclass
from threading import Lock
from typing import Any

from flask import current_app

TRANSACTION_AI_PAYLOAD_TTL_SECONDS = 300
TRANSACTION_AI_PAYLOAD_EXTENSION_KEY = "transaction_ai_payloads"


@dataclass(frozen=True)
class TransactionAiPayload:
    """One pending single-transaction AI payload."""

    payload: dict[str, Any]
    expires_at: float

    def to_payload(self) -> dict[str, Any]:
        """Return a defensive copy of the stored payload."""
        return copy.deepcopy(self.payload)


class TransactionAiPayloadStore:
    """Process-local store for one-time transaction AI payloads."""

    def __init__(self) -> None:
        """Initialize an empty protected store."""
        self._entries: dict[str, TransactionAiPayload] = {}
        self._lock = Lock()

    def store(self, payload: Mapping[str, Any]) -> str:
        """Store a payload and return an opaque browser-session reference."""
        now = time.monotonic()
        reference = secrets.token_urlsafe(32)
        entry = TransactionAiPayload(
            payload=copy.deepcopy(dict(payload)),
            expires_at=now + TRANSACTION_AI_PAYLOAD_TTL_SECONDS,
        )
        with self._lock:
            self._prune_expired(now)
            self._entries[reference] = entry
        return reference

    def get(self, reference: object) -> dict[str, Any] | None:
        """Return one payload for a valid unexpired reference without removing it."""
        if not isinstance(reference, str) or not reference:
            return None

        now = time.monotonic()
        with self._lock:
            self._prune_expired(now)
            entry = self._entries.get(reference)

        if entry is None or entry.expires_at <= now:
            return None
        return entry.to_payload()

    def pop(self, reference: object) -> dict[str, Any] | None:
        """Return and remove one payload for a valid unexpired reference."""
        if not isinstance(reference, str) or not reference:
            return None

        now = time.monotonic()
        with self._lock:
            self._prune_expired(now)
            entry = self._entries.pop(reference, None)

        if entry is None or entry.expires_at <= now:
            return None
        return entry.to_payload()

    def _prune_expired(self, now: float) -> None:
        """Remove expired payloads while the store lock is held."""
        expired_references = [reference for reference, entry in self._entries.items() if entry.expires_at <= now]
        for reference in expired_references:
            del self._entries[reference]


def store_transaction_ai_result(payload: Mapping[str, Any]) -> str:
    """Store a transaction AI modal result payload and return its reference."""
    return transaction_ai_payload_store().store(payload)


def pop_transaction_ai_result(reference: object) -> dict[str, Any] | None:
    """Pop a transaction AI modal result payload for an opaque reference."""
    return transaction_ai_payload_store().pop(reference)


def store_transaction_ai_suggestion(payload: Mapping[str, Any]) -> str:
    """Store a transaction AI apply payload and return its reference."""
    return transaction_ai_payload_store().store(payload)


def get_transaction_ai_suggestion(reference: object) -> dict[str, Any] | None:
    """Return a transaction AI apply payload for an opaque reference."""
    return transaction_ai_payload_store().get(reference)


def pop_transaction_ai_suggestion(reference: object) -> dict[str, Any] | None:
    """Pop a transaction AI apply payload for an opaque reference."""
    return transaction_ai_payload_store().pop(reference)


def transaction_ai_payload_store() -> TransactionAiPayloadStore:
    """Return the current Flask app's transaction AI payload store."""
    store = current_app.extensions.get(TRANSACTION_AI_PAYLOAD_EXTENSION_KEY)
    if not isinstance(store, TransactionAiPayloadStore):
        store = TransactionAiPayloadStore()
        current_app.extensions[TRANSACTION_AI_PAYLOAD_EXTENSION_KEY] = store
    return store
