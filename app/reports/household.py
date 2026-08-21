"""Personal mode — whole-statement household analysis.

This is NOT the business pipeline with different labels. It treats the entire
M-Pesa statement as one person's money: everything in is income, everything out
is spending, and the questions are the ones a household actually asks —

    Did I spend more than I earned?
    What is eating my money?
    Am I saving anything, or just servicing loans?
    How long could I survive if the money stopped?

Nothing here references sales, stock, margin or profit. A seller who wants those
should run Business mode instead.
"""
from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from enum import Enum
from typing import Sequence

from ..schema import Direction, Transaction
from .personal import Spend, categorise_personal

ZERO = Decimal("0")


def _q(v: Decimal) -> Decimal:
    return Decimal(v).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


class Income(str, Enum):
    """Where a household's money comes from."""

    SALARY = "Salary & Wages"
    BUSINESS = "Business & Sales Income"
    TRANSFERS = "Money Received from People"
    LOANS = "Loans & Overdrafts"
    REFUNDS = "Reversals & Refunds"
    SAVINGS_OUT = "Savings Withdrawn"
    OTHER = "Other Income"

    @property
    def is_earned(self) -> bool:
        """Earned income is money you actually made, not money you borrowed
        or moved from your own savings. Only earned income is sustainable."""
        return self in {Income.SALARY, Income.BUSINESS, Income.OTHER}


_INCOME_RULES: list[tuple[str, Income]] = [
    ("salary", Income.SALARY), ("payroll", Income.SALARY), ("wages", Income.SALARY),
    ("business payment from", Income.BUSINESS), ("merchant", Income.BUSINESS),
    ("till", Income.BUSINESS), ("customer payment", Income.BUSINESS),
    ("funds received", Income.TRANSFERS), ("received from", Income.TRANSFERS),
    ("fuliza", Income.LOANS), ("overdraft", Income.LOANS), ("od loan", Income.LOANS),
    ("m-shwari loan", Income.LOANS), ("kcb m-pesa loan", Income.LOANS),
    ("loan disburse", Income.LOANS), ("credit party", Income.LOANS),
    ("tala", Income.LOANS), ("branch loan", Income.LOANS),
    ("reversal", Income.REFUNDS), ("refund", Income.REFUNDS),
    ("m-shwari withdraw", Income.SAVINGS_OUT), ("lock withdraw", Income.SAVINGS_OUT),
    ("sacco withdraw", Income.SAVINGS_OUT), ("savings withdraw", Income.SAVINGS_OUT),
]


def categorise_income(text: str) -> Income:
    """Bucket one money-in transaction by source."""
    haystack = (text or "").lower()
    best: tuple[int, Income] | None = None
    for needle, bucket in _INCOME_RULES:
        if needle in haystack and (best is None or len(needle) > best[0]):
            best = (len(needle), bucket)
    return best[1] if best else Income.OTHER


@dataclass
class Line:
    label: str
    amount: Decimal
    count: int
    share_pct: Decimal
    essential: bool = False
    top_payees: list[tuple[str, Decimal]] = field(default_factory=list)


@dataclass
class HouseholdReport:
    period_start: date
    period_end: date
    months: int

    total_income: Decimal = ZERO
    total_spending: Decimal = ZERO
    total_fees: Decimal = ZERO
    closing_balance: Decimal = ZERO
    transaction_count: int = 0

    income_lines: list[Line] = field(default_factory=list)
    spend_lines: list[Line] = field(default_factory=list)
    insights: list[str] = field(default_factory=list)
    #: Kept so time-series charts can plot flow and balance over the period.
    transactions: list[Transaction] = field(default_factory=list)

    # --- headline figures --------------------------------------------------

    @property
    def net_position(self) -> Decimal:
        """Did more come in than went out? The single most important number."""
        return self.total_income - self.total_spending

    @property
    def earned_income(self) -> Decimal:
        return sum(
            (l.amount for l in self.income_lines
             if _income_of(l.label) and _income_of(l.label).is_earned),
            ZERO,
        )

    @property
    def borrowed_income(self) -> Decimal:
        return sum(
            (l.amount for l in self.income_lines if l.label == Income.LOANS.value), ZERO
        )

    @property
    def essential_spending(self) -> Decimal:
        return sum((l.amount for l in self.spend_lines if l.essential), ZERO)

    @property
    def discretionary_spending(self) -> Decimal:
        return sum((l.amount for l in self.spend_lines if not l.essential), ZERO)

    @property
    def savings_total(self) -> Decimal:
        return sum(
            (l.amount for l in self.spend_lines if l.label == Spend.SAVINGS.value), ZERO
        )

    @property
    def debt_payments(self) -> Decimal:
        return sum(
            (l.amount for l in self.spend_lines if l.label == Spend.DEBT.value), ZERO
        )

    # --- rates -------------------------------------------------------------

    @property
    def monthly_income(self) -> Decimal:
        return _q(self.total_income / Decimal(max(1, self.months)))

    @property
    def monthly_spending(self) -> Decimal:
        return _q(self.total_spending / Decimal(max(1, self.months)))

    @property
    def monthly_essentials(self) -> Decimal:
        return _q(self.essential_spending / Decimal(max(1, self.months)))

    @property
    def savings_rate_pct(self) -> Decimal:
        """Share of income kept. The number that decides long-run outcomes."""
        if self.total_income <= ZERO:
            return ZERO
        return _q(self.savings_total / self.total_income * 100)

    @property
    def debt_ratio_pct(self) -> Decimal:
        """Debt payments as a share of income. Above ~35% is heavy."""
        if self.total_income <= ZERO:
            return ZERO
        return _q(self.debt_payments / self.total_income * 100)

    @property
    def borrowed_share_pct(self) -> Decimal:
        """How much of 'income' was actually borrowed, not earned."""
        if self.total_income <= ZERO:
            return ZERO
        return _q(self.borrowed_income / self.total_income * 100)

    @property
    def runway_months(self) -> Decimal:
        if self.monthly_essentials <= ZERO:
            return ZERO
        return _q(self.closing_balance / self.monthly_essentials)

    @property
    def fee_pct(self) -> Decimal:
        if self.total_spending <= ZERO:
            return ZERO
        return _q(self.total_fees / self.total_spending * 100)


def _income_of(label: str) -> Income | None:
    for member in Income:
        if member.value == label:
            return member
    return None


def build_household_report(transactions: Sequence[Transaction]) -> HouseholdReport:
    """Analyse a whole statement as one household's money."""
    if not transactions:
        raise ValueError("Cannot build a household report from zero transactions.")

    stamps = [t.timestamp for t in transactions]
    report = HouseholdReport(
        period_start=min(stamps).date(),
        period_end=max(stamps).date(),
        months=max(1, len({(t.year, t.month) for t in stamps})),
        transaction_count=len(transactions),
        transactions=list(transactions),
    )

    income_totals: dict[Income, Decimal] = defaultdict(lambda: ZERO)
    income_counts: dict[Income, int] = defaultdict(int)
    spend_totals: dict[Spend, Decimal] = defaultdict(lambda: ZERO)
    spend_counts: dict[Spend, int] = defaultdict(int)
    spend_payees: dict[Spend, dict[str, Decimal]] = defaultdict(
        lambda: defaultdict(lambda: ZERO)
    )

    for txn in transactions:
        text = f"{txn.entity_name} {txn.raw_text}"
        report.total_fees += txn.fee_amount
        if txn.direction is Direction.IN:
            bucket = categorise_income(text)
            income_totals[bucket] += txn.gross_amount
            income_counts[bucket] += 1
            report.total_income += txn.gross_amount
        else:
            bucket = categorise_personal(text)
            spend_totals[bucket] += txn.gross_amount
            spend_counts[bucket] += 1
            spend_payees[bucket][txn.entity_name.title()] += txn.gross_amount
            report.total_spending += txn.gross_amount

    # Fees are real spending even when merged onto another line.
    report.total_spending += report.total_fees

    balances = [
        t.balance_after for t in sorted(transactions, key=lambda t: t.timestamp)
        if t.balance_after is not None
    ]
    report.closing_balance = balances[-1] if balances else ZERO

    report.income_lines = [
        Line(
            label=bucket.value, amount=_q(amount), count=income_counts[bucket],
            share_pct=_q(amount / report.total_income * 100)
            if report.total_income > ZERO else ZERO,
        )
        for bucket, amount in sorted(income_totals.items(), key=lambda kv: kv[1], reverse=True)
    ]
    report.spend_lines = [
        Line(
            label=bucket.value, amount=_q(amount), count=spend_counts[bucket],
            share_pct=_q(amount / report.total_spending * 100)
            if report.total_spending > ZERO else ZERO,
            essential=bucket.is_essential,
            top_payees=sorted(
                spend_payees[bucket].items(), key=lambda kv: kv[1], reverse=True
            )[:4],
        )
        for bucket, amount in sorted(spend_totals.items(), key=lambda kv: kv[1], reverse=True)
    ]
    report.insights = _insights(report)
    return report


def _insights(r: HouseholdReport) -> list[str]:
    out: list[str] = []

    if r.net_position < ZERO:
        out.append(
            f"You spent {_q(abs(r.net_position)):,.0f} KES more than came in. "
            "That gap is funded by savings or borrowing — neither lasts."
        )
    else:
        out.append(
            f"You kept {_q(r.net_position):,.0f} KES more than you spent over "
            f"{r.months} month(s)."
        )

    if r.borrowed_share_pct >= 20:
        out.append(
            f"{r.borrowed_share_pct}% of money coming in was borrowed "
            f"({_q(r.borrowed_income):,.0f} KES), not earned. Borrowing is "
            "propping up the month."
        )

    if r.debt_ratio_pct >= 35:
        out.append(
            f"Loan repayments take {r.debt_ratio_pct}% of your income — heavy. "
            "Clearing the most expensive loan first frees the most cash."
        )
    elif r.debt_ratio_pct > ZERO:
        out.append(f"Loan repayments take {r.debt_ratio_pct}% of your income.")

    if r.savings_total <= ZERO:
        out.append(
            "No savings activity was found. Even a small fixed amount each month "
            "is what stops a bad week turning into a loan."
        )
    else:
        out.append(
            f"You saved {r.savings_rate_pct}% of income. A common target is 10–20%."
        )

    if r.runway_months > ZERO:
        out.append(
            f"Your balance covers about {r.runway_months} month(s) of essentials "
            "if income stopped today."
        )

    if r.total_fees > ZERO:
        out.append(
            f"Transaction fees cost you {_q(r.total_fees):,.0f} KES "
            f"({r.fee_pct}% of spending). Fewer, larger transfers cost less."
        )

    if r.spend_lines and r.spend_lines[0].share_pct >= 30:
        top = r.spend_lines[0]
        out.append(
            f"{top.label} is your biggest outgoing at {top.share_pct}% "
            f"({_q(top.amount):,.0f} KES)."
        )
    return out


def render_household_markdown(r: HouseholdReport) -> str:
    def money(v: Decimal) -> str:
        return f"KES {_q(v):,.2f}"

    verdict = "SURPLUS" if r.net_position >= ZERO else "SHORTFALL"
    lines = [
        "# PERSONAL FINANCE REPORT",
        "### _Clean books, clear value_",
        "",
        f"**Period:** {r.period_start:%d %b %Y} — {r.period_end:%d %b %Y} "
        f"({r.months} month(s))  ",
        f"**Transactions:** {r.transaction_count}",
        "",
        "---",
        "",
        "## 1. Did you spend more than you earned?",
        "",
        "| | Amount | Per month |",
        "|---|---:|---:|",
        f"| Money in | {money(r.total_income)} | {money(r.monthly_income)} |",
        f"| Money out | {money(r.total_spending)} | {money(r.monthly_spending)} |",
        f"| **{verdict}** | **{money(r.net_position)}** | |",
        f"| Closing balance | {money(r.closing_balance)} | |",
        "",
        "## 2. Where your money came from",
        "",
        "| Source | Amount | Share | Items |",
        "|---|---:|---:|---:|",
    ]
    for line in r.income_lines:
        lines.append(
            f"| {line.label} | {money(line.amount)} | {line.share_pct}% | {line.count} |"
        )

    lines += [
        "",
        f"Earned income: **{money(r.earned_income)}** · "
        f"Borrowed: **{money(r.borrowed_income)}** ({r.borrowed_share_pct}%)",
        "",
        "## 3. Where your money went",
        "",
        "| Category | Amount | Share | Items | Essential |",
        "|---|---:|---:|---:|:--:|",
    ]
    for line in r.spend_lines:
        mark = "✓" if line.essential else "—"
        lines.append(
            f"| {line.label} | {money(line.amount)} | {line.share_pct}% | "
            f"{line.count} | {mark} |"
        )

    lines += [
        "",
        f"Essentials: **{money(r.essential_spending)}** · "
        f"Discretionary: **{money(r.discretionary_spending)}**",
        "",
        "## 4. Your financial health",
        "",
        "| Measure | Value | Healthy range |",
        "|---|---:|---|",
        f"| Savings rate | {r.savings_rate_pct}% | 10–20%+ |",
        f"| Debt repayments | {r.debt_ratio_pct}% of income | under 35% |",
        f"| Borrowed share of income | {r.borrowed_share_pct}% | as low as possible |",
        f"| Emergency runway | {r.runway_months} months | 3+ months |",
        f"| Transaction fees | {money(r.total_fees)} ({r.fee_pct}%) | as low as possible |",
        "",
        "## 5. What this means",
        "",
    ]
    lines += [f"- {insight}" for insight in r.insights]
    lines += ["", "---", "", "_Bookworths — clean books, clear value._"]
    return "\n".join(lines)
