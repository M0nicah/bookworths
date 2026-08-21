"""Bookworths — Streamlit interface.

Run with:  streamlit run app.py

The GUI is a thin shell over `app.pipeline.run_pipeline`. Everything it shows is
computed by the same code path the CLI uses, so the two can never disagree.
"""
from __future__ import annotations

import io
import os
import tempfile
import uuid
from decimal import Decimal
from pathlib import Path

import pandas as pd
import streamlit as st

from app import TAGLINE, __version__
from app.engine import EntityMemory
from app.ingest import load_statement_csv, load_statement_pdf, normalize_statement
from app.mockdata import load_mock_statement
from app.pipeline import BookworthsResult
from app.engine import CategorizationEngine
from app.reports.profit_pack import build_profit_pack, kes, render_html, render_markdown
from app.reports import charts, theme
from app.reports.people import Counterparty, top_counterparties
from app.reports.household import build_household_report, render_household_markdown
from app.reports.personal import build_personal_report, render_personal_markdown
from app.reports.restock import calculate_restock_budget, render_restock_markdown
from app.reports.whatsapp import build_whatsapp_draft
from app.schema import Account

def _db_path() -> str:
    """Where this visitor's learned counterparty memory lives.

    Locally that is a single shared file, which is what you want: the app gets
    smarter every run. Deployed, every visitor gets their own file keyed to
    their session, so one person's confirmed suppliers never leak into another
    person's categorisations. Sessions are ephemeral by design — a deployed
    demo should not accumulate strangers' financial relationships on disk.
    """
    if not os.getenv("BOOKWORTHS_MULTIUSER"):
        return "data/bookworths.db"
    if "db_path" not in st.session_state:
        st.session_state["db_path"] = (
            f"{tempfile.gettempdir()}/bookworths-{uuid.uuid4().hex[:12]}.db"
        )
    return st.session_state["db_path"]


DB_PATH = _db_path()

#: The one-word answers a seller can give a flagged transaction, and where each
#: one files it. Mirrors the WhatsApp reply key so both channels agree.
QUICK_ANSWERS: list[tuple[str, str, Account]] = [
    ("STOCK", "Stock / bale", Account.COGS_RESTOCK),
    ("RIDER", "Rider / delivery", Account.LOGISTICS),
    ("ADS", "IG boost / promo", Account.MARKETING),
    ("PACK", "Packaging", Account.PACKAGING),
    ("ME", "Personal / family", Account.OWNER_DRAWINGS),
]

st.set_page_config(
    page_title="Bookworths — Clear books, real value",
    page_icon="B",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Design tokens come from the Pesabook theme (app/reports/theme.py), so the
# page, the cards and the chart marks are all drawn from one source.
st.html(
    f"""
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="{theme.FONT_URL}" rel="stylesheet">
<style>
  :root {{
    --page: {theme.PAGE};  --card: {theme.CARD};  --inset: {theme.INSET};
    --border: {theme.BORDER};  --divider: {theme.DIVIDER};
    --rule: {theme.ROW_RULE};  --track: {theme.TRACK};
    --sidebar: {theme.SIDEBAR};  --sidebar-text: {theme.SIDEBAR_TEXT};
    --sidebar-muted: {theme.SIDEBAR_MUTED};  --sidebar-rule: {theme.SIDEBAR_RULE};
        --sidebar-active: {theme.SIDEBAR_ACTIVE};
    --ink: {theme.INK};  --ink-num: {theme.INK_NUMERIC};
    --ink-2: {theme.INK_SECONDARY};  --label: {theme.INK_LABEL};
    --muted: {theme.INK_MUTED};
    --in: {theme.MONEY_IN};  --out: {theme.MONEY_OUT};
    --serif: {theme.FONT_DISPLAY};  --sans: {theme.FONT_BODY};
    --mono: {theme.FONT_MONO};
  }}

  .stApp {{ background: var(--page); }}
  html, body, [class*="css"] {{ font-family: var(--sans); color: var(--ink); }}
  h1, h2, h3 {{ font-family: var(--serif) !important; font-weight: 400 !important;
                letter-spacing: -0.01em; }}
  a {{ color: {theme.LINK}; text-decoration: none; }}
  a:hover {{ color: {theme.LINK_HOVER}; text-decoration: underline; }}
  @keyframes riseIn {{ from {{ opacity:0; transform:translateY(10px); }}
                       to {{ opacity:1; transform:translateY(0); }} }}
  .main .block-container {{ animation: riseIn .5s {theme.EASE} both;
                            padding-top: 2rem; max-width: 1500px; }}

  /* Dark sidebar ------------------------------------------------------ */
  section[data-testid="stSidebar"] {{ background: var(--sidebar); }}
  section[data-testid="stSidebar"] * {{ color: var(--sidebar-text); }}
  section[data-testid="stSidebar"] hr {{ border-color: var(--sidebar-rule); }}
  section[data-testid="stSidebar"] .stCaption,
  section[data-testid="stSidebar"] label,
  section[data-testid="stSidebar"] p {{ color: var(--sidebar-muted) !important; }}
  section[data-testid="stSidebar"] h1,
  section[data-testid="stSidebar"] h2,
  section[data-testid="stSidebar"] h3 {{ color: {theme.PAGE} !important; }}
  /* Inputs sit on the dark ground, so they need an opaque dark fill and
     explicitly light text. Streamlit's own secondaryBackgroundColor would
     otherwise paint them cream and leave the text unreadable. */
  section[data-testid="stSidebar"] input,
  section[data-testid="stSidebar"] textarea,
  section[data-testid="stSidebar"] div[data-baseweb="input"],
  section[data-testid="stSidebar"] div[data-baseweb="base-input"],
  section[data-testid="stSidebar"] div[data-baseweb="select"] > div,
  section[data-testid="stSidebar"] div[data-testid="stNumberInputContainer"],
  section[data-testid="stSidebar"] div[data-testid="stFileUploaderDropzone"] {{
    background: #24352B !important;
    border-color: var(--sidebar-active) !important;
    color: {theme.PAGE} !important;
  }}
  section[data-testid="stSidebar"] input,
  section[data-testid="stSidebar"] textarea,
  section[data-testid="stSidebar"] div[data-baseweb="select"] div {{
    color: {theme.PAGE} !important;
    -webkit-text-fill-color: {theme.PAGE} !important;
  }}
  /* Placeholders must be dimmer than real input, but still legible. */
  section[data-testid="stSidebar"] input::placeholder,
  section[data-testid="stSidebar"] textarea::placeholder {{
    color: #A9BCAE !important;
    -webkit-text-fill-color: #A9BCAE !important;
    opacity: 1;
  }}
  /* File uploader. Test IDs confirmed against the installed Streamlit build:
     stFileUploader (outer), stFileUploaderDropzone, …DropzoneInput,
     …DropzoneInstructions. The instructions line carries the size/format hint
     and was the least readable element on the dark ground. */
  section[data-testid="stSidebar"] div[data-testid="stFileUploader"],
  section[data-testid="stSidebar"] div[data-testid="stFileUploaderDropzone"] {{
    background: #24352B !important;
    border: 1px dashed var(--sidebar-active) !important;
    color: {theme.PAGE} !important;
  }}
  section[data-testid="stSidebar"] div[data-testid="stFileUploader"] * {{
    color: {theme.PAGE} !important;
  }}
  section[data-testid="stSidebar"] div[data-testid="stFileUploaderDropzoneInstructions"],
  section[data-testid="stSidebar"] div[data-testid="stFileUploaderDropzoneInstructions"] * {{
    color: #A9BCAE !important;
  }}
  /* The Browse button reads as the action, so it gets a solid fill. */
  section[data-testid="stSidebar"] div[data-testid="stFileUploader"] button {{
    background: var(--sidebar-active) !important;
    color: {theme.PAGE} !important;
    border: 1px solid var(--sidebar-active) !important;
    font-weight: 600;
  }}
  section[data-testid="stSidebar"] div[data-testid="stFileUploader"] button:hover {{
    background: {theme.MONEY_IN} !important;
    border-color: {theme.MONEY_IN} !important;
  }}
  /* The uploaded-file row that replaces the hint once a file is chosen. */
  section[data-testid="stSidebar"] div[data-testid="stFileUploader"] li,
  section[data-testid="stSidebar"] div[data-testid="stFileUploader"] li * {{
    color: {theme.PAGE} !important;
  }}
  section[data-testid="stSidebar"] div[data-testid="stFileUploader"] li {{
    background: rgba(255,255,255,.05) !important;
    border-radius: 8px;
  }}
  section[data-testid="stSidebar"] div[data-testid="stFileUploader"] svg {{
    fill: {theme.PAGE} !important; color: {theme.PAGE} !important;
  }}

  /* Number-input steppers and the password reveal eye. */
  section[data-testid="stSidebar"] button[data-testid="stNumberInputStepUp"],
  section[data-testid="stSidebar"] button[data-testid="stNumberInputStepDown"],
  section[data-testid="stSidebar"] div[data-baseweb="input"] button {{
    background: transparent !important; color: {theme.PAGE} !important;
  }}
  section[data-testid="stSidebar"] svg {{ fill: {theme.PAGE}; }}

  /* Masthead ---------------------------------------------------------- */
  .bw-head {{ margin: 0 0 22px; }}
  .bw-head h1 {{ font-family: var(--serif); font-size: 40px; margin: 0;
                 color: var(--ink); }}
  .bw-head .tag {{ font-size: 14px; color: var(--ink-2); margin-top: 6px; }}
  .bw-head .mode {{ display: inline-block; margin-top: 12px; padding: 5px 15px;
                    border-radius: {theme.RADIUS_PILL}; background: var(--inset);
                    border: 1px solid var(--divider); color: var(--ink-2);
                    font-size: 11px; letter-spacing: .08em; text-transform: uppercase;
                    font-weight: 600; }}

  .bw-sec {{ font-size: 12px; text-transform: uppercase; letter-spacing: .08em;
             color: var(--label); font-weight: 600; margin: 6px 0 10px; }}

  /* KPI tiles --------------------------------------------------------- */
  div[data-testid="stMetric"] {{
    background: var(--card); border: 1px solid var(--border);
    border-radius: {theme.RADIUS_KPI}; padding: 18px 20px;
  }}
  div[data-testid="stMetricValue"] {{
    font-family: var(--mono) !important; font-size: 1.5rem !important;
    font-weight: 600; letter-spacing: -0.02em; color: var(--ink);
  }}
  div[data-testid="stMetricLabel"] p {{
    font-size: 12px !important; text-transform: uppercase;
    letter-spacing: .07em; color: var(--label) !important; font-weight: 600;
  }}
  div[data-testid="stMetricDelta"] {{ font-family: var(--mono); font-size: 12px; }}

  /* Panels & notes ---------------------------------------------------- */
  .bw-panel {{ background: var(--card); border: 1px solid var(--border);
               border-radius: {theme.RADIUS_PANEL}; padding: 20px 22px; }}
  .bw-note, .bw-warn, .bw-flag, .bw-insight {{
    border-radius: {theme.RADIUS_CARD}; padding: 12px 16px; margin-bottom: 8px;
    background: var(--card); border: 1px solid var(--border);
    border-left: 4px solid var(--in); font-size: .92rem;
  }}
  .bw-warn {{ border-left-color: var(--out); }}
  .bw-flag {{ border-left-color: {theme.BILLS}; }}
  .bw-insight {{ border-left-color: {theme.DELIVERY}; }}

  /* Tabs -------------------------------------------------------------- */
  div[data-baseweb="tab-list"] {{ border-bottom: 1px solid var(--divider); gap: 2px;
                                  overflow-x: auto; scrollbar-width: thin; }}
  button[data-baseweb="tab"] {{ font-weight: 600; font-size: 20px;
                                color: var(--label); padding: 12px 16px 14px; }}
  button[data-baseweb="tab"][aria-selected="true"] {{ color: var(--ink); }}
  div[data-baseweb="tab-highlight"] {{ background-color: var(--in); height: 3px; }}

  /* The action tab is the only one that blocks the numbers, so its count
     renders as a red pill and the tab keeps that colour when unselected. */
  @keyframes bwPulse {{
    0%, 100% {{ box-shadow: 0 0 0 0 rgba(194,69,45,.45); }}
    50%      {{ box-shadow: 0 0 0 6px rgba(194,69,45,0); }}
  }}
  div[data-baseweb="tab-list"] button[data-baseweb="tab"]:nth-of-type(2) {{
    color: var(--out) !important;
  }}
  div[data-baseweb="tab-list"] button[data-baseweb="tab"]:nth-of-type(2) code {{
    background: var(--out); color: {theme.PAGE};
    font-family: var(--mono); font-size: 14px; font-weight: 700;
    padding: 3px 11px; border-radius: 999px; margin-left: 5px;
    animation: bwPulse 2.4s ease-in-out infinite;
  }}

  /* Call-to-action banner, shown above the tabs from every tab. */
  .bw-cta {{ display: flex; align-items: center; gap: 16px;
             background: {theme.CHIPS["sent"][0]};
             border: 1px solid var(--out);
             border-left: 5px solid var(--out);
             border-radius: {theme.RADIUS_PANEL};
             padding: 14px 20px; margin: 4px 0 18px; }}
  .bw-cta .icon {{ width: 38px; height: 38px; flex: none; border-radius: 999px;
                   background: var(--out); color: {theme.PAGE};
                   font-family: var(--mono); font-size: 15px; font-weight: 700;
                   display: flex; align-items: center; justify-content: center;
                   animation: bwPulse 2.4s ease-in-out infinite; }}
  .bw-cta .body {{ flex: 1; min-width: 0; }}
  .bw-cta .t {{ font-weight: 700; font-size: 15px; color: var(--ink); }}
  .bw-cta .d {{ font-size: 13px; color: var(--ink-2); margin-top: 2px; }}
  .bw-cta .amt {{ font-family: var(--mono); font-weight: 700; color: var(--out);
                  white-space: nowrap; font-size: 15px; }}

  .bw-cta-ok {{ display: flex; align-items: center; gap: 14px;
                background: {theme.CHIPS["income"][0]};
                border: 1px solid var(--in); border-left: 5px solid var(--in);
                border-radius: {theme.RADIUS_PANEL}; padding: 12px 20px;
                margin: 4px 0 18px; }}
  .bw-cta-ok .icon {{ width: 34px; height: 34px; flex: none; border-radius: 999px;
                      background: var(--in); color: {theme.PAGE}; font-weight: 700;
                      display: flex; align-items: center; justify-content: center; }}

  .stButton button[kind="primary"] {{
    background: var(--sidebar); border-color: var(--sidebar);
    border-radius: {theme.RADIUS_PILL}; font-weight: 600; padding: 8px 22px;
  }}
  .stButton button {{ border-radius: {theme.RADIUS_PILL}; font-family: var(--sans); }}
  div[data-testid="stDataFrame"] {{ border-radius: {theme.RADIUS_CARD}; }}

  /* Breakdown / P&L cards --------------------------------------------- */
  .bw-bd {{ background: var(--card); border: 1px solid var(--border);
            border-radius: {theme.RADIUS_PANEL}; overflow: hidden;
            margin-bottom: 14px; }}
  .bw-bd-head {{ background: var(--accent); color: {theme.PAGE};
                 padding: 9px 14px; font-size: 12px; font-weight: 600;
                 text-transform: uppercase; letter-spacing: .07em; }}
  .bw-bd-table {{ width: 100%; border-collapse: collapse; font-size: .86rem; }}
  .bw-bd-table td {{ padding: 7px 14px; border-bottom: 1px solid var(--rule); }}
  .bw-bd-table tr:last-child td {{ border-bottom: none; }}
  .bw-bd-table td.n {{ text-align: right; font-family: var(--mono);
                       color: var(--ink-num); font-weight: 600; white-space: nowrap; }}
  .bw-bd-table tr.tot td {{ border-top: 2px solid var(--ink); border-bottom: none;
                            font-weight: 700; font-size: .94rem; padding-top: 9px; }}
  .bw-bd-note {{ padding: 10px 14px; font-size: 12px; color: var(--muted);
                 background: {theme.ROW_HOVER}; border-top: 1px solid var(--rule); }}

  /* People panel ------------------------------------------------------ */
  .bw-people {{ display: flex; flex-direction: column; }}
  .bw-person {{ display: flex; align-items: center; gap: 12px;
                padding: 10px 0; border-bottom: 1px solid var(--rule); }}
  .bw-person:last-child {{ border-bottom: none; }}
  .bw-av {{ width: 34px; height: 34px; border-radius: {theme.RADIUS_PILL};
            background: var(--inset); color: var(--ink); display: flex;
            align-items: center; justify-content: center; font-size: 13px;
            font-weight: 600; flex: none; }}
  .bw-person-main {{ min-width: 0; flex: 1; }}
  .bw-person-name {{ font-size: 13px; font-weight: 600; overflow: hidden;
                     text-overflow: ellipsis; white-space: nowrap; }}
  .bw-person-sub {{ font-family: var(--mono); font-size: 11px; color: var(--muted); }}
  .bw-person-amt {{ font-family: var(--mono); font-size: 13px; font-weight: 600;
                    white-space: nowrap; }}

  /* Transactions table ------------------------------------------------ */
  .bw-txn {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
  .bw-txn th {{ font-size: 11px; font-weight: 600; letter-spacing: .07em;
                text-transform: uppercase; color: var(--label);
                text-align: left; padding: 0 8px 10px;
                border-bottom: 1px solid var(--border); }}
  .bw-txn td {{ padding: 11px 8px; border-bottom: 1px solid var(--rule);
                vertical-align: middle; }}
  .bw-txn tr:last-child td {{ border-bottom: none; }}
  .bw-txn tbody tr:hover {{ background: {theme.ROW_HOVER}; }}
  .bw-txn td.c {{ font-family: var(--mono); font-size: 11px;
                  color: var(--ink-2); white-space: nowrap; }}
  .bw-txn td.n, .bw-txn th.n {{ text-align: right; font-family: var(--mono);
                                font-weight: 600; white-space: nowrap; }}
  .bw-txn td.b {{ color: var(--ink-2); font-weight: 400; }}
  .bw-txn .d {{ font-weight: 500; overflow: hidden; text-overflow: ellipsis;
                white-space: nowrap; }}
  .bw-txn .s {{ font-size: 11px; color: var(--muted); font-family: var(--mono); }}
  .bw-txn .chip {{ font-size: 11px; font-weight: 600; padding: 3px 10px;
                   border-radius: {theme.RADIUS_PILL}; white-space: nowrap; }}
</style>
"""
)


def section(title: str) -> None:
    """A compact, colour-anchored section heading."""
    st.html(f'<div class="bw-sec">{title}</div>')


def breakdown_card(label: str, items) -> str:
    """One account's top counterparties as a compact colour-headed card."""
    accent = theme.hue_for(label)
    rows = "".join(
        f'<tr><td>{name}</td><td class="n">{amount:,.0f}</td></tr>'
        for name, amount in items
    )
    return (
        f'<div class="bw-bd" style="--accent:{accent}">'
        f'<div class="bw-bd-head">{label}</div>'
        f'<table class="bw-bd-table">{rows}</table></div>'
    )


def pl_card(title: str, rows, accent: str, *, total: tuple | None = None,
            note: str = "") -> str:
    """A P&L block as a colour-headed card.

    `rows` are (label, value) pairs; `total` is the emphasised closing line.
    """
    body = "".join(
        f'<tr><td>{label}</td><td class="n">{value}</td></tr>'
        for label, value in rows
    )
    if total is not None:
        body += (
            f'<tr class="tot"><td>{total[0]}</td>'
            f'<td class="n">{total[1]}</td></tr>'
        )
    footer = f'<div class="bw-bd-note">{note}</div>' if note else ""
    return (
        f'<div class="bw-bd" style="--accent:{accent}">'
        f'<div class="bw-bd-head">{title}</div>'
        f'<table class="bw-bd-table">{body}</table>{footer}</div>'
    )


def people_panel(rows, money_in: bool) -> str:
    """Top counterparties as avatar rows, ranked by value moved."""
    colour = theme.MONEY_IN if money_in else theme.MONEY_OUT
    sign = "+" if money_in else "\u2212"
    noun = "order" if money_in else "payment"
    if not rows:
        return '<div class="bw-bd-note">No counterparties with an identifier.</div>'
    items = "".join(
        f'<div class="bw-person">'
        f'<div class="bw-av">{c.initials}</div>'
        f'<div class="bw-person-main"><div class="bw-person-name">{c.name}</div>'
        f'<div class="bw-person-sub">{c.identifier} &middot; {c.count} '
        f'{noun}{"" if c.count == 1 else "s"}</div></div>'
        f'<div class="bw-person-amt" style="color:{colour}">{sign}{c.total:,.0f}</div>'
        f'</div>'
        for c in rows
    )
    return f'<div class="bw-people">{items}</div>'


def txn_table(rows) -> str:
    """Recent transactions with a coloured category chip per row."""
    if not rows:
        return '<div class="bw-bd-note">No transactions to show.</div>'
    body = ""
    for txn, category in rows:
        hue = theme.hue_for(category)
        money_in = txn.direction.value == "IN"
        colour = theme.MONEY_IN if money_in else theme.MONEY_OUT
        sign = "+" if money_in else "\u2212"
        body += (
            f'<tr>'
            f'<td class="c">{txn.transaction_id}</td>'
            f'<td><div class="d">{txn.entity_name.title()[:38]}</div>'
            f'<div class="s">{txn.timestamp:%d %b %Y}'
            + (f' &middot; {txn.entity_identifier}' if txn.entity_identifier else "")
            + f'</div></td>'
            f'<td><span class="chip" style="background:{hue}1a;color:{hue}">'
            f'{category}</span></td>'
            f'<td class="n" style="color:{colour}">{sign}{txn.gross_amount:,.0f}</td>'
            f'<td class="n b">'
            + (f'{txn.balance_after:,.0f}' if txn.balance_after is not None else "&mdash;")
            + f'</td></tr>'
        )
    return (
        '<table class="bw-txn"><thead><tr>'
        '<th>Receipt</th><th>Details</th><th>Category</th>'
        '<th class="n">Amount</th><th class="n">Balance</th>'
        f'</tr></thead><tbody>{body}</tbody></table>'
    )


def insight_list(items) -> None:
    """Render insights as tinted cards rather than a plain bullet list."""
    for text in items:
        st.markdown(f'<div class="bw-insight">{text}</div>', unsafe_allow_html=True)


with st.sidebar:
    st.subheader("What are you analysing?")
    mode = st.radio(
        "Mode",
        ["Business", "Personal"],
        label_visibility="collapsed",
        help=(
            "Business: profit, stock and restock for a trading business. "
            "Personal: income, spending and savings for a household."
        ),
    )
    st.caption(
        "**Business** — sales, stock, margin, restock budget."
        if mode == "Business"
        else "**Personal** — income vs spending, savings, debt, runway."
    )

    st.divider()
    st.subheader("Statement")
    source = st.radio(
        "Source", ["Built-in demo", "Upload a file"], label_visibility="collapsed"
    )
    upload = None
    pdf_password = None
    if source == "Upload a file":
        upload = st.file_uploader(
            "M-Pesa export", type=["csv", "xlsx", "xls", "ods", "pdf", "txt"],
            help="The statement Safaricom emails you, or an export from the M-Pesa app.",
        )
        pdf_password = st.text_input("PDF password (if protected)", type="password") or None

    business = "Thrift by Njeri"
    backend = "auto"
    cash_override = 0.0

    if mode == "Business":
        st.divider()
        st.subheader("Settings")
        business = st.text_input("Vendor name", value="Thrift by Njeri")
        backend = st.selectbox(
            "Classifier", ["auto", "heuristic", "anthropic", "openai"],
            help="'auto' uses an LLM when a key is configured, else the offline classifier.",
        )
        cash_override = st.number_input(
            "Cash in M-Pesa (0 = use closing balance)",
            min_value=0.0, value=0.0, step=500.0,
            help="Override if your real balance differs from the statement's last line.",
        )

        st.divider()
        stats = EntityMemory(DB_PATH).stats()
        st.caption(
            f"**Learned memory**\n\n"
            f"{stats['entities']} counterparties · {stats['name_hints']} patterns\n\n"
            f"{stats['decisions_logged']} decisions logged"
        )
        if st.button("Reset learned memory", width="stretch"):
            Path(DB_PATH).unlink(missing_ok=True)
            st.session_state.pop("result", None)
            st.rerun()

    st.divider()
    st.caption(f"v{__version__}")


# --- pipeline --------------------------------------------------------------

def _load_raw() -> pd.DataFrame | None:
    if source == "Built-in demo":
        return load_mock_statement()
    if upload is None:
        return None
    suffix = Path(upload.name).suffix.lower()
    if suffix == ".pdf":
        tmp = Path("data") / f"_upload{suffix}"
        tmp.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_bytes(upload.getvalue())
        try:
            return load_statement_pdf(tmp, password=pdf_password)
        finally:
            tmp.unlink(missing_ok=True)
    # Spill to a temp file and reuse the CLI loader, so the GUI and CLI can
    # never disagree about what a given format means.
    tmp = Path("data") / f"_upload{suffix}"
    tmp.parent.mkdir(parents=True, exist_ok=True)
    tmp.write_bytes(upload.getvalue())
    try:
        return load_statement_csv(tmp)
    finally:
        tmp.unlink(missing_ok=True)


def _run() -> BookworthsResult:
    raw = _load_raw()
    if raw is None:
        raise ValueError("Upload a statement, or switch to the built-in demo.")
    transactions = normalize_statement(raw)
    engine = CategorizationEngine(
        memory=EntityMemory(DB_PATH), backend_preference=backend
    )
    results = engine.classify_all(transactions)
    cash = Decimal(str(cash_override)) if cash_override > 0 else None
    pack = build_profit_pack(results, business_name=business)
    return BookworthsResult(
        transactions=results,
        profit_pack=pack,
        restock_budget=calculate_restock_budget(results, cash_on_hand=cash),
        personal_report=build_personal_report(results, business_profit=pack.net_profit),
        whatsapp_draft=build_whatsapp_draft(results, seller_name=business),
        engine_stats=engine.stats.as_dict(),
    )


def _run_personal():
    raw = _load_raw()
    if raw is None:
        raise ValueError("Upload a statement, or switch to the built-in demo.")
    return build_household_report(normalize_statement(raw))


st.html(
    f"""<div class="bw-head">
      <h1>Bookworths</h1>
      <div class="tag">{TAGLINE}</div>
      <div class="mode">{mode} mode &middot; all amounts in KES</div>
    </div>"""
)

ready = source == "Built-in demo" or upload is not None
if st.button(f"Analyse {mode.lower()} statement", type="primary", disabled=not ready):
    progress = st.progress(0, text="Reading statement…")
    try:
        if mode == "Personal":
            progress.progress(30, text="Reading statement…")
            report = _run_personal()
            progress.progress(80, text="Analysing household spending…")
            st.session_state["household"] = report
            st.session_state.pop("result", None)
        else:
            progress.progress(25, text="Reading statement…")
            result_ = _run()
            progress.progress(85, text="Categorising transactions…")
            st.session_state["result"] = result_
            st.session_state.pop("household", None)
        progress.progress(100, text="Done")
        progress.empty()
    except ImportError as exc:
        progress.empty()
        st.error(f"Missing an optional dependency: {exc}")
    except ValueError as exc:
        progress.empty()
        st.error(f"That statement could not be read: {exc}")
        st.caption(
            "Bookworths reads CSV, XLSX, XLS, ODS, PDF and fixed-width text exports. "
            "It expects the Safaricom columns: Receipt No., Completion Time, Details, "
            "Transaction Status, Paid In, Withdrawn, Balance. If a converted PDF fails, "
            "re-export as CSV from the M-Pesa app."
        )

if not ready and source == "Upload a file":
    st.info("Upload an M-Pesa statement in the sidebar, or switch to the built-in demo.")

# --- Personal mode ---------------------------------------------------------

household = st.session_state.get("household")
if mode == "Personal":
    if household is None:
        st.info("Choose a statement and click **Analyse personal statement**.")
        st.stop()

    h = household
    surplus = h.net_position >= 0
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Money in", kes(h.total_income), f"{kes(h.monthly_income)}/mo")
    m2.metric("Money out", kes(h.total_spending), f"{kes(h.monthly_spending)}/mo")
    m3.metric(
        "Surplus" if surplus else "Shortfall", kes(h.net_position),
        delta_color="normal" if surplus else "inverse",
    )
    m4.metric(
        "Saved", kes(h.savings_total), f"{h.savings_rate_pct}% of income",
        delta_color="normal" if h.savings_total > 0 else "inverse",
    )

    ptabs = st.tabs(["Overview", "Income", "Spending", "Financial health"])

    with ptabs[0]:
        if surplus:
            st.success(
                f"You kept {kes(h.net_position)} more than you spent over "
                f"{h.months} month(s)."
            )
        else:
            st.error(
                f"You spent {kes(abs(h.net_position))} more than came in. "
                "That gap is funded by savings or borrowing."
            )
        left, right = st.columns([1, 1], gap="medium")
        with left:
            section("Money in vs money out")
            st.altair_chart(
                charts.in_out_bars(h.total_income, h.total_spending, height=150),
                width="stretch",
            )
            section("Essential vs discretionary")
            st.altair_chart(
                charts.essential_split(
                    h.essential_spending, h.discretionary_spending, height=120
                ),
                width="stretch",
            )
        with right:
            section("Balance over time")
            st.altair_chart(charts.balance_line(h.transactions, height=300), width="stretch")

        section("Month by month")
        st.altair_chart(charts.monthly_flow(h.transactions), width="stretch")

        section("What this means")
        insight_list(h.insights)

        from app.schema import Direction as _Dir
        from app.reports.personal import categorise_personal
        from app.reports.household import categorise_income

        in_col, out_col = st.columns(2, gap="medium")
        with in_col:
            section("Top sources")
            st.markdown(
                people_panel(top_counterparties(h.transactions, _Dir.IN), True),
                unsafe_allow_html=True,
            )
        with out_col:
            section("Top recipients")
            st.markdown(
                people_panel(top_counterparties(h.transactions, _Dir.OUT), False),
                unsafe_allow_html=True,
            )

        section("Recent transactions")
        recent = sorted(h.transactions, key=lambda t: t.timestamp, reverse=True)[:12]
        st.markdown(
            txn_table([
                (
                    t,
                    (categorise_income if t.direction is _Dir.IN else categorise_personal)(
                        f"{t.entity_name} {t.raw_text}"
                    ).value,
                )
                for t in recent
            ]),
            unsafe_allow_html=True,
        )
        st.caption(f"Showing 12 of {h.transaction_count:,} transactions.")

        st.download_button(
            "Download report", render_household_markdown(h),
            file_name="bookworths_personal_finance.md", mime="text/markdown",
        )

    with ptabs[1]:
        st.caption(
            "Borrowed money and savings withdrawals are shown separately — they "
            "are cash you can spend, but they are not income you earned."
        )
        e1, e2, e3 = st.columns(3)
        e1.metric("Total in", kes(h.total_income))
        e2.metric("Earned", kes(h.earned_income))
        e3.metric("Borrowed", kes(h.borrowed_income),
                  f"{h.borrowed_share_pct}% of money in", delta_color="inverse")

        chart_col, table_col = st.columns([3, 2], gap="medium")
        with chart_col:
            section("By source")
            if h.income_lines:
                st.altair_chart(
                    charts.category_bars(
                        [l.label for l in h.income_lines],
                        [l.amount for l in h.income_lines],
                        height=max(200, 44 * len(h.income_lines)),
                    ),
                    width="stretch",
                )
        with table_col:
            section("Detail")
            st.dataframe(
                pd.DataFrame([
                    {"Source": l.label, "Amount": f"{l.amount:,.0f}",
                     "Share": f"{l.share_pct}%", "Items": l.count}
                    for l in h.income_lines
                ]), width="stretch", hide_index=True, height=360,
            )

    with ptabs[2]:
        s1, s2, s3 = st.columns(3)
        s1.metric("Total out", kes(h.total_spending), f"{kes(h.monthly_spending)}/mo")
        s2.metric("Essentials", kes(h.essential_spending), f"{kes(h.monthly_essentials)}/mo")
        s3.metric("Discretionary", kes(h.discretionary_spending))

        chart_col, table_col = st.columns([3, 2], gap="medium")
        with chart_col:
            section("By category")
            if h.spend_lines:
                st.altair_chart(
                    charts.category_bars(
                        [l.label for l in h.spend_lines],
                        [l.amount for l in h.spend_lines],
                        height=max(220, 38 * len(h.spend_lines)),
                    ),
                    width="stretch",
                )
        with table_col:
            section("Detail")
            st.dataframe(
                pd.DataFrame([
                    {"Category": l.label, "Amount": f"{l.amount:,.0f}",
                     "Share": f"{l.share_pct}%", "Items": l.count,
                     "Essential": "Yes" if l.essential else "No"}
                    for l in h.spend_lines
                ]), width="stretch", hide_index=True, height=430,
            )

    with ptabs[3]:
        checks = [
            ("Savings rate", f"{h.savings_rate_pct}%", "10–20%+", h.savings_rate_pct >= 10),
            ("Debt repayments", f"{h.debt_ratio_pct}% of income", "under 35%",
             h.debt_ratio_pct < 35),
            ("Borrowed share of income", f"{h.borrowed_share_pct}%", "as low as possible",
             h.borrowed_share_pct < 20),
            ("Emergency runway", f"{h.runway_months} months", "3+ months",
             h.runway_months >= 3),
            ("Transaction fees", f"{kes(h.total_fees)} ({h.fee_pct}%)",
             "as low as possible", h.fee_pct < 1),
        ]
        good = [c for c in checks if c[3]]
        watch = [c for c in checks if not c[3]]

        left, right = st.columns([2, 3], gap="medium")
        with left:
            section("Needs attention" if watch else "All clear")
            if watch:
                for name, value, target, _ in watch:
                    st.markdown(
                        f'<div class="bw-warn"><strong>{name}</strong><br>'
                        f'{value} &mdash; healthy is {target}</div>',
                        unsafe_allow_html=True,
                    )
            else:
                st.markdown(
                    '<div class="bw-note">Every measure sits in a healthy range.</div>',
                    unsafe_allow_html=True,
                )
            if good:
                section("On track")
                for name, value, _, _ in good:
                    st.markdown(
                        f'<div class="bw-note"><strong>{name}</strong> &mdash; {value}</div>',
                        unsafe_allow_html=True,
                    )
        with right:
            section("All measures")
            st.dataframe(
                pd.DataFrame([
                    {"Measure": name, "You": value, "Healthy range": target,
                     "Status": "On track" if ok else "Needs attention"}
                    for name, value, target, ok in checks
                ]), width="stretch", hide_index=True, height=250,
            )
            st.caption(
                "These are general guidelines, not advice. What counts as healthy "
                "depends on your circumstances."
            )
    st.stop()


# --- Business mode ---------------------------------------------------------

result: BookworthsResult | None = st.session_state.get("result")
if result is None:
    st.stop()

pack = result.profit_pack
budget = result.restock_budget


# --- headline --------------------------------------------------------------

c1, c2, c3, c4 = st.columns(4)
c1.metric("Gross sales", kes(pack.gross_revenue))
c2.metric("Net business profit", kes(pack.net_profit), f"{pack.net_margin_pct}% margin")
c3.metric("Hidden leakage", kes(pack.financial_fees), f"{pack.leakage_pct}% of sales",
          delta_color="inverse")
c4.metric("Owner drawings", kes(pack.owner_drawings),
          f"{pack.drawings_pct_of_profit}% of profit", delta_color="inverse")

# The exceptions queue gates the accuracy of every figure above, so it is
# announced from wherever the user happens to be rather than only on its tab.
_pending = len(result.exceptions)
if _pending:
    _held = sum(
        (r.transaction.gross_amount for r in result.exceptions), Decimal("0")
    )
    st.html(
        f"""<div class="bw-cta">
          <div class="icon">{_pending}</div>
          <div class="body">
            <div class="t">{_pending} transaction{"" if _pending == 1 else "s"} need your confirmation</div>
            <div class="d">Until these are categorised they sit outside your profit
              figures. Open <strong>Needs your input</strong> &mdash; each one takes a tap.</div>
          </div>
          <div class="amt">{kes(_held)}</div>
        </div>"""
    )
else:
    st.html(
        """<div class="bw-cta-ok">
          <div class="icon">&#10003;</div>
          <div>Every transaction is categorised &mdash; these figures are complete.</div>
        </div>"""
    )

# Tab labels take Markdown, not HTML — bold plus an inline-code count is the
# strongest emphasis available, and CSS below colours the tab itself.
_alert_label = (
    f"**Needs your input** `{_pending}`" if _pending else "Needs your input"
)

tabs = st.tabs(
    ["Profit Pack", _alert_label,
     "Restock budget", "My personal finances", "All transactions", "WhatsApp draft"]
)


# --- Profit Pack -----------------------------------------------------------

with tabs[0]:
    chart_col, text_col = st.columns([3, 2], gap="medium")
    with chart_col:
        section("Where the revenue went")
        st.altair_chart(charts.profit_waterfall(pack), width="stretch")
    with text_col:
        section("Headline")
        st.markdown(
            f'<div class="bw-note"><strong>Net take-home profit</strong><br>'
            f'{kes(pack.net_profit)} &mdash; {pack.net_margin_pct}% of sales</div>',
            unsafe_allow_html=True,
        )
        st.markdown(
            f'<div class="bw-warn"><strong>Hidden leakage</strong><br>'
            f'{kes(pack.financial_fees)} in M-Pesa tariffs &mdash; '
            f'{pack.leakage_pct}% of sales</div>',
            unsafe_allow_html=True,
        )
        st.markdown(
            f'<div class="bw-flag"><strong>Owner drawings</strong><br>'
            f'{kes(pack.owner_drawings)} &mdash; '
            f'{pack.drawings_pct_of_profit}% of profit consumed</div>',
            unsafe_allow_html=True,
        )
    st.divider()
    section("The numbers")
    money_col, took_col, leak_col = st.columns(3, gap="medium")

    with money_col:
        st.markdown(
            pl_card(
                "Where the money actually went",
                [
                    ("Gross sales revenue", kes(pack.gross_revenue)),
                    ("Less: stock restocked", f"({kes(pack.total_cogs)})"),
                    ("Gross product margin", kes(pack.gross_margin)),
                    ("Less: logistics &amp; riders", f"({kes(pack.logistics)})"),
                    ("Less: marketing", f"({kes(pack.marketing)})"),
                    ("Less: packaging", f"({kes(pack.packaging)})"),
                    ("Less: M-Pesa tariffs", f"({kes(pack.financial_fees)})"),
                ],
                theme.MONEY_IN,
                total=("Net take-home profit", kes(pack.net_profit)),
                note=f"Gross margin {pack.gross_margin_pct}% &middot; "
                     f"net margin {pack.net_margin_pct}%",
            ),
            unsafe_allow_html=True,
        )

    with took_col:
        st.markdown(
            pl_card(
                "What you already took out",
                [
                    ("Net business profit", kes(pack.net_profit)),
                    ("Less: owner drawings", f"({kes(pack.owner_drawings)})"),
                ],
                theme.BILLS,
                total=("Left in the business", kes(pack.profit_after_drawings)),
                note=f"{pack.drawings_pct_of_profit}% of profit consumed. Drawings "
                     "are not a business cost &mdash; they are profit already spent.",
            ),
            unsafe_allow_html=True,
        )

    with leak_col:
        st.markdown(
            pl_card(
                "Hidden leakages",
                [
                    ("Safaricom tariffs &amp; Fuliza", kes(pack.financial_fees)),
                    ("Share of gross sales", f"{pack.leakage_pct}%"),
                    ("Transactions analysed", f"{pack.transaction_count}"),
                ],
                theme.MONEY_OUT,
                note="Fees are charged per transaction. Fewer, larger transfers "
                     "cost less than many small ones.",
            ),
            unsafe_allow_html=True,
        )

    with st.expander("Full statement (Markdown)"):
        st.markdown(render_markdown(pack, include_breakdown=False))

    st.divider()
    from app.schema import Direction as _Dir2
    buyers_col, payees_col = st.columns(2, gap="medium")
    txns = [item.transaction for item in result.transactions]
    with buyers_col:
        section("Top customers")
        st.markdown(people_panel(top_counterparties(txns, _Dir2.IN), True),
                    unsafe_allow_html=True)
    with payees_col:
        section("Top payees")
        st.markdown(people_panel(top_counterparties(txns, _Dir2.OUT), False),
                    unsafe_allow_html=True)

    if pack.breakdowns:
        st.divider()
        section("Category breakdown")
        populated = [(label, items) for label, items in pack.breakdowns.items() if items]
        # Three columns keeps eight accounts on one screen instead of a
        # scroll of stacked tables.
        columns = st.columns(3, gap="medium")
        for index, (label, items) in enumerate(populated):
            with columns[index % 3]:
                st.markdown(breakdown_card(label, items), unsafe_allow_html=True)
    st.divider()
    d1, d2 = st.columns(2)
    d1.download_button(
        "Download printable HTML", render_html(pack),
        file_name="bookworths_profit_pack.html", mime="text/html",
        width="stretch",
    )
    d2.download_button(
        "Download Markdown",
        render_markdown(pack) + "\n\n---\n\n" + render_restock_markdown(budget),
        file_name="bookworths_profit_pack.md", mime="text/markdown",
        width="stretch",
    )


# --- exceptions ------------------------------------------------------------

with tabs[1]:
    exceptions = result.exceptions
    if not exceptions:
        st.html(
            '<div class="bw-cta-ok"><div class="icon">&#10003;</div>'
            '<div><strong>Nothing needs your input.</strong> Every transaction '
            'was matched automatically, so the figures are complete.</div></div>'
        )
    else:
        held = sum((r.transaction.gross_amount for r in exceptions), Decimal("0"))
        q1, q2, q3 = st.columns(3)
        q1.metric("Awaiting you", f"{len(exceptions)}")
        q2.metric("Value held back", kes(held))
        q3.metric("Already sorted", f"{len(result.transactions) - len(exceptions)}")
        st.caption(
            "Tap the right answer for each. Your choice is remembered, so this "
            "counterparty is classified automatically from now on — and these "
            "amounts join your profit figures straight away."
        )
        # Rendering hundreds of button rows makes the page unusable, and a
        # seller would not work through them anyway. Show the biggest amounts
        # first — confirming those moves the numbers most.
        BATCH = 15
        if len(exceptions) > BATCH:
            st.info(
                f"Showing the {BATCH} largest of {len(exceptions)} unconfirmed "
                "transactions. Answer these, and the next batch appears."
            )
        ordered = sorted(
            exceptions, key=lambda r: r.transaction.gross_amount, reverse=True
        )[:BATCH]
        for position, item in enumerate(ordered):
            txn = item.transaction
            label = item.classification.counterparty_label or txn.entity_name
            st.markdown(
                f'<div class="bw-flag"><strong>{kes(txn.gross_amount)}</strong> — '
                f'{txn.timestamp:%d %b} — {label.title()}'
                + (f' · {txn.entity_identifier}' if txn.entity_identifier else "")
                + f'<br><small>{item.classification.rationale}</small></div>',
                unsafe_allow_html=True,
            )
            cols = st.columns(len(QUICK_ANSWERS))
            for col, (code, human, account) in zip(cols, QUICK_ANSWERS):
                # A receipt can appear on several rows (payment, charge, Fuliza
                # drawdown), so the position is what makes the key unique.
                if col.button(human, key=f"{position}:{txn.transaction_id}:{code}",
                              width="stretch"):
                    if txn.entity_identifier:
                        EntityMemory(DB_PATH).confirm(
                            txn.entity_identifier, account, label.title()
                        )
                        with st.spinner("Learning and re-analysing…"):
                            st.session_state["result"] = _run()
                        st.rerun()
                    else:
                        st.warning(
                            "This line has no phone/till to remember. Categorise it "
                            "in your ledger export instead."
                        )


# --- restock ---------------------------------------------------------------

with tabs[2]:
    b1, b2, b3 = st.columns(3)
    b1.metric("Cash in M-Pesa", kes(budget.cash_on_hand))
    b2.metric("Committed", kes(budget.total_commitments))
    b3.metric(
        "Safe to restock", kes(budget.safe_to_spend),
        delta_color="normal" if budget.safe_to_spend > 0 else "inverse",
    )

    verdict_col, detail_col = st.columns([2, 3], gap="medium")
    with verdict_col:
        section("Verdict")
        if budget.safe_to_spend > 0:
            st.markdown(
                f'<div class="bw-note"><strong>Safe to spend '
                f'{kes(budget.safe_to_spend)}</strong> on your next restock '
                f'({budget.coverage_ratio}x a typical run).</div>',
                unsafe_allow_html=True,
            )
        else:
            st.markdown(
                '<div class="bw-warn"><strong>Do not restock right now.</strong><br>'
                'Committed costs already exceed your cash.</div>',
                unsafe_allow_html=True,
            )
        if budget.warnings:
            section("Watch out")
            for warning in budget.warnings:
                st.markdown(f'<div class="bw-flag">{warning}</div>',
                            unsafe_allow_html=True)
    with detail_col:
        section("How it is calculated")
        st.dataframe(
            pd.DataFrame([
                {"Line": "Cash in M-Pesa", "Amount": f"{budget.cash_on_hand:,.0f}"},
                {"Line": "Less: CBD shelf rent due", "Amount": f"-{budget.shelf_rent_due:,.0f}"},
                {"Line": "Less: rider balances", "Amount": f"-{budget.rider_balance_due:,.0f}"},
                {"Line": "Less: owner draw reserve", "Amount": f"-{budget.owner_draw_reserve:,.0f}"},
                {"Line": "Less: operating buffer", "Amount": f"-{budget.operating_buffer:,.0f}"},
                {"Line": "Less: unconfirmed items", "Amount": f"-{budget.unresolved_holdback:,.0f}"},
                {"Line": "Safe to spend", "Amount": f"{budget.safe_to_spend:,.0f}"},
            ]), width="stretch", hide_index=True, height=290,
        )


# --- personal finances -----------------------------------------------------

with tabs[3]:
    personal = result.personal_report
    if personal.total_drawings <= 0:
        st.info("No personal spending was identified in this statement.")
    else:
        p1, p2, p3, p4 = st.columns(4)
        p1.metric("Personal spending", kes(personal.total_drawings))
        p2.metric("Monthly burn", kes(personal.monthly_burn))
        p3.metric("Essentials / month", kes(personal.essential_burn))
        p4.metric(
            "Saved", kes(personal.savings_total), f"{personal.savings_rate_pct}%",
            delta_color="normal" if personal.savings_total > 0 else "inverse",
        )

        st.markdown(
            "> This is **your household money only** — what left the business for "
            "personal use. It does not change the business profit figures."
        )

        chart_col, table_col = st.columns([3, 2], gap="medium")
        with chart_col:
            section("By category")
            if personal.lines:
                st.altair_chart(
                    charts.category_bars(
                        [l.category.value for l in personal.lines],
                        [l.amount for l in personal.lines],
                        height=max(220, 38 * len(personal.lines)),
                    ),
                    width="stretch",
                )
        with table_col:
            section("Detail")
            st.dataframe(
                pd.DataFrame([
                    {
                        "Category": l.category.value,
                        "Amount": f"{l.amount:,.0f}",
                        "Share": f"{l.share_pct}%",
                        "Items": l.count,
                        "Essential": "Yes" if l.category.is_essential else "No",
                    }
                    for l in personal.lines
                ]),
                width="stretch", hide_index=True, height=400,
            )

        if personal.insights:
            section("What this means")
            insight_list(personal.insights)

        st.download_button(
            "Download report",
            render_personal_markdown(personal),
            file_name="bookworths_personal_finance.md", mime="text/markdown",
        )


# --- ledger ----------------------------------------------------------------

with tabs[4]:
    section("Recent transactions")
    recent = sorted(result.transactions, key=lambda r: r.transaction.timestamp,
                    reverse=True)[:12]
    st.markdown(
        txn_table([(item.transaction, item.account.label) for item in recent]),
        unsafe_allow_html=True,
    )
    st.divider()

    section("Full ledger")
    df = result.to_dataframe()
    only_review = st.checkbox("Show only items needing review")
    view = df[df["needs_review"]] if only_review else df
    st.dataframe(view, width="stretch", hide_index=True)
    st.download_button(
        "Download ledger CSV", df.to_csv(index=False),
        file_name="bookworths_ledger.csv", mime="text/csv",
    )
    with st.expander("How each layer performed"):
        for key, value in result.engine_stats.items():
            st.write(f"**{key}** — {value}")


# --- whatsapp --------------------------------------------------------------

with tabs[5]:
    st.caption("Copy this into WhatsApp and send it to the seller.")
    st.code(result.whatsapp_draft, language=None)
    st.download_button(
        "Download Download draft", result.whatsapp_draft,
        file_name="bookworths_whatsapp_draft.txt", mime="text/plain",
    )
