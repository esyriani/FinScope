"""Tests for dashboard presenter behavior."""

from finance_app.modules.dashboard.presenter import build_quick_view_options


def test_build_quick_view_options_hides_zero_count_buttons():
    """Verify dashboard quick views only show buttons with matching transactions."""
    options = build_quick_view_options(
        "unknown",
        {
            "categorized_count": 0,
            "needs_review_count": 2,
            "unknown_count": 3,
            "all_count": 5,
        },
    )

    assert [(option["value"], option["count"], option["active"]) for option in options] == [
        ("needs_review", 2, False),
        ("unknown", 3, True),
        ("all", 5, False),
    ]
