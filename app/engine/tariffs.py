"""Layer 1 — deterministic regex, Safaricom tariffs, and known utility paybills.

Anything matched here is certain: it never reaches the LLM, never costs a token,
and never appears in the seller's exception list.
"""
from __future__ import annotations

import re
from typing import Optional

from ..schema import Account, Transaction

#: Lines that ARE a Safaricom charge (already merged as `fee_amount` in most
#: cases, but standalone Fuliza/access fees arrive as their own transaction).
TARIFF_PATTERNS = [
    re.compile(p, re.IGNORECASE)
    for p in (
        r"fuliza",
        r"pay\s*bill\s+charge",
        r"send\s+money\s+charge",
        r"withdrawal\s+charge",
        r"transaction\s+charge",
        r"m-?pesa\s+charge",
        r"access\s+fee",
        r"tariff",
        r"excise\s+duty",
        r"bank\s+charge",
    )
]

#: Well-known Kenyan paybills/tills, keyed by number.
KNOWN_PAYBILLS: dict[str, tuple[Account, str, float]] = {
    "888880": (Account.OWNER_DRAWINGS, "KPLC prepaid tokens", 0.86),
    "888888": (Account.OWNER_DRAWINGS, "KPLC postpaid", 0.86),
    "5296000": (Account.MARKETING, "Meta / Facebook Payments", 0.98),
    "5296001": (Account.MARKETING, "Meta / Facebook Payments", 0.98),
    "400200": (Account.OWNER_DRAWINGS, "Safaricom postpaid", 0.85),
    "150501": (Account.OWNER_DRAWINGS, "Nairobi Water", 0.86),
    "501200": (Account.OWNER_DRAWINGS, "NHIF", 0.90),
    "800088": (Account.OWNER_DRAWINGS, "DSTV / MultiChoice", 0.92),
}

#: Raw-text patterns handled without any counterparty identifier at all.
TEXT_RULES: list[tuple[re.Pattern[str], Account, str, float]] = [
    (re.compile(r"airtime\s+purchase", re.I), Account.MARKETING, "Safaricom airtime/data", 0.87),
    (re.compile(r"\bdata\s+bundle", re.I), Account.MARKETING, "Safaricom airtime/data", 0.87),
    (re.compile(r"\bkplc\b|kenya\s+power", re.I), Account.OWNER_DRAWINGS, "KPLC electricity", 0.86),
    (re.compile(r"facebook\s+payments|meta\s+platforms|\bigboost\b", re.I),
     Account.MARKETING, "Meta / IG boost", 0.97),
    (re.compile(r"\bshelf\s+rent\b", re.I), Account.LOGISTICS, "CBD pickup shelf rent", 0.95),
]


def is_tariff(txn: Transaction) -> bool:
    return any(p.search(txn.raw_text) for p in TARIFF_PATTERNS)


def classify_layer1(
    txn: Transaction,
) -> Optional[tuple[Account, float, str, str]]:
    """Return (account, confidence, rationale, label) or None if no rule fires."""
    if is_tariff(txn):
        return (
            Account.FINANCIAL_FEES,
            0.99,
            "Safaricom tariff / financial charge matched by deterministic rule.",
            "Safaricom M-Pesa tariff",
        )

    identifier = txn.entity_identifier or ""
    if identifier in KNOWN_PAYBILLS:
        account, label, confidence = KNOWN_PAYBILLS[identifier]
        return (account, confidence, f"Known paybill/till {identifier} ({label}).", label)

    for pattern, account, label, confidence in TEXT_RULES:
        if pattern.search(txn.raw_text):
            return (account, confidence, f"Matched deterministic rule for {label}.", label)

    return None
