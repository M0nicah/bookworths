"""Month-on-month comparison.

A single month tells a seller what happened. Several months tell them whether
it is getting better or worse, which is the more useful question and the one a
statement export can actually answer.

Only meaningful with more than one month of data, so every entry point checks
`is_available` before offering it.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from typing import Sequence

from ..schema import Account, ClassifiedTransaction, Direction, Transaction

ZERO = Decimal("0")


def _q(v: Decimal) -> Decimal:
    return Decimal(v).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


@dataclass
class MonthRow:
    """One month's figures. Field meaning depends on the mode."""

    key: str                 # "2026-07", sortable
    label: str               # "Jul 2026", readable
    money_in: Decimal = ZERO
    money_out: Decimal = ZERO
    fees: Decimal = ZERO
    transactions: int = 0
    #: Business only.
    cogs: Decimal = ZERO
    drawings: Decimal = ZERO
    #: Personal only.
    essentials: Decimal = ZERO
    savings: Decimal = ZERO

    @property
    def net(self) -> Decimal:
        return self.money_in - self.money_out

    @property
    def margin_pct(self) -> Decimal:
        if self.money_in <= ZERO:
            return ZERO
        return _q(self.net / self.money_in * 100)


@dataclass
class Trend:
    """A month-on-month view, plus what changed and what it means."""

    rows: list[MonthRow] = field(default_factory=list)
    mode: str = "Business"
    insights: list[str] = field(default_factory=list)

    @property
    def is_available(self) -> bool:
        """Comparison needs at least two months to compare."""
        return len(self.rows) >= 2

    @property
    def latest(self) -> MonthRow | None:
        return self.rows[-1] if self.rows else None

    @property
    def previous(self) -> MonthRow | None:
        return self.rows[-2] if len(self.rows) >= 2 else None

    @property
    def best(self) -> MonthRow | None:
        return max(self.rows, key=lambda r: r.net) if self.rows else None

    @property
    def worst(self) -> MonthRow | None:
        return min(self.rows, key=lambda r: r.net) if self.rows else None

    def average(self, field_name: str) -> Decimal:
        if not self.rows:
            return ZERO
        total = sum((getattr(r, field_name) for r in self.rows), ZERO)
        return _q(total / Decimal(len(self.rows)))

    def change(self, field_name: str) -> Decimal | None:
        """Absolute change from the previous month to the latest."""
        if not self.is_available:
            return None
        return _q(getattr(self.latest, field_name) - getattr(self.previous, field_name))

    def change_pct(self, field_name: str) -> Decimal | None:
        """Percentage change, or None when the base month was zero."""
        if not self.is_available:
            return None
        base = getattr(self.previous, field_name)
        if base == ZERO:
            return None
        delta = getattr(self.latest, field_name) - base
        return _q(delta / abs(base) * 100)


def _month_parts(when: date) -> tuple[str, str]:
    return when.strftime("%Y-%m"), when.strftime("%b %Y")


def build_business_trend(results: Sequence[ClassifiedTransaction]) -> Trend:
    """Month-on-month for a trading business."""
    buckets: dict[str, MonthRow] = {}
    for item in results:
        txn = item.transaction
        key, label = _month_parts(txn.timestamp.date())
        row = buckets.setdefault(key, MonthRow(key=key, label=label))
        row.transactions += 1
        row.fees += txn.fee_amount

        account = item.account
        if account in (Account.REVENUE_ORDERS, Account.REVENUE_DELIVERY):
            row.money_in += txn.gross_amount
        elif account is Account.OWNER_DRAWINGS:
            # Drawings are equity, not a business cost, so they sit outside
            # money_out — exactly as the Profit Pack treats them.
            row.drawings += txn.gross_amount
        elif account is not Account.UNRESOLVED:
            row.money_out += txn.gross_amount
            if account in (Account.COGS_RESTOCK, Account.COGS_SOURCING_TRANSPORT):
                row.cogs += txn.gross_amount

    trend = Trend(rows=[buckets[k] for k in sorted(buckets)], mode="Business")
    trend.insights = _business_insights(trend)
    return trend


def build_personal_trend(transactions: Sequence[Transaction]) -> Trend:
    """Month-on-month for a household."""
    from .personal import Spend, categorise_personal

    buckets: dict[str, MonthRow] = {}
    for txn in transactions:
        key, label = _month_parts(txn.timestamp.date())
        row = buckets.setdefault(key, MonthRow(key=key, label=label))
        row.transactions += 1
        row.fees += txn.fee_amount
        if txn.direction is Direction.IN:
            row.money_in += txn.gross_amount
            continue
        row.money_out += txn.gross_amount
        bucket = categorise_personal(f"{txn.entity_name} {txn.raw_text}")
        if bucket.is_essential:
            row.essentials += txn.gross_amount
        if bucket is Spend.SAVINGS:
            row.savings += txn.gross_amount

    trend = Trend(rows=[buckets[k] for k in sorted(buckets)], mode="Personal")
    trend.insights = _personal_insights(trend)
    return trend


def _direction_word(value: Decimal) -> str:
    return "up" if value > ZERO else "down"


def _business_insights(trend: Trend) -> list[str]:
    if not trend.is_available:
        return []
    out: list[str] = []
    latest, previous = trend.latest, trend.previous

    sales_pct = trend.change_pct("money_in")
    if sales_pct is not None and abs(sales_pct) >= 5:
        out.append(
            f"Sales are {_direction_word(sales_pct)} {abs(sales_pct)}% on "
            f"{previous.label} — {latest.money_in:,.0f} against "
            f"{previous.money_in:,.0f} KES."
        )

    net_change = trend.change("net")
    if net_change is not None and latest.net < ZERO <= previous.net:
        out.append(
            f"{latest.label} ran at a loss after a profitable {previous.label}. "
            "Check stock spend against sales for the month."
        )

    cogs_pct = trend.change_pct("cogs")
    if cogs_pct is not None and sales_pct is not None and cogs_pct - sales_pct >= 15:
        out.append(
            f"Stock spend rose {cogs_pct}% while sales moved {sales_pct}%. "
            "You are buying faster than you are selling."
        )

    if trend.best and trend.worst and trend.best.key != trend.worst.key:
        out.append(
            f"Best month was {trend.best.label} ({trend.best.net:,.0f} KES net); "
            f"weakest was {trend.worst.label} ({trend.worst.net:,.0f} KES)."
        )

    drawings_avg = trend.average("drawings")
    if drawings_avg > ZERO and latest.drawings > drawings_avg * Decimal("1.5"):
        out.append(
            f"You took out {latest.drawings:,.0f} KES in {latest.label}, well "
            f"above your {drawings_avg:,.0f} KES monthly average."
        )
    return out


def _personal_insights(trend: Trend) -> list[str]:
    if not trend.is_available:
        return []
    out: list[str] = []
    latest, previous = trend.latest, trend.previous

    spend_pct = trend.change_pct("money_out")
    if spend_pct is not None and abs(spend_pct) >= 5:
        out.append(
            f"Spending is {_direction_word(spend_pct)} {abs(spend_pct)}% on "
            f"{previous.label} — {latest.money_out:,.0f} against "
            f"{previous.money_out:,.0f} KES."
        )

    overspent = [r for r in trend.rows if r.net < ZERO]
    if overspent:
        out.append(
            f"You spent more than came in during {len(overspent)} of "
            f"{len(trend.rows)} months ("
            + ", ".join(r.label for r in overspent[:3])
            + ("…" if len(overspent) > 3 else "") + ")."
        )
    else:
        out.append("You stayed in surplus every month in this statement.")

    saved = [r for r in trend.rows if r.savings > ZERO]
    if not saved:
        out.append("No savings activity in any month covered here.")
    elif len(saved) < len(trend.rows):
        out.append(
            f"You saved in {len(saved)} of {len(trend.rows)} months. Making it "
            "monthly, however small, is what builds a buffer."
        )

    if trend.best and trend.worst and trend.best.key != trend.worst.key:
        out.append(
            f"Strongest month was {trend.best.label} ({trend.best.net:,.0f} KES "
            f"left over); tightest was {trend.worst.label} "
            f"({trend.worst.net:,.0f} KES)."
        )
    return out
