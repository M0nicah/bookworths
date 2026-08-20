"""Design tokens — the Pesabook theme.

Imported from the Claude Design project "M-Pesa Bookkeeping Analyzer": a warm
cream canvas, a dark-green sidebar, Instrument Serif headings over IBM Plex
Sans, and IBM Plex Mono for every numeral.

Two category hues were nudged from the source. The design's brown (#8C5A2B) and
blue (#4A6FA5) fall below the chroma floor as chart fills — they read as grey
rather than as colours. They are deepened here to #9A5A1E / #3A6FB5, which
clears the floor while staying visually faithful. Chips and text keep the
original values, where the lower chroma is doing real work.

Money-in green and money-out red separate at only ΔE 6.7 under protanopia, so
they are never the sole signal: every amount carries a +/- prefix, and the
in/out charts are separately labelled.
"""
from __future__ import annotations

# --- surfaces --------------------------------------------------------------
PAGE = "#F4F0E6"
CARD = "#FFFDF6"
INSET = "#EAE4D4"
ROW_HOVER = "#FAF6EA"

# --- borders ---------------------------------------------------------------
BORDER = "#E3DCC8"
DIVIDER = "#DCD4BE"
ROW_RULE = "#EFE9DA"
TRACK = "#EEE8D8"

# --- dark sidebar ----------------------------------------------------------
SIDEBAR = "#1B2A21"
SIDEBAR_TEXT = "#EDE8DA"
SIDEBAR_MUTED = "#9DB2A4"
SIDEBAR_RULE = "#33443A"
SIDEBAR_ACTIVE = "#5E7264"
SIDEBAR_SUCCESS = "#7FC79E"

# --- ink -------------------------------------------------------------------
INK = "#1B2A21"
INK_NUMERIC = "#3D4A40"
INK_SECONDARY = "#5C6B60"
INK_LABEL = "#7A8578"
INK_MUTED = "#8A9488"

# --- semantic --------------------------------------------------------------
MONEY_IN = "#1F7A4D"
MONEY_OUT = "#C2452D"
LINK = "#1F7A4D"
LINK_HOVER = "#145636"

#: Chart-safe category hues (validated: chroma floor, contrast, normal-vision).
STOCK = "#9A5A1E"       # deepened from #8C5A2B
DELIVERY = "#3A6FB5"    # deepened from #4A6FA5
BILLS = "#B8860B"
WITHDRAWAL = "#6B4E9B"
NEUTRAL = "#8A9488"

#: Fixed order — a category must keep its hue however many are shown.
CATEGORICAL = [MONEY_IN, STOCK, DELIVERY, BILLS, WITHDRAWAL, MONEY_OUT, "#7A8578", NEUTRAL]

#: Soft chip pairs (background, foreground) straight from the design.
CHIPS: dict[str, tuple[str, str]] = {
    "income": ("#E2EFE5", "#1F7A4D"),
    "stock": ("#EFE6DA", "#6E4722"),
    "delivery": ("#E2E9F3", "#3A5885"),
    "bills": ("#F3EBD3", "#8A6508"),
    "sent": ("#F6E3DD", "#A03A24"),
    "withdrawal": ("#EAE3F3", "#563E80"),
    "fees": ("#ECECE6", "#5C6B60"),
}

# --- typography ------------------------------------------------------------
FONT_DISPLAY = "'Instrument Serif', Georgia, serif"
FONT_BODY = "'IBM Plex Sans', -apple-system, Segoe UI, sans-serif"
FONT_MONO = "'IBM Plex Mono', ui-monospace, SFMono-Regular, monospace"
FONT_URL = (
    "https://fonts.googleapis.com/css2?"
    "family=Instrument+Serif:ital@0;1"
    "&family=IBM+Plex+Sans:wght@400;500;600"
    "&family=IBM+Plex+Mono:wght@400;500;600&display=swap"
)

# --- shape -----------------------------------------------------------------
RADIUS_KPI = "14px"
RADIUS_PANEL = "16px"
RADIUS_CARD = "10px"
RADIUS_PILL = "999px"
EASE = "cubic-bezier(0.23, 1, 0.32, 1)"


#: Chart account -> hue. Business and household categories share the map so a
#: category reads the same colour in either mode.
ACCOUNT_HUES: dict[str, str] = {
    # Business accounts
    "Customer Orders": MONEY_IN,
    "Delivery Fees Collected": MONEY_IN,
    "Stock Restocks & Bale Purchases": STOCK,
    "Sourcing Transport": STOCK,
    "Logistics & Delivery": DELIVERY,
    "Marketing & Visibility": WITHDRAWAL,
    "Packaging & Operational": BILLS,
    "Transaction Tariffs & Financial Fees": MONEY_OUT,
    "Owner Personal Drawings": BILLS,
    "Unresolved — Needs Seller Confirmation": NEUTRAL,
    # Household spending
    "Food & Groceries": STOCK,
    "Rent & Housing": BILLS,
    "Utilities & Airtime": DELIVERY,
    "Transport & Fuel": DELIVERY,
    "Family & Dependants": MONEY_OUT,
    "Health & Medical": WITHDRAWAL,
    "Education & Fees": BILLS,
    "Eating Out & Leisure": STOCK,
    "Personal Care": WITHDRAWAL,
    "Loans & Repayments": MONEY_OUT,
    "Savings & Investment": MONEY_IN,
    "Giving & Contributions": WITHDRAWAL,
    "Shopping & Merchants": STOCK,
    "Other Personal": NEUTRAL,
    # Household income
    "Salary & Wages": MONEY_IN,
    "Business & Sales Income": MONEY_IN,
    "Money Received from People": DELIVERY,
    "Loans & Overdrafts": MONEY_OUT,
    "Reversals & Refunds": NEUTRAL,
    "Savings Withdrawn": BILLS,
    "Other Income": NEUTRAL,
}


def hue_for(label: str, fallback_index: int = 0) -> str:
    """Colour for a named category, stable across renders."""
    return ACCOUNT_HUES.get(label, CATEGORICAL[fallback_index % len(CATEGORICAL)])
