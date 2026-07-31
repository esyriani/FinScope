"""Tests for configured money display helpers."""

from decimal import Decimal
from types import SimpleNamespace

import pytest

from finance_app.core import money
from finance_app.core.filters import format_money
from finance_app.modules.recurring import service as recurring_service


@pytest.mark.parametrize(
    ("raw_value", "expected"),
    [
        ("1.004", "1.00"),
        ("1.005", "1.01"),
        ("2.675", "2.68"),
        ("999.994", "999.99"),
        ("999.995", "1000.00"),
        ("-1.004", "-1.00"),
        ("-1.005", "-1.01"),
    ],
)
def test_quantize_money_rounds_half_up_at_cent_boundaries(raw_value, expected):
    """Verify persisted money values use half-up cent rounding."""
    assert money.quantize_money(Decimal(raw_value)) == Decimal(expected)


@pytest.mark.parametrize(
    ("raw_value", "places", "expected"),
    [
        ("1234.444", 2, "1 234.44 CAD"),
        ("1234.445", 2, "1 234.45 CAD"),
        ("1234.5", 0, "1 235 CAD"),
        ("-42.505", 2, "-42.51 CAD"),
    ],
)
def test_format_money_display_table_driven_rounding(raw_value, places, expected):
    """Verify display formatting rounds and groups values consistently."""
    assert money.format_money_display(Decimal(raw_value), places=places, symbol="CAD") == expected


def test_quantize_money_is_idempotent_and_sign_symmetric_for_generated_values():
    """Verify money quantization invariants across representative values."""
    samples = [Decimal(cents) / Decimal("1000") for cents in range(-2500, 2501, 37)]

    for value in samples:
        rounded = money.quantize_money(value)

        assert money.quantize_money(rounded) == rounded
        assert money.quantize_money(-value) == -rounded


def test_rounded_money_decimal_uses_default_for_blank_values():
    """Verify rounded money defaults are honored for absent scalar values."""
    default = Decimal("7.50")

    assert money.rounded_money_decimal(None, default=default) == default
    assert money.rounded_money_decimal("", default=default) == default


def test_money_formatting_uses_configured_currency_symbol(monkeypatch):
    """Verify Python money display helpers use the configured currency symbol."""
    monkeypatch.setattr(money, "settings", SimpleNamespace(currency_symbol="€"))

    assert money.format_money_display(Decimal("1234.5")) == "1 234.50 €"
    assert money.format_money_display(Decimal("1234.5"), places=0) == "1 235 €"
    assert money.format_signed_money_display(Decimal("-12.3")) == "-12.30 €"
    assert format_money(Decimal("12.3")) == "12.30 €"
    assert recurring_service.recurring_signed_amount_label({"type": "income", "amount": 42}) == "+42.00 €"
