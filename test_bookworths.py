"""Test suite for Bookworths.

Run with:  python -m pytest test_bookworths.py -v
       or:  python test_bookworths.py     (no pytest required)
"""
from __future__ import annotations

import io
import re
import tempfile
from decimal import Decimal
from pathlib import Path

from app.engine import CategorizationEngine, EntityMemory
from app.engine.llm import HeuristicBackend
from app.ingest import normalize_statement
from app.mockdata import load_mock_statement
from app.pipeline import run_pipeline
from app.reports.profit_pack import build_profit_pack
from app.reports.restock import calculate_restock_budget
from app.reports.whatsapp import build_whatsapp_draft
from app.schema import Account, Direction, Transaction

ZERO = Decimal("0")


def _tmp_db() -> str:
    return str(Path(tempfile.mkdtemp()) / "test.db")


# --- ingestion -------------------------------------------------------------

def test_normalization_produces_transactions():
    txns = normalize_statement(load_mock_statement())
    assert len(txns) == 59
    assert all(isinstance(t, Transaction) for t in txns)
    assert all(t.gross_amount >= ZERO for t in txns)


def test_fee_lines_are_merged_onto_parent():
    txns = {t.transaction_id: t for t in normalize_statement(load_mock_statement())}
    # The Gikomba payout carried a KES 105 send-money charge on its own row.
    assert txns["TG73CC21F6"].fee_amount == Decimal("105.00")
    # The fee row itself must not survive as a separate transaction.
    assert "TG73CC21F6-FEE" not in txns


def test_orphan_fuliza_fee_survives_as_own_transaction():
    txns = {t.transaction_id: t for t in normalize_statement(load_mock_statement())}
    assert "TG73CC22G7" in txns
    assert txns["TG73CC22G7"].gross_amount == Decimal("72.00")


def test_entity_extraction():
    txns = {t.transaction_id: t for t in normalize_statement(load_mock_statement())}
    phone = txns["TG71AA01B2"]
    assert phone.entity_identifier == "254712004411"
    assert phone.direction is Direction.IN
    till = txns["TG72BB12E5"]
    assert till.entity_identifier == "5423119"
    assert "NAIVAS" in till.entity_name


def test_net_amount_sign_convention():
    inbound = Transaction(
        transaction_id="A", timestamp="2026-07-01T10:00:00", raw_text="x",
        direction=Direction.IN, gross_amount=Decimal("1000"),
    )
    outbound = Transaction(
        transaction_id="B", timestamp="2026-07-01T10:00:00", raw_text="x",
        direction=Direction.OUT, gross_amount=Decimal("1000"), fee_amount=Decimal("23"),
    )
    assert inbound.net_amount == Decimal("1000")
    assert outbound.net_amount == Decimal("-1023")


# --- categorization --------------------------------------------------------

def test_every_transaction_gets_an_account():
    txns = normalize_statement(load_mock_statement())
    engine = CategorizationEngine(memory=EntityMemory(_tmp_db()), backend=HeuristicBackend())
    results = engine.classify_all(txns)
    assert len(results) == len(txns)
    assert all(r.account in set(Account) for r in results)


def test_tariffs_route_to_financial_fees():
    txns = normalize_statement(load_mock_statement())
    engine = CategorizationEngine(memory=EntityMemory(_tmp_db()), backend=HeuristicBackend())
    results = {r.transaction.transaction_id: r for r in engine.classify_all(txns)}
    assert results["TG73CC22G7"].account is Account.FINANCIAL_FEES  # Fuliza


def test_personal_spend_separated_from_business():
    txns = normalize_statement(load_mock_statement())
    engine = CategorizationEngine(memory=EntityMemory(_tmp_db()), backend=HeuristicBackend())
    results = {r.transaction.transaction_id: r for r in engine.classify_all(txns)}
    assert results["TG72BB12E5"].account is Account.OWNER_DRAWINGS  # Naivas
    assert results["TG86RR52G4"].account is Account.OWNER_DRAWINGS  # school fees
    assert results["TG73CC21F6"].account is Account.COGS_RESTOCK    # Gikomba bale


def test_low_confidence_is_flagged():
    txns = normalize_statement(load_mock_statement())
    engine = CategorizationEngine(memory=EntityMemory(_tmp_db()), backend=HeuristicBackend())
    results = engine.classify_all(txns)
    flagged = [r for r in results if r.needs_review]
    assert flagged, "the ambiguous rows must surface"
    assert all(r.classification.confidence < 0.85 for r in flagged)
    ids = {r.transaction.transaction_id for r in flagged}
    assert "TG94ZZ32X1" in ids  # UNKNOWN RECIPIENT


def test_entity_memory_persists_and_learns():
    db = _tmp_db()
    memory = EntityMemory(db)
    assert memory.lookup("254701447788").account is Account.COGS_RESTOCK
    memory.confirm("254999888777", Account.PACKAGING, "New mailer supplier")
    # A fresh handle on the same file must see the confirmation.
    assert EntityMemory(db).lookup("254999888777").account is Account.PACKAGING


def test_seller_confirmation_beats_prior_mapping():
    db = _tmp_db()
    memory = EntityMemory(db)
    memory.confirm("888880", Account.LOGISTICS, "Stall electricity")
    record = memory.lookup("888880")
    assert record.account is Account.LOGISTICS
    assert record.confidence >= 0.95


# --- reporting -------------------------------------------------------------

def _pack():
    txns = normalize_statement(load_mock_statement())
    engine = CategorizationEngine(memory=EntityMemory(_tmp_db()), backend=HeuristicBackend())
    return engine.classify_all(txns)


def test_profit_pack_arithmetic():
    pack = build_profit_pack(_pack())
    assert pack.gross_margin == pack.gross_revenue - pack.total_cogs
    assert pack.net_profit == pack.gross_margin - pack.total_operating
    assert pack.profit_after_drawings == pack.net_profit - pack.owner_drawings
    assert pack.gross_revenue > ZERO


def test_drawings_excluded_from_business_profit():
    results = _pack()
    pack = build_profit_pack(results)
    assert pack.owner_drawings > ZERO
    # Drawings must not be inside any operating-expense line.
    assert pack.total_operating == (
        pack.logistics + pack.marketing + pack.packaging + pack.financial_fees
    )


def test_fees_counted_once_and_completely():
    results = _pack()
    pack = build_profit_pack(results)
    merged = sum((r.transaction.fee_amount for r in results), ZERO)
    standalone = sum(
        (r.transaction.gross_amount for r in results if r.account is Account.FINANCIAL_FEES),
        ZERO,
    )
    assert pack.financial_fees == merged + standalone


def test_restock_budget_never_negative():
    budget = calculate_restock_budget(_pack())
    assert budget.safe_to_spend >= ZERO
    assert budget.coverage_ratio >= ZERO
    assert budget.total_commitments > ZERO


def test_restock_budget_respects_explicit_cash():
    budget = calculate_restock_budget(_pack(), cash_on_hand=Decimal("100000"))
    assert budget.cash_on_hand == Decimal("100000.00")
    assert budget.safe_to_spend > ZERO


def test_whatsapp_draft_caps_questions():
    draft = build_whatsapp_draft(_pack(), seller_name="Thrift by Njeri", max_questions=5)
    assert "Njeri" in draft
    assert draft.count("\n*") <= 5
    assert "clean books, clear value" in draft.lower()


def test_whatsapp_all_clear_path():
    results = [r for r in _pack() if not r.needs_review]
    draft = build_whatsapp_draft(results, seller_name="Njeri")
    assert "nothing needs your input" in draft


# --- end to end ------------------------------------------------------------

def test_pipeline_end_to_end():
    result = run_pipeline(backend_preference="heuristic", db_path=_tmp_db())
    assert len(result.transactions) == 59
    assert result.profit_pack.gross_revenue > ZERO
    assert result.restock_budget.safe_to_spend >= ZERO
    assert result.whatsapp_draft
    df = result.to_dataframe()
    assert len(df) == 59
    assert df["account_code"].notna().all()


def test_pipeline_writes_all_deliverables():
    from app.pipeline import write_outputs

    result = run_pipeline(backend_preference="heuristic", db_path=_tmp_db())
    out = Path(tempfile.mkdtemp())
    paths = write_outputs(result, out)
    for path in paths.values():
        assert path.exists() and path.stat().st_size > 0
    html = paths["profit_pack_html"].read_text()
    assert "Clean books, clear value" in html
    assert "<table" in html


# --- fixed-width / converted exports --------------------------------------

_FIXED_WIDTH_SAMPLE = """\
Receipt No.                         Completion Time              Details                                         Transaction Status Paid In Withdrawn Balance
ABC1234567                     2026-07-01 20:19:22         to - 07******212 JANE DOE        Completed                    -50               950
ABC1234567                     2026-07-01 20:19:22         OverDraft of Credit Party             Completed          50                      1000
to 7384394 - JANE
DOE
Receipt No.                         Completion Time              Details
XYZ9876543                     2026-07-02 10:00:00         Funds received from 254712004411 SALE        Completed     2000                   3000
"""


def _fixed_width_frame():
    import pandas as pd

    return pd.DataFrame({0: [_FIXED_WIDTH_SAMPLE]})


def test_detects_fixed_width_export():
    from app.ingest.fixedwidth import looks_fixed_width

    assert looks_fixed_width(_fixed_width_frame())


def test_real_table_not_treated_as_fixed_width():
    from app.ingest.fixedwidth import looks_fixed_width

    assert not looks_fixed_width(load_mock_statement())


def test_fixed_width_recovers_columns_and_rows():
    from app.ingest.fixedwidth import parse_fixed_width

    out = parse_fixed_width(_fixed_width_frame())
    assert list(out.columns) == [
        "Receipt No.", "Completion Time", "Details", "Transaction Status",
        "Paid In", "Withdrawn", "Balance",
    ]
    assert len(out) == 3               # two ABC rows + one XYZ row
    assert "Receipt No." not in set(out["Receipt No."])   # repeated header dropped


def test_fixed_width_parses_signed_amounts():
    from app.ingest.fixedwidth import parse_fixed_width

    out = parse_fixed_width(_fixed_width_frame())
    debit = out[out["Withdrawn"] != ""].iloc[0]
    assert debit["Withdrawn"].startswith("-")
    assert debit["Balance"] == "950"


def test_masked_phone_yields_stable_identifier():
    from app.ingest.normalize import _extract_entity

    _, first, kind = _extract_entity("to - 07******212 JANE DOE")
    _, second, _ = _extract_entity("to - 07******212 JANE DOE")
    assert first is not None and first == second
    assert kind.value == "PHONE"


def test_bare_till_is_extracted():
    from app.ingest.normalize import _extract_entity

    name, identifier, kind = _extract_entity("OverDraft of Credit Party to 7384394 - DAMARIS MORAA")
    assert identifier == "7384394"
    assert kind.value == "TILL"
    assert "DAMARIS" in name


def test_fixed_width_flows_through_normalization():
    from app.ingest.fixedwidth import parse_fixed_width

    txns = normalize_statement(parse_fixed_width(_fixed_width_frame()))
    assert txns
    assert all(t.gross_amount >= ZERO for t in txns)


# --- format coverage -------------------------------------------------------

def _write_formats(target: Path):
    """Materialise the demo statement in every supported format."""
    from app.mockdata import MPESA_STATEMENT_CSV

    df = load_mock_statement()
    (target / "s.csv").write_text(MPESA_STATEMENT_CSV)
    df.to_excel(target / "s.xlsx", index=False)
    df.to_excel(target / "s.ods", index=False, engine="odf")
    return target


def test_csv_xlsx_ods_all_parse_identically():
    from app.ingest import load_statement_csv

    target = _write_formats(Path(tempfile.mkdtemp()))
    counts = {
        name: len(normalize_statement(load_statement_csv(target / name)))
        for name in ("s.csv", "s.xlsx", "s.ods")
    }
    assert len(set(counts.values())) == 1, counts
    assert counts["s.csv"] == 59


def test_plain_csv_is_not_misread_as_fixed_width():
    """A normal delimited CSV must take the fast path, not the recovery path."""
    from app.ingest import load_statement_csv

    target = _write_formats(Path(tempfile.mkdtemp()))
    table = load_statement_csv(target / "s.csv")
    assert len(table.columns) == 7


def test_non_transaction_rows_are_skipped_not_fatal():
    """Footers and disclaimers must not abort a whole statement."""
    import pandas as pd

    raw = load_mock_statement()
    footer = pd.DataFrame([{
        "Receipt No.": "", "Completion Time": "To verify this statement visit...",
        "Details": "", "Transaction Status": "", "Paid In": "",
        "Withdrawn": "", "Balance": "",
    }])
    combined = pd.concat([raw, footer], ignore_index=True)
    assert len(normalize_statement(combined)) == 59



# --- repeated receipt numbers ---------------------------------------------

_REPEATED_RECEIPT_CSV = """\
Receipt No.,Completion Time,Details,Transaction Status,Paid In,Withdrawn,Balance
QEG13WW71B,2026-05-16 14:19:25,Customer Transfer of FundsCharge,Completed,,-12,0
QEG13WW71B,2026-05-16 14:19:25,Customer Transfer Fuliza MPesato - 254712004411 JANE,Completed,,-850,12
QEG13WW71B,2026-05-16 14:19:25,OverDraft of Credit Party,Completed,292.37,,862
"""


def _repeated_receipt_frame():
    import pandas as pd

    return pd.read_csv(io.StringIO(_REPEATED_RECEIPT_CSV), dtype=str).fillna("")


def test_one_receipt_can_hold_several_transactions():
    """Safaricom reuses a receipt for payment, charge and Fuliza drawdown."""
    txns = normalize_statement(_repeated_receipt_frame())
    # The charge merges into the payment, leaving payment + overdraft.
    assert len(txns) == 2
    kinds = {(t.direction.value, t.gross_amount) for t in txns}
    assert ("OUT", Decimal("850")) in kinds
    assert ("IN", Decimal("292.37")) in kinds


def test_same_receipt_fee_merges_onto_the_payment():
    """The charge belongs to the payment, not to the Fuliza drawdown."""
    txns = normalize_statement(_repeated_receipt_frame())
    payment = next(t for t in txns if t.direction.value == "OUT")
    overdraft = next(t for t in txns if t.direction.value == "IN")
    assert payment.fee_amount == Decimal("12")
    assert overdraft.fee_amount == ZERO


def test_no_transaction_is_silently_duplicated():
    """Rows sharing a receipt must not overwrite or clone each other."""
    txns = normalize_statement(_repeated_receipt_frame())
    fingerprints = [(t.transaction_id, t.direction.value, t.gross_amount) for t in txns]
    assert len(fingerprints) == len(set(fingerprints))


def test_glued_charge_text_is_detected_as_a_fee():
    """Converted exports lose the space: 'of FundsCharge'."""
    from app.ingest.normalize import _FEE_LINE_RE

    assert _FEE_LINE_RE.search("Customer Transfer of FundsCharge")
    assert _FEE_LINE_RE.search("Pay Bill Charge")
    assert not _FEE_LINE_RE.search("Customer Transfer to 254712004411 JANE")
    assert not _FEE_LINE_RE.search("OverDraft of Credit Party")


def test_gui_button_keys_are_unique_per_row():
    """Regression: keying on receipt alone collided on repeated receipts."""
    txns = normalize_statement(_repeated_receipt_frame())
    engine = CategorizationEngine(memory=EntityMemory(_tmp_db()), backend=HeuristicBackend())
    results = engine.classify_all(txns)
    ordered = sorted(results, key=lambda r: r.transaction.gross_amount, reverse=True)
    keys = [
        f"{i}:{item.transaction.transaction_id}:{code}"
        for i, item in enumerate(ordered)
        for code in ("STOCK", "RIDER", "ADS", "PACK", "ME")
    ]
    assert len(keys) == len(set(keys))



# --- personal finance analysis --------------------------------------------

def test_personal_categories_are_recognised():
    from app.reports.personal import Spend, categorise_personal

    assert categorise_personal("NAIVAS SUPERMARKET") is Spend.FOOD
    assert categorise_personal("KPLC prepaid token") is Spend.UTILITIES
    assert categorise_personal("Fuliza loan repayment") is Spend.DEBT
    assert categorise_personal("SACCO monthly deposit") is Spend.SAVINGS
    assert categorise_personal("nothing recognisable here") is Spend.OTHER


def test_longest_pattern_wins():
    """'school fees' must beat a bare 'fees' match."""
    from app.reports.personal import Spend, categorise_personal

    assert categorise_personal("school fees term 2") is Spend.EDUCATION


def test_personal_report_only_covers_drawings():
    """Business spending must never leak into the household picture."""
    from app.reports.personal import build_personal_report

    results = _pack()
    report = build_personal_report(results)
    drawings = sum(
        (r.transaction.gross_amount for r in results if r.account is Account.OWNER_DRAWINGS),
        ZERO,
    )
    assert report.total_drawings == drawings
    assert sum((l.amount for l in report.lines), ZERO) == drawings


def test_personal_report_splits_essential_and_discretionary():
    from app.reports.personal import build_personal_report

    report = build_personal_report(_pack())
    assert report.essential_total + report.discretionary_total == report.total_drawings
    assert report.essential_total > ZERO


def test_month_count_is_calendar_months_not_30_day_blocks():
    """A single-month statement must not be averaged as two."""
    from app.reports.personal import build_personal_report

    report = build_personal_report(_pack())
    assert report.months == 1
    assert report.monthly_burn == report.total_drawings


def test_personal_report_does_not_change_business_profit():
    """Module D is read-only over the classified ledger."""
    from app.reports.personal import build_personal_report

    results = _pack()
    before = build_profit_pack(results).net_profit
    build_personal_report(results, business_profit=before)
    assert build_profit_pack(results).net_profit == before


def test_personal_report_renders_and_has_insights():
    from app.reports.personal import build_personal_report, render_personal_markdown

    report = build_personal_report(_pack(), business_profit=Decimal("25153"))
    text = render_personal_markdown(report)
    assert "PERSONAL FINANCE ANALYSIS" in text
    assert report.insights


def test_pipeline_exposes_personal_report():
    result = run_pipeline(backend_preference="heuristic", db_path=_tmp_db())
    assert result.personal_report.total_drawings > ZERO
    assert result.personal_report.lines



# --- personal mode (standalone household analysis) ------------------------

def test_income_sources_are_recognised():
    from app.reports.household import Income, categorise_income

    assert categorise_income("Salary payment from ACME") is Income.SALARY
    assert categorise_income("OverDraft of Credit Party") is Income.LOANS
    assert categorise_income("Funds received from 254712004411 JANE") is Income.TRANSFERS
    assert categorise_income("M-Shwari Withdraw") is Income.SAVINGS_OUT


def test_borrowed_money_is_not_counted_as_earned():
    """A loan is cash you can spend, but it is not income you made."""
    from app.reports.household import Income

    assert not Income.LOANS.is_earned
    assert not Income.SAVINGS_OUT.is_earned
    assert Income.SALARY.is_earned


def test_household_report_balances():
    from app.reports.household import build_household_report

    txns = normalize_statement(load_mock_statement())
    report = build_household_report(txns)
    assert report.total_income > ZERO
    assert report.total_spending > ZERO
    assert report.net_position == report.total_income - report.total_spending
    # Every shilling in and out is bucketed somewhere.
    assert sum((l.amount for l in report.income_lines), ZERO) == report.total_income


def test_household_spending_splits_essential_and_discretionary():
    from app.reports.household import build_household_report

    report = build_household_report(normalize_statement(load_mock_statement()))
    total = report.essential_spending + report.discretionary_spending
    # Fees are spending but sit outside the category lines.
    assert total + report.total_fees == report.total_spending


def test_personal_mode_uses_no_business_concepts():
    """Personal mode must not need the engine, memory or chart of accounts."""
    from app.reports.household import build_household_report, render_household_markdown

    report = build_household_report(normalize_statement(load_mock_statement()))
    text = render_household_markdown(report)
    for business_word in ("Profit", "COGS", "Restock", "Margin", "Vendor"):
        assert business_word not in text, business_word


def test_run_personal_needs_no_database():
    """No SQLite file should be created by a personal-mode run."""
    from app.pipeline import run_personal

    workdir = Path(tempfile.mkdtemp())
    report = run_personal()
    assert report.total_income > ZERO
    assert not list(workdir.glob("*.db"))


def test_personal_and_business_modes_are_independent():
    """The same statement can be read either way without interference."""
    from app.pipeline import run_personal

    household = run_personal()
    business = run_pipeline(backend_preference="heuristic", db_path=_tmp_db())
    assert household.total_income > ZERO
    assert business.profit_pack.gross_revenue > ZERO
    # Personal counts every inflow; business counts only what it books as revenue.
    assert household.total_income >= business.profit_pack.gross_revenue



# --- charts ----------------------------------------------------------------

def test_all_charts_produce_valid_specs():
    from app.reports import charts

    household = run_personal_report()
    business = run_pipeline(backend_preference="heuristic", db_path=_tmp_db())
    txns = household.transactions

    built = [
        charts.category_bars(["A", "B"], [Decimal("5"), Decimal("3")]),
        charts.in_out_bars(household.total_income, household.total_spending),
        charts.essential_split(
            household.essential_spending, household.discretionary_spending
        ),
        charts.monthly_flow(txns),
        charts.balance_line(txns),
        charts.profit_waterfall(business.profit_pack),
    ]
    for chart in built:
        spec = chart.to_dict()          # raises if the spec is malformed
        assert spec


def run_personal_report():
    from app.pipeline import run_personal

    return run_personal()


def test_categorical_colours_are_never_cycled_into_a_new_hue():
    """A category must keep its colour regardless of how many are shown."""
    from app.reports.charts import CATEGORICAL, category_bars

    few = category_bars(["A", "B"], [Decimal("2"), Decimal("1")]).to_dict()
    many = category_bars(
        list("ABCDE"), [Decimal(str(n)) for n in range(5, 0, -1)]
    ).to_dict()

    def first_colour(spec):
        scale = spec["layer"][0]["encoding"]["color"]["scale"]
        return scale["range"][scale["domain"].index("A")]

    assert first_colour(few) == first_colour(many) == CATEGORICAL[0]


def test_waterfall_ends_at_net_profit():
    """The final bar must equal the Profit Pack's own net profit figure."""
    from app.reports.charts import profit_waterfall

    business = run_pipeline(backend_preference="heuristic", db_path=_tmp_db())
    spec = profit_waterfall(business.profit_pack).to_dict()
    rows = spec["datasets"][spec["data"]["name"]]
    total = next(r for r in rows if r["Step"] == "Net profit")
    assert abs(total["End"] - float(business.profit_pack.net_profit)) < 0.01


def test_money_in_and_out_use_opposing_colours():
    """In/out is a polarity, so the two poles must differ."""
    from app.reports.charts import NEGATIVE, POSITIVE, in_out_bars

    spec = in_out_bars(Decimal("100"), Decimal("60")).to_dict()
    colours = spec["layer"][0]["encoding"]["color"]["scale"]["range"]
    assert colours == [POSITIVE, NEGATIVE]
    assert POSITIVE != NEGATIVE


def test_charts_survive_empty_input():
    """Charts must not explode on a statement with no usable rows."""
    from app.reports import charts

    assert charts.monthly_flow([]) is not None
    assert charts.balance_line([]) is not None
    assert charts.essential_split(ZERO, ZERO) is not None


def test_household_report_keeps_transactions_for_charts():
    household = run_personal_report()
    assert household.transactions
    assert len(household.transactions) == household.transaction_count



def test_breakdown_can_be_omitted_from_markdown():
    """The GUI draws the breakdown itself, so the markdown must be able to skip it."""
    from app.reports.profit_pack import render_markdown

    pack = build_profit_pack(_pack())
    full = render_markdown(pack)
    short = render_markdown(pack, include_breakdown=False)
    assert "Category breakdown" in full
    assert "Category breakdown" not in short
    assert len(short.splitlines()) < len(full.splitlines())
    # Dropping the appendix must not touch the figures above it.
    assert "NET TAKE-HOME BUSINESS PROFIT" in short


def test_written_report_keeps_the_breakdown():
    """Downloaded files are read standalone, so they keep the detail."""
    from app.pipeline import write_outputs

    result = run_pipeline(backend_preference="heuristic", db_path=_tmp_db())
    paths = write_outputs(result, Path(tempfile.mkdtemp()))
    assert "Category breakdown" in paths["profit_pack_md"].read_text()



def test_profit_pack_cards_reconcile():
    """The three GUI cards must add up exactly as displayed."""
    pack = build_profit_pack(_pack())
    # Card 1: revenue less every cost equals net profit.
    assert pack.gross_revenue - pack.total_cogs == pack.gross_margin
    costs = pack.logistics + pack.marketing + pack.packaging + pack.financial_fees
    assert pack.gross_margin - costs == pack.net_profit
    # Card 2: profit less drawings equals what is left.
    assert pack.net_profit - pack.owner_drawings == pack.profit_after_drawings
    # Card 3: the leakage figure is the same one card 1 subtracts.
    assert pack.financial_fees > ZERO



# --- theme & imported design components -----------------------------------

def test_theme_hues_are_stable_per_category():
    """A category must keep its colour however many are displayed."""
    from app.reports import theme

    assert theme.hue_for("Customer Orders") == theme.MONEY_IN
    assert theme.hue_for("Customer Orders") == theme.hue_for("Customer Orders")
    # An unmapped label still resolves, deterministically.
    assert theme.hue_for("Something New", 0) == theme.CATEGORICAL[0]


def test_money_in_and_out_stay_distinguishable():
    """Green/red separate poorly under protanopia, so a sign must carry it."""
    from app.reports import theme

    assert theme.MONEY_IN != theme.MONEY_OUT


def test_charts_use_the_theme_palette():
    from app.reports import charts, theme

    assert charts.POSITIVE == theme.MONEY_IN
    assert charts.NEGATIVE == theme.MONEY_OUT
    assert charts.CATEGORICAL is theme.CATEGORICAL


def test_counterparties_are_keyed_on_identifier_not_name():
    """Two people sharing a display name must not be merged."""
    from app.reports.people import top_counterparties

    txns = normalize_statement(load_mock_statement())
    rows = top_counterparties(txns, Direction.IN)
    identifiers = [c.identifier for c in rows]
    assert len(identifiers) == len(set(identifiers))
    assert all(c.total > ZERO and c.count >= 1 for c in rows)


def test_counterparties_rank_by_value():
    from app.reports.people import top_counterparties

    txns = normalize_statement(load_mock_statement())
    rows = top_counterparties(txns, Direction.IN, limit=5)
    totals = [c.total for c in rows]
    assert totals == sorted(totals, reverse=True)
    assert len(rows) <= 5


def test_counterparty_initials_never_crash():
    """Statement names include numbers and punctuation."""
    from app.reports.people import Counterparty

    for name in ("Jane Wambui", "254712004411", "", "M-PESA", "A"):
        card = Counterparty(name=name, identifier="x", count=1,
                            total=Decimal("1"), direction=Direction.IN)
        assert isinstance(card.initials, str) and card.initials



def test_stylesheet_is_injected_with_st_html_not_markdown():
    """CSS must not go through Markdown.

    Two real regressions shipped here. A 4-space indent made Markdown render the
    stylesheet as a code block; then a blank line inside it ended the HTML block
    and leaked the remainder as visible text. st.html does no Markdown parsing,
    so neither can happen.
    """
    source = Path("app.py").read_text()
    assert "st.html(" in source, "the stylesheet must be injected with st.html"

    # No <style> may be handed to st.markdown, whatever its indentation.
    for match in re.finditer(r"st\.markdown\(", source):
        chunk = source[match.start(): match.start() + 4000]
        end = chunk.find("unsafe_allow_html")
        if end != -1 and "<style>" in chunk[:end]:
            raise AssertionError("a <style> block is still routed through st.markdown")


def test_raw_html_blocks_contain_no_blank_lines():
    """A blank line inside raw HTML terminates the block in Markdown."""
    source = Path("app.py").read_text()
    for match in re.finditer(r'st\.markdown\(\s*f?"""(.*?)"""', source, re.S):
        assert "\n\n" not in match.group(1), (
            "raw-HTML passed to st.markdown contains a blank line, which ends "
            "the HTML block and leaks the rest as text"
        )



def test_app_has_no_undefined_names():
    """Catch NameErrors in app.py without running Streamlit.

    app.py is a script, not an importable module, so a helper deleted during a
    refactor only surfaces when a user clicks the tab that calls it. This walks
    the AST instead.
    """
    import ast
    import builtins

    tree = ast.parse(Path("app.py").read_text())
    # Module-level names Python injects that are not builtins.
    defined = set(dir(builtins)) | {"__file__", "__name__", "__doc__"}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            defined.add(node.name)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            defined.add(node.id)
        elif isinstance(node, ast.arg):
            defined.add(node.arg)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                defined.add((alias.asname or alias.name).split(".")[0])
        elif isinstance(node, ast.ExceptHandler) and node.name:
            defined.add(node.name)

    undefined = {
        node.id: node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Name)
        and isinstance(node.ctx, ast.Load)
        and node.id not in defined
    }
    assert not undefined, f"app.py references undefined names: {undefined}"



def _contrast(hex_a: str, hex_b: str) -> float:
    """WCAG contrast ratio between two hex colours."""
    def luminance(value: str) -> float:
        value = value.lstrip("#")
        channels = [int(value[i:i + 2], 16) / 255 for i in (0, 2, 4)]
        channels = [
            c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
            for c in channels
        ]
        return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]

    light, dark = sorted([luminance(hex_a), luminance(hex_b)], reverse=True)
    return (light + 0.05) / (dark + 0.05)


def test_sidebar_input_text_is_readable():
    """Regression: sidebar inputs rendered pale text on a cream fill (1.2:1).

    Streamlit's own secondaryBackgroundColor paints inputs light, so the dark
    sidebar must set an opaque dark fill AND explicit light text.
    """
    from app.reports import theme

    source = Path("app.py").read_text()
    assert "#24352B" in source, "sidebar inputs need an opaque dark fill"

    # Entered text and placeholder must both clear WCAG AA on that fill.
    assert _contrast(theme.PAGE, "#24352B") >= 4.5
    assert _contrast("#A9BCAE", "#24352B") >= 4.5
    # And the placeholder must be dimmer than real input, or it reads as a value.
    assert _contrast("#A9BCAE", "#24352B") < _contrast(theme.PAGE, "#24352B")


def test_file_uploader_is_readable_on_the_dark_sidebar():
    """The uploader sits on the dark ground and has three text layers.

    Its test IDs were confirmed against the installed Streamlit build rather
    than guessed, so assert they are the ones actually targeted.
    """
    from app.reports import theme

    source = Path("app.py").read_text()
    for test_id in ("stFileUploader", "stFileUploaderDropzone",
                    "stFileUploaderDropzoneInstructions"):
        assert test_id in source, f"uploader rule missing for {test_id}"

    # Filename, the size/format hint, and the Browse button all clear AA.
    assert _contrast(theme.PAGE, "#24352B") >= 4.5
    assert _contrast("#A9BCAE", "#24352B") >= 4.5
    assert _contrast(theme.PAGE, theme.SIDEBAR_ACTIVE) >= 4.5


def test_theme_body_text_is_readable_on_its_surfaces():
    """Ink on card and page must clear AA — these carry every figure."""
    from app.reports import theme

    for ink in (theme.INK, theme.INK_NUMERIC, theme.INK_SECONDARY):
        assert _contrast(ink, theme.CARD) >= 4.5, ink
        assert _contrast(ink, theme.PAGE) >= 4.5, ink



# --- privacy ---------------------------------------------------------------

def test_uploads_never_use_a_shared_filename():
    """Regression: uploads went to a fixed data/_upload.<ext>.

    Two concurrent visitors would have written and read the same path, so one
    person's statement could be parsed into another person's session.
    """
    source = Path("app.py").read_text()
    assert '"data") / f"_upload' not in source, "upload uses a shared filename"
    assert "tempfile.mkstemp(" in source, "upload must use a private temp file"
    assert "os.chmod(tmp, 0o600)" in source, "upload temp file must be owner-only"


def test_upload_temp_file_is_removed_and_overwritten():
    """The bytes must be overwritten, not merely unlinked."""
    source = Path("app.py").read_text()
    loader = source[source.index("def _load_raw("): source.index("def _run(")]
    assert "unlink(missing_ok=True)" in loader
    assert 'b"\\0" * size' in loader, "temp file contents must be overwritten"


def test_no_outbound_network_calls_in_the_pipeline():
    """Only the optional LLM backend may talk to the network."""
    import pathlib as _p

    for path in _p.Path("app").rglob("*.py"):
        if path.name == "llm.py":
            continue  # the documented exception
        text = path.read_text()
        for banned in ("import requests", "import httpx", "urllib.request"):
            assert banned not in text, f"{path} makes network calls"


def test_offline_backend_transmits_nothing():
    """With no key configured the classifier must stay on-machine."""
    import os

    from app.engine.llm import HeuristicBackend, build_backend

    saved = {k: os.environ.pop(k, None) for k in
             ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "OPENAI_API_KEY")}
    try:
        assert isinstance(build_backend("auto"), HeuristicBackend)
    finally:
        for key, value in saved.items():
            if value is not None:
                os.environ[key] = value


def test_privacy_disclosure_is_shown_in_the_app():
    """A tester must be able to see what happens to their data."""
    source = Path("app.py").read_text()
    assert "Your data & privacy" in source
    # It must state the AI case, which is the only route data leaves the machine.
    assert "sent to that provider" in source



def test_hosted_environments_isolate_by_default():
    """Privacy must not depend on remembering to set a secret."""
    import os

    source = Path("app.py").read_text()
    namespace = {"os": os, "Path": Path, "__file__": "app.py"}
    exec(source[source.index("def _is_hosted"): source.index("def _db_path")], namespace)
    is_hosted = namespace["_is_hosted"]

    keys = ["BOOKWORTHS_MULTIUSER", "SPACE_ID", "RENDER", "DYNO",
            "STREAMLIT_SHARING_MODE", "FLY_APP_NAME", "K_SERVICE",
            "RAILWAY_ENVIRONMENT", "STREAMLIT_SERVER_ADDRESS"]
    saved = {k: os.environ.pop(k, None) for k in keys}
    try:
        assert is_hosted() is False, "a plain local run must share one database"
        for marker in ("BOOKWORTHS_MULTIUSER", "SPACE_ID", "RENDER",
                       "STREAMLIT_SHARING_MODE", "DYNO"):
            os.environ[marker] = "1"
            assert is_hosted() is True, f"{marker} must force isolation"
            del os.environ[marker]
    finally:
        for key, value in saved.items():
            if value is not None:
                os.environ[key] = value


def test_two_sessions_get_separate_databases():
    """The isolation claim, exercised through real Streamlit sessions."""
    import os

    from streamlit.testing.v1 import AppTest

    source = Path("app.py").read_text()
    snippet = (
        "import os, tempfile, uuid\nfrom pathlib import Path\nimport streamlit as st\n"
        + source[source.index("def _is_hosted"): source.index("DB_PATH = _db_path()")]
        + "DB_PATH = _db_path()\nst.text(DB_PATH)\n"
    )
    os.environ["BOOKWORTHS_MULTIUSER"] = "1"
    try:
        paths = []
        for _ in range(2):
            harness = AppTest.from_string(snippet)
            harness.run(timeout=30)
            paths.append(harness.text[0].value)
        assert paths[0] != paths[1], "two sessions shared one database"
        assert all("bookworths-" in p for p in paths)
    finally:
        os.environ.pop("BOOKWORTHS_MULTIUSER", None)



# --- brand -----------------------------------------------------------------

def test_logo_renders_for_both_backgrounds():
    from app.assets.logo import favicon_svg, logo_svg

    for dark in (False, True):
        svg = logo_svg(44, dark=dark)
        assert svg.lstrip().startswith("<svg")
        assert "</svg>" in svg
        assert 'role="img"' in svg and "aria-label" in svg
    # The two variants must actually differ, or the sidebar mark is invisible.
    assert logo_svg(44) != logo_svg(44, dark=True)
    assert favicon_svg().startswith("data:image/svg+xml;base64,")


def test_logo_carries_no_background_rectangle():
    """A baked-in background would show as a block on the cream page."""
    from app.assets.logo import logo_svg

    svg = logo_svg(44)
    assert "<rect" not in svg, "the mark must be transparent"


def test_printable_report_embeds_the_logo_inline():
    """The report is downloaded and printed, so nothing may be fetched."""
    from app.reports.profit_pack import render_html

    html = render_html(build_profit_pack(_pack()))
    assert "<svg" in html
    assert "src=" not in html, "the report must stay self-contained"


def test_tagline_is_consistent_everywhere():
    """The wordmark and the product must not disagree."""
    from app import TAGLINE
    from app.reports.profit_pack import TAGLINE as REPORT_TAGLINE

    assert TAGLINE == REPORT_TAGLINE == "Clean books, clear value"
    for name in ("app.py", "main.py", "README.md"):
        assert "real value" not in Path(name).read_text(), name



if __name__ == "__main__":
    import sys, traceback

    tests = [(n, o) for n, o in sorted(globals().items())
             if n.startswith("test_") and callable(o)]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"  \033[32mPASS\033[0m  {name}")
        except Exception:
            failed += 1
            print(f"  \033[31mFAIL\033[0m  {name}")
            traceback.print_exc()
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)
