"""Database seed orchestration helpers."""


def seed_runtime_settings_defaults(conn):
    """Seed runtime settings defaults."""
    from finance_app.modules.settings.runtime import seed_runtime_settings

    seed_runtime_settings(conn)


def seed_statement_type_defaults(conn):
    """Seed statement type defaults."""
    from finance_app.modules.settings.runtime import seed_statement_types

    seed_statement_types(conn)


def seed_category_taxonomy_defaults(conn):
    """Seed category taxonomy defaults."""
    from finance_app.modules.categories.taxonomy import seed_category_taxonomy

    seed_category_taxonomy(conn)
