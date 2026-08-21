"""End-to-end Bookworths pipeline: statement in, deliverables out."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Optional, Sequence

import pandas as pd

from .engine import CategorizationEngine, EntityMemory
from .ingest import load_statement_csv, load_statement_pdf, normalize_statement
from .mockdata import load_demo_statement
from .reports.profit_pack import ProfitPack, build_profit_pack, render_html, render_markdown
from .reports.household import HouseholdReport, build_household_report, render_household_markdown
from .reports.personal import PersonalReport, build_personal_report, render_personal_markdown
from .reports.restock import RestockBudget, calculate_restock_budget, render_restock_markdown
from .reports.whatsapp import build_whatsapp_draft
from .schema import ClassifiedTransaction


@dataclass
class BookworthsResult:
    transactions: list[ClassifiedTransaction]
    profit_pack: ProfitPack
    restock_budget: RestockBudget
    personal_report: PersonalReport
    whatsapp_draft: str
    engine_stats: dict[str, object]

    @property
    def exceptions(self) -> list[ClassifiedTransaction]:
        return [t for t in self.transactions if t.needs_review]

    def to_dataframe(self) -> pd.DataFrame:
        return pd.DataFrame(
            [
                {
                    "transaction_id": r.transaction.transaction_id,
                    "date": r.transaction.timestamp,
                    "counterparty": r.transaction.entity_name,
                    "identifier": r.transaction.entity_identifier,
                    "direction": r.transaction.direction.value,
                    "gross": r.transaction.gross_amount,
                    "fee": r.transaction.fee_amount,
                    "net": r.transaction.net_amount,
                    "account_code": r.account.value,
                    "account": r.account.label,
                    "confidence": r.classification.confidence,
                    "layer": r.classification.layer,
                    "needs_review": r.needs_review,
                    "rationale": r.classification.rationale,
                }
                for r in self.transactions
            ]
        )


def run_pipeline(
    statement_path: Optional[str | Path] = None,
    *,
    business_name: str = "Bookworths Vendor",
    db_path: str | Path = "data/bookworths.db",
    backend_preference: str = "auto",
    cash_on_hand: Optional[Decimal] = None,
    pdf_password: Optional[str] = None,
) -> BookworthsResult:
    """Run ingestion -> categorization -> reporting.

    With no ``statement_path`` the built-in mock statement is used, so the
    pipeline is runnable out of the box.
    """
    if statement_path is None:
        raw = load_demo_statement()
    else:
        path = Path(statement_path)
        raw = (
            load_statement_pdf(path, password=pdf_password)
            if path.suffix.lower() == ".pdf"
            else load_statement_csv(path)
        )

    transactions = normalize_statement(raw)
    engine = CategorizationEngine(
        memory=EntityMemory(db_path), backend_preference=backend_preference
    )
    results = engine.classify_all(transactions)

    pack = build_profit_pack(results, business_name=business_name)
    budget = calculate_restock_budget(results, cash_on_hand=cash_on_hand)
    draft = build_whatsapp_draft(results, seller_name=business_name)

    return BookworthsResult(
        transactions=results,
        profit_pack=pack,
        restock_budget=budget,
        personal_report=build_personal_report(results, business_profit=pack.net_profit),
        whatsapp_draft=draft,
        engine_stats=engine.stats.as_dict(),
    )


def write_outputs(result: BookworthsResult, out_dir: str | Path = "output") -> dict[str, Path]:
    """Write every deliverable to disk; returns a name -> path map."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    markdown = (
        render_markdown(result.profit_pack)
        + "\n\n---\n\n"
        + render_restock_markdown(result.restock_budget)
        + "\n\n---\n\n"
        + render_personal_markdown(result.personal_report)
    )

    paths = {
        "profit_pack_pdf": out / "bookworths_profit_pack.pdf",
        "profit_pack_html": out / "bookworths_profit_pack.html",
        "whatsapp": out / "bookworths_whatsapp_draft.txt",
        "ledger_csv": out / "bookworths_ledger.csv",
    }
    from .reports.pdf import profit_pack_pdf

    paths["profit_pack_pdf"].write_bytes(
        profit_pack_pdf(result.profit_pack, result.profit_pack.business_name)
    )
    paths["profit_pack_html"].write_text(render_html(result.profit_pack), encoding="utf-8")
    paths["whatsapp"].write_text(result.whatsapp_draft, encoding="utf-8")
    result.to_dataframe().to_csv(paths["ledger_csv"], index=False)
    return paths


def run_personal(
    statement_path: Optional[str | Path] = None,
    *,
    pdf_password: Optional[str] = None,
) -> HouseholdReport:
    """Personal mode: analyse a statement as one household's money.

    Deliberately independent of the business pipeline — no categorization
    engine, no chart of accounts, no entity memory. A personal statement is not
    a business one with different labels, so it does not borrow that machinery.
    """
    if statement_path is None:
        raw = load_demo_statement()
    else:
        path = Path(statement_path)
        raw = (
            load_statement_pdf(path, password=pdf_password)
            if path.suffix.lower() == ".pdf"
            else load_statement_csv(path)
        )
    return build_household_report(normalize_statement(raw))
