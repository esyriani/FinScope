"""Flask routes for authentication and user administration."""

from flask import Blueprint, flash, redirect, render_template, request, session, url_for
from flask_login import current_user, login_required, login_user, logout_user

from finance_app.core.constants import USER_ROLE_EDITOR, USER_ROLE_VIEWER
from finance_app.modules.auth.permissions import owner_required
from finance_app.modules.auth.service import (
    authenticate_user,
    bootstrap_owner,
    change_password as change_user_password,
    change_user_role,
    create_managed_user,
    get_user_account,
    has_owner_account,
    hand_off_ownership,
    list_managed_users,
    load_login_user,
    reset_user_password,
    set_user_active,
    update_own_display_name,
)


auth_bp = Blueprint("auth", __name__)
PENDING_PASSWORD_CHANGE_USER_ID = "pending_password_change_user_id"
PENDING_PASSWORD_CHANGE_USERNAME = "pending_password_change_username"
PENDING_PASSWORD_CHANGE_DISPLAY_NAME = "pending_password_change_display_name"
TEMPORARY_PASSWORD_MODAL = "temporary_password_modal"


@auth_bp.route("/auth/bootstrap", methods=["GET", "POST"])
def bootstrap():
    """Create the initial owner account when no owner exists.

    GET renders the first-run owner form. POST expects username, password, and
    password confirmation fields protected by CSRF. When bootstrap succeeds the
    new owner is logged in and redirected to the home page.
    """
    if has_owner_account():
        return redirect(url_for("auth.login"))

    if request.method == "POST":
        try:
            row = bootstrap_owner(
                request.form.get("username"),
                request.form.get("password"),
                request.form.get("confirm_password"),
                ip_address=request.remote_addr,
                display_name=request.form.get("display_name"),
            )
        except ValueError as exc:
            flash(str(exc))
        else:
            user = authenticate_user(row["username"], request.form.get("password"), request.remote_addr)
            if user is not None:
                login_user(user)
            flash("Owner account created.")
            return redirect(url_for("home.home"))

    return render_template("auth_bootstrap.html")


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    """Log a user in or complete the forced first-login password change."""
    if request.method == "POST" and request.form.get("auth_action") == "force_password_change":
        return complete_forced_password_change()

    if current_user.is_authenticated:
        if current_user.must_change_password:
            return render_forced_password_change()
        return redirect(url_for("home.home"))

    if not has_owner_account():
        return redirect(url_for("auth.bootstrap"))

    if request.method == "POST":
        user = authenticate_user(
            request.form.get("username"),
            request.form.get("password"),
            ip_address=request.remote_addr,
        )
        if user is None:
            flash("Invalid username or password.")
        else:
            if user.must_change_password:
                session[PENDING_PASSWORD_CHANGE_USER_ID] = user.id
                session[PENDING_PASSWORD_CHANGE_USERNAME] = user.username
                session[PENDING_PASSWORD_CHANGE_DISPLAY_NAME] = user.display_name
                return render_forced_password_change()
            login_user(user)
            return redirect(safe_next_url(request.form.get("next")) or url_for("home.home"))

    return render_template("auth_login.html", next_url=safe_next_url(request.args.get("next")))


@auth_bp.route("/logout", methods=["POST"])
@login_required
def logout():
    """Log out the current user and redirect to the login page."""
    clear_pending_password_change()
    logout_user()
    flash("You have been logged out.")
    return redirect(url_for("auth.login"))


@auth_bp.route("/password", methods=["GET", "POST"])
@login_required
def change_password():
    """Allow an authenticated user to change their own password.

    POST expects current password, new password, and confirmation fields. Users
    flagged with ``must_change_password`` remain restricted to this route until
    the change succeeds.
    """
    if current_user.must_change_password:
        return redirect(url_for("auth.login"))
    return redirect(url_for("auth.account"))


@auth_bp.route("/account", methods=["GET", "POST"])
@login_required
def account():
    """Render and process the current user's Account page."""
    if current_user.must_change_password:
        return redirect(url_for("auth.login"))

    if request.method == "POST":
        action = request.form.get("account_action")
        try:
            if action == "display_name":
                row = update_own_display_name(
                    current_user.id,
                    request.form.get("display_name"),
                    actor=current_user,
                    ip_address=request.remote_addr,
                )
                login_user(load_login_user(row["id"]))
                flash("Display name updated.")
            elif action == "password":
                change_user_password(
                    current_user.id,
                    request.form.get("current_password"),
                    request.form.get("new_password"),
                    request.form.get("confirm_password"),
                    ip_address=request.remote_addr,
                )
                login_user(load_login_user(current_user.id))
                flash("Password changed.")
            else:
                flash("Choose an account action.")
        except ValueError as exc:
            flash(str(exc))
        return redirect(url_for("auth.account"))

    return render_template("account.html", account_user=get_user_account(current_user.id))


@auth_bp.route("/admin/users")
@owner_required
def users():
    """Render owner-only user administration."""
    managed_users = list_managed_users()
    return render_template(
        "admin_users.html",
        users=managed_users,
        managed_roles=(USER_ROLE_EDITOR, USER_ROLE_VIEWER),
        ownership_handoff_candidates=ownership_handoff_candidates(managed_users),
        temporary_password_modal=session.pop(TEMPORARY_PASSWORD_MODAL, None),
    )


@auth_bp.route("/admin/users/create", methods=["POST"])
@owner_required
def create_user():
    """Create an owner-managed editor or viewer account."""
    try:
        user, temporary_password = create_managed_user(
            request.form.get("username"),
            request.form.get("role"),
            actor=current_user,
            ip_address=request.remote_addr,
            display_name=request.form.get("display_name"),
        )
    except ValueError as exc:
        flash(str(exc))
    else:
        session[TEMPORARY_PASSWORD_MODAL] = temporary_password_modal_payload(user, temporary_password)
        flash("User created.")
    return redirect(url_for("auth.users"))


@auth_bp.route("/admin/users/<int:user_id>/deactivate", methods=["POST"])
@owner_required
def deactivate_user(user_id):
    """Deactivate a user unless doing so would remove the final active owner."""
    try:
        set_user_active(user_id, False, actor=current_user, ip_address=request.remote_addr)
        flash("User deactivated.")
    except ValueError as exc:
        flash(str(exc))
    return redirect(url_for("auth.users"))


@auth_bp.route("/admin/users/<int:user_id>/reactivate", methods=["POST"])
@owner_required
def reactivate_user(user_id):
    """Reactivate a previously deactivated user."""
    try:
        set_user_active(user_id, True, actor=current_user, ip_address=request.remote_addr)
        flash("User reactivated.")
    except ValueError as exc:
        flash(str(exc))
    return redirect(url_for("auth.users"))


@auth_bp.route("/admin/users/<int:user_id>/role", methods=["POST"])
@owner_required
def update_user_role(user_id):
    """Change a managed user's role."""
    try:
        change_user_role(
            user_id,
            request.form.get("role"),
            actor=current_user,
            ip_address=request.remote_addr,
        )
        flash("User role updated.")
    except ValueError as exc:
        flash(str(exc))
    return redirect(url_for("auth.users"))


@auth_bp.route("/admin/users/<int:user_id>/reset-password", methods=["POST"])
@owner_required
def reset_password(user_id):
    """Generate a temporary password and require a password change."""
    try:
        user, temporary_password = reset_user_password(user_id, actor=current_user, ip_address=request.remote_addr)
        session[TEMPORARY_PASSWORD_MODAL] = temporary_password_modal_payload(user, temporary_password)
        flash("Temporary password generated.")
    except ValueError as exc:
        flash(str(exc))
    return redirect(url_for("auth.users"))


@auth_bp.route("/admin/users/handoff-ownership", methods=["POST"])
@owner_required
def handoff_ownership():
    """Transfer ownership to another active user and demote the actor to viewer."""
    current_owner_id = current_user.id
    try:
        hand_off_ownership(
            current_owner_id,
            request.form.get("target_user_id"),
            actor=current_user,
            ip_address=request.remote_addr,
        )
    except ValueError as exc:
        flash(str(exc))
        return redirect(url_for("auth.users"))

    login_user(load_login_user(current_owner_id))
    flash("Ownership handed off. Your account is now Viewer.")
    return redirect(url_for("auth.account"))


def complete_forced_password_change():
    """Complete the first-login password change without exposing app pages."""
    pending_user_id = forced_password_change_user_id()
    if pending_user_id is None:
        return redirect(url_for("auth.login"))

    try:
        change_user_password(
            pending_user_id,
            request.form.get("current_password"),
            request.form.get("new_password"),
            request.form.get("confirm_password"),
            ip_address=request.remote_addr,
        )
    except ValueError as exc:
        return render_forced_password_change(error=str(exc))

    clear_pending_password_change()
    user = load_login_user(pending_user_id)
    if user is not None:
        login_user(user)
    flash("Password changed.")
    return redirect(url_for("home.home"))


def forced_password_change_user_id():
    """Return the authenticated or pending user ID for forced password changes."""
    if current_user.is_authenticated and current_user.must_change_password:
        return int(current_user.id)
    pending = session.get(PENDING_PASSWORD_CHANGE_USER_ID)
    try:
        return int(pending)
    except (TypeError, ValueError):
        return None


def render_forced_password_change(error=None):
    """Render the login shell with the forced password-change modal open."""
    forced_user = {
        "username": getattr(current_user, "username", None) if current_user.is_authenticated else session.get(PENDING_PASSWORD_CHANGE_USERNAME),
        "display_name": getattr(current_user, "display_name", None) if current_user.is_authenticated else session.get(PENDING_PASSWORD_CHANGE_DISPLAY_NAME),
    }
    return render_template(
        "auth_login.html",
        auth_shell=True,
        force_password_change=True,
        forced_user=forced_user,
        force_password_error=error,
        next_url=None,
    )


def clear_pending_password_change():
    """Remove pending password-change identity from the browser session."""
    session.pop(PENDING_PASSWORD_CHANGE_USER_ID, None)
    session.pop(PENDING_PASSWORD_CHANGE_USERNAME, None)
    session.pop(PENDING_PASSWORD_CHANGE_DISPLAY_NAME, None)


def temporary_password_modal_payload(user, temporary_password):
    """Return modal data for showing a generated temporary password once."""
    return {
        "username": user["username"],
        "display_name": user["display_name"] or user["username"],
        "temporary_password": temporary_password,
    }


def ownership_handoff_candidates(users):
    """Return active non-owner users eligible to receive ownership."""
    return [
        user
        for user in users
        if user["role"] != "owner" and user["is_active"]
    ]


def safe_next_url(value):
    """Return a local redirect path or ``None`` for unsafe values."""
    target = str(value or "").strip()
    if target.startswith("/") and not target.startswith("//"):
        return target
    return None
