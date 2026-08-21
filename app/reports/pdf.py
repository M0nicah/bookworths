"""PDF rendering for the Profit Pack and the Personal Finance Report.

Built with reportlab rather than an HTML-to-PDF converter: those need system
libraries (Cairo, wkhtmltopdf) that a managed host will not have, and this must
work on Streamlit Cloud unchanged.

The layout mirrors the on-screen cards — same figures, same colours, same
ordering — so a seller who prints this recognises what they saw.
"""
from __future__ import annotations

import io
from decimal import Decimal

from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    KeepTogether,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from . import theme

_INK = colors.HexColor(theme.INK)
_MUTED = colors.HexColor(theme.INK_MUTED)
_LABEL = colors.HexColor(theme.INK_LABEL)
_LINE = colors.HexColor(theme.BORDER)
_RULE = colors.HexColor(theme.ROW_RULE)
_IN = colors.HexColor(theme.MONEY_IN)
_OUT = colors.HexColor(theme.MONEY_OUT)
_CARD = colors.HexColor(theme.CARD)


def _styles() -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle(
            "t", parent=base["Title"], fontName="Times-Roman", fontSize=24,
            textColor=_INK, alignment=0, spaceAfter=2, leading=28,
        ),
        "tag": ParagraphStyle(
            "g", parent=base["Normal"], fontName="Helvetica-Oblique", fontSize=10,
            textColor=colors.HexColor(theme.MONEY_IN), spaceAfter=10,
        ),
        "meta": ParagraphStyle(
            "m", parent=base["Normal"], fontName="Helvetica", fontSize=9,
            textColor=_MUTED, spaceAfter=14,
        ),
        "h2": ParagraphStyle(
            "h", parent=base["Normal"], fontName="Helvetica-Bold", fontSize=9,
            textColor=_LABEL, spaceBefore=14, spaceAfter=6, leading=12,
        ),
        "body": ParagraphStyle(
            "b", parent=base["Normal"], fontName="Helvetica", fontSize=9.5,
            textColor=_INK, leading=14, spaceAfter=5,
        ),
        "note": ParagraphStyle(
            "n", parent=base["Normal"], fontName="Helvetica", fontSize=8.5,
            textColor=_MUTED, leading=12, spaceAfter=4,
        ),
        "foot": ParagraphStyle(
            "f", parent=base["Normal"], fontName="Helvetica", fontSize=7.5,
            textColor=_MUTED, leading=10,
        ),
    }


def _money(value: Decimal) -> str:
    value = Decimal(value)
    if value < 0:
        return f"(KES {abs(value):,.2f})"
    return f"KES {value:,.2f}"


def _figure_table(rows, *, total_row: int | None = None, width=170 * mm) -> Table:
    """A two-column figures table: label left, amount right."""
    table = Table(rows, colWidths=[width * 0.62, width * 0.38])
    style = [
        ("FONT", (0, 0), (-1, -1), "Helvetica", 9.5),
        ("FONT", (1, 0), (1, -1), "Courier", 9.5),
        ("ALIGN", (1, 0), (1, -1), "RIGHT"),
        ("TEXTCOLOR", (0, 0), (-1, -1), _INK),
        ("LINEBELOW", (0, 0), (-1, -1), 0.4, _RULE),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
    ]
    if total_row is not None:
        style += [
            ("FONT", (0, total_row), (0, total_row), "Helvetica-Bold", 10.5),
            ("FONT", (1, total_row), (1, total_row), "Courier-Bold", 10.5),
            ("LINEABOVE", (0, total_row), (-1, total_row), 1.1, _INK),
            ("LINEBELOW", (0, total_row), (-1, total_row), 0, colors.white),
            ("TOPPADDING", (0, total_row), (-1, total_row), 7),
        ]
    table.setStyle(TableStyle(style))
    return table


def _header(story, styles, title: str, subtitle: str, tagline: str) -> None:
    story.append(Paragraph(title, styles["title"]))
    story.append(Paragraph(tagline, styles["tag"]))
    story.append(Paragraph(subtitle, styles["meta"]))


def profit_pack_pdf(pack, business_name: str = "") -> bytes:
    """Render the Vendor Profit Pack as a print-ready A4 PDF."""
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=A4,
        leftMargin=20 * mm, rightMargin=20 * mm,
        topMargin=18 * mm, bottomMargin=16 * mm,
        title=f"Bookworths Profit Pack — {business_name or pack.business_name}",
        author="Bookworths",
    )
    styles = _styles()
    story: list = []

    _header(
        story, styles, "Bookworths Vendor Profit Pack",
        f"{business_name or pack.business_name} &nbsp;·&nbsp; "
        f"{pack.period_start:%d %b %Y} to {pack.period_end:%d %b %Y} "
        f"&nbsp;·&nbsp; {pack.transaction_count} transactions",
        "Clean books, clear value",
    )

    story.append(Paragraph("WHERE THE MONEY ACTUALLY WENT", styles["h2"]))
    story.append(_figure_table([
        ["Gross sales revenue", _money(pack.gross_revenue)],
        ["Less: cost of stock restocked", _money(-pack.total_cogs)],
        ["Gross product margin", _money(pack.gross_margin)],
        ["Less: logistics, riders and parcels", _money(-pack.logistics)],
        ["Less: marketing and boosts", _money(-pack.marketing)],
        ["Less: packaging and branding", _money(-pack.packaging)],
        ["Less: Safaricom tariffs and fees", _money(-pack.financial_fees)],
        ["NET TAKE-HOME BUSINESS PROFIT", _money(pack.net_profit)],
    ], total_row=7))
    story.append(Spacer(1, 4))
    story.append(Paragraph(
        f"Gross margin {pack.gross_margin_pct}% &nbsp;·&nbsp; "
        f"net margin {pack.net_margin_pct}%", styles["note"]))

    story.append(Paragraph("WHAT YOU ALREADY TOOK OUT", styles["h2"]))
    story.append(_figure_table([
        ["Net business profit", _money(pack.net_profit)],
        ["Less: owner personal drawings", _money(-pack.owner_drawings)],
        ["LEFT IN THE BUSINESS", _money(pack.profit_after_drawings)],
    ], total_row=2))
    story.append(Spacer(1, 4))
    story.append(Paragraph(
        f"You consumed {pack.drawings_pct_of_profit}% of profit personally. "
        "Drawings are not a business cost — they are profit you have already "
        "spent on yourself.", styles["note"]))

    story.append(Paragraph("HIDDEN LEAKAGES", styles["h2"]))
    story.append(Paragraph(
        f"You paid <b>{_money(pack.financial_fees)}</b> in Safaricom tariffs and "
        f"Fuliza fees — {pack.leakage_pct}% of everything you sold. Fees are "
        "charged per transaction, so fewer, larger transfers cost less.",
        styles["body"]))

    if pack.breakdowns:
        story.append(PageBreak())
        story.append(Paragraph("CATEGORY BREAKDOWN", styles["h2"]))
        for label, items in pack.breakdowns.items():
            if not items:
                continue
            hue = colors.HexColor(theme.hue_for(label))
            head = Table([[label.upper()]], colWidths=[170 * mm])
            head.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, -1), hue),
                ("TEXTCOLOR", (0, 0), (-1, -1), colors.HexColor(theme.PAGE)),
                ("FONT", (0, 0), (-1, -1), "Helvetica-Bold", 8),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ]))
            rows = [[name, f"{amount:,.2f}"] for name, amount in items]
            story.append(KeepTogether([head, _figure_table(rows), Spacer(1, 9)]))

    story.append(Spacer(1, 14))
    story.append(Paragraph(
        "Bookworths — clean books, clear value. Figures derived from M-Pesa "
        "statement data; confirm any flagged items before relying on them.",
        styles["foot"]))

    doc.build(buffer and story, onFirstPage=_rule, onLaterPages=_rule)
    return buffer.getvalue()


#: The mark, as reportlab path instructions. Same geometry as the SVG, in the
#: SVG's own 120x108 coordinate space; the caller scales and positions it.
_BARS = [
    [(34, 62), (34, 40), (45, 34), (45, 62)],
    [(50, 62), (50, 22), (62, 15), (62, 62)],
    [(67, 62), (67, 34), (78, 28), (78, 62)],
    [(83, 62), (83, 12), (95, 5), (95, 62)],
]


def _draw_mark(canvas, x: float, y: float, size: float, colour, alpha: float = 1.0):
    """Draw the Bookworths mark with its lower-left corner at (x, y).

    reportlab has no SVG support, so the mark is redrawn with native path
    calls. `size` is the width in points; height follows the 120:108 ratio.
    """
    canvas.saveState()
    canvas.translate(x, y)
    scale = size / 120.0
    canvas.scale(scale, scale)
    canvas.setFillColor(colour)
    if alpha < 1.0:
        canvas.setFillAlpha(alpha)

    # Bars. The SVG y-axis points down and the PDF's points up, so flip.
    for bar in _BARS:
        path = canvas.beginPath()
        first = True
        for px, py in bar:
            if first:
                path.moveTo(px, 108 - py)
                first = False
            else:
                path.lineTo(px, 108 - py)
        path.close()
        canvas.drawPath(path, stroke=0, fill=1)

    # The two open leaves, as cubic curves matching the SVG.
    for leaf in (
        [(58, 66), (46, 56), (30, 54), (12, 57), (4, 78), (24, 74), (44, 76), (58, 86)],
        [(62, 66), (74, 56), (90, 54), (108, 57), (116, 78), (96, 74), (76, 76), (62, 86)],
    ):
        pts = [(px, 108 - py) for px, py in leaf]
        path = canvas.beginPath()
        path.moveTo(*pts[0])
        path.curveTo(*pts[1], *pts[2], *pts[3])
        path.lineTo(*pts[4])
        path.curveTo(*pts[5], *pts[6], *pts[7])
        path.close()
        canvas.drawPath(path, stroke=0, fill=1)
    canvas.restoreState()


def _rule(canvas, doc) -> None:
    """Brand furniture drawn on every page: watermark, rule, logo, footer."""
    canvas.saveState()

    # Watermark first, so everything else sits on top of it.
    page_w, page_h = doc.pagesize
    mark = 300
    _draw_mark(
        canvas, (page_w - mark) / 2, (page_h - mark * 0.9) / 2,
        mark, _IN, alpha=0.045,
    )

    # Header rule with the mark sitting on it.
    y = page_h - 13 * mm
    canvas.setStrokeColor(_IN)
    canvas.setLineWidth(2.2)
    canvas.line(20 * mm, y, page_w - 20 * mm, y)
    _draw_mark(canvas, page_w - 20 * mm - 26, y + 4, 26, _IN)

    canvas.setFont("Helvetica", 7.5)
    canvas.setFillColor(_MUTED)
    canvas.drawRightString(page_w - 20 * mm, 10 * mm, f"Page {doc.page}")
    canvas.drawString(20 * mm, 10 * mm, "Bookworths — clean books, clear value")
    canvas.restoreState()


def personal_report_pdf(report, name: str = "") -> bytes:
    """Render the Personal Finance Report as a print-ready A4 PDF."""
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=A4,
        leftMargin=20 * mm, rightMargin=20 * mm,
        topMargin=18 * mm, bottomMargin=16 * mm,
        title="Bookworths Personal Finance Report", author="Bookworths",
    )
    styles = _styles()
    story: list = []

    _header(
        story, styles, "Personal Finance Report",
        (f"{name} &nbsp;·&nbsp; " if name else "")
        + f"{report.period_start:%d %b %Y} to {report.period_end:%d %b %Y} "
        f"&nbsp;·&nbsp; {report.months} month(s) "
        f"&nbsp;·&nbsp; {report.transaction_count} transactions",
        "Clean books, clear value",
    )

    surplus = report.net_position >= 0
    story.append(Paragraph("DID YOU SPEND MORE THAN YOU EARNED?", styles["h2"]))
    story.append(_figure_table([
        ["Money in", _money(report.total_income)],
        ["Money out", _money(report.total_spending)],
        ["SURPLUS" if surplus else "SHORTFALL", _money(report.net_position)],
        ["Closing balance", _money(report.closing_balance)],
    ], total_row=2))

    story.append(Paragraph("WHERE YOUR MONEY CAME FROM", styles["h2"]))
    story.append(_figure_table(
        [[line.label, f"{line.amount:,.2f}   {line.share_pct}%"]
         for line in report.income_lines]))
    story.append(Spacer(1, 4))
    story.append(Paragraph(
        f"Earned {_money(report.earned_income)} &nbsp;·&nbsp; "
        f"borrowed {_money(report.borrowed_income)} "
        f"({report.borrowed_share_pct}%). Borrowed money and savings withdrawn "
        "are cash you can spend, but not income you made.", styles["note"]))

    story.append(Paragraph("WHERE YOUR MONEY WENT", styles["h2"]))
    story.append(_figure_table(
        [[f"{line.label}{'  (essential)' if line.essential else ''}",
          f"{line.amount:,.2f}   {line.share_pct}%"]
         for line in report.spend_lines]))

    story.append(PageBreak())
    story.append(Paragraph("YOUR FINANCIAL HEALTH", styles["h2"]))
    checks = Table([
        ["Measure", "You", "Healthy range"],
        ["Savings rate", f"{report.savings_rate_pct}%", "10-20% or more"],
        ["Debt repayments", f"{report.debt_ratio_pct}% of income", "under 35%"],
        ["Borrowed share of income", f"{report.borrowed_share_pct}%", "as low as possible"],
        ["Emergency runway", f"{report.runway_months} months", "3 months or more"],
        ["Transaction fees", f"{_money(report.total_fees)} ({report.fee_pct}%)",
         "as low as possible"],
    ], colWidths=[70 * mm, 50 * mm, 50 * mm])
    checks.setStyle(TableStyle([
        ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 8),
        ("TEXTCOLOR", (0, 0), (-1, 0), _LABEL),
        ("FONT", (0, 1), (-1, -1), "Helvetica", 9),
        ("FONT", (1, 1), (1, -1), "Courier", 9),
        ("LINEBELOW", (0, 0), (-1, -1), 0.4, _RULE),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
    ]))
    story.append(checks)

    story.append(Paragraph("WHAT THIS MEANS", styles["h2"]))
    for insight in report.insights:
        story.append(Paragraph(f"• {insight}", styles["body"]))

    story.append(Spacer(1, 14))
    story.append(Paragraph(
        "Bookworths — clean books, clear value. Figures derived from M-Pesa "
        "statement data. General guidance, not financial advice.", styles["foot"]))

    doc.build(story, onFirstPage=_rule, onLaterPages=_rule)
    return buffer.getvalue()
