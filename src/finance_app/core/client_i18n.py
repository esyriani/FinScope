"""Registry for browser-facing translation message ids.

Feature modules and shared browser-script manifests register the English source
strings that can be translated by ``window.financeTranslate``. The app factory
reads the assembled registry when rendering the base template.
"""

from collections.abc import Iterable

_CLIENT_TRANSLATION_MESSAGES: dict[str, None] = {}


SHARED_CLIENT_TRANSLATION_MESSAGES = (
    "Chart {number}",
    "Close",
    "CSV",
    "Excel",
    "Expand",
    "Expand {label}",
    "Export {label}",
    "Min",
    "Q1",
    "Median",
    "Mean",
    "Q3",
    "Max",
    "n/a",
    "Page refresh failed.",
    "Page refresh returned no content.",
    "Refresh target was not found.",
    "Remove {label}",
    "Showing {start}-{end} of {total} rows",
    "Table {number}",
    "The action could not be completed.",
    "The page section could not be refreshed.",
    "The table could not be exported.",
    "Total",
    "{count} selected",
)


def register_client_translation_messages(messages: Iterable[object]) -> None:
    """Register browser translation message ids, preserving first-seen order."""
    for message in messages:
        text = str(message or "").strip()
        if text:
            _CLIENT_TRANSLATION_MESSAGES.setdefault(text, None)


def register_core_client_translation_messages() -> None:
    """Register shared browser messages used by global static scripts."""
    register_client_translation_messages(SHARED_CLIENT_TRANSLATION_MESSAGES)


def client_translation_messages() -> tuple[str, ...]:
    """Return all registered browser translation message ids."""
    return tuple(_CLIENT_TRANSLATION_MESSAGES)
