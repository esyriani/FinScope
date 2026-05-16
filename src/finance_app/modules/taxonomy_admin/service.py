"""Application orchestration for the taxonomy admin feature."""

from sqlalchemy import delete, func, or_, select, update

from finance_app.database.engine import db_core_transaction
from finance_app.database.tables import (
    categories as categories_table,
    category_rule_tags as category_rule_tags_table,
    category_rules as category_rules_table,
    tags as tags_table,
    transaction_tags as transaction_tags_table,
    transactions as transactions_table,
)
from finance_app.modules.categories.repository import rename_category
from finance_app.modules.categories.taxonomy import (
    clean_color,
    tag_color_for_name,
    upsert_category_metadata,
    upsert_tag_metadata,
)
from finance_app.modules.taxonomy_admin.forms import (
    parse_category_form,
    parse_required_int,
    parse_tag_form,
)


def build_taxonomy_context():
    """Build taxonomy context."""
    with db_core_transaction() as conn:
        return {
            "categories": fetch_category_rows(conn),
            "tags": fetch_tag_rows(conn),
        }


def create_category_from_form(form):
    """Create category from form."""
    values = parse_category_form(form)
    with db_core_transaction() as conn:
        category = upsert_category_metadata(
            conn,
            values["name"],
            values["description"],
            values["instruction"],
        )
        return category


def update_category_from_form(form):
    """Update category from form."""
    values = parse_category_form(form)
    category_id = values["id"]
    if category_id is None:
        raise ValueError("Category was not found.")

    with db_core_transaction() as conn:
        current = fetch_category_by_id(conn, category_id)
        if current is None:
            raise ValueError("Category was not found.")

        if current["name"] != values["name"]:
            renamed = rename_category(conn, current["name"], values["name"])
            if not renamed:
                raise ValueError("Choose a unique category name.")

        conn.execute(
            update(categories_table)
            .where(categories_table.c.id == category_id)
            .values(
                description=values["description"],
                instruction=values["instruction"],
            )
        )
        return values["name"]


def create_tag_from_form(form):
    """Create tag from form."""
    values = parse_tag_form(form)
    with db_core_transaction() as conn:
        tag = upsert_tag_metadata(
            conn,
            values["name"],
            values["description"],
            values["instruction"],
            values["color"],
        )
        return tag


def update_tag_from_form(form):
    """Update tag from form."""
    values = parse_tag_form(form)
    tag_id = values["id"]
    if tag_id is None:
        raise ValueError("Tag was not found.")

    with db_core_transaction() as conn:
        if fetch_tag_by_id(conn, tag_id) is None:
            raise ValueError("Tag was not found.")

        existing = conn.execute(
            select(tags_table.c.id).where(
                tags_table.c.name == values["name"],
                tags_table.c.id != tag_id,
            )
        ).fetchone()
        if existing:
            raise ValueError("Choose a unique tag name.")

        conn.execute(
            update(tags_table)
            .where(tags_table.c.id == tag_id)
            .values(
                name=values["name"],
                description=values["description"],
                instruction=values["instruction"],
                color=clean_color(values["color"]) or tag_color_for_name(values["name"]),
            ),
        )
        return values["name"]


def delete_tag_from_form(form):
    """Delete tag from form."""
    tag_id = parse_required_int(form.get("tag_id"), "Tag")
    with db_core_transaction() as conn:
        tag = fetch_tag_by_id(conn, tag_id)
        if tag is None:
            raise ValueError("Tag was not found.")

        usage = fetch_tag_usage(conn, tag_id)
        if usage["transaction_count"] or usage["rule_count"]:
            raise ValueError("Only unused tags can be deleted.")

        conn.execute(delete(tags_table).where(tags_table.c.id == tag_id))
        return tag["name"]


def delete_category_from_form(form):
    """Delete category from form."""
    category_id = parse_required_int(form.get("category_id"), "Category")
    with db_core_transaction() as conn:
        category = fetch_category_by_id(conn, category_id)
        if category is None:
            raise ValueError("Category was not found.")

        usage = fetch_category_usage(conn, category_id, category["name"])
        if usage["transaction_count"] or usage["rule_count"]:
            raise ValueError("Only unused categories can be deleted.")

        conn.execute(delete(categories_table).where(categories_table.c.id == category_id))
        return category["name"]


def fetch_category_rows(conn):
    """Fetch category rows."""
    transaction_count = (
        select(func.count())
        .select_from(transactions_table)
        .where(
            or_(
                transactions_table.c.category_id == categories_table.c.id,
                transactions_table.c.category == categories_table.c.name,
            )
        )
        .correlate(categories_table)
        .scalar_subquery()
    )
    rule_count = (
        select(func.count())
        .select_from(category_rules_table)
        .where(
            or_(
                category_rules_table.c.category_id == categories_table.c.id,
                category_rules_table.c.category == categories_table.c.name,
            )
        )
        .correlate(categories_table)
        .scalar_subquery()
    )
    return conn.execute(
        select(
            categories_table.c.id,
            categories_table.c.name,
            func.coalesce(categories_table.c.description, "").label("description"),
            func.coalesce(categories_table.c.instruction, "").label("instruction"),
            transaction_count.label("transaction_count"),
            rule_count.label("rule_count"),
        ).order_by(func.lower(categories_table.c.name), categories_table.c.name)
    ).mappings().fetchall()


def fetch_tag_rows(conn):
    """Fetch tag rows."""
    transaction_count = (
        select(func.count())
        .select_from(transaction_tags_table)
        .where(transaction_tags_table.c.tag_id == tags_table.c.id)
        .correlate(tags_table)
        .scalar_subquery()
    )
    rule_count = (
        select(func.count())
        .select_from(category_rule_tags_table)
        .where(category_rule_tags_table.c.tag_id == tags_table.c.id)
        .correlate(tags_table)
        .scalar_subquery()
    )
    rows = conn.execute(
        select(
            tags_table.c.id,
            tags_table.c.name,
            func.coalesce(tags_table.c.description, "").label("description"),
            func.coalesce(tags_table.c.instruction, "").label("instruction"),
            tags_table.c.color,
            transaction_count.label("transaction_count"),
            rule_count.label("rule_count"),
        ).order_by(func.lower(tags_table.c.name), tags_table.c.name)
    ).mappings().fetchall()

    return [
        {
            **dict(row),
            "color": clean_color(row["color"]) or tag_color_for_name(row["name"]),
        }
        for row in rows
    ]


def fetch_category_by_id(conn, category_id):
    """Fetch category by ID."""
    return conn.execute(
        select(
            categories_table.c.id,
            categories_table.c.name,
            categories_table.c.description,
            categories_table.c.instruction,
        ).where(categories_table.c.id == category_id)
    ).mappings().fetchone()


def fetch_tag_by_id(conn, tag_id):
    """Fetch tag by ID."""
    return conn.execute(
        select(
            tags_table.c.id,
            tags_table.c.name,
            tags_table.c.description,
            tags_table.c.instruction,
            tags_table.c.color,
        ).where(tags_table.c.id == tag_id)
    ).mappings().fetchone()


def fetch_tag_usage(conn, tag_id):
    """Fetch tag usage."""
    return {
        "transaction_count": conn.execute(
            select(func.count())
            .select_from(transaction_tags_table)
            .where(transaction_tags_table.c.tag_id == tag_id)
        ).scalar_one(),
        "rule_count": conn.execute(
            select(func.count())
            .select_from(category_rule_tags_table)
            .where(category_rule_tags_table.c.tag_id == tag_id)
        ).scalar_one(),
    }


def fetch_category_usage(conn, category_id, category_name):
    """Fetch category usage."""
    return {
        "transaction_count": conn.execute(
            select(func.count())
            .select_from(transactions_table)
            .where(
                or_(
                    transactions_table.c.category_id == category_id,
                    transactions_table.c.category == category_name,
                )
            )
        ).scalar_one(),
        "rule_count": conn.execute(
            select(func.count())
            .select_from(category_rules_table)
            .where(
                or_(
                    category_rules_table.c.category_id == category_id,
                    category_rules_table.c.category == category_name,
                )
            )
        ).scalar_one(),
    }
