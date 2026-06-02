"""Tests for merchant normalization behavior."""

import pytest

from finance_app.modules.merchants.normalization import (
    clean_merchant_description,
    normalize_merchant,
)


@pytest.mark.parametrize(
    ("raw_description", "cleaned_key", "location_code"),
    [
        ("PRESTO FARE/QG5H8MJTZF", "PRESTO FARE", None),
        ("AMZN Mktp CA*QI44D1DJ3", "AMZN MKTP", None),
        ("HYDRO-QUEBEC FAC", "HYDRO-QUEBEC", None),
        ("ENERGIR          FAC", "ENERGIR", None),
        ("EBOX INC PAI", "EBOX", None),
        ("LouisBoheme DIV", "LOUISBOHEME", None),
        ("CROIX BLEUE      DIV", "CROIX BLEUE", None),
        ("JUDO QUEBEC INC  DIV", "JUDO QUEBEC", None),
        ("CIE BELAIR       ASS", "CIE BELAIR", None),
        ("SAAQ-PERMIS X3Q8Z5", "SAAQ-PERMIS", None),
        ("SAAQ-IMMATRI K6U3U5", "SAAQ-IMMATRI", None),
        ("MONTREAL TAX A9X4W3", "MONTREAL TAX", None),
        ("MT-ROYAL TAX Z3R7Y5", "MT-ROYAL TAX", None),
        ("TAX SCOL.MTL W6K9W9", "TAX SCOL MTL", None),
        ("CIBC MC X6A2W2", "CIBC MC", None),
        ("BMO ENTREPR  Z4R9W4", "BMO ENTREPR", None),
        ("Recept - VFC ***HQc", "RECEPT VFC", None),
        ("Recept-VFC *2Uq REN", "RECEPT VFC", None),
        ("Envoi - VFC ***uKN", "ENVOI VFC", None),
        ("PMT PRET *326060301", "PMT PRET", None),
        ("WAL-MART SUPERCENTER#3180", "WAL-MART SUPERCENTER", "3180"),
        ("SHOPPERS DRUG MART #94", "SHOPPERS DRUG MART", "94"),
        ("THE HOME DEPOT #7124", "THE HOME DEPOT", "7124"),
        ("CANADIAN TIRE STORE #292", "CANADIAN TIRE STORE", "292"),
        ("DOLLARAMA # 126", "DOLLARAMA", "126"),
        ("ULTRAMAR #26345", "ULTRAMAR", "26345"),
        ("SHELL C03122", "SHELL", "C03122"),
        ("FIDO Mobile ******2373", "FIDO MOBILE", None),
        ("GRAMMARLY CO*VWCL7WU", "GRAMMARLY", None),
        ("LIBRAIRIE RENAUD BRAY", "LIBRAIRIE RENAUD BRAY", None),
        ("LIBRAIRE RENAUD-BRAY", "LIBRAIRE RENAUD-BRAY", None),
        ("LaMaisonSimons", "LAMAISONSIMONS", None),
        ("WALMART.CA", "WALMART CA", None),
        ("Amazon Mktplace CA*ABCD1234", "AMAZON MKTPLACE", None),
        ("WALMART CA 12345", "WALMART CA", None),
        ("Home Depot #1234", "HOME DEPOT", "1234"),
        ("SAAQ-Permis 123456", "SAAQ-PERMIS", None),
        ("TAX SCOL.MTL/ABCDEF1", "TAX SCOL MTL", None),
        ("Card No. 1234 Metro Grocery", "METRO GROCERY", None),
        ("SHOPIFY *ABCD1234", "SHOPIFY", None),
        ("SQ *COSMETA", "COSMETA", None),
        ("SQ*COSMETA", "COSMETA", None),
        ("SQUARE *COSMETA", "COSMETA", None),
    ],
)
def test_required_cleanup_examples(raw_description, cleaned_key, location_code):
    """Verify required merchant cleanup examples."""
    result = clean_merchant_description(raw_description)

    assert result.cleaned_key == cleaned_key
    assert result.location_code == location_code


@pytest.mark.parametrize(
    "cleaned_key",
    [
        "PRESTO FARE",
        "AMZN MKTP",
        "AMAZON MKTPLACE",
        "WAL-MART SUPERCENTER",
        "WALMART CA",
        "HOME DEPOT",
        "SAAQ-PERMIS",
        "TAX SCOL MTL",
        "RECEPT VFC",
        "ENVOI VFC",
    ],
)
def test_normalize_merchant_keeps_cleaned_key_without_database_alias(cleaned_key):
    """Verify deterministic cleanup does not apply hidden merchant aliases."""
    result = normalize_merchant(cleaned_key)

    assert result.cleaned_key == cleaned_key
    assert result.merchant_key == cleaned_key
    assert result.normalization_source == "fallback"
