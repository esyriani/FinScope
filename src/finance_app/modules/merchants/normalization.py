"""Merchant normalization helpers."""

import re
from dataclasses import dataclass

from sqlalchemy import func, or_, select

from finance_app.core.constants import (
    MERCHANT_ALIAS_CONFIDENCE_HIGH,
    MERCHANT_ALIAS_CONFIDENCE_LOW,
    MERCHANT_ALIAS_SOURCE_ALIAS,
    MERCHANT_ALIAS_SOURCE_FALLBACK,
    MERCHANT_ALIAS_SOURCE_RULE,
)
from finance_app.core.text import strip_accents
from finance_app.database.tables import (
    merchant_aliases as merchant_aliases_table,
    merchants as merchants_table,
)


@dataclass(frozen=True)
class CleanedMerchant:
    """Represent cleaned merchant."""
    cleaned_key: str
    location_code: str | None = None
    removed_tokens: tuple[str, ...] = ()
    confidence: str = MERCHANT_ALIAS_CONFIDENCE_HIGH


@dataclass(frozen=True)
class NormalizedMerchant:
    """Represent normalized merchant."""
    raw_description: str
    cleaned_key: str
    canonical_name: str
    normalization_source: str
    confidence: str
    location_code: str | None = None
    removed_tokens: tuple[str, ...] = ()


DEFAULT_MERCHANT_ALIASES = {
    "AMZN MKTP": "AMAZON",
    "AMAZON MKTPLACE": "AMAZON",
    "PRESTO FARE": "PRESTO",
    "WAL-MART SUPERCENTER": "WALMART",
    "WALMART CA": "WALMART",
    "LIBRAIRIE RENAUD BRAY": "RENAUD-BRAY",
    "LIBRAIRE RENAUD BRAY": "RENAUD-BRAY",
    "LA MAISON SIMONS": "LA MAISON SIMONS",
    "THE HOME DEPOT": "THE HOME DEPOT",
    "HOME DEPOT": "THE HOME DEPOT",
    "CANADIAN TIRE": "CANADIAN TIRE",
    "RECEPT VFC": "RECEIVED E-TRANSFER",
    "ENVOI VFC": "SENT E-TRANSFER",
    "SAAQ-PERMIS": "SAAQ",
    "SAAQ-IMMATRI": "SAAQ",
    "TAX SCOL MTL": "TAX SCOLAIRE MONTREAL",
}

ARTIFACT_SUFFIXES = {"FAC", "PAI", "DIV", "ASS", "REN"}
BUSINESS_SUFFIXES = {"INC"}


def clean_merchant_description(raw_description: str) -> CleanedMerchant:
    """Clean merchant description."""
    raw_text = str(raw_description or "").strip()
    text = strip_accents(raw_text).upper()
    text = text.replace("\u00a0", " ")
    text = normalize_spaces(text)
    removed_tokens: list[str] = []
    location_code = None

    text = re.sub(r"\b(RECEPT|ENVOI)\s*-\s*VFC\b", r"\1 VFC", text)
    text = re.sub(r"\b(RECEPT|ENVOI)-VFC\b", r"\1 VFC", text)
    text = re.sub(r"\bRENAUD-BRAY\b", "RENAUD BRAY", text)
    text = re.sub(r"\bLAMAISONSIMONS\b", "LA MAISON SIMONS", text)

    text = remove_pattern(text, r"/[A-Z0-9]{6,}\s*$", removed_tokens)
    text = remove_pattern(text, r"\b(?:CA|CO)\s*\*[A-Z0-9]{4,}\b", removed_tokens)
    text = remove_pattern(text, r"\*{2,}[A-Z0-9]{2,}\b", removed_tokens)
    text = remove_pattern(text, r"\*[A-Z0-9]{3,}\b", removed_tokens)
    text = remove_pattern(text, r"\b(?:CARD|ACCT|ACCOUNT)\s*(?:NO\.?|NUMBER|NUM)?\s*\*?\d{3,6}\b", removed_tokens)

    text, location_code = extract_location_code(text, removed_tokens)

    text = re.sub(r"\s+-\s+", " ", text)
    text = re.sub(r"[./]+", " ", text)
    text = re.sub(r"[,:;|_]+", " ", text)
    text = re.sub(r"[()\[\]{}]+", " ", text)
    text = re.sub(r"['`\"]+", "", text)
    text = re.sub(r"[#*]+", " ", text)
    text = normalize_spaces(text)

    text = re.sub(r"\bAMZN\s+MKTP\s+CA\b", "AMZN MKTP", text)
    text = re.sub(r"\bCANADIAN\s+TIRE\s+STORE\b", "CANADIAN TIRE", text)

    text = remove_trailing_reference_tokens(text, removed_tokens)
    text = remove_trailing_suffixes(text, removed_tokens, ARTIFACT_SUFFIXES)
    text = remove_trailing_suffixes(text, removed_tokens, BUSINESS_SUFFIXES)
    text = remove_trailing_reference_tokens(text, removed_tokens)

    text = normalize_spaces(text).strip(" -*/#")
    confidence = MERCHANT_ALIAS_CONFIDENCE_HIGH if text else MERCHANT_ALIAS_CONFIDENCE_LOW
    return CleanedMerchant(
        cleaned_key=text,
        location_code=location_code,
        removed_tokens=tuple(removed_tokens),
        confidence=confidence,
    )


def normalize_merchant_description(description):
    """Normalize merchant description."""
    return clean_merchant_description(description).cleaned_key


def normalize_merchant(raw_description: str, conn=None) -> NormalizedMerchant:
    """Normalize merchant."""
    raw_text = str(raw_description or "")
    cleaned = clean_merchant_description(raw_text)
    merchant = merchant_for_cleaned_key(conn, cleaned.cleaned_key)

    if merchant:
        return NormalizedMerchant(
            raw_description=raw_text,
            cleaned_key=cleaned.cleaned_key,
            canonical_name=merchant["display_name"],
            normalization_source=merchant["source"],
            confidence=merchant["confidence"],
            location_code=cleaned.location_code,
            removed_tokens=cleaned.removed_tokens,
        )

    canonical = canonical_alias_for_key(cleaned.cleaned_key)

    if canonical:
        canonical_name = canonical
        source = MERCHANT_ALIAS_SOURCE_ALIAS
    else:
        canonical_name = cleaned.cleaned_key
        source = (
            MERCHANT_ALIAS_SOURCE_RULE
            if cleaned.removed_tokens or cleaned.location_code
            else MERCHANT_ALIAS_SOURCE_FALLBACK
        )

    return NormalizedMerchant(
        raw_description=raw_text,
        cleaned_key=cleaned.cleaned_key,
        canonical_name=canonical_name,
        normalization_source=source,
        confidence=cleaned.confidence,
        location_code=cleaned.location_code,
        removed_tokens=cleaned.removed_tokens,
    )


def canonicalize_merchant_key(value: str, conn=None) -> str:
    """Canonicalize merchant key."""
    merchant = merchant_for_name(conn, value)
    if merchant:
        return merchant["display_name"]
    return normalize_merchant(value, conn=conn).canonical_name


def canonical_merchant_description(description: str, conn=None) -> str:
    """Return the canonical merchant description."""
    return normalize_merchant(description, conn=conn).canonical_name


def canonical_alias_for_key(cleaned_key: str) -> str | None:
    """Return the canonical alias for key."""
    alias_key = clean_merchant_description(cleaned_key).cleaned_key
    if not alias_key:
        return None
    return DEFAULT_MERCHANT_ALIASES.get(alias_key)


def merchant_for_cleaned_key(conn, cleaned_key):
    """Return persisted merchant metadata for a cleaned key when available."""
    if conn is None:
        return None

    alias_key = str(cleaned_key or "").strip()
    if not alias_key:
        return None

    return conn.execute(
        select(
            merchants_table.c.display_name,
            merchant_aliases_table.c.source,
            merchant_aliases_table.c.confidence,
        )
        .select_from(
            merchant_aliases_table.join(
                merchants_table,
                merchants_table.c.id == merchant_aliases_table.c.merchant_id,
            )
        )
        .where(merchant_aliases_table.c.alias_key == alias_key)
    ).mappings().fetchone()


def merchant_for_name(conn, merchant_name):
    """Return persisted merchant metadata for a display name when available."""
    if conn is None:
        return None

    text = str(merchant_name or "").strip()
    if not text:
        return None

    normalized = text.lower()
    return conn.execute(
        select(merchants_table.c.display_name).where(
            or_(
                func.lower(merchants_table.c.display_name) == normalized,
                func.lower(merchants_table.c.system_name) == normalized,
                func.lower(merchants_table.c.canonical_key) == normalized,
            )
        )
    ).mappings().fetchone()


def extract_location_code(text, removed_tokens):
    """Extract location code."""
    location_code = None

    match = re.search(r"#\s*(\d{2,6})\s*$", text)
    if match:
        location_code = match.group(1)
        removed_tokens.append(match.group(0).strip())
        text = text[: match.start()] + " "
        return normalize_spaces(text), location_code

    match = re.search(r"\bSHELL\s+(C\d{4,6})\s*$", text)
    if match:
        location_code = match.group(1)
        removed_tokens.append(location_code)
        text = text[: match.start(1)] + " "
        return normalize_spaces(text), location_code

    return text, None


def remove_trailing_reference_tokens(text, removed_tokens):
    """Remove trailing reference tokens."""
    text = normalize_spaces(text)
    while True:
        match = re.search(r"\b(?=[A-Z0-9]*[A-Z])(?=[A-Z0-9]*\d)[A-Z0-9]{5,12}\b\s*$", text)
        if not match:
            match = re.search(r"\b\d{5,}\b\s*$", text)
        if not match:
            break

        removed_tokens.append(match.group(0).strip())
        text = normalize_spaces(text[: match.start()] + " ")

    return text


def remove_trailing_suffixes(text, removed_tokens, suffixes):
    """Remove trailing suffixes."""
    text = normalize_spaces(text)
    while True:
        words = text.split()
        if not words or words[-1] not in suffixes:
            return text

        removed_tokens.append(words[-1])
        text = " ".join(words[:-1])


def remove_pattern(text, pattern, removed_tokens):
    """Remove pattern."""
    def replace(match):
        """Handle replace."""
        token = normalize_spaces(match.group(0)).strip()
        if token:
            removed_tokens.append(token)
        return " "

    return re.sub(pattern, replace, text)


def normalize_spaces(value):
    """Normalize spaces."""
    return re.sub(r"\s+", " ", str(value or "")).strip()
