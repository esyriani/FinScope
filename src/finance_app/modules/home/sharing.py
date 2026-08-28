"""User-sharing and greeting helpers for the Home title area.

These helpers are request-aware because they read the authenticated user, but
they do not own Home SQL queries or activity presentation.
"""

from datetime import datetime
from typing import Any

from flask import has_request_context
from flask_login import current_user  # type: ignore[import-untyped]

from finance_app.modules.users import repository as user_repository


def build_home_greeting() -> Any:
    """Return the personalized Home greeting message and display name."""
    display_name = "there"
    if has_request_context() and getattr(current_user, "is_authenticated", False):
        display_name = current_user.display_name or current_user.username

    hour = datetime.now().hour
    if hour < 12:
        message = "Good morning, {name}"
    elif hour < 18:
        message = "Good afternoon, {name}"
    else:
        message = "Good evening, {name}"
    return {"message": message, "name": display_name}


def build_user_sharing_context(conn: Any) -> Any:
    """Return subtle shared-access copy for the Home title area."""
    if not has_request_context() or not getattr(current_user, "is_authenticated", False):
        return {"message": "", "params": {}}

    users = [dict(row) for row in user_repository.list_users(conn) if row["is_active"]]
    owner = next((user for user in users if user["role"] == "owner"), None)
    current_user_id = int(current_user.id)
    others = [user for user in users if int(user["id"]) != current_user_id]
    owner_name = display_name_for_user(owner) if owner else "Owner"

    if not others:
        return sharing_context("Only you have access")

    shared_params = sharing_names_or_count(others)
    if owner and current_user_id == int(owner["id"]):
        return sharing_context(shared_params["message"], **shared_params["params"])

    return sharing_context(
        f"{shared_params['message']} \u00b7 Owner: {{owner}}",
        **shared_params["params"],
        owner=owner_name,
    )


def sharing_names_or_count(users: Any) -> Any:
    """Return shared-with message parts for a compact user list."""
    names = [display_name_for_user(user) for user in users]
    if len(names) <= 2:
        return {"message": "Shared with {names}", "params": {"names": " and ".join(names)}}
    return {"message": "Shared with {count} users", "params": {"count": len(names)}}


def sharing_context(message: Any, names: Any = "", count: Any = 0, owner: Any = "") -> Any:
    """Return a complete Home sharing message object for templates."""
    return {
        "message": message,
        "params": {
            "names": names,
            "count": count,
            "owner": owner,
        },
    }


def display_name_for_user(user: Any) -> Any:
    """Return a user's display label for collaborative context."""
    return (user or {}).get("display_name") or (user or {}).get("username") or ""
