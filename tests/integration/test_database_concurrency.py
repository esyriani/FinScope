"""Tests for repository conflict fallbacks during race-like writes.

Exercises SQLAlchemy Core repository helpers when another writer creates the
same logical row between the helper's preselect and insert attempt.
"""

from sqlalchemy import func, insert, select

from finance_app.database.tables import (
    categories as categories_table,
    merchant_aliases as merchant_aliases_table,
    merchants as merchants_table,
    tags as tags_table,
)
from finance_app.modules.categories import taxonomy as category_taxonomy
from finance_app.modules.merchants import repository as merchant_repository


def test_concurrent_merchant_get_or_create_reselects_existing_row(db_conn, monkeypatch):
    """Verify merchant creation tolerates a unique conflict and reselects the row."""
    real_insert_or_select = merchant_repository.insert_or_select_unique_row
    injected = {"merchant": False}

    def simulate_racing_insert(conn, insert_statement, select_statement):
        """Insert the merchant just before the repository insert is attempted."""
        if not injected["merchant"] and insert_statement.table is merchants_table:
            injected["merchant"] = True
            conn.execute(
                insert(merchants_table).values(
                    canonical_key="RACE MERCHANT",
                    system_name="RACE MERCHANT",
                    display_name="Race Merchant",
                    display_name_source="system",
                    active=1,
                )
            )
        return real_insert_or_select(conn, insert_statement, select_statement)

    monkeypatch.setattr(
        merchant_repository,
        "insert_or_select_unique_row",
        simulate_racing_insert,
    )

    merchant = merchant_repository.get_or_create_merchant(
        db_conn,
        "RACE MERCHANT",
        display_name="Race Merchant",
        alias_key="RACE STORE",
    )

    merchant_count = db_conn.execute(
        select(func.count())
        .select_from(merchants_table)
        .where(merchants_table.c.canonical_key == "RACE MERCHANT")
    ).scalar_one()
    alias_rows = db_conn.execute(
        select(merchant_aliases_table.c.alias_key, merchant_aliases_table.c.merchant_id)
        .where(merchant_aliases_table.c.merchant_id == merchant["id"])
        .order_by(merchant_aliases_table.c.alias_key)
    ).fetchall()

    assert injected["merchant"] is True
    assert merchant_count == 1
    assert [(row.alias_key, row.merchant_id) for row in alias_rows] == [
        ("RACE MERCHANT", merchant["id"]),
        ("RACE STORE", merchant["id"]),
    ]


def test_concurrent_category_and_tag_metadata_create_handles_unique_conflicts(db_conn, monkeypatch):
    """Verify taxonomy metadata helpers reselect rows created by racing writers."""
    real_insert_or_select = category_taxonomy.insert_or_select_unique_row
    injected = {"category": False, "tag": False}

    def simulate_racing_insert(conn, insert_statement, select_statement):
        """Insert taxonomy rows just before each helper insert is attempted."""
        if not injected["category"] and insert_statement.table is categories_table:
            injected["category"] = True
            conn.execute(
                insert(categories_table).values(
                    name="Race Category",
                    description="Inserted by another writer",
                    instruction="",
                )
            )
        elif not injected["tag"] and insert_statement.table is tags_table:
            injected["tag"] = True
            conn.execute(
                insert(tags_table).values(
                    name="Race Tag",
                    description="Inserted by another writer",
                    instruction="",
                    color="#123456",
                )
            )
        return real_insert_or_select(conn, insert_statement, select_statement)

    monkeypatch.setattr(
        category_taxonomy,
        "insert_or_select_unique_row",
        simulate_racing_insert,
    )

    category = category_taxonomy.upsert_category_metadata(
        db_conn,
        "Race Category",
        description="Updated description",
        instruction="Updated instruction",
    )
    tag = category_taxonomy.upsert_tag_metadata(
        db_conn,
        "Race Tag",
        description="Updated tag description",
        instruction="Updated tag instruction",
        color="#abcdef",
    )

    category_row = db_conn.execute(
        select(
            categories_table.c.description,
            categories_table.c.instruction,
            func.count().over().label("row_count"),
        ).where(categories_table.c.name == "Race Category")
    ).fetchone()
    tag_row = db_conn.execute(
        select(
            tags_table.c.description,
            tags_table.c.instruction,
            tags_table.c.color,
            func.count().over().label("row_count"),
        ).where(tags_table.c.name == "Race Tag")
    ).fetchone()

    assert category == "Race Category"
    assert tag == "Race Tag"
    assert injected == {"category": True, "tag": True}
    assert tuple(category_row) == ("Updated description", "Updated instruction", 1)
    assert tuple(tag_row) == ("Updated tag description", "Updated tag instruction", "#123456", 1)
