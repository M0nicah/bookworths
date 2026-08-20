"""Module B — Restock Safety Calculator.

The failure mode this exists to prevent: a seller sees KES 17,000 sitting in
M-Pesa, spends it all on a bale, and then cannot pay the rider, the shelf rent,
or their own rent. Safe-to-spend is cash minus every commitment that has not
been paid yet, minus a deliberate buffer.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from typing import Sequence

from ..schema import Account, ClassifiedTransaction

ZERO = Decimal("0")


def _q(v: Decimal) -> Decimal:
    return Decimal(v).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


@dataclass
class RestockBudget:
    cash_on_hand: Decimal
    shelf_rent_due: Decimal
    rider_balance_due: Decimal
    owner_draw_reserve: Decimal
    operating_buffer: Decimal
    unresolved_holdback: Decimal
    safe_to_spend: Decimal
    avg_monthly_restock: Decimal
    coverage_ratio: Decimal
    warnings: list[str] = field(default_factory=list)
    as_of: date = field(default_factory=date.today)

    @property
    def total_commitments(self) -> Decimal:
        return (
            self.shelf_rent_due
            + self.rider_balance_due
            + self.owner_draw_reserve
            + self.operating_buffer
            + self.unresolved_holdback
        )


def calculate_restock_budget(
    results: Sequence[ClassifiedTransaction],
    cash_on_hand: Decimal | None = None,
    *,
    shelf_rent_due: Decimal | None = None,
    rider_balance_due: Decimal | None = None,
    owner_draw_reserve: Decimal | None = None,
    buffer_pct: Decimal = Decimal("0.10"),
) -> RestockBudget:
    """Compute a safe next-restock figure.

    Any commitment left as ``None`` is estimated from the seller's own history in
    this statement rather than assumed to be zero — under-estimating commitments
    is the expensive direction of error.
    """
    if not results:
        raise ValueError("Cannot compute a restock budget from zero transactions.")

    ordered = sorted(results, key=lambda r: r.transaction.timestamp)

    if cash_on_hand is None:
        balances = [r.transaction.balance_after for r in ordered
                    if r.transaction.balance_after is not None]
        cash_on_hand = balances[-1] if balances else ZERO
    cash_on_hand = Decimal(cash_on_hand)

    # Distinct calendar months, not 30-day blocks — a one-month statement
    # must not be averaged as two.
    months = max(1, len({
        (r.transaction.timestamp.year, r.transaction.timestamp.month) for r in ordered
    }))

    def _total(account: Account) -> Decimal:
        return sum((r.transaction.gross_amount for r in results if r.account is account), ZERO)

    warnings: list[str] = []

    # Shelf rent: recurring, so the next one is the size of the last one.
    if shelf_rent_due is None:
        rents = [
            r.transaction.gross_amount
            for r in ordered
            if r.account is Account.LOGISTICS and "shelf" in r.classification.counterparty_label.lower()
        ]
        shelf_rent_due = rents[-1] if rents else ZERO
    shelf_rent_due = Decimal(shelf_rent_due)

    # Riders are paid per delivery; hold back a typical week of rider spend.
    if rider_balance_due is None:
        rider_payments = [
            r.transaction.gross_amount
            for r in results
            if r.account is Account.LOGISTICS
            and "shelf" not in r.classification.counterparty_label.lower()
        ]
        monthly_rider = sum(rider_payments, ZERO) / Decimal(months)
        rider_balance_due = _q(monthly_rider / Decimal("4"))
    rider_balance_due = Decimal(rider_balance_due)

    # The owner has to eat. Reserving their observed monthly draw is what stops
    # next month's groceries from being funded out of stock money.
    if owner_draw_reserve is None:
        owner_draw_reserve = _q(_total(Account.OWNER_DRAWINGS) / Decimal(months))
    owner_draw_reserve = Decimal(owner_draw_reserve)

    operating_buffer = _q(max(cash_on_hand, ZERO) * Decimal(buffer_pct))

    unresolved_holdback = sum(
        (r.transaction.gross_amount for r in results
         if r.needs_review and r.transaction.direction.value == "OUT"),
        ZERO,
    )

    safe = (
        cash_on_hand
        - shelf_rent_due
        - rider_balance_due
        - owner_draw_reserve
        - operating_buffer
        - unresolved_holdback
    )

    restock_total = _total(Account.COGS_RESTOCK)
    avg_restock = _q(restock_total / Decimal(months)) if restock_total else ZERO
    # Report coverage against the clamped figure — a negative "x of a run" is
    # not a meaningful thing to show a seller.
    safe_clamped = max(safe, ZERO)
    coverage = _q(safe_clamped / avg_restock) if avg_restock > ZERO else ZERO

    if safe <= ZERO:
        warnings.append(
            "Do NOT restock from M-Pesa right now — committed costs already exceed "
            "your cash. Collect outstanding orders first."
        )
    elif avg_restock > ZERO and safe < avg_restock * Decimal("0.5"):
        warnings.append(
            "Safe budget is under half your usual restock. Consider a smaller bale "
            "or splitting the run across two weeks."
        )
    if unresolved_holdback > ZERO:
        warnings.append(
            f"KES {_q(unresolved_holdback):,.2f} is held back pending confirmation of "
            "flagged transactions — confirm them to release it."
        )
    if owner_draw_reserve > _total(Account.COGS_RESTOCK) and _total(Account.COGS_RESTOCK) > ZERO:
        warnings.append(
            "Your personal drawings exceed your stock spend. The business is funding "
            "the household faster than it funds itself."
        )

    return RestockBudget(
        cash_on_hand=_q(cash_on_hand),
        shelf_rent_due=_q(shelf_rent_due),
        rider_balance_due=_q(rider_balance_due),
        owner_draw_reserve=_q(owner_draw_reserve),
        operating_buffer=operating_buffer,
        unresolved_holdback=_q(unresolved_holdback),
        safe_to_spend=_q(max(safe, ZERO)),
        avg_monthly_restock=avg_restock,
        coverage_ratio=coverage,
        warnings=warnings,
        as_of=ordered[-1].transaction.timestamp.date(),
    )


def render_restock_markdown(budget: RestockBudget) -> str:
    def money(v: Decimal) -> str:
        return f"KES {v:,.2f}"

    lines = [
        "## RESTOCK SAFETY BUDGET",
        f"_As at {budget.as_of:%d %b %Y}_",
        "",
        "| | Line | Amount |",
        "|---|---|---:|",
        f"| | Cash in M-Pesa | {money(budget.cash_on_hand)} |",
        f"| − | CBD shelf rent due | {money(budget.shelf_rent_due)} |",
        f"| − | Rider balances owing | {money(budget.rider_balance_due)} |",
        f"| − | Owner draw reserve (your living costs) | {money(budget.owner_draw_reserve)} |",
        f"| − | Operating buffer | {money(budget.operating_buffer)} |",
        f"| − | Held back for unconfirmed items | {money(budget.unresolved_holdback)} |",
        f"| **=** | **SAFE TO SPEND ON NEXT RESTOCK** | **{money(budget.safe_to_spend)}** |",
        "",
        f"Your typical restock run is **{money(budget.avg_monthly_restock)}** — "
        f"this budget covers **{budget.coverage_ratio}x** of one run.",
        "",
    ]
    if budget.warnings:
        lines.append("**Watch out:**")
        lines.append("")
        lines += [f"- ⚠️ {w}" for w in budget.warnings]
        lines.append("")
    return "\n".join(lines)
