"""Flask routes for the taxonomy admin feature."""

from flask import Blueprint, flash, redirect, render_template, request, url_for

from finance_app.modules.taxonomy_admin.service import (
    build_taxonomy_context,
    create_category_from_form,
    create_tag_from_form,
    delete_category_from_form,
    delete_tag_from_form,
    update_category_from_form,
    update_tag_from_form,
)


taxonomy_admin_bp = Blueprint("taxonomy_admin", __name__)


@taxonomy_admin_bp.route("/taxonomy")
def taxonomy():
    """Render the taxonomy page."""
    return render_template("taxonomy.html", **build_taxonomy_context())


@taxonomy_admin_bp.route("/taxonomy/categories/create", methods=["POST"])
def create_category():
    """Create category."""
    try:
        category = create_category_from_form(request.form)
        flash(f"Category saved: {category}")
    except ValueError as exc:
        flash(str(exc))
    return redirect(url_for("taxonomy_admin.taxonomy"))


@taxonomy_admin_bp.route("/taxonomy/categories/update", methods=["POST"])
def update_category():
    """Update category."""
    try:
        category = update_category_from_form(request.form)
        flash(f"Category updated: {category}")
    except ValueError as exc:
        flash(str(exc))
    return redirect(url_for("taxonomy_admin.taxonomy"))


@taxonomy_admin_bp.route("/taxonomy/tags/create", methods=["POST"])
def create_tag():
    """Create tag."""
    try:
        tag = create_tag_from_form(request.form)
        flash(f"Tag saved: {tag}")
    except ValueError as exc:
        flash(str(exc))
    return redirect(url_for("taxonomy_admin.taxonomy"))


@taxonomy_admin_bp.route("/taxonomy/tags/update", methods=["POST"])
def update_tag():
    """Update tag."""
    try:
        tag = update_tag_from_form(request.form)
        flash(f"Tag updated: {tag}")
    except ValueError as exc:
        flash(str(exc))
    return redirect(url_for("taxonomy_admin.taxonomy"))


@taxonomy_admin_bp.route("/taxonomy/categories/delete", methods=["POST"])
def delete_category():
    """Delete category."""
    try:
        category = delete_category_from_form(request.form)
        flash(f"Category deleted: {category}")
    except ValueError as exc:
        flash(str(exc))
    return redirect(url_for("taxonomy_admin.taxonomy"))


@taxonomy_admin_bp.route("/taxonomy/tags/delete", methods=["POST"])
def delete_tag():
    """Delete tag."""
    try:
        tag = delete_tag_from_form(request.form)
        flash(f"Tag deleted: {tag}")
    except ValueError as exc:
        flash(str(exc))
    return redirect(url_for("taxonomy_admin.taxonomy"))
