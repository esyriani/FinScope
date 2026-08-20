"""Server-side one-time storage for generated temporary password modals.

Keeps temporary credentials out of Flask's signed client-side session cookie
while preserving the owner user-management redirect flow. Payloads are
process-local, short-lived, and removed as soon as the Users page consumes them.
"""

import secrets
import time
from collections.abc import Mapping
from dataclasses import dataclass
from threading import Lock
from typing import Any

from flask import current_app

TEMPORARY_PASSWORD_MODAL_TTL_SECONDS = 300
TEMPORARY_PASSWORD_MODAL_EXTENSION_KEY = "auth_temporary_password_modals"


@dataclass(frozen=True)
class TemporaryPasswordModal:
    """One pending temporary-password modal payload."""

    username: str
    display_name: str
    temporary_password: str
    expires_at: float

    def to_template_payload(self) -> dict[str, str]:
        """Return template-ready modal data."""
        return {
            "username": self.username,
            "display_name": self.display_name,
            "temporary_password": self.temporary_password,
        }


class TemporaryPasswordModalStore:
    """Process-local store for one-time temporary password modal payloads."""

    def __init__(self) -> None:
        """Initialize an empty protected store."""
        self._entries: dict[str, TemporaryPasswordModal] = {}
        self._lock = Lock()

    def store(self, user: Mapping[str, Any], temporary_password: str) -> str:
        """Store a modal payload and return an opaque browser-session reference."""
        now = time.monotonic()
        reference = secrets.token_urlsafe(32)
        modal = TemporaryPasswordModal(
            username=str(user["username"]),
            display_name=str(user["display_name"] or user["username"]),
            temporary_password=temporary_password,
            expires_at=now + TEMPORARY_PASSWORD_MODAL_TTL_SECONDS,
        )
        with self._lock:
            self._prune_expired(now)
            self._entries[reference] = modal
        return reference

    def pop(self, reference: object) -> dict[str, str] | None:
        """Return and remove one modal payload for a valid unexpired reference."""
        if not isinstance(reference, str) or not reference:
            return None

        now = time.monotonic()
        with self._lock:
            self._prune_expired(now)
            modal = self._entries.pop(reference, None)

        if modal is None or modal.expires_at <= now:
            return None
        return modal.to_template_payload()

    def _prune_expired(self, now: float) -> None:
        """Remove expired modal payloads while the store lock is held."""
        expired_references = [reference for reference, modal in self._entries.items() if modal.expires_at <= now]
        for reference in expired_references:
            del self._entries[reference]


def store_temporary_password_modal(user: Mapping[str, Any], temporary_password: str) -> str:
    """Store a temporary password modal payload and return its opaque reference."""
    return temporary_password_modal_store().store(user, temporary_password)


def pop_temporary_password_modal(reference: object) -> dict[str, str] | None:
    """Pop a temporary password modal payload for an opaque reference."""
    return temporary_password_modal_store().pop(reference)


def temporary_password_modal_store() -> TemporaryPasswordModalStore:
    """Return the current Flask app's temporary password modal store."""
    store = current_app.extensions.get(TEMPORARY_PASSWORD_MODAL_EXTENSION_KEY)
    if not isinstance(store, TemporaryPasswordModalStore):
        store = TemporaryPasswordModalStore()
        current_app.extensions[TEMPORARY_PASSWORD_MODAL_EXTENSION_KEY] = store
    return store
