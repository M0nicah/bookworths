"""Altair chart builders for the Bookworths GUI.

Colour and form follow one rule set:

  * Categorical hues are assigned in a fixed order and never cycled, so a
    category keeps its colour when a filter changes the row count.
  * Money in / money out is a *polarity*, so it uses the diverging pair
    (blue vs red) rather than two arbitrary categorical hues.
  * Every bar carries a direct value label. The mid-tone hues sit below 3:1
    against the surface, and visible labels are what makes that legible.
  * No pie or donut for close values — horizontal bars are read accurately at
    any number of categories, and category names get room to breathe.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Sequence

import altair as alt
import pandas as pd

from . import theme

# --- palette ---------------------------------------------------------------
# Sourced from the Pesabook theme. Never reorder: a category's colour must not
# change because another category appeared or vanished.
CATEGORICAL = theme.CATEGORICAL
POSITIVE = theme.MONEY_IN
NEGATIVE = theme.MONEY_OUT
NEUTRAL = theme.NEUTRAL
ESSENTIAL = theme.MONEY_IN
DISCRETIONARY = theme.STOCK

_LABEL = theme.INK_LABEL
_FONT = "IBM Plex Sans, -apple-system, Segoe UI, sans-serif"
_MONO = "IBM Plex Mono, ui-monospace, monospace"

_AXIS = alt.Axis(labelColor=_LABEL, titleColor=_LABEL, grid=False,
                 labelFont=_FONT, titleFont=_FONT)
_GRID = alt.Axis(labelColor=_LABEL, titleColor=_LABEL, gridColor=theme.TRACK,
                 tickCount=5, labelFont=_MONO, titleFont=_FONT)


def _fmt(value: float) -> str:
    """Compact KES label: 12,500 -> 12.5K, 1,250,000 -> 1.25M."""
    magnitude = abs(value)
    if magnitude >= 1_000_000:
        return f"{value / 1_000_000:,.2f}M"
    if magnitude >= 1_000:
        return f"{value / 1_000:,.1f}K"
    return f"{value:,.0f}"


def category_bars(
    labels: Sequence[str],
    amounts: Sequence[Decimal],
    *,
    title: str = "",
    height: int = 320,
) -> alt.LayerChart:
    """Horizontal bars, largest first, with direct value labels.

    Horizontal because category names are words, and a reader compares bar
    lengths against a shared baseline far more accurately than angles.
    """
    df = pd.DataFrame(
        {"Category": list(labels), "Amount": [float(a) for a in amounts]}
    ).sort_values("Amount", ascending=False)
    df["Label"] = df["Amount"].map(_fmt)

    base = alt.Chart(df).encode(
        y=alt.Y("Category:N", sort="-x", title=None, axis=_AXIS),
        x=alt.X("Amount:Q", title="KES", axis=_GRID),
    )
    bars = base.mark_bar(cornerRadiusEnd=4, height=18).encode(
        color=alt.Color(
            "Category:N",
            scale=alt.Scale(domain=df["Category"].tolist(), range=[theme.hue_for(c, i) for i, c in enumerate(df["Category"])]),
            legend=None,
        ),
        tooltip=[
            alt.Tooltip("Category:N"),
            alt.Tooltip("Amount:Q", format=",.2f", title="KES"),
        ],
    )
    # Direct labels are required relief for the mid-tone hues, not decoration.
    text = base.mark_text(align="left", dx=6, color=theme.INK_SECONDARY,
                          fontSize=11, font=_MONO).encode(
        text="Label:N"
    )
    return (bars + text).properties(
        height=height, title=title
    ).configure_view(strokeWidth=0)


def in_out_bars(
    income: Decimal, spending: Decimal, *, height: int = 190
) -> alt.LayerChart:
    """Money in vs money out — a polarity, so the diverging pair applies."""
    df = pd.DataFrame({
        "Flow": ["Money in", "Money out"],
        "Amount": [float(income), float(spending)],
    })
    df["Label"] = df["Amount"].map(_fmt)

    base = alt.Chart(df).encode(
        y=alt.Y("Flow:N", sort=["Money in", "Money out"], title=None, axis=_AXIS),
        x=alt.X("Amount:Q", title="KES", axis=_GRID),
    )
    bars = base.mark_bar(cornerRadiusEnd=4, height=32).encode(
        color=alt.Color(
            "Flow:N",
            scale=alt.Scale(domain=["Money in", "Money out"],
                            range=[POSITIVE, NEGATIVE]),
            legend=None,
        ),
        tooltip=[alt.Tooltip("Flow:N"), alt.Tooltip("Amount:Q", format=",.2f", title="KES")],
    )
    text = base.mark_text(align="left", dx=6, color=theme.INK_SECONDARY,
                          fontSize=12, font=_MONO).encode(
        text="Label:N"
    )
    return (bars + text).properties(height=height).configure_view(strokeWidth=0)


def monthly_flow(transactions, *, height: int = 300) -> alt.LayerChart:
    """Money in and out per month — the shape of the year, not one snapshot."""
    rows = [
        {
            "Month": t.timestamp.strftime("%Y-%m"),
            "Flow": "Money in" if t.direction.value == "IN" else "Money out",
            "Amount": float(t.gross_amount),
        }
        for t in transactions
    ]
    if not rows:
        return alt.LayerChart()
    df = pd.DataFrame(rows).groupby(["Month", "Flow"], as_index=False)["Amount"].sum()

    bars = alt.Chart(df).mark_bar(cornerRadiusEnd=3).encode(
        x=alt.X("Month:N", title=None, axis=_AXIS),
        y=alt.Y("Amount:Q", title="KES", axis=_GRID, stack=None),
        color=alt.Color(
            "Flow:N",
            scale=alt.Scale(domain=["Money in", "Money out"],
                            range=[POSITIVE, NEGATIVE]),
            legend=alt.Legend(title=None, orient="top", labelColor=_LABEL),
        ),
        xOffset="Flow:N",
        tooltip=[
            alt.Tooltip("Month:N"), alt.Tooltip("Flow:N"),
            alt.Tooltip("Amount:Q", format=",.2f", title="KES"),
        ],
    )
    return bars.properties(height=height).configure_view(strokeWidth=0)


def balance_line(transactions, *, height: int = 260) -> alt.Chart:
    """Running M-Pesa balance over time — did it trend up or bleed down?"""
    rows = [
        {"Date": t.timestamp, "Balance": float(t.balance_after)}
        for t in sorted(transactions, key=lambda t: t.timestamp)
        if t.balance_after is not None
    ]
    if not rows:
        return alt.Chart(pd.DataFrame({"Date": [], "Balance": []})).mark_line()
    df = pd.DataFrame(rows)

    hover = alt.selection_point(
        fields=["Date"], nearest=True, on="mouseover", empty=False
    )
    line = alt.Chart(df).mark_line(strokeWidth=2, color=POSITIVE).encode(
        x=alt.X("Date:T", title=None, axis=_AXIS),
        y=alt.Y("Balance:Q", title="Balance (KES)", axis=_GRID),
    )
    points = line.mark_point(size=60, filled=True, color=POSITIVE).encode(
        opacity=alt.condition(hover, alt.value(1), alt.value(0)),
        tooltip=[
            alt.Tooltip("Date:T", format="%d %b %Y"),
            alt.Tooltip("Balance:Q", format=",.2f", title="KES"),
        ],
    ).add_params(hover)
    return (line + points).properties(height=height).configure_view(strokeWidth=0)


def essential_split(essential: Decimal, discretionary: Decimal,
                    *, height: int = 130) -> alt.LayerChart:
    """A single stacked bar: what share of spending is truly unavoidable."""
    total = float(essential) + float(discretionary)
    if total <= 0:
        return alt.LayerChart()
    df = pd.DataFrame({
        "Kind": ["Essential", "Discretionary"],
        "Amount": [float(essential), float(discretionary)],
    })
    df["Share"] = df["Amount"] / total

    base = alt.Chart(df).encode(
        x=alt.X("Amount:Q", stack="normalize", title=None,
                axis=alt.Axis(format="%", labelColor=_LABEL, grid=False)),
        color=alt.Color(
            "Kind:N",
            scale=alt.Scale(domain=["Essential", "Discretionary"],
                            range=[ESSENTIAL, DISCRETIONARY]),
            legend=alt.Legend(title=None, orient="top", labelColor=_LABEL),
        ),
        tooltip=[
            alt.Tooltip("Kind:N"),
            alt.Tooltip("Amount:Q", format=",.2f", title="KES"),
            alt.Tooltip("Share:Q", format=".1%"),
        ],
    )
    # A 2px surface gap keeps the two fills from bleeding into one another.
    bar = base.mark_bar(height=44, stroke=theme.CARD, strokeWidth=2)
    text = base.mark_text(color="white", fontSize=12, fontWeight="bold", font=_MONO).encode(
        text=alt.Text("Share:Q", format=".0%")
    )
    return (bar + text).properties(height=height).configure_view(strokeWidth=0)


def profit_waterfall(pack, *, height: int = 330) -> alt.LayerChart:
    """Revenue → profit, showing what each cost takes out along the way.

    A waterfall is the right form here because the reader's question is not
    "how big is each cost" but "where did the revenue go".
    """
    steps = [
        ("Gross sales", float(pack.gross_revenue)),
        ("Stock (COGS)", -float(pack.total_cogs)),
        ("Logistics", -float(pack.logistics)),
        ("Marketing", -float(pack.marketing)),
        ("Packaging", -float(pack.packaging)),
        ("M-Pesa fees", -float(pack.financial_fees)),
    ]
    rows, running = [], 0.0
    for label, delta in steps:
        rows.append({
            "Step": label, "Start": running, "End": running + delta,
            "Delta": delta, "Kind": "Increase" if delta >= 0 else "Decrease",
        })
        running += delta
    rows.append({
        "Step": "Net profit", "Start": 0.0, "End": running,
        "Delta": running, "Kind": "Total",
    })
    df = pd.DataFrame(rows)
    df["Label"] = df["Delta"].map(_fmt)

    order = df["Step"].tolist()
    base = alt.Chart(df).encode(
        x=alt.X("Step:N", sort=order, title=None,
                axis=alt.Axis(labelAngle=-35, labelColor=_LABEL, grid=False))
    )
    bars = base.mark_bar(cornerRadius=3, size=34, stroke=theme.CARD, strokeWidth=2).encode(
        y=alt.Y("Start:Q", title="KES", axis=_GRID),
        y2="End:Q",
        color=alt.Color(
            "Kind:N",
            scale=alt.Scale(domain=["Increase", "Decrease", "Total"],
                            range=[POSITIVE, NEGATIVE, NEUTRAL]),
            legend=alt.Legend(title=None, orient="top", labelColor=_LABEL),
        ),
        tooltip=[
            alt.Tooltip("Step:N"),
            alt.Tooltip("Delta:Q", format=",.2f", title="Change (KES)"),
            alt.Tooltip("End:Q", format=",.2f", title="Running total"),
        ],
    )
    text = base.mark_text(dy=-8, color=theme.INK_SECONDARY, fontSize=10, font=_MONO).encode(
        y=alt.Y("End:Q"), text="Label:N"
    )
    return (bars + text).properties(height=height).configure_view(strokeWidth=0)
