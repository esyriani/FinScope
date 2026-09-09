"""Accessibility route tests for calendar and recurring templates."""

from tests.support.html import assert_has_element, assert_no_element


def test_calendar_and_recurring_month_links_have_accessible_names(owner_client):
    """Verify icon-only month navigation links expose localized accessible names."""
    calendar_response = owner_client.get("/calendar?month=2026-05")
    recurring_response = owner_client.get("/recurring?view=calendar&month=2026-05")

    assert calendar_response.status_code == 200
    assert recurring_response.status_code == 200
    assert_has_element(
        calendar_response,
        "a",
        attrs={"data-calendar-ajax-link": True, "aria-label": "Previous month"},
    )
    assert_has_element(
        calendar_response,
        "a",
        attrs={"data-calendar-ajax-link": True, "aria-label": "Next month"},
    )
    assert_has_element(
        recurring_response,
        "a",
        attrs={"data-recurring-ajax-link": True, "aria-label": "Previous month"},
    )
    assert_has_element(
        recurring_response,
        "a",
        attrs={"data-recurring-ajax-link": True, "aria-label": "Next month"},
    )


def test_calendar_day_modal_uses_native_button_trigger(owner_client):
    """Verify calendar days do not expose incomplete custom button semantics."""
    response = owner_client.get("/calendar?month=2026-05")

    assert response.status_code == 200
    assert_has_element(
        response,
        "section",
        attrs={"data-calendar-day": "2026-05-01", "role": False, "tabindex": False},
    )
    assert_no_element(response, "section", attrs={"data-calendar-day": "2026-05-01", "role": "button"})
    assert_has_element(
        response,
        "button",
        attrs={
            "type": "button",
            "data-calendar-day-open": "2026-05-01",
            "aria-label": "Review 2026-05-01 transactions",
        },
    )
