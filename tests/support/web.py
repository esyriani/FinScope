"""Shared Flask route test helpers.

Provides small helpers and client wrappers for authenticated test-client flows.
The helpers assume Flask's test client session support is available.
"""

from finance_app.core.csrf import CSRF_FIELD_NAME, CSRF_HEADER_NAME, CSRF_SESSION_KEY

DEFAULT_TEST_CSRF_TOKEN = "test-csrf-token"


class CsrfEnabledClient:
    """Wrap a Flask test client with automatic CSRF token submission."""

    def __init__(self, client, token=DEFAULT_TEST_CSRF_TOKEN):
        """Store the wrapped client and seed its session with a CSRF token."""
        self.client = client
        self._token = token
        set_csrf_token(self.client, token)

    def set_token(self, token=DEFAULT_TEST_CSRF_TOKEN):
        """Replace the stored CSRF token and return it for direct assertions."""
        self._token = set_csrf_token(self.client, token)
        return self._token

    @property
    def token(self):
        """Return the token currently stored on the wrapped test client."""
        return self._token

    def form_data(self, data=None):
        """Return form data with a CSRF field included when one is absent."""
        payload = dict(data or {})
        payload.setdefault(CSRF_FIELD_NAME, self._token)
        return payload

    def json_headers(self, headers=None):
        """Return request headers with a CSRF header included when absent."""
        payload = dict(headers or {})
        payload.setdefault(CSRF_HEADER_NAME, self._token)
        return payload

    def post(self, *args, data=None, json=None, headers=None, **kwargs):
        """Issue a POST request with an injected form field or JSON header."""
        return self._request("post", *args, data=data, json=json, headers=headers, **kwargs)

    def put(self, *args, data=None, json=None, headers=None, **kwargs):
        """Issue a PUT request with an injected form field or JSON header."""
        return self._request("put", *args, data=data, json=json, headers=headers, **kwargs)

    def delete(self, *args, data=None, json=None, headers=None, **kwargs):
        """Issue a DELETE request with an injected form field or JSON header."""
        return self._request("delete", *args, data=data, json=json, headers=headers, **kwargs)

    def _request(self, method, *args, data=None, json=None, headers=None, **kwargs):
        """Dispatch a mutating request with the matching CSRF transport."""
        request = getattr(self.client, method)
        if json is None:
            return request(
                *args,
                data=self.form_data(data),
                headers=headers,
                **kwargs,
            )
        return request(
            *args,
            json=json,
            headers=self.json_headers(headers),
            **kwargs,
        )

    def __getattr__(self, name):
        """Delegate non-CSRF helpers such as ``get`` to the wrapped client."""
        return getattr(self.client, name)


def set_csrf_token(client, token=DEFAULT_TEST_CSRF_TOKEN):
    """Store a deterministic CSRF token in the test client's session.

    Args:
        client: Flask test client with an active session transaction helper.
        token: Token value to persist.

    Returns:
        The token value stored in the session so callers can include it in form
        payloads.
    """
    with client.session_transaction() as session:
        session[CSRF_SESSION_KEY] = token
    return token


def csrf_enabled_client(client, token=DEFAULT_TEST_CSRF_TOKEN):
    """Return a CSRF-enabled wrapper around a Flask test client."""
    return CsrfEnabledClient(client, token)
