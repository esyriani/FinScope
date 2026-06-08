"""Flask routes for the rules feature."""

from pathlib import Path

from flask import Blueprint, Response, abort, flash, jsonify, redirect, render_template, request, url_for

from finance_app.background.runner import submit_background_job
from finance_app.core.config import settings
from finance_app.core.i18n import gettext
from finance_app.database.engine import db_core_transaction
from finance_app.modules.auth.permissions import PERMISSION_MANAGE_RULES, permission_required
from finance_app.modules.rules.audit_presenter import (
    build_rule_audit_context,
    build_rule_change_preview_context,
    build_rule_detail_context,
    build_rule_import_preview_context,
    build_rule_overlap_detail_context,
)
from finance_app.modules.rules.engine import (
    apply_all_rules_job,
    apply_rule_where_it_wins_to_transactions,
    apply_single_rule_to_transactions,
    preview_rule_matches,
    undo_apply_all_rules_job,
)
from finance_app.modules.rules.import_export import (
    RULE_IMPORT_MODE_ADD,
    RULE_IMPORT_MODES,
    export_rules_csv,
    import_rules_job,
    undo_import_rules_job,
)
from finance_app.modules.rules.listing import build_rules_context
from finance_app.modules.rules.service import (
    approve_automatic_rule,
    count_rule_transaction_references,
    create_rule_from_form,
    get_rule_for_apply,
    preview_rule_from_form,
    update_rule_from_form,
)
from finance_app.modules.rules.service import (
    delete_rule as delete_rule_record,
)
from finance_app.modules.settings.runtime import get_int_setting

rules_bp = Blueprint("rules", __name__)


@rules_bp.route("/rules")
@permission_required(PERMISSION_MANAGE_RULES)
def rules():
    """Render the rules page."""
    return render_template("rules.html", **build_rules_context(request.args))


@rules_bp.route("/rules/audit")
@permission_required(PERMISSION_MANAGE_RULES)
def audit_rules():
    """Render the read-only rule audit page."""
    with db_core_transaction() as conn:
        context = build_rule_audit_context(
            conn,
            request.args,
            transaction_limit=rule_audit_transaction_limit(conn),
        )
    return render_template("rules_audit.html", **context)


@rules_bp.route("/rules/audit/overlap/<int:rule_a_id>/<int:rule_b_id>")
@permission_required(PERMISSION_MANAGE_RULES)
def audit_rule_overlap(rule_a_id, rule_b_id):
    """Render shared transactions for one overlapping rule pair."""
    with db_core_transaction() as conn:
        context = build_rule_overlap_detail_context(
            conn,
            rule_a_id,
            rule_b_id,
            request.args,
            transaction_limit=rule_audit_transaction_limit(conn),
        )
    if context is None:
        abort(404)
    return render_template("rules_audit_overlap.html", **context)


@rules_bp.route("/rules/audit/rule/<int:rule_id>")
@permission_required(PERMISSION_MANAGE_RULES)
def audit_rule_detail(rule_id):
    """Render read-only audit diagnostics for one rule."""
    with db_core_transaction() as conn:
        context = build_rule_detail_context(
            conn,
            rule_id,
            transaction_limit=rule_audit_transaction_limit(conn),
        )
    if context is None:
        abort(404)
    return render_template("rules_audit_rule.html", **context)


@rules_bp.route("/rules/audit/preview", methods=["POST"])
@permission_required(PERMISSION_MANAGE_RULES)
def audit_rule_preview():
    """Render a read-only impact preview for a proposed rule audit action."""
    action = request.form.get("action", "").strip()
    rule_id = request.form.get("rule_id", type=int)
    if rule_id is None and action not in {"apply_all_rules", "create_rule"}:
        abort(400)

    try:
        with db_core_transaction() as conn:
            proposed_rule = (
                preview_rule_from_form(conn, request.form) if action in {"create_rule", "edit_rule"} else None
            )
            context = build_rule_change_preview_context(
                conn,
                action,
                rule_id,
                proposed_rule=proposed_rule,
                transaction_limit=rule_audit_transaction_limit(conn),
            )
    except ValueError:
        abort(400)
    if context is None:
        abort(404)
    return render_template("rules_audit_preview.html", **context)


@rules_bp.route("/rules/create", methods=["POST"])
@permission_required(PERMISSION_MANAGE_RULES)
def add_rule():
    """Create a manual rule after an impact preview confirmation.

    Requires a manage-rules session and CSRF-protected POST with
    ``confirm_preview=1``. Unconfirmed requests are redirected without saving
    so new rules follow the same preview workflow as rule edits.
    """
    next_url = rules_redirect_target()
    if request.form.get("confirm_preview") != "1":
        flash(gettext("Preview creation before saving a rule."))
        return redirect(next_url)

    try:
        with db_core_transaction() as conn:
            keyword = create_rule_from_form(conn, request.form)
    except ValueError as exc:
        flash(gettext(str(exc)))
        return redirect(next_url)

    flash(gettext("Rule saved for: {keyword}", keyword=keyword))
    return redirect(next_url)


@rules_bp.route("/rules/preview", methods=["POST"])
@permission_required(PERMISSION_MANAGE_RULES)
def preview_rule():
    """Preview rule."""
    try:
        with db_core_transaction() as conn:
            rule = preview_rule_from_form(conn, request.form)
            preview_limit = get_int_setting(conn, "rule_preview_limit", settings.default_rule_preview_limit)
            match_count, sample = preview_rule_matches(conn, rule, limit=preview_limit)
    except ValueError as exc:
        return jsonify({"ok": False, "message": gettext(str(exc)), "match_count": 0, "transactions": []}), 400

    return jsonify(
        {
            "ok": True,
            "keyword": rule["keyword"],
            "category": rule["category"],
            "match_count": match_count,
            "transactions": sample,
        }
    )


@rules_bp.route("/rules/export.csv")
@permission_required(PERMISSION_MANAGE_RULES)
def export_rules():
    """Export rules."""
    output = export_rules_csv()

    return Response(
        output,
        mimetype="text/csv",
        headers={
            "Content-Disposition": "attachment; filename=category-rules.csv",
        },
    )


@rules_bp.route("/rules/import", methods=["POST"])
@permission_required(PERMISSION_MANAGE_RULES)
def import_rules():
    """Preview or queue a rules CSV import.

    Initial POSTs with an uploaded CSV render a read-only import preview.
    Confirmed POSTs with ``confirm_preview=1`` queue the background import job
    using the previewed CSV text and selected mode.
    """
    next_url = rules_redirect_target()
    mode = request.form.get("mode", RULE_IMPORT_MODE_ADD).strip()

    if mode not in RULE_IMPORT_MODES:
        flash(gettext("Choose whether to add new rules or override existing rules."))
        return redirect(next_url)

    confirmed = request.form.get("confirm_preview") == "1"
    if confirmed:
        filename = Path(request.form.get("filename") or "rules.csv").name
        raw_text = request.form.get("raw_text", "")
    else:
        uploaded_file = request.files.get("rules_file")
        if uploaded_file is None or uploaded_file.filename == "":
            flash(gettext("Choose a CSV file to import."))
            return redirect(next_url)

        filename = Path(uploaded_file.filename).name
        if not filename.lower().endswith(".csv"):
            flash(gettext("Rules import currently supports CSV files."))
            return redirect(next_url)

        raw_text = uploaded_file.read().decode("utf-8-sig", errors="replace")

    if not raw_text.strip():
        flash(gettext("The selected rules file is empty."))
        return redirect(next_url)

    if not confirmed:
        try:
            with db_core_transaction() as conn:
                context = build_rule_import_preview_context(
                    conn,
                    raw_text,
                    mode,
                    filename,
                    transaction_limit=rule_audit_transaction_limit(conn),
                )
        except ValueError as exc:
            flash(gettext(str(exc)))
            return redirect(next_url)
        return render_template("rules_import_preview.html", **context)

    try:
        with db_core_transaction() as conn:
            build_rule_import_preview_context(
                conn,
                raw_text,
                mode,
                filename,
                transaction_limit=rule_audit_transaction_limit(conn),
            )
    except ValueError as exc:
        flash(gettext(str(exc)))
        return redirect(next_url)

    undo_state = {}
    job_id = submit_background_job(
        f"Import rules from {filename}",
        import_rules_job,
        raw_text,
        mode,
        undo_state,
        undo_handler=undo_import_rules_job,
        undo_args=(undo_state,),
    )

    flash(
        gettext(
            "Rules import queued in the background. Track progress on the Jobs page. Job: {job_id}",
            job_id=job_id[:8],
        )
    )
    return redirect(next_url)


@rules_bp.route("/rules/<int:rule_id>/update", methods=["POST"])
@permission_required(PERMISSION_MANAGE_RULES)
def update_rule(rule_id):
    """Update a rule after an impact preview confirmation.

    Requires a CSRF-protected POST with ``confirm_preview=1`` and rule form
    fields produced by the preview page. Unconfirmed requests are redirected
    without mutating the rule so edits follow the read-only preview workflow.
    """
    next_url = rules_redirect_target()
    if request.form.get("confirm_preview") != "1":
        flash(gettext("Preview changes before updating a rule."))
        return redirect(next_url)

    try:
        with db_core_transaction() as conn:
            update_rule_from_form(conn, rule_id, request.form)
    except ValueError as exc:
        flash(gettext(str(exc)))
        return redirect(next_url)

    flash(gettext("Rule updated."))
    return redirect(next_url)


@rules_bp.route("/rules/<int:rule_id>/approve", methods=["POST"])
@permission_required(PERMISSION_MANAGE_RULES)
def approve_rule(rule_id):
    """Approve an automatic rule without requiring an impact preview.

    Requires a manage-rules session and CSRF-protected POST. Approval only
    changes rule metadata, so it does not need the read-only transaction impact
    preview used by write actions. Returns JSON for fetch callers and otherwise
    flashes a message before redirecting.
    """
    next_url = rules_redirect_target()
    try:
        with db_core_transaction() as conn:
            keyword, changed = approve_automatic_rule(conn, rule_id)
    except ValueError as exc:
        if wants_json_response():
            return jsonify({"ok": False, "message": gettext(str(exc))}), 400
        flash(gettext(str(exc)))
        return redirect(next_url)

    message = gettext("Rule approved: {keyword}", keyword=keyword) if changed else gettext("Rule already approved.")
    if wants_json_response():
        return jsonify(
            {
                "ok": True,
                "action": "approve",
                "rule_id": rule_id,
                "keyword": keyword,
                "changed": changed,
                "message": message,
                "approval_label": gettext("Approved"),
                "approval_badge_class": "text-bg-success",
            }
        )

    flash(message)
    return redirect(next_url)


@rules_bp.route("/rules/<int:rule_id>/apply", methods=["POST"])
@permission_required(PERMISSION_MANAGE_RULES)
def apply_rule(rule_id):
    """Apply a rule after an impact preview confirmation.

    Requires a valid manage-rules session permission and CSRF-protected POST
    data with ``confirm_preview=1``. The optional ``mode`` field selects the
    safe default ``apply_where_wins`` behavior or the explicit
    ``force_apply_rule`` behavior. Returns JSON for fetch/table actions and
    otherwise flashes a message before redirecting to the rules target.
    """
    next_url = rules_redirect_target()
    with db_core_transaction() as conn:
        rule = get_rule_for_apply(conn, rule_id)

        if rule is None:
            message = gettext("Rule not found.")
            if wants_json_response():
                return jsonify({"ok": False, "message": message}), 404
            flash(message)
            return redirect(next_url)

        if request.form.get("confirm_preview") != "1":
            message = gettext("Preview apply before applying a rule.")
            if wants_json_response():
                return jsonify({"ok": False, "message": message}), 400
            flash(message)
            return redirect(next_url)

        mode = request.form.get("mode", "apply_where_wins").strip() or "apply_where_wins"
        if mode == "force_apply_rule":
            updated_count = apply_single_rule_to_transactions(conn, rule)
            message = gettext("Rule force-applied to {count} existing transactions.", count=updated_count)
        elif mode == "apply_where_wins":
            updated_count = apply_rule_where_it_wins_to_transactions(conn, rule)
            message = gettext("Rule applied where it wins to {count} existing transactions.", count=updated_count)
        else:
            message = gettext("Unsupported apply mode.")
            if wants_json_response():
                return jsonify({"ok": False, "message": message}), 400
            flash(message)
            return redirect(next_url)

    if wants_json_response():
        return jsonify(
            {
                "ok": True,
                "action": "apply",
                "rule_id": rule_id,
                "mode": mode,
                "updated_count": updated_count,
                "message": message,
            }
        )

    flash(message)
    return redirect(next_url)


@rules_bp.route("/rules/apply-all", methods=["POST"])
@permission_required(PERMISSION_MANAGE_RULES)
def apply_all_rules():
    """Queue apply-all rules after an impact preview confirmation.

    Requires a manage-rules session and CSRF-protected POST with
    ``confirm_preview=1``. Unconfirmed requests are redirected without queuing
    the background job so bulk rule writes follow the read-only preview flow.
    """
    next_url = rules_redirect_target()
    if request.form.get("confirm_preview") != "1":
        flash(gettext("Preview apply before applying all rules."))
        return redirect(next_url)

    undo_state = {}
    job_id = submit_background_job(
        "Apply all category rules",
        apply_all_rules_job,
        undo_state,
        undo_handler=undo_apply_all_rules_job,
        undo_args=(undo_state,),
    )

    flash(
        gettext(
            "Applying all rules in the background. Track progress on the Jobs page. Job: {job_id}",
            job_id=job_id[:8],
        )
    )
    return redirect(next_url)


@rules_bp.route("/rules/<int:rule_id>/delete", methods=["POST"])
@permission_required(PERMISSION_MANAGE_RULES)
def delete_rule(rule_id):
    """Delete a rule after preview unless it has no transaction references.

    Unconfirmed POSTs are allowed only when no existing transaction stores the
    rule as a category assignment or rule-applied tag. Applied rules still
    require ``confirm_preview=1`` so the user can inspect the impact before the
    rule is removed. Returns JSON for fetch/table actions.
    """
    next_url = rules_redirect_target()
    with db_core_transaction() as conn:
        confirmed = request.form.get("confirm_preview") == "1"
        reference_count = count_rule_transaction_references(conn, rule_id)
        if not confirmed and reference_count:
            message = gettext("Preview deletion before deleting a rule.")
            if wants_json_response():
                return jsonify({"ok": False, "message": message}), 400
            flash(message)
            return redirect(next_url)

        deleted = delete_rule_record(conn, rule_id)

    message = gettext("Rule deleted.") if deleted else gettext("Rule not found.")
    if wants_json_response():
        status = 200 if deleted else 404
        return (
            jsonify(
                {
                    "ok": deleted,
                    "action": "delete",
                    "rule_id": rule_id,
                    "message": message,
                }
            ),
            status,
        )

    flash(message)
    return redirect(next_url)


def wants_json_response():
    """Return whether a route should respond with JSON for a table action."""
    return request.headers.get("X-Requested-With") == "fetch"


def rule_audit_transaction_limit(conn):
    """Return the configured newest-transaction cap for rule audit analysis."""
    return get_int_setting(
        conn,
        "rule_audit_transaction_limit",
        settings.default_rule_audit_transaction_limit,
    )


def rules_redirect_target():
    """Render the rules redirect target page."""
    target = request.form.get("next", "").strip()
    if target.startswith("/rules"):
        return target

    return url_for("rules.rules")
