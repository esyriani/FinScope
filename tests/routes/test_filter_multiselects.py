"""Route tests for shared category and tag multiselect filters."""

from sqlalchemy import text
from tests.support.html import parse_html


def test_filter_multiselects_render_accessible_disclosure_contract(owner_client, core_conn):
    """Verify shared filter multiselects expose a controlled checkbox group."""
    core_conn.execute(text("""
        INSERT INTO transactions (tx_date, description, amount, category, category_source, fingerprint)
        VALUES ('2026-01-02', 'Accessible filter store', 12.34, 'Food', 'rule', 'route-accessible-filter')
        """))
    core_conn.commit()

    expected_counts = {
        "/transactions": 2,
        "/reports/income": 2,
        "/comparison": 5,
        "/calendar": 2,
        "/recurring": 2,
        "/rules": 2,
    }

    for path, expected_count in expected_counts.items():
        response = owner_client.get(path)
        document = parse_html(response)
        toggles = document.find_all("button", attrs={"data-tag-multiselect-toggle": True})
        menus = document.find_all("div", attrs={"data-tag-multiselect-menu": True})
        menus_by_id = {menu.attrs.get("id"): menu for menu in menus}

        assert response.status_code == 200
        assert len(toggles) == expected_count, path
        assert len(menus) == expected_count, path
        for toggle in toggles:
            controlled_menu = menus_by_id[toggle.attrs["aria-controls"]]
            assert toggle.attrs["aria-expanded"] == "false"
            assert "aria-labelledby" in toggle.attrs
            assert controlled_menu.attrs["role"] == "group"
            assert controlled_menu.attrs["tabindex"] == "-1"
            assert "hidden" in controlled_menu.attrs
