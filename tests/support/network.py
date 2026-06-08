"""Network guards for the test suite.

Provides socket-level blocking helpers used by pytest fixtures to prevent
accidental external service calls from tests, especially LLM provider calls.
"""

import socket


class NetworkAccessBlockedError(RuntimeError):
    """Raised when test code attempts to open a network connection."""


def blocked_network_call(*args, **kwargs):
    """Raise a clear error for network APIs patched by the test guard.

    Args:
        *args: Positional arguments from the patched socket API.
        **kwargs: Keyword arguments from the patched socket API.

    Raises:
        NetworkAccessBlockedError: Always, because tests must inject fakes
            instead of contacting network services.
    """
    del args, kwargs
    raise NetworkAccessBlockedError(
        "Tests may not open network connections. Inject a fake client or stub " "the external service call instead."
    )


def install_network_guard(monkeypatch):
    """Patch socket connection APIs so tests cannot make network calls.

    Args:
        monkeypatch: pytest monkeypatch fixture used to restore the socket APIs
            after each test.

    Side effects:
        Replaces ``socket.create_connection``, ``socket.socket.connect``, and
        ``socket.socket.connect_ex`` for the duration of the current test.
    """
    monkeypatch.setattr(socket, "create_connection", blocked_network_call)
    monkeypatch.setattr(socket.socket, "connect", blocked_network_call)
    monkeypatch.setattr(socket.socket, "connect_ex", blocked_network_call)
