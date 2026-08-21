"""Module A — the Bookworths Vendor Profit Pack.

One page. The seller should be able to read it on a phone in under a minute and
answer: did I make money, where did it leak, and how much of it did I already eat?
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP
from typing import Iterable, Sequence

from ..schema import Account, ClassifiedTransaction

TAGLINE = "Clean books, clear value"
ZERO = Decimal("0")


def _q(value: Decimal) -> Decimal:
    return Decimal(value).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def kes(value: Decimal) -> str:
    """Format a KES figure, negatives in accounting parentheses."""
    v = _q(value)
    if v < 0:
        return f"(KES {abs(v):,.2f})"
    return f"KES {v:,.2f}"


@dataclass
class LineItem:
    label: str
    amount: Decimal
    count: int = 0
    detail: list[tuple[str, Decimal]] = field(default_factory=list)


@dataclass
class ProfitPack:
    period_start: date
    period_end: date
    business_name: str

    revenue_orders: Decimal = ZERO
    revenue_delivery: Decimal = ZERO
    cogs_restock: Decimal = ZERO
    cogs_transport: Decimal = ZERO
    logistics: Decimal = ZERO
    marketing: Decimal = ZERO
    packaging: Decimal = ZERO
    financial_fees: Decimal = ZERO
    owner_drawings: Decimal = ZERO
    unresolved: Decimal = ZERO

    closing_balance: Decimal = ZERO
    transaction_count: int = 0
    exception_count: int = 0
    breakdowns: dict[str, list[tuple[str, Decimal]]] = field(default_factory=dict)

    # --- derived figures ---------------------------------------------------

    @property
    def gross_revenue(self) -> Decimal:
        return self.revenue_orders + self.revenue_delivery

    @property
    def total_cogs(self) -> Decimal:
        return self.cogs_restock + self.cogs_transport

    @property
    def gross_margin(self) -> Decimal:
        return self.gross_revenue - self.total_cogs

    @property
    def gross_margin_pct(self) -> Decimal:
        if self.gross_revenue == ZERO:
            return ZERO
        return _q(self.gross_margin / self.gross_revenue * 100)

    @property
    def total_operating(self) -> Decimal:
        return self.logistics + self.marketing + self.packaging + self.financial_fees

    @property
    def net_profit(self) -> Decimal:
        """Business profit BEFORE the owner takes anything out."""
        return self.gross_margin - self.total_operating

    @property
    def net_margin_pct(self) -> Decimal:
        if self.gross_revenue == ZERO:
            return ZERO
        return _q(self.net_profit / self.gross_revenue * 100)

    @property
    def profit_after_drawings(self) -> Decimal:
        """What is actually left in the business after personal consumption."""
        return self.net_profit - self.owner_drawings

    @property
    def leakage_pct(self) -> Decimal:
        if self.gross_revenue == ZERO:
            return ZERO
        return _q(self.financial_fees / self.gross_revenue * 100)

    @property
    def drawings_pct_of_profit(self) -> Decimal:
        if self.net_profit <= ZERO:
            return ZERO
        return _q(self.owner_drawings / self.net_profit * 100)


def build_profit_pack(
    results: Sequence[ClassifiedTransaction],
    business_name: str = "Bookworths Vendor",
) -> ProfitPack:
    if not results:
        raise ValueError("Cannot build a Profit Pack from zero transactions.")

    timestamps = [r.transaction.timestamp for r in results]
    pack = ProfitPack(
        period_start=min(timestamps).date(),
        period_end=max(timestamps).date(),
        business_name=business_name,
        transaction_count=len(results),
    )

    bucket: dict[Account, Decimal] = defaultdict(lambda: ZERO)
    detail: dict[Account, dict[str, Decimal]] = defaultdict(lambda: defaultdict(lambda: ZERO))
    fees_total = ZERO

    for item in results:
        txn = item.transaction
        account = item.account
        bucket[account] += txn.gross_amount
        label = item.classification.counterparty_label or txn.entity_name.title()
        detail[account][label] += txn.gross_amount
        # Tariffs merged onto a payment line are leakage no matter which
        # account the payment itself landed in.
        fees_total += txn.fee_amount
        if item.needs_review:
            pack.exception_count += 1

    pack.revenue_orders = bucket[Account.REVENUE_ORDERS]
    pack.revenue_delivery = bucket[Account.REVENUE_DELIVERY]
    pack.cogs_restock = bucket[Account.COGS_RESTOCK]
    pack.cogs_transport = bucket[Account.COGS_SOURCING_TRANSPORT]
    pack.logistics = bucket[Account.LOGISTICS]
    pack.marketing = bucket[Account.MARKETING]
    pack.packaging = bucket[Account.PACKAGING]
    # Standalone fee transactions + fees merged onto other payment lines.
    pack.financial_fees = bucket[Account.FINANCIAL_FEES] + fees_total
    pack.owner_drawings = bucket[Account.OWNER_DRAWINGS]
    pack.unresolved = bucket[Account.UNRESOLVED]

    balances = [
        r.transaction.balance_after
        for r in sorted(results, key=lambda r: r.transaction.timestamp)
        if r.transaction.balance_after is not None
    ]
    pack.closing_balance = balances[-1] if balances else ZERO

    pack.breakdowns = {
        account.label: sorted(items.items(), key=lambda kv: kv[1], reverse=True)[:6]
        for account, items in detail.items()
        if items
    }
    return pack


# --- Markdown --------------------------------------------------------------

def render_markdown(pack: ProfitPack, include_breakdown: bool = True) -> str:
    """Render the Profit Pack.

    `include_breakdown` is off in the GUI, which draws the per-account
    breakdown as colour-coded columns rather than a long stack of tables.
    """
    period = f"{pack.period_start:%d %b %Y} — {pack.period_end:%d %b %Y}"
    lines = [
        "# BOOKWORTHS VENDOR PROFIT PACK",
        f"### _{TAGLINE}_",
        "",
        f"**Vendor:** {pack.business_name}  ",
        f"**Period:** {period}  ",
        f"**Transactions analysed:** {pack.transaction_count}  ",
        f"**Generated:** {datetime.now():%d %b %Y %H:%M}",
        "",
        "---",
        "",
        "## 1. Where the money actually went",
        "",
        "| | Line | Amount |",
        "|---|---|---:|",
        f"| | **Gross Sales Revenue** | **{kes(pack.gross_revenue)}** |",
        f"| | &nbsp;&nbsp;Customer Orders | {kes(pack.revenue_orders)} |",
        f"| | &nbsp;&nbsp;Delivery Fees Collected | {kes(pack.revenue_delivery)} |",
        f"| − | Cost of Stock Restocked (COGS) | {kes(-pack.total_cogs)} |",
        f"| **=** | **Gross Product Margin** | **{kes(pack.gross_margin)}** |",
        f"| | _Margin %_ | _{pack.gross_margin_pct}%_ |",
        f"| − | Logistics, Riders & Parcel Fees | {kes(-pack.logistics)} |",
        f"| − | Marketing & IG Boosts | {kes(-pack.marketing)} |",
        f"| − | Packaging & Branding | {kes(-pack.packaging)} |",
        f"| − | ⚠️ Safaricom Tariffs & Fees **(Hidden Leakage)** | {kes(-pack.financial_fees)} |",
        f"| **=** | **NET TAKE-HOME BUSINESS PROFIT** | **{kes(pack.net_profit)}** |",
        f"| | _Net margin %_ | _{pack.net_margin_pct}%_ |",
        "",
        "---",
        "",
        "## 2. What you already took out",
        "",
        "> Owner drawings are **not** a business cost — they are profit you have",
        "> already spent on yourself. Shown separately so the profit figure above",
        "> stays honest.",
        "",
        "| Line | Amount |",
        "|---|---:|",
        f"| Owner Personal Drawings (groceries, family, personal) | {kes(pack.owner_drawings)} |",
        f"| Share of business profit consumed | {pack.drawings_pct_of_profit}% |",
        f"| **Profit left in the business** | **{kes(pack.profit_after_drawings)}** |",
        "",
        "---",
        "",
        "## 3. Hidden leakages",
        "",
        f"You paid **{kes(pack.financial_fees)}** in Safaricom tariffs and Fuliza fees "
        f"this period — that is **{pack.leakage_pct}%** of everything you sold.",
        "",
    ]

    if pack.unresolved > ZERO:
        lines += [
            f"**KES {_q(pack.unresolved):,.2f}** across {pack.exception_count} transaction(s) "
            "could not be confirmed and is excluded from the figures above.",
            "",
        ]

    if include_breakdown:
        lines += ["---", "", "## 4. Category breakdown", ""]
        for account_label, items in pack.breakdowns.items():
            if not items:
                continue
            lines.append(f"**{account_label}**")
            lines.append("")
            lines.append("| Counterparty | Amount |")
            lines.append("|---|---:|")
            for name, amount in items:
                lines.append(f"| {name} | {kes(amount)} |")
            lines.append("")

    lines += [
        "---",
        "",
        f"_Bookworths — {TAGLINE}. Figures derived from M-Pesa statement data; "
        "confirm flagged items before filing._",
    ]
    return "\n".join(lines)


# --- Printable HTML --------------------------------------------------------

_CSS = """
:root {
  --ink: #14231c; --muted: #5d6f66; --line: #d9e2dc;
  --accent: #0f7a4f; --warn: #b23a2f; --bg: #ffffff; --panel: #f4f8f5;
}
* { box-sizing: border-box; }
body {
  margin: 0; padding: 32px; background: var(--bg); color: var(--ink);
  font: 15px/1.55 "Inter", "Helvetica Neue", Arial, sans-serif;
}
.sheet { max-width: 820px; margin: 0 auto; }
header { border-bottom: 3px solid var(--accent); padding-bottom: 16px; margin-bottom: 24px; }
h1 { margin: 0; font-size: 26px; letter-spacing: .01em; }
.tagline { color: var(--accent); font-style: italic; font-weight: 600; margin-top: 4px; }
.meta { color: var(--muted); font-size: 13px; margin-top: 10px; }
h2 { font-size: 16px; text-transform: uppercase; letter-spacing: .06em;
     color: var(--muted); margin: 28px 0 10px; }
table { width: 100%; border-collapse: collapse; }
th, td { padding: 8px 10px; border-bottom: 1px solid var(--line); text-align: left; }
td.amt, th.amt { text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }
tr.total td { border-top: 2px solid var(--ink); border-bottom: 2px solid var(--ink);
              font-weight: 700; font-size: 16px; }
tr.sub td { color: var(--muted); padding-left: 26px; font-size: 14px; }
tr.leak td { color: var(--warn); font-weight: 600; }
.pct { color: var(--muted); font-size: 13px; font-style: italic; }
.cards { display: flex; gap: 12px; flex-wrap: wrap; margin: 16px 0 4px; }
.card { flex: 1 1 180px; background: var(--panel); border: 1px solid var(--line);
        border-radius: 10px; padding: 14px 16px; }
.card .k { font-size: 12px; color: var(--muted); text-transform: uppercase; letter-spacing: .05em; }
.card .v { font-size: 20px; font-weight: 700; margin-top: 4px; font-variant-numeric: tabular-nums; }
.card.warn .v { color: var(--warn); }
.card.good .v { color: var(--accent); }
.note { background: var(--panel); border-left: 4px solid var(--accent);
        padding: 12px 16px; border-radius: 0 8px 8px 0; margin: 14px 0; color: var(--muted); }
footer { margin-top: 32px; padding-top: 14px; border-top: 1px solid var(--line);
         color: var(--muted); font-size: 12px; }
@media print {
  body { padding: 0; font-size: 12px; }
  .sheet { max-width: none; }
  h2 { margin-top: 18px; }
  @page { size: A4; margin: 14mm; }
}
"""


def render_html(pack: ProfitPack) -> str:
    from ..assets.logo import logo_svg

    _logo = logo_svg(46)
    period = f"{pack.period_start:%d %b %Y} &ndash; {pack.period_end:%d %b %Y}"

    breakdown_html = []
    for account_label, items in pack.breakdowns.items():
        if not items:
            continue
        rows = "".join(
            f"<tr><td>{name}</td><td class='amt'>{kes(amount)}</td></tr>"
            for name, amount in items
        )
        breakdown_html.append(
            f"<h2>{account_label}</h2><table><tbody>{rows}</tbody></table>"
        )

    unresolved_note = ""
    if pack.unresolved > ZERO:
        unresolved_note = (
            f"<div class='note'><strong>{kes(pack.unresolved)}</strong> across "
            f"{pack.exception_count} transaction(s) is still unconfirmed and sits "
            f"outside the figures above.</div>"
        )

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Bookworths Vendor Profit Pack — {pack.business_name}</title>
<style>{_CSS}</style></head><body>
<div class="sheet">
  <header>
    <div class="brand">{_logo}<h1>Bookworths Vendor Profit Pack</h1></div>
    <div class="tagline">{TAGLINE}</div>
    <div class="meta">
      <strong>{pack.business_name}</strong> &middot; {period} &middot;
      {pack.transaction_count} transactions &middot;
      generated {datetime.now():%d %b %Y %H:%M}
    </div>
  </header>

  <div class="cards">
    <div class="card"><div class="k">Gross Sales</div><div class="v">{kes(pack.gross_revenue)}</div></div>
    <div class="card good"><div class="k">Net Business Profit</div><div class="v">{kes(pack.net_profit)}</div></div>
    <div class="card warn"><div class="k">Hidden Leakage</div><div class="v">{kes(pack.financial_fees)}</div></div>
    <div class="card"><div class="k">Owner Drawings</div><div class="v">{kes(pack.owner_drawings)}</div></div>
  </div>

  <h2>Profit &amp; Loss</h2>
  <table>
    <tbody>
      <tr><td><strong>Gross Sales Revenue</strong></td><td class="amt"><strong>{kes(pack.gross_revenue)}</strong></td></tr>
      <tr class="sub"><td>Customer Orders</td><td class="amt">{kes(pack.revenue_orders)}</td></tr>
      <tr class="sub"><td>Delivery Fees Collected</td><td class="amt">{kes(pack.revenue_delivery)}</td></tr>
      <tr><td>&minus; Cost of Stock Restocked (COGS)</td><td class="amt">{kes(-pack.total_cogs)}</td></tr>
      <tr class="total"><td>= Gross Product Margin</td><td class="amt">{kes(pack.gross_margin)}</td></tr>
      <tr><td class="pct">Gross margin</td><td class="amt pct">{pack.gross_margin_pct}%</td></tr>
      <tr><td>&minus; Logistics, Riders &amp; Parcel Fees</td><td class="amt">{kes(-pack.logistics)}</td></tr>
      <tr><td>&minus; Marketing &amp; IG Boosts</td><td class="amt">{kes(-pack.marketing)}</td></tr>
      <tr><td>&minus; Packaging &amp; Branding</td><td class="amt">{kes(-pack.packaging)}</td></tr>
      <tr class="leak"><td>&minus; Safaricom Tariffs &amp; Fees (hidden leakage)</td><td class="amt">{kes(-pack.financial_fees)}</td></tr>
      <tr class="total"><td>= NET TAKE-HOME BUSINESS PROFIT</td><td class="amt">{kes(pack.net_profit)}</td></tr>
      <tr><td class="pct">Net margin</td><td class="amt pct">{pack.net_margin_pct}%</td></tr>
    </tbody>
  </table>

  <h2>Owner Personal Drawings</h2>
  <div class="note">
    Drawings are not a business cost &mdash; they are profit you have already spent
    on yourself. Kept separate so the profit figure above stays honest.
  </div>
  <table>
    <tbody>
      <tr><td>Owner Personal Drawings</td><td class="amt">{kes(pack.owner_drawings)}</td></tr>
      <tr><td>Share of business profit consumed</td><td class="amt">{pack.drawings_pct_of_profit}%</td></tr>
      <tr class="total"><td>Profit left in the business</td><td class="amt">{kes(pack.profit_after_drawings)}</td></tr>
    </tbody>
  </table>

  <h2>Hidden Leakages</h2>
  <div class="note">
    You paid <strong>{kes(pack.financial_fees)}</strong> in Safaricom tariffs and
    Fuliza fees this period &mdash; <strong>{pack.leakage_pct}%</strong> of everything you sold.
  </div>
  {unresolved_note}

  {''.join(breakdown_html)}

  <footer>Bookworths &mdash; {TAGLINE}. Figures derived from M-Pesa statement data;
  confirm flagged items before filing.</footer>
</div>
</body></html>"""
