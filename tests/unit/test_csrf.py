"""Tests for csrf behavior."""

import pytest
from flask import Flask, jsonify

from finance_app.core.csrf import CSRF_FIELD_NAME, CSRF_HEADER_NAME, csrf_token, register_csrf


@pytest.fixture
def csrf_client():
    """Create a minimal Flask client with CSRF protection registered."""
    app = Flask(__name__)
    app.secret_key = "test-secret"
    register_csrf(app)

    @app.route("/token")
    def token():
        """Return a CSRF token."""
        return jsonify({"token": csrf_token()})

    @app.route("/form", methods=["POST"])
    def form_post():
        """Accept a protected form post."""
        return "ok"

    @app.route("/json", methods=["POST"])
    def json_post():
        """Accept a protected JSON post."""
        return jsonify({"ok": True})

    return app.test_client()


def test_form_post_rejects_missing_csrf_token(csrf_client):
    """Verify form posts reject missing CSRF tokens."""
    response = csrf_client.post("/form", data={})

    assert response.status_code == 403


def test_form_post_accepts_valid_csrf_token(csrf_client):
    """Verify form posts accept valid CSRF tokens."""
    token = csrf_client.get("/token").get_json()["token"]

    response = csrf_client.post("/form", data={CSRF_FIELD_NAME: token})

    assert response.status_code == 200


def test_form_post_rejects_invalid_csrf_token(csrf_client):
    """Verify form posts reject invalid CSRF tokens."""
    csrf_client.get("/token")

    response = csrf_client.post("/form", data={CSRF_FIELD_NAME: "invalid"})

    assert response.status_code == 403


def test_json_post_accepts_valid_csrf_header(csrf_client):
    """Verify JSON posts accept valid CSRF headers."""
    token = csrf_client.get("/token").get_json()["token"]

    response = csrf_client.post(
        "/json",
        json={"value": 1},
        headers={CSRF_HEADER_NAME: token},
    )

    assert response.status_code == 200
    assert response.get_json() == {"ok": True}


def test_json_post_returns_json_for_invalid_csrf_token(csrf_client):
    """Verify JSON posts return JSON for invalid CSRF tokens."""
    csrf_client.get("/token")

    response = csrf_client.post(
        "/json",
        json={"value": 1},
        headers={CSRF_HEADER_NAME: "invalid"},
    )

    assert response.status_code == 403
    assert response.get_json() == {"ok": False, "message": "Invalid CSRF token."}
