"""Flask routes for the rules feature."""

from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from flask import Blueprint, Response, abort, flash, jsonify, redirect, render_template, request, url_for
from flask.typing import ResponseReturnValue

from finance_app.core.i18n import gettext
from finance_app.modules.auth.permissions import PERMISSION_MANAGE_RULES, permission_required
from finance_app.modules.rules import workflow as rules_workflow
from finance_app.modules.rules.import_export import (
    RULE_IMPORT_MODE_ADD,
    RULE_IMPORT_MODES,
    export_rules_csv,
)
from finance_app.modules.rules.listing import build_rules_context

rules_bp = Blueprint("rules", __name__)


@rules_bp.route("/rules")
@permission_required(PERMISSION_MANAGE_RULES)
def rules() -> ResponseReturnValue:
    """Render the rules page."""
    return render_template("rules.html", **build_rules_context(request.args))


@rules_bp.route("/rules/audit")
@permission_required(PERMISSION_MANAGE_RULES)
def audit_rules() -> ResponseReturnValue:
    """Render the read-only rule audit page."""
    context = rules_workflow.build_rule_audit_page_context(request.args)
    return render_template("rules_audit.html", **context)


@rules_bp.route("/rules/audit/overlap/<int:rule_a_id>/<int:rule_b_id>")
@permission_required(PERMISSION_MANAGE_RULES)
def audit_rule_overlap(rule_a_id: int, rule_b_id: int) -> ResponseReturnValue:
    """Render shared transactions for one overlapping rule pair."""
    context = rules_workflow.build_rule_overlap_page_context(rule_a_id, rule_b_id, request.args)
    if context is None:
        abort(404)
    return render_template("rules_audit_overlap.html", **context)


@rules_bp.route("/rules/audit/rule/<int:rule_id>")
@permission_required(PERMISSION_MANAGE_RULES)
def audit_rule_detail(rule_id: int) -> ResponseReturnValue:
    """Render read-only audit diagnostics for one rule."""
    context = rules_workflow.build_rule_detail_page_context(rule_id)
    if context is None:
        abort(404)
    return render_template("rules_audit_rule.html", **context)


@rules_bp.route("/rules/audit/preview", methods=["POST"])
@permission_required(PERMISSION_MANAGE_RULES)
def audit_rule_preview() -> ResponseReturnValue:
    """Render a read-only impact preview for a proposed rule audit action."""
    action = request.form.get("action", "").strip()
    rule_id = request.form.get("rule_id", type=int)
    if rule_id is None and action not in {"apply_all_rules", "create_rule"}:
        abort(400)

    try:
        context = rules_workflow.build_rule_audit_preview_page_context(action, rule_id, request.form)
    except ValueError:
        abort(400)
    if context is None:
        abort(404)
    context["preview_next_url"] = optional_rules_redirect_target()
    return render_template("rules_audit_preview.html", **context)


@rules_bp.route("/rules/create", methods=["POST"])
@permission_required(PERMISSION_MANAGE_RULES)
def add_rule() -> ResponseReturnValue:
    """Create a manual rule from a CSRF-protected POST and return to Rules."""
    next_url = rules_redirect_target()
    try:
        rule_id, keyword = rules_workflow.create_rule_action(request.form)
    except ValueError as exc:
        flash(gettext(str(exc)))
        return redirect(next_url)

    flash(gettext("Rule saved for: {keyword}", keyword=keyword))
    return redirect(rules_url_with_saved_rule(next_url, rule_id, "created"))


@rules_bp.route("/rules/preview", methods=["POST"])
@permission_required(PERMISSION_MANAGE_RULES)
def preview_rule() -> ResponseReturnValue:
    """Preview rule."""
    try:
        result = rules_workflow.preview_rule_action(request.form)
    except ValueError as exc:
        return jsonify({"ok": False, "message": gettext(str(exc)), "match_count": 0, "transactions": []}), 400

    return jsonify(result)


@rules_bp.route("/rules/export.csv")
@permission_required(PERMISSION_MANAGE_RULES)
def export_rules() -> ResponseReturnValue:
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
def import_rules() -> ResponseReturnValue:
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

        filename = Path(uploaded_file.filename or "").name
        if not filename.lower().endswith(".csv"):
            flash(gettext("Rules import currently supports CSV files."))
            return redirect(next_url)

        raw_text = uploaded_file.read().decode("utf-8-sig", errors="replace")

    if not raw_text.strip():
        flash(gettext("The selected rules file is empty."))
        return redirect(next_url)

    if not confirmed:
        try:
            context = rules_workflow.build_rules_import_preview(raw_text, mode, filename)
        except ValueError as exc:
            flash(gettext(str(exc)))
            return redirect(next_url)
        return render_template("rules_import_preview.html", **context)

    try:
        job_id = rules_workflow.queue_rules_import(raw_text, mode, filename)
    except ValueError as exc:
        flash(gettext(str(exc)))
        return redirect(next_url)

    flash(
        gettext(
            "Rules import queued in the background. Track progress on the Processing page. Job: {job_id}",
            job_id=job_id[:8],
        )
    )
    return redirect(next_url)


@rules_bp.route("/rules/<int:rule_id>/update", methods=["POST"])
@permission_required(PERMISSION_MANAGE_RULES)
def update_rule(rule_id: int) -> ResponseReturnValue:
    """Update a rule from a CSRF-protected POST and return to Rules."""
    next_url = rules_redirect_target()
    try:
        rules_workflow.update_rule_action(rule_id, request.form)
    except ValueError as exc:
        flash(gettext(str(exc)))
        return redirect(next_url)

    flash(gettext("Rule updated."))
    return redirect(rules_url_with_saved_rule(next_url, rule_id, "updated"))


@rules_bp.route("/rules/<int:rule_id>/approve", methods=["POST"])
@permission_required(PERMISSION_MANAGE_RULES)
def approve_rule(rule_id: int) -> ResponseReturnValue:
    """Approve an automatic rule without requiring an impact preview.

    Requires a manage-rules session and CSRF-protected POST. Approval only
    changes rule metadata, so it does not need the read-only transaction impact
    preview used by write actions. Returns JSON for fetch callers and otherwise
    flashes a message before redirecting.
    """
    next_url = rules_redirect_target()
    try:
        keyword, changed = rules_workflow.approve_rule_action(rule_id)
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
def apply_rule(rule_id: int) -> ResponseReturnValue:
    """Apply a rule after an impact preview confirmation.

    Requires a valid manage-rules session permission and CSRF-protected POST
    data with ``confirm_preview=1``. The optional ``mode`` field selects the
    safe default ``apply_where_wins`` behavior or the explicit
    ``force_apply_rule`` behavior. Returns JSON for fetch/table actions and
    otherwise flashes a message before redirecting to the rules target.
    """
    next_url = rules_redirect_target()
    result = rules_workflow.apply_rule_action(
        rule_id,
        request.form.get("confirm_preview") == "1",
        request.form.get("mode", "apply_where_wins"),
    )
    message = gettext(result["message"], **result.get("params", {}))
    if not result["ok"]:
        if wants_json_response():
            return jsonify({"ok": False, "message": message}), result["status"]
        flash(message)
        return redirect(next_url)

    if wants_json_response():
        return jsonify(
            {
                "ok": True,
                "action": "apply",
                "rule_id": rule_id,
                "mode": result["mode"],
                "updated_count": result["updated_count"],
                "message": message,
            }
        )

    flash(message)
    return redirect(next_url)


@rules_bp.route("/rules/apply-all", methods=["POST"])
@permission_required(PERMISSION_MANAGE_RULES)
def apply_all_rules() -> ResponseReturnValue:
    """Queue apply-all rules after an impact preview confirmation.

    Requires a manage-rules session and CSRF-protected POST with
    ``confirm_preview=1``. Unconfirmed requests are redirected without queuing
    the background job so bulk rule writes follow the read-only preview flow.
    """
    next_url = rules_redirect_target()
    result = rules_workflow.queue_apply_all_rules(request.form.get("confirm_preview") == "1")
    if not result["ok"]:
        flash(gettext(result["message"]))
        return redirect(next_url)

    flash(
        gettext(
            "Applying all rules in the background. Track progress on the Processing page. Job: {job_id}",
            job_id=result["job_id"][:8],
        )
    )
    return redirect(next_url)


@rules_bp.route("/rules/<int:rule_id>/delete", methods=["POST"])
@permission_required(PERMISSION_MANAGE_RULES)
def delete_rule(rule_id: int) -> ResponseReturnValue:
    """Delete a rule after preview unless it has no transaction references.

    Unconfirmed POSTs are allowed only when no existing transaction stores the
    rule as a category assignment or rule-applied tag. Applied rules still
    require ``confirm_preview=1`` so the user can inspect the impact before the
    rule is removed. Returns JSON for fetch/table actions.
    """
    next_url = rules_redirect_target()
    result = rules_workflow.delete_rule_action(rule_id, request.form.get("confirm_preview") == "1")
    message = gettext(result["message"])
    if not result["ok"] and result["status"] == 400:
        if wants_json_response():
            return jsonify({"ok": False, "message": message}), 400
        flash(message)
        return redirect(next_url)

    if wants_json_response():
        return (
            jsonify(
                {
                    "ok": result["ok"],
                    "action": "delete",
                    "rule_id": rule_id,
                    "message": message,
                }
            ),
            result["status"],
        )

    flash(message)
    return redirect(next_url)


def wants_json_response() -> bool:
    """Return whether a route should respond with JSON for a table action."""
    return request.headers.get("X-Requested-With") == "fetch"


def rules_redirect_target() -> str:
    """Return a safe rules redirect target from submitted form data."""
    target = request.form.get("next", "").strip()
    if target.startswith("/rules"):
        return target

    return url_for("rules.rules")


def optional_rules_redirect_target() -> str:
    """Return a submitted rules URL when present, otherwise an empty string."""
    target = request.form.get("next", "").strip()
    return target if target.startswith("/rules") else ""


def rules_url_with_saved_rule(target: str, rule_id: int, action: str) -> str:
    """Return target URL with a short-lived saved-rule follow-up marker."""
    parts = urlsplit(target)
    query_params = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if key not in {"saved_rule_id", "saved_rule_action"}
    ]
    query_params.extend(
        [
            ("saved_rule_id", str(rule_id)),
            ("saved_rule_action", action),
        ]
    )
    return urlunsplit(("", "", parts.path or url_for("rules.rules"), urlencode(query_params, doseq=True), ""))
