"""Flask routes for the rules feature."""

from pathlib import Path

from flask import Blueprint, Response, flash, jsonify, redirect, render_template, request, url_for

from finance_app.background.runner import submit_background_job
from finance_app.core.config import settings
from finance_app.database.engine import db_core_transaction
from finance_app.modules.rules.engine import (
    apply_all_rules_job,
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
from finance_app.modules.settings.runtime import get_int_setting
from finance_app.modules.rules.service import (
    approve_automatic_rule,
    create_rule_from_form,
    delete_rule as delete_rule_record,
    get_rule_for_apply,
    preview_rule_from_form,
    update_rule_from_form,
)


rules_bp = Blueprint("rules", __name__)


@rules_bp.route("/rules")
def rules():
    """Render the rules page."""
    return render_template("rules.html", **build_rules_context(request.args))


@rules_bp.route("/rules/create", methods=["POST"])
def add_rule():
    """Add rule."""
    next_url = rules_redirect_target()
    try:
        with db_core_transaction() as conn:
            keyword = create_rule_from_form(conn, request.form)
    except ValueError as exc:
        flash(str(exc))
        return redirect(next_url)

    flash(f"Rule saved for: {keyword}")
    return redirect(next_url)


@rules_bp.route("/rules/preview", methods=["POST"])
def preview_rule():
    """Preview rule."""
    try:
        with db_core_transaction() as conn:
            rule = preview_rule_from_form(conn, request.form)
            preview_limit = get_int_setting(conn, "rule_preview_limit", settings.default_rule_preview_limit)
            match_count, sample = preview_rule_matches(conn, rule, limit=preview_limit)
    except ValueError as exc:
        return jsonify({"ok": False, "message": str(exc), "match_count": 0, "transactions": []}), 400

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
def import_rules():
    """Import rules."""
    next_url = rules_redirect_target()
    uploaded_file = request.files.get("rules_file")
    mode = request.form.get("mode", RULE_IMPORT_MODE_ADD).strip()

    if mode not in RULE_IMPORT_MODES:
        flash("Choose whether to add new rules or override existing rules.")
        return redirect(next_url)

    if uploaded_file is None or uploaded_file.filename == "":
        flash("Choose a CSV file to import.")
        return redirect(next_url)

    filename = Path(uploaded_file.filename).name
    if not filename.lower().endswith(".csv"):
        flash("Rules import currently supports CSV files.")
        return redirect(next_url)

    raw_text = uploaded_file.read().decode("utf-8-sig", errors="replace")
    if not raw_text.strip():
        flash("The selected rules file is empty.")
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
        "Rules import queued in the background. "
        f"Track progress on the Jobs page. Job: {job_id[:8]}"
    )
    return redirect(next_url)


@rules_bp.route("/rules/<int:rule_id>/update", methods=["POST"])
def update_rule(rule_id):
    """Update rule."""
    next_url = rules_redirect_target()
    try:
        with db_core_transaction() as conn:
            update_rule_from_form(conn, rule_id, request.form)
    except ValueError as exc:
        flash(str(exc))
        return redirect(next_url)

    flash("Rule updated.")
    return redirect(next_url)


@rules_bp.route("/rules/<int:rule_id>/approve", methods=["POST"])
def approve_rule(rule_id):
    """Approve an automatic rule and return JSON for table actions."""
    next_url = rules_redirect_target()
    try:
        with db_core_transaction() as conn:
            keyword, changed = approve_automatic_rule(conn, rule_id)
    except ValueError as exc:
        if wants_json_response():
            return jsonify({"ok": False, "message": str(exc)}), 400
        flash(str(exc))
        return redirect(next_url)

    message = f"Rule approved: {keyword}" if changed else "Rule already approved."
    if wants_json_response():
        return jsonify(
            {
                "ok": True,
                "action": "approve",
                "rule_id": rule_id,
                "keyword": keyword,
                "changed": changed,
                "message": message,
                "approval_label": "Approved",
                "approval_badge_class": "text-bg-success",
            }
        )

    flash(message)
    return redirect(next_url)


@rules_bp.route("/rules/<int:rule_id>/apply", methods=["POST"])
def apply_rule(rule_id):
    """Apply a rule and return JSON for table actions."""
    next_url = rules_redirect_target()
    with db_core_transaction() as conn:
        rule = get_rule_for_apply(conn, rule_id)

        if rule is None:
            if wants_json_response():
                return jsonify({"ok": False, "message": "Rule not found."}), 404
            flash("Rule not found.")
            return redirect(next_url)

        updated_count = apply_single_rule_to_transactions(conn, rule)

    message = f"Rule applied to {updated_count} existing transactions."
    if wants_json_response():
        return jsonify(
            {
                "ok": True,
                "action": "apply",
                "rule_id": rule_id,
                "updated_count": updated_count,
                "message": message,
            }
        )

    flash(message)
    return redirect(next_url)


@rules_bp.route("/rules/apply-all", methods=["POST"])
def apply_all_rules():
    """Apply all rules."""
    next_url = rules_redirect_target()
    undo_state = {}
    job_id = submit_background_job(
        "Apply all category rules",
        apply_all_rules_job,
        undo_state,
        undo_handler=undo_apply_all_rules_job,
        undo_args=(undo_state,),
    )

    flash(
        "Applying all rules in the background. "
        f"Track progress on the Jobs page. Job: {job_id[:8]}"
    )
    return redirect(next_url)


@rules_bp.route("/rules/<int:rule_id>/delete", methods=["POST"])
def delete_rule(rule_id):
    """Delete a rule and return JSON for table actions."""
    next_url = rules_redirect_target()
    with db_core_transaction() as conn:
        deleted = delete_rule_record(conn, rule_id)

    message = "Rule deleted." if deleted else "Rule not found."
    if wants_json_response():
        status = 200 if deleted else 404
        return jsonify(
            {
                "ok": deleted,
                "action": "delete",
                "rule_id": rule_id,
                "message": message,
            }
        ), status

    flash(message)
    return redirect(next_url)


def wants_json_response():
    """Return whether a route should respond with JSON for a table action."""
    return request.headers.get("X-Requested-With") == "fetch"


def rules_redirect_target():
    """Render the rules redirect target page."""
    target = request.form.get("next", "").strip()
    if target.startswith("/rules"):
        return target

    return url_for("rules.rules")
