"""Tests for the global network guard.

Ensures pytest blocks accidental socket connections so tests cannot contact
external APIs such as LLM providers without an injected fake.
"""

import socket

import pytest

from tests.support.network import NetworkAccessBlockedError


def test_socket_create_connection_is_blocked():
    """Verify high-level socket connection attempts fail before network access."""
    with pytest.raises(NetworkAccessBlockedError, match="Inject a fake client"):
        socket.create_connection(("example.com", 443), timeout=0.01)


def test_socket_connect_methods_are_blocked():
    """Verify low-level socket connection attempts fail before network access."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as client_socket:
        with pytest.raises(NetworkAccessBlockedError, match="Inject a fake client"):
            client_socket.connect(("93.184.216.34", 443))

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as client_socket:
        with pytest.raises(NetworkAccessBlockedError, match="Inject a fake client"):
            client_socket.connect_ex(("93.184.216.34", 443))
