"""Server-side storage for pending transaction backup imports.

The backup import modal needs to carry the uploaded CSV between preview and
confirmation without placing financial data in Flask's signed client-side
session. Entries are process-local, short-lived, and addressed by opaque
references rendered into the confirmation form.
"""

import secrets
import time
from dataclasses import dataclass
from threading import Lock

from flask import current_app

TRANSACTION_BACKUP_IMPORT_PAYLOAD_TTL_SECONDS = 900
TRANSACTION_BACKUP_IMPORT_PAYLOAD_EXTENSION_KEY = "transaction_backup_import_payloads"


@dataclass(frozen=True)
class TransactionBackupImportPayload:
    """One pending transaction backup CSV upload."""

    csv_text: str
    user_id: str
    expires_at: float


class TransactionBackupImportPayloadStore:
    """Process-local store for pending transaction backup CSV uploads."""

    def __init__(self) -> None:
        """Initialize an empty protected store."""
        self._entries: dict[str, TransactionBackupImportPayload] = {}
        self._lock = Lock()

    def store(self, csv_text: str, user_id: str) -> str:
        """Store uploaded backup CSV text and return an opaque reference."""
        now = time.monotonic()
        reference = secrets.token_urlsafe(32)
        entry = TransactionBackupImportPayload(
            csv_text=csv_text,
            user_id=user_id,
            expires_at=now + TRANSACTION_BACKUP_IMPORT_PAYLOAD_TTL_SECONDS,
        )
        with self._lock:
            self._prune_expired(now)
            self._entries[reference] = entry
        return reference

    def claim(self, reference: object, user_id: str) -> TransactionBackupImportPayload | None:
        """Atomically consume an unexpired upload owned by the confirming user."""
        if not isinstance(reference, str) or not reference:
            return None

        now = time.monotonic()
        with self._lock:
            self._prune_expired(now)
            entry = self._entries.get(reference)
            if entry is None or entry.user_id != user_id:
                return None
            del self._entries[reference]
            return entry

    def release(self, reference: str, payload: TransactionBackupImportPayload) -> None:
        """Restore a claimed upload after queue rejection while preserving expiry."""
        with self._lock:
            if payload.expires_at > time.monotonic():
                self._entries[reference] = payload

    def get(self, reference: object, user_id: str) -> TransactionBackupImportPayload | None:
        """Read an unexpired user-owned upload when rendering its confirmation modal."""
        if not isinstance(reference, str) or not reference:
            return None
        with self._lock:
            self._prune_expired(time.monotonic())
            entry = self._entries.get(reference)
            return entry if entry is not None and entry.user_id == user_id else None

    def discard(self, reference: object, user_id: str) -> None:
        """Remove a user's previous upload when a new preview replaces it."""
        self.claim(reference, user_id)

    def _prune_expired(self, now: float) -> None:
        """Remove expired payloads while the store lock is held."""
        expired_references = [reference for reference, entry in self._entries.items() if entry.expires_at <= now]
        for reference in expired_references:
            del self._entries[reference]


def store_transaction_backup_import(csv_text: str, user_id: str) -> str:
    """Store one pending transaction backup import and return its reference."""
    return transaction_backup_import_payload_store().store(csv_text, user_id)


def transaction_backup_import_payload_store() -> TransactionBackupImportPayloadStore:
    """Return the current Flask app's transaction backup import payload store."""
    store = current_app.extensions.get(TRANSACTION_BACKUP_IMPORT_PAYLOAD_EXTENSION_KEY)
    if not isinstance(store, TransactionBackupImportPayloadStore):
        store = TransactionBackupImportPayloadStore()
        current_app.extensions[TRANSACTION_BACKUP_IMPORT_PAYLOAD_EXTENSION_KEY] = store
    return store
