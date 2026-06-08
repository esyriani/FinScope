"""Normalize imported merchant descriptions into deterministic merchant keys.

The helpers strip statement-specific artifacts without consulting runtime
merchant aliases. Repository and workflow code can therefore use them for stable
fallback keys before database-backed merchant matching is available.
"""

import re
from dataclasses import dataclass

from finance_app.core.text import strip_accents

MERCHANT_CLEANUP_CONFIDENCE_HIGH = "high"
MERCHANT_CLEANUP_CONFIDENCE_LOW = "low"
MERCHANT_CLEANUP_SOURCE_FALLBACK = "fallback"
MERCHANT_CLEANUP_SOURCE_RULE = "rule"


@dataclass(frozen=True)
class CleanedMerchant:
    """Represent a merchant string after statement artifacts are removed."""

    cleaned_key: str
    location_code: str | None = None
    removed_tokens: tuple[str, ...] = ()
    confidence: str = MERCHANT_CLEANUP_CONFIDENCE_HIGH


@dataclass(frozen=True)
class NormalizedMerchant:
    """Represent a deterministic merchant key derived from a description."""

    raw_description: str
    merchant_key: str
    normalization_source: str
    confidence: str
    location_code: str | None = None
    removed_tokens: tuple[str, ...] = ()

    @property
    def cleaned_key(self) -> str:
        """Return the deterministic merchant key for legacy callers."""
        return self.merchant_key


ARTIFACT_SUFFIXES = {"FAC", "PAI", "DIV", "ASS", "REN"}
BUSINESS_SUFFIXES = {"INC"}


def clean_merchant_description(raw_description: object) -> CleanedMerchant:
    """Remove card, processor, location, and reference artifacts from a description."""
    raw_text = str(raw_description or "").strip()
    text = strip_accents(raw_text).upper()
    text = text.replace("\u00a0", " ")
    text = normalize_spaces(text)
    removed_tokens: list[str] = []
    location_code: str | None = None

    text = re.sub(r"\b(RECEPT|ENVOI)\s*-\s*VFC\b", r"\1 VFC", text)
    text = re.sub(r"\b(RECEPT|ENVOI)-VFC\b", r"\1 VFC", text)
    text = preserve_starred_processor_merchant(text, removed_tokens)

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

    text = remove_trailing_reference_tokens(text, removed_tokens)
    text = remove_trailing_suffixes(text, removed_tokens, ARTIFACT_SUFFIXES)
    text = remove_trailing_suffixes(text, removed_tokens, BUSINESS_SUFFIXES)
    text = remove_trailing_reference_tokens(text, removed_tokens)

    text = normalize_spaces(text).strip(" -*/#")
    confidence = MERCHANT_CLEANUP_CONFIDENCE_HIGH if text else MERCHANT_CLEANUP_CONFIDENCE_LOW
    return CleanedMerchant(
        cleaned_key=text,
        location_code=location_code,
        removed_tokens=tuple(removed_tokens),
        confidence=confidence,
    )


def normalize_merchant_description(description: object) -> str:
    """Return only the cleaned key for call sites that do not need metadata."""
    return clean_merchant_description(description).cleaned_key


def normalize_merchant(raw_description: object, conn: object | None = None) -> NormalizedMerchant:
    """Return a deterministic merchant key for a raw transaction description."""
    del conn
    raw_text = str(raw_description or "")
    cleaned = clean_merchant_description(raw_text)
    source = (
        MERCHANT_CLEANUP_SOURCE_RULE
        if cleaned.removed_tokens or cleaned.location_code
        else MERCHANT_CLEANUP_SOURCE_FALLBACK
    )

    return NormalizedMerchant(
        raw_description=raw_text,
        merchant_key=cleaned.cleaned_key,
        normalization_source=source,
        confidence=cleaned.confidence,
        location_code=cleaned.location_code,
        removed_tokens=cleaned.removed_tokens,
    )


def canonicalize_merchant_key(value: object, conn: object | None = None) -> str:
    """Return the deterministic merchant key for a filter or rule value."""
    del conn
    return normalize_merchant(value).merchant_key


def extract_location_code(text: str, removed_tokens: list[str]) -> tuple[str, str | None]:
    """Split a known trailing store/location code from merchant text."""
    location_code: str | None = None

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


def remove_trailing_reference_tokens(text: str, removed_tokens: list[str]) -> str:
    """Strip trailing mixed or numeric reference tokens until none remain."""
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


def remove_trailing_suffixes(text: str, removed_tokens: list[str], suffixes: set[str]) -> str:
    """Remove known bank artifact suffixes from the end of the merchant text."""
    text = normalize_spaces(text)
    while True:
        words = text.split()
        if not words or words[-1] not in suffixes:
            return text

        removed_tokens.append(words[-1])
        text = " ".join(words[:-1])


def remove_pattern(text: str, pattern: str, removed_tokens: list[str]) -> str:
    """Replace pattern matches with spaces while recording removed tokens."""

    def replace(match: re.Match[str]) -> str:
        """Record a removed regex match for normalization diagnostics."""
        token = normalize_spaces(match.group(0)).strip()
        if token:
            removed_tokens.append(token)
        return " "

    return re.sub(pattern, replace, text)


def preserve_starred_processor_merchant(text: str, removed_tokens: list[str]) -> str:
    """Preserve merchant names from payment-processor star descriptors.

    Some payment processors prefix the true merchant with a short processor
    token, such as ``SQ *COSMETA``. The generic star-token cleanup treats
    starred values as card/reference artifacts, so these processor-specific
    descriptors must be normalized before that cleanup runs.
    """
    match = re.match(
        r"^(SQ|SQUARE)\s*\*\s*(?P<merchant>(?=[A-Z0-9 .&'`/,-]*[A-Z])[A-Z0-9 .&'`/,-]{3,})$",
        text,
    )
    if not match:
        return text

    processor = match.group(1)
    merchant = normalize_spaces(match.group("merchant"))
    if not merchant:
        return text

    removed_tokens.append(processor)
    return merchant


def normalize_spaces(value: object) -> str:
    """Collapse any whitespace run to a single ASCII space."""
    return re.sub(r"\s+", " ", str(value or "")).strip()
