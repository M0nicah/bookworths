"""Top counterparties — who your money actually flows to and from.

The design's insight worth keeping: rank by the *identifier*, not the name.
Two different customers are often saved under the same display name, and one
person's name is spelled three ways across a statement. The phone/till number
is the only stable key.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal
from typing import Sequence

from ..schema import Direction, Transaction

ZERO = Decimal("0")


@dataclass
class Counterparty:
    name: str
    identifier: str
    count: int
    total: Decimal
    direction: Direction

    @property
    def initials(self) -> str:
        words = [w for w in self.name.split() if w and w[0].isalpha()]
        if not words:
            return "?"
        return "".join(w[0] for w in words[:2]).upper()

    @property
    def average(self) -> Decimal:
        return self.total / self.count if self.count else ZERO


def top_counterparties(
    transactions: Sequence[Transaction],
    direction: Direction,
    limit: int = 6,
) -> list[Counterparty]:
    """Rank counterparties by value moved, keyed on identifier."""
    grouped: dict[str, dict] = defaultdict(
        lambda: {"name": "", "count": 0, "total": ZERO}
    )
    for txn in transactions:
        if txn.direction is not direction or not txn.entity_identifier:
            continue
        entry = grouped[txn.entity_identifier]
        entry["count"] += 1
        entry["total"] += txn.gross_amount
        # Keep the longest name seen — statements truncate inconsistently.
        if len(txn.entity_name) > len(entry["name"]):
            entry["name"] = txn.entity_name
    ranked = sorted(grouped.items(), key=lambda kv: kv[1]["total"], reverse=True)
    return [
        Counterparty(
            name=(entry["name"] or identifier).title(),
            identifier=identifier,
            count=entry["count"],
            total=entry["total"],
            direction=direction,
        )
        for identifier, entry in ranked[:limit]
    ]
