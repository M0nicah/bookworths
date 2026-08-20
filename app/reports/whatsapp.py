"""Module C — the 5-Minute WhatsApp Exception Template.

Sellers do not read spreadsheets. They read WhatsApp. This draft asks only about
the transactions the engine genuinely could not settle, caps the list so it stays
answerable in one sitting, and is written for one-word replies.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Sequence

from ..schema import ClassifiedTransaction

MAX_QUESTIONS = 5

#: The reply vocabulary. Deliberately tiny — a seller can answer from memory
#: while walking, without opening anything.
REPLY_KEY = [
    ("STOCK", "buying stock / bale"),
    ("RIDER", "rider or parcel delivery"),
    ("ADS", "IG boost or promo"),
    ("PACK", "packaging or branding"),
    ("ME", "personal / family money"),
]


#: Words that mark a name as a business rather than a person, so we do not
#: greet "Thrift by Njeri" as "Hi Thrift".
_BRAND_WORDS = {"by", "thrift", "collection", "store", "shop", "boutique",
                "ke", "kenya", "vendor", "closet", "wear", "styles", "hub"}


def _greeting(seller_name: str) -> str:
    """Greet a person by first name; greet a brand without mangling it."""
    name = (seller_name or "").strip()
    if not name:
        return "Hi"
    tokens = name.split()
    # "Thrift by Njeri" -> the human is the word after "by".
    lowered = [t.lower() for t in tokens]
    if "by" in lowered:
        tail = tokens[lowered.index("by") + 1:]
        if tail:
            return f"Hi {tail[0]}"
    if len(tokens) == 1 or lowered[0] not in _BRAND_WORDS:
        return f"Hi {tokens[0]}"
    return "Hi"


def build_whatsapp_draft(
    results: Sequence[ClassifiedTransaction],
    seller_name: str = "",
    max_questions: int = MAX_QUESTIONS,
) -> str:
    """Return a ready-to-paste WhatsApp message, or a clean all-good note."""
    exceptions = [r for r in results if r.needs_review]
    # Biggest money first — a KES 14,000 mystery matters more than a KES 200 one.
    exceptions.sort(key=lambda r: r.transaction.gross_amount, reverse=True)
    shortlist = exceptions[:max_questions]

    greeting = _greeting(seller_name)

    if not shortlist:
        return (
            f"{greeting} 👋\n\n"
            "Your books are done for this period — every transaction was matched "
            "automatically, nothing needs your input. 🎉\n\n"
            "Your Profit Pack is attached.\n\n"
            "_Bookworths — clear books, real value._"
        )

    total = sum((r.transaction.gross_amount for r in shortlist), Decimal("0"))
    plural = "transactions" if len(shortlist) > 1 else "transaction"

    lines = [
        f"{greeting} 👋 Your books are almost done!",
        "",
        f"I just need help with {len(shortlist)} {plural} "
        f"(KES {total:,.0f} total). Reply with one word for each 👇",
        "",
    ]

    for index, item in enumerate(shortlist, start=1):
        txn = item.transaction
        name = (item.classification.counterparty_label or txn.entity_name).title()
        tail = f" ({txn.entity_identifier[-4:]})" if txn.entity_identifier else ""
        lines.append(
            f"*{index}.* KES {txn.gross_amount:,.0f} — {txn.timestamp:%d %b} — "
            f"{name}{tail}"
        )

    lines += [
        "",
        "Just reply like: *1 STOCK, 2 ME, 3 RIDER*",
        "",
        "_Key:_",
    ]
    lines += [f"• *{code}* = {meaning}" for code, meaning in REPLY_KEY]

    remaining = len(exceptions) - len(shortlist)
    if remaining > 0:
        lines += [
            "",
            f"(There are {remaining} smaller ones too — I'll ask about those next "
            "time so this stays quick.)",
        ]

    lines += [
        "",
        "Takes 30 seconds and your Profit Pack is final ✅",
        "",
        "_Bookworths — clear books, real value._",
    ]
    return "\n".join(lines)
