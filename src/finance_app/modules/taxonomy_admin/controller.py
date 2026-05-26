"""Flask routes for the taxonomy admin feature."""

from pathlib import Path

from flask import Blueprint, Response, flash, redirect, render_template, request, url_for

from finance_app.core.i18n import gettext
from finance_app.modules.auth.permissions import PERMISSION_MANAGE_TAXONOMY, permission_required
from finance_app.modules.taxonomy_admin.service import (
    build_taxonomy_context,
    create_category_from_form,
    create_tag_from_form,
    delete_category_from_form,
    delete_tag_from_form,
    export_taxonomy_yaml,
    import_taxonomy_yaml_text,
    update_category_from_form,
    update_tag_from_form,
)


taxonomy_admin_bp = Blueprint("taxonomy_admin", __name__)


@taxonomy_admin_bp.route("/taxonomy")
@permission_required(PERMISSION_MANAGE_TAXONOMY)
def taxonomy():
    """Render the taxonomy page."""
    return render_template("taxonomy.html", **build_taxonomy_context())


@taxonomy_admin_bp.route("/taxonomy/export.yml")
@permission_required(PERMISSION_MANAGE_TAXONOMY)
def export_taxonomy():
    """Download category and tag metadata as a YAML taxonomy file."""
    return Response(
        export_taxonomy_yaml(),
        mimetype="application/x-yaml",
        headers={
            "Content-Disposition": "attachment; filename=taxonomy.yml",
        },
    )


@taxonomy_admin_bp.route("/taxonomy/import", methods=["POST"])
@permission_required(PERMISSION_MANAGE_TAXONOMY)
def import_taxonomy():
    """Import category and tag metadata from an uploaded YAML taxonomy file.

    Requires a manage-taxonomy session and a CSRF-protected multipart POST with
    a ``taxonomy_file`` upload. The import updates or creates user-managed
    category and tag metadata, skips built-in categories, flashes the result,
    and redirects back to the taxonomy admin page.
    """
    uploaded_file = request.files.get("taxonomy_file")
    if uploaded_file is None or uploaded_file.filename == "":
        flash(gettext("Choose a YAML file to import."))
        return redirect(url_for("taxonomy_admin.taxonomy"))

    filename = Path(uploaded_file.filename).name
    if not filename.lower().endswith((".yml", ".yaml")):
        flash(gettext("Taxonomy import currently supports YAML files."))
        return redirect(url_for("taxonomy_admin.taxonomy"))

    raw_text = uploaded_file.read().decode("utf-8-sig", errors="replace")
    if not raw_text.strip():
        flash(gettext("The selected taxonomy file is empty."))
        return redirect(url_for("taxonomy_admin.taxonomy"))

    try:
        result = import_taxonomy_yaml_text(raw_text)
    except ValueError as exc:
        flash(gettext(str(exc)))
        return redirect(url_for("taxonomy_admin.taxonomy"))

    message = gettext(
        "Imported {category_count} categories and {tag_count} tags.",
        category_count=result["categories"],
        tag_count=result["tags"],
    )
    if result["skipped_builtin_categories"]:
        message += " " + gettext(
            "Skipped {count} built-in categories.",
            count=result["skipped_builtin_categories"],
        )
    flash(message)
    return redirect(url_for("taxonomy_admin.taxonomy"))


@taxonomy_admin_bp.route("/taxonomy/categories/create", methods=["POST"])
@permission_required(PERMISSION_MANAGE_TAXONOMY)
def create_category():
    """Create category."""
    try:
        category = create_category_from_form(request.form)
        flash(f"Category saved: {category}")
    except ValueError as exc:
        flash(str(exc))
    return redirect(url_for("taxonomy_admin.taxonomy"))


@taxonomy_admin_bp.route("/taxonomy/categories/update", methods=["POST"])
@permission_required(PERMISSION_MANAGE_TAXONOMY)
def update_category():
    """Update category."""
    try:
        category = update_category_from_form(request.form)
        flash(f"Category updated: {category}")
    except ValueError as exc:
        flash(str(exc))
    return redirect(url_for("taxonomy_admin.taxonomy"))


@taxonomy_admin_bp.route("/taxonomy/tags/create", methods=["POST"])
@permission_required(PERMISSION_MANAGE_TAXONOMY)
def create_tag():
    """Create tag."""
    try:
        tag = create_tag_from_form(request.form)
        flash(f"Tag saved: {tag}")
    except ValueError as exc:
        flash(str(exc))
    return redirect(url_for("taxonomy_admin.taxonomy"))


@taxonomy_admin_bp.route("/taxonomy/tags/update", methods=["POST"])
@permission_required(PERMISSION_MANAGE_TAXONOMY)
def update_tag():
    """Update tag."""
    try:
        tag = update_tag_from_form(request.form)
        flash(f"Tag updated: {tag}")
    except ValueError as exc:
        flash(str(exc))
    return redirect(url_for("taxonomy_admin.taxonomy"))


@taxonomy_admin_bp.route("/taxonomy/categories/delete", methods=["POST"])
@permission_required(PERMISSION_MANAGE_TAXONOMY)
def delete_category():
    """Delete category."""
    try:
        category = delete_category_from_form(request.form)
        flash(f"Category deleted: {category}")
    except ValueError as exc:
        flash(str(exc))
    return redirect(url_for("taxonomy_admin.taxonomy"))


@taxonomy_admin_bp.route("/taxonomy/tags/delete", methods=["POST"])
@permission_required(PERMISSION_MANAGE_TAXONOMY)
def delete_tag():
    """Delete tag."""
    try:
        tag = delete_tag_from_form(request.form)
        flash(f"Tag deleted: {tag}")
    except ValueError as exc:
        flash(str(exc))
    return redirect(url_for("taxonomy_admin.taxonomy"))
