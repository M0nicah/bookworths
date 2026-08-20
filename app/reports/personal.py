"""Module D — Personal Finance Analysis.

The Profit Pack deliberately treats every non-business shilling as one line:
"Owner Personal Drawings". That is the right call for the *business* books —
drawings are equity, not cost.

But the seller is also a household. Once they see that KES 25,040 left the
business for personal use, the next question is always the same: *on what?*
This module answers it by sub-categorising drawings only. It never touches, and
never re-opens, the business ledger.
"""
from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from enum import Enum
from typing import Sequence

from ..schema import Account, ClassifiedTransaction

ZERO = Decimal("0")


def _q(v: Decimal) -> Decimal:
    return Decimal(v).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


class Spend(str, Enum):
    """Household spending categories, tuned for a Kenyan urban household."""

    FOOD = "Food & Groceries"
    HOUSING = "Rent & Housing"
    UTILITIES = "Utilities & Airtime"
    TRANSPORT = "Transport & Fuel"
    FAMILY = "Family & Dependants"
    HEALTH = "Health & Medical"
    EDUCATION = "Education & Fees"
    LIFESTYLE = "Eating Out & Leisure"
    PERSONAL_CARE = "Personal Care"
    DEBT = "Loans & Repayments"
    SAVINGS = "Savings & Investment"
    GIVING = "Giving & Contributions"
    SHOPPING = "Shopping & Merchants"
    OTHER = "Other Personal"

    @property
    def is_essential(self) -> bool:
        """Essentials are what a household cannot simply stop paying."""
        return self in _ESSENTIALS


#: Spending a household cannot switch off next month without real consequences.
_ESSENTIALS = frozenset({
    Spend.FOOD, Spend.HOUSING, Spend.UTILITIES, Spend.TRANSPORT,
    Spend.HEALTH, Spend.EDUCATION, Spend.FAMILY, Spend.DEBT,
})

#: Longest match wins, so "school fees" beats a bare "fees".
_RULES: list[tuple[str, Spend]] = [
    # Food
    ("supermarket", Spend.FOOD), ("naivas", Spend.FOOD), ("quickmart", Spend.FOOD),
    ("carrefour", Spend.FOOD), ("chandarana", Spend.FOOD), ("tuskys", Spend.FOOD),
    ("cleanshelf", Spend.FOOD), ("groceries", Spend.FOOD), ("butchery", Spend.FOOD),
    ("greengrocer", Spend.FOOD), ("mama mboga", Spend.FOOD), ("soko", Spend.FOOD),
    # Housing
    ("rent", Spend.HOUSING), ("landlord", Spend.HOUSING), ("caretaker", Spend.HOUSING),
    ("service charge", Spend.HOUSING), ("apartment", Spend.HOUSING),
    # Utilities
    ("kplc", Spend.UTILITIES), ("kenya power", Spend.UTILITIES),
    ("token", Spend.UTILITIES), ("water", Spend.UTILITIES),
    ("airtime", Spend.UTILITIES), ("bundle", Spend.UTILITIES),
    ("safaricom", Spend.UTILITIES), ("zuku", Spend.UTILITIES),
    ("dstv", Spend.UTILITIES), ("gotv", Spend.UTILITIES), ("startimes", Spend.UTILITIES),
    ("netflix", Spend.UTILITIES), ("showmax", Spend.UTILITIES),
    # Transport
    ("fuel", Spend.TRANSPORT), ("petrol", Spend.TRANSPORT), ("shell", Spend.TRANSPORT),
    ("total energies", Spend.TRANSPORT), ("rubis", Spend.TRANSPORT),
    ("matatu", Spend.TRANSPORT), ("fare", Spend.TRANSPORT), ("uber", Spend.TRANSPORT),
    ("bolt", Spend.TRANSPORT), ("littlecab", Spend.TRANSPORT), ("parking", Spend.TRANSPORT),
    # Family
    ("family", Spend.FAMILY), ("mum", Spend.FAMILY), ("mom", Spend.FAMILY),
    ("dad", Spend.FAMILY), ("baba", Spend.FAMILY), ("mama watoto", Spend.FAMILY),
    ("upkeep", Spend.FAMILY), ("shosho", Spend.FAMILY), ("cucu", Spend.FAMILY),
    # Health
    ("hospital", Spend.HEALTH), ("clinic", Spend.HEALTH), ("chemist", Spend.HEALTH),
    ("pharmacy", Spend.HEALTH), ("nhif", Spend.HEALTH), ("sha", Spend.HEALTH),
    ("medical", Spend.HEALTH), ("dental", Spend.HEALTH), ("optical", Spend.HEALTH),
    # Education
    ("school fees", Spend.EDUCATION), ("school", Spend.EDUCATION),
    ("college", Spend.EDUCATION), ("university", Spend.EDUCATION),
    ("tuition", Spend.EDUCATION), ("hela imarika", Spend.EDUCATION),
    # Lifestyle
    ("java", Spend.LIFESTYLE), ("restaurant", Spend.LIFESTYLE), ("cafe", Spend.LIFESTYLE),
    ("kfc", Spend.LIFESTYLE), ("pizza", Spend.LIFESTYLE), ("burger", Spend.LIFESTYLE),
    ("night out", Spend.LIFESTYLE), ("club", Spend.LIFESTYLE), ("bar", Spend.LIFESTYLE),
    ("wines", Spend.LIFESTYLE), ("spirits", Spend.LIFESTYLE), ("betting", Spend.LIFESTYLE),
    ("sportpesa", Spend.LIFESTYLE), ("betika", Spend.LIFESTYLE), ("odibets", Spend.LIFESTYLE),
    ("movie", Spend.LIFESTYLE), ("cinema", Spend.LIFESTYLE),
    ("hotel", Spend.LIFESTYLE), ("eatery", Spend.LIFESTYLE),
    ("grill", Spend.LIFESTYLE), ("lounge", Spend.LIFESTYLE),
    ("bistro", Spend.LIFESTYLE), ("kibanda", Spend.LIFESTYLE),
    # Personal care
    ("salon", Spend.PERSONAL_CARE), ("barber", Spend.PERSONAL_CARE),
    ("spa", Spend.PERSONAL_CARE), ("kinyozi", Spend.PERSONAL_CARE),
    ("cosmetic", Spend.PERSONAL_CARE), ("boutique", Spend.PERSONAL_CARE),
    # Debt
    ("loan", Spend.DEBT), ("repayment", Spend.DEBT), ("fuliza", Spend.DEBT),
    ("mshwari", Spend.DEBT), ("kcb m-pesa", Spend.DEBT), ("tala", Spend.DEBT),
    ("branch", Spend.DEBT), ("overdraft", Spend.DEBT), ("sacco loan", Spend.DEBT),
    # Savings
    ("sacco", Spend.SAVINGS), ("chama", Spend.SAVINGS), ("mali", Spend.SAVINGS),
    ("m-shwari lock", Spend.SAVINGS), ("lock savings", Spend.SAVINGS),
    ("lock withdraw", Spend.SAVINGS), ("ziidi", Spend.SAVINGS),
    ("money market", Spend.SAVINGS), ("lock savings", Spend.SAVINGS),
    ("investment", Spend.SAVINGS), ("shares", Spend.SAVINGS),
    # Giving
    ("church", Spend.GIVING), ("tithe", Spend.GIVING), ("offering", Spend.GIVING),
    ("harambee", Spend.GIVING), ("mosque", Spend.GIVING), ("donation", Spend.GIVING),
    ("contribution", Spend.GIVING),
]


#: Structural hints. When no keyword matches, the transaction *type* still says
#: something: paying a till is buying goods or services, while sending to a
#: person is almost always supporting someone.
_MERCHANT_RE = re.compile(r"merchant\s+payment|pay\s*bill|buy\s+goods", re.IGNORECASE)
_PERSON_RE = re.compile(r"customer\s+transfer|send\s+money|funds\s+sent", re.IGNORECASE)


def categorise_personal(text: str) -> Spend:
    """Bucket one personal transaction by its counterparty text.

    Keyword rules first (longest match wins), then a structural fallback so a
    bare name does not automatically become "Other".
    """
    haystack = (text or "").lower()
    best: tuple[int, Spend] | None = None
    for needle, bucket in _RULES:
        if needle in haystack and (best is None or len(needle) > best[0]):
            best = (len(needle), bucket)
    if best is not None:
        return best[1]

    # No keyword matched. Fall back on what the transaction type implies.
    if _MERCHANT_RE.search(haystack):
        return Spend.SHOPPING
    if _PERSON_RE.search(haystack):
        return Spend.FAMILY
    return Spend.OTHER


@dataclass
class SpendLine:
    category: Spend
    amount: Decimal
    count: int
    share_pct: Decimal
    top_payees: list[tuple[str, Decimal]] = field(default_factory=list)


@dataclass
class PersonalReport:
    period_start: date
    period_end: date
    months: int

    total_drawings: Decimal
    business_profit: Decimal
    lines: list[SpendLine] = field(default_factory=list)
    insights: list[str] = field(default_factory=list)

    @property
    def essential_total(self) -> Decimal:
        return sum((l.amount for l in self.lines if l.category.is_essential), ZERO)

    @property
    def discretionary_total(self) -> Decimal:
        return sum((l.amount for l in self.lines if not l.category.is_essential), ZERO)

    @property
    def savings_total(self) -> Decimal:
        return sum((l.amount for l in self.lines if l.category is Spend.SAVINGS), ZERO)

    @property
    def debt_total(self) -> Decimal:
        return sum((l.amount for l in self.lines if l.category is Spend.DEBT), ZERO)

    @property
    def monthly_burn(self) -> Decimal:
        """Average personal spend per month — the household's run rate."""
        return _q(self.total_drawings / Decimal(max(1, self.months)))

    @property
    def essential_burn(self) -> Decimal:
        """The floor: what the household costs even after cutting extras."""
        return _q(self.essential_total / Decimal(max(1, self.months)))

    @property
    def savings_rate_pct(self) -> Decimal:
        if self.total_drawings <= ZERO:
            return ZERO
        return _q(self.savings_total / self.total_drawings * 100)

    @property
    def discretionary_pct(self) -> Decimal:
        if self.total_drawings <= ZERO:
            return ZERO
        return _q(self.discretionary_total / self.total_drawings * 100)

    @property
    def drawings_vs_profit_pct(self) -> Decimal:
        if self.business_profit <= ZERO:
            return ZERO
        return _q(self.total_drawings / self.business_profit * 100)

    @property
    def runway_months(self) -> Decimal:
        """How long savings alone would cover essentials if income stopped."""
        if self.essential_burn <= ZERO:
            return ZERO
        return _q(self.savings_total / self.essential_burn)


def build_personal_report(
    results: Sequence[ClassifiedTransaction],
    business_profit: Decimal = ZERO,
) -> PersonalReport:
    """Sub-categorise owner drawings into a household spending picture."""
    personal = [r for r in results if r.account is Account.OWNER_DRAWINGS]
    if not results:
        raise ValueError("Cannot build a personal report from zero transactions.")

    timestamps = [r.transaction.timestamp for r in results]
    start, end = min(timestamps).date(), max(timestamps).date()
    # Count distinct calendar months, not 30-day blocks: a single July
    # statement is one month, even though it spans 30 days.
    months = max(1, len({(t.year, t.month) for t in timestamps}))

    totals: dict[Spend, Decimal] = defaultdict(lambda: ZERO)
    counts: dict[Spend, int] = defaultdict(int)
    payees: dict[Spend, dict[str, Decimal]] = defaultdict(lambda: defaultdict(lambda: ZERO))

    for item in personal:
        txn = item.transaction
        label = item.classification.counterparty_label or txn.entity_name
        bucket = categorise_personal(f"{label} {txn.raw_text}")
        totals[bucket] += txn.gross_amount
        counts[bucket] += 1
        payees[bucket][label.title()] += txn.gross_amount

    total = sum(totals.values(), ZERO)
    lines = [
        SpendLine(
            category=bucket,
            amount=_q(amount),
            count=counts[bucket],
            share_pct=_q(amount / total * 100) if total > ZERO else ZERO,
            top_payees=sorted(payees[bucket].items(), key=lambda kv: kv[1], reverse=True)[:4],
        )
        for bucket, amount in sorted(totals.items(), key=lambda kv: kv[1], reverse=True)
    ]

    report = PersonalReport(
        period_start=start, period_end=end, months=months,
        total_drawings=_q(total), business_profit=Decimal(business_profit), lines=lines,
    )
    report.insights = _build_insights(report)
    return report


def _build_insights(report: PersonalReport) -> list[str]:
    """Plain-language observations. Each one must be actionable."""
    out: list[str] = []
    if report.total_drawings <= ZERO:
        return ["No personal spending was identified in this period."]

    if report.business_profit > ZERO:
        share = report.drawings_vs_profit_pct
        if share >= 100:
            out.append(
                f"You took out {share}% of business profit — more than the business "
                "earned. The shortfall comes out of working capital, which is what "
                "pays for your next restock."
            )
        elif share >= 80:
            out.append(
                f"You took out {share}% of business profit. Little is left to grow "
                "stock; aim to keep at least 20–30% in the business."
            )

    if report.savings_total <= ZERO:
        out.append(
            "No savings or investment activity was detected. Even a small standing "
            "amount per month builds the buffer that stops a bad week becoming a "
            "Fuliza loan."
        )
    else:
        out.append(
            f"You saved {report.savings_rate_pct}% of personal spending "
            f"({report.runway_months} months of essentials if income paused)."
        )

    if report.debt_total > ZERO:
        share = _q(report.debt_total / report.total_drawings * 100)
        if share >= 20:
            out.append(
                f"Loan repayments are {share}% of personal spending — heavy enough "
                "that borrowing costs are shaping your month."
            )

    if report.discretionary_pct >= 40:
        out.append(
            f"{report.discretionary_pct}% of personal spending is discretionary "
            "(eating out, leisure, personal care). This is the easiest place to "
            "free up cash without changing how you live day to day."
        )

    biggest = report.lines[0] if report.lines else None
    if biggest and biggest.share_pct >= 35:
        out.append(
            f"{biggest.category.value} alone is {biggest.share_pct}% of personal "
            f"spending ({_q(biggest.amount):,.0f} KES)."
        )

    unknown = next((l for l in report.lines if l.category is Spend.OTHER), None)
    if unknown and unknown.share_pct >= 25:
        out.append(
            f"{unknown.share_pct}% could not be categorised. Confirming those "
            "counterparties will sharpen this picture."
        )
    return out


def render_personal_markdown(report: PersonalReport) -> str:
    def money(v: Decimal) -> str:
        return f"KES {_q(v):,.2f}"

    period = f"{report.period_start:%d %b %Y} — {report.period_end:%d %b %Y}"
    lines = [
        "## PERSONAL FINANCE ANALYSIS",
        f"_{period} · {report.months} month(s)_",
        "",
        "> This covers **your household money only** — what left the business for "
        "personal use. It does not change the business profit figures.",
        "",
        "| Metric | Amount |",
        "|---|---:|",
        f"| Total personal spending | {money(report.total_drawings)} |",
        f"| Monthly burn rate | {money(report.monthly_burn)} |",
        f"| Essentials per month | {money(report.essential_burn)} |",
        f"| Savings / investment | {money(report.savings_total)} ({report.savings_rate_pct}%) |",
        f"| Discretionary share | {report.discretionary_pct}% |",
    ]
    if report.business_profit > ZERO:
        lines.append(
            f"| Share of business profit consumed | {report.drawings_vs_profit_pct}% |"
        )
    if report.savings_total > ZERO:
        lines.append(f"| Emergency runway | {report.runway_months} months |")

    lines += ["", "### Where your personal money went", "",
              "| Category | Amount | Share | Items | Essential |", "|---|---:|---:|---:|:--:|"]
    for line in report.lines:
        mark = "✓" if line.category.is_essential else "—"
        lines.append(
            f"| {line.category.value} | {money(line.amount)} | "
            f"{line.share_pct}% | {line.count} | {mark} |"
        )

    lines += ["", "### What this means", ""]
    lines += [f"- {insight}" for insight in report.insights] or ["- Nothing notable."]

    detailed = [l for l in report.lines if l.top_payees][:5]
    if detailed:
        lines += ["", "### Biggest personal payees", ""]
        for line in detailed:
            payees = ", ".join(
                f"{name} ({money(amount)})" for name, amount in line.top_payees[:3]
            )
            lines.append(f"- **{line.category.value}** — {payees}")

    return "\n".join(lines)
