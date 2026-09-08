"""Unit tests for user interface translation helpers."""

from datetime import date

from finance_app.core.i18n import (
    format_month_year,
    locale_for_language,
    month_abbreviation,
    normalize_language,
    translate,
    weekday_abbreviation_labels,
)


def test_normalize_language_accepts_supported_languages_and_locales():
    """Verify language normalization accepts app-supported locale shapes."""
    assert normalize_language("fr_CA") == "fr"
    assert normalize_language("fr-CA") == "fr"
    assert normalize_language("en") == "en"
    assert normalize_language("de") == "en"
    assert normalize_language("") == "en"


def test_translate_uses_catalog_and_falls_back_to_source_text():
    """Verify catalog lookup, formatting, and fallback behavior."""
    assert translate("Settings", "fr") == "Paramètres"
    assert translate("Daily summaries.", "fr") == "Résumés quotidiens."
    assert translate("Next month", "fr") == "Mois suivant"
    assert translate("Previous month", "fr") == "Mois précédent"
    assert translate("Review {date} transactions", "fr", date="2026-05-01") == (
        "Consulter les transactions du 2026-05-01"
    )
    assert (
        translate("Select all transactions on this page", "fr") == "Sélectionner toutes les transactions de cette page"
    )
    assert (
        translate(
            "Select transaction on {date}: {description}, {amount}",
            "fr",
            date="2026-01-01",
            description="Coffee Shop",
            amount="4.56 €",
        )
        == "Sélectionner la transaction du 2026-01-01 : Coffee Shop, 4.56 €"
    )
    assert translate("Upload", "en") == "Upload"
    assert translate("Missing source", "fr") == "Missing source"
    assert translate("Table {number}", "fr", number=3) == "Table 3"


def test_locale_for_language_returns_browser_locale():
    """Verify language choices map to browser locale identifiers."""
    assert locale_for_language("fr") == "fr-CA"
    assert locale_for_language("en") == "en-CA"
    assert locale_for_language("unknown") == "en-CA"


def test_calendar_labels_use_selected_language():
    """Verify month and weekday helpers use the translation catalog."""
    assert month_abbreviation(2, "fr") == "févr."
    assert weekday_abbreviation_labels("fr")[:3] == ["lun.", "mar.", "mer."]
    assert format_month_year(date(2026, 5, 1), "fr") == "mai 2026"
