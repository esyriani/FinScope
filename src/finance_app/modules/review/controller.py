"""Flask routes for the review feature."""

from typing import Any

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask.typing import ResponseReturnValue

from finance_app.background.runner import submit_background_job
from finance_app.core.constants import UNKNOWN_CATEGORY
from finance_app.core.i18n import gettext
from finance_app.database.engine import db_core_transaction
from finance_app.modules.auth.permissions import PERMISSION_EDIT_TRANSACTIONS, permission_required
from finance_app.modules.categories.service import get_category_options, normalize_category
from finance_app.modules.categories.taxonomy import get_tag_options, normalize_tag_names
from finance_app.modules.review.service import (
    apply_review_group_job,
    build_review_context,
    review_group_rows,
    review_merchant_key,
    short_label,
    undo_review_group_job,
)
from finance_app.modules.rules.forms import normalize_rule_keyword, parse_amount_bounds
from finance_app.modules.settings.runtime import get_unknown_category

review_bp = Blueprint("review", __name__)


@review_bp.route("/review")
@permission_required(PERMISSION_EDIT_TRANSACTIONS)
def review() -> str:
    """Render the review page."""
    return render_template("review.html", **build_review_context(request.args))


@review_bp.route("/review/apply", methods=["POST"])
@permission_required(PERMISSION_EDIT_TRANSACTIONS)
def apply_review_group() -> ResponseReturnValue:
    """Apply review group."""
    next_url = review_redirect_target()
    merchant_key = review_merchant_key(request.form.get("merchant_key", ""))
    transaction_id_text = request.form.get("transaction_id", "").strip()
    transaction_id = None
    if transaction_id_text:
        try:
            transaction_id = int(transaction_id_text)
        except ValueError:
            flash(gettext("Review transaction not found."))
            return redirect(next_url)

        if transaction_id <= 0:
            flash(gettext("Review transaction not found."))
            return redirect(next_url)

    try:
        selected_transaction_ids = (
            [] if transaction_id is not None else parse_review_transaction_ids(request.form.getlist("transaction_ids"))
        )
    except ValueError:
        flash(gettext("Review transaction not found."))
        return redirect(next_url)

    if not merchant_key:
        flash(gettext("Review group not found."))
        return redirect(next_url)

    create_rule = request.form.get("create_rule") == "1" and not selected_transaction_ids
    with db_core_transaction() as conn:
        category_options = get_category_options(conn)
        tag_options = get_tag_options(conn)
        unknown_category = get_unknown_category(conn)
        category = normalize_category(request.form.get("category", ""), category_options)
        tags = normalize_tag_names(request.form.getlist("tags"), tag_options)
        group_transaction_ids = (
            {row["id"] for row in review_group_rows(conn, merchant_key, unknown_category)}
            if selected_transaction_ids
            else set()
        )
    rule_keyword = ""
    amount_min = None
    amount_max = None

    if selected_transaction_ids and not set(selected_transaction_ids).issubset(group_transaction_ids):
        flash(gettext("Review transaction not found."))
        return redirect(next_url)

    if (
        not category
        or category not in category_options
        or category == unknown_category
        or category.upper() == UNKNOWN_CATEGORY
    ):
        flash(gettext("Choose a category before applying the review group."))
        return redirect(next_url)

    if create_rule:
        rule_keyword = normalize_rule_keyword(request.form.get("keyword", ""), merchant_key)
        try:
            amount_min, amount_max = parse_amount_bounds(
                request.form.get("amount_min", ""),
                request.form.get("amount_max", ""),
            )
        except ValueError as exc:
            flash(gettext(str(exc)))
            return redirect(next_url)

        if not rule_keyword:
            flash(gettext("Rule keyword is required when saving a rule."))
            return redirect(next_url)

    undo_state: dict[str, Any] = {}
    job_label = (
        f"Review transaction {transaction_id} as {category}"
        if transaction_id
        else (
            f"Review {len(selected_transaction_ids)} transactions as {category}"
            if selected_transaction_ids
            else f"Review {short_label(merchant_key)} as {category}"
        )
    )
    job_kwargs: dict[str, Any] = {}
    if selected_transaction_ids:
        job_kwargs["selected_transaction_ids"] = selected_transaction_ids

    job_id = submit_background_job(
        job_label,
        apply_review_group_job,
        undo_state,
        merchant_key,
        category,
        tags,
        create_rule,
        rule_keyword,
        amount_min,
        amount_max,
        transaction_id,
        undo_handler=undo_review_group_job,
        undo_args=(undo_state,),
        **job_kwargs,
    )

    review_target = "transaction" if transaction_id else "transactions" if selected_transaction_ids else "group"
    flash(
        gettext(
            "Review {target} queued in the background. Track progress on the Jobs page. Job: {job_id}",
            target=gettext(review_target),
            job_id=job_id[:8],
        )
    )
    return redirect(next_url)


def parse_review_transaction_ids(values: list[str]) -> list[int]:
    """Parse selected review transaction ids from submitted form values."""
    transaction_ids: list[int] = []
    seen: set[int] = set()
    for value in values:
        text = str(value or "").strip()
        if not text:
            continue
        try:
            transaction_id = int(text)
        except ValueError as exc:
            raise ValueError("Review transaction not found.") from exc

        if transaction_id <= 0:
            raise ValueError("Review transaction not found.")
        if transaction_id in seen:
            continue
        transaction_ids.append(transaction_id)
        seen.add(transaction_id)
    return transaction_ids


def review_redirect_target() -> str:
    """Render the review redirect target page."""
    target = request.form.get("next", "").strip()
    if target.startswith("/review"):
        return target

    return url_for("review.review")
