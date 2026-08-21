#!/usr/bin/env python3
"""Bookworths — clean books, clear value.

Run the complete pipeline against the built-in mock M-Pesa statement:

    python main.py

Or against a real Safaricom export:

    python main.py --statement statement.csv --business "Thrift by Njeri"
    python main.py --statement statement.pdf --pdf-password 123456
"""
from __future__ import annotations

import argparse
from pathlib import Path
from decimal import Decimal

from app import TAGLINE, __version__
from app.pipeline import run_personal, run_pipeline, write_outputs
from app.reports.profit_pack import render_markdown
from app.reports.household import render_household_markdown
from app.reports.personal import render_personal_markdown
from app.reports.restock import render_restock_markdown


def _rule(title: str = "") -> None:
    if title:
        print(f"\n\033[1m{'━' * 78}\033[0m")
        print(f"\033[1m  {title}\033[0m")
        print(f"\033[1m{'━' * 78}\033[0m")
    else:
        print("━" * 78)


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="bookworths", description=f"Bookworths — {TAGLINE}"
    )
    parser.add_argument(
        "--mode", default="business", choices=["business", "personal"],
        help="business: profit and restock. personal: household income vs spending.",
    )
    parser.add_argument("--statement", help="Path to an M-Pesa CSV/XLSX/PDF export")
    parser.add_argument("--pdf-password", help="Password for a protected PDF statement")
    parser.add_argument("--business", default="Thrift by Njeri", help="Vendor name")
    parser.add_argument("--db", default="data/bookworths.db", help="Entity-memory SQLite path")
    parser.add_argument("--out", default="output", help="Directory for deliverables")
    parser.add_argument(
        "--backend",
        default="auto",
        choices=["auto", "anthropic", "openai", "heuristic"],
        help="Layer 3 classifier backend (default: auto)",
    )
    parser.add_argument("--cash", type=Decimal, help="Override current M-Pesa balance")
    parser.add_argument("--quiet", action="store_true", help="Write files without printing reports")
    parser.add_argument(
        "--no-personal", action="store_true",
        help="Skip the personal finance analysis (business books only)",
    )
    parser.add_argument("--version", action="version", version=f"Bookworths {__version__}")
    args = parser.parse_args()

    print(f"\n\033[1;32mBOOKWORTHS\033[0m \033[3m{TAGLINE}\033[0m  v{__version__}")
    print(f"Mode:   {args.mode}")
    print(f"Source: {args.statement or 'built-in mock M-Pesa statement'}")

    if args.mode == "personal":
        try:
            report = run_personal(args.statement, pdf_password=args.pdf_password)
        except FileNotFoundError:
            print(f"\n\033[31mCould not find that statement:\033[0m {args.statement}\n")
            return 1
        except ValueError as exc:
            print(f"\n\033[31mThat statement could not be read:\033[0m {exc}\n")
            return 1

        _rule("PERSONAL FINANCE REPORT")
        markdown = render_household_markdown(report)
        if not args.quiet:
            print(markdown)
        out = Path(args.out)
        out.mkdir(parents=True, exist_ok=True)
        destination = out / "bookworths_personal_finance.md"
        destination.write_text(markdown, encoding="utf-8")
        _rule("DELIVERABLE WRITTEN")
        print(f"  personal_report      {destination}\n")
        return 0

    try:
        result = run_pipeline(
            statement_path=args.statement,
            business_name=args.business,
            db_path=args.db,
            backend_preference=args.backend,
            cash_on_hand=args.cash,
            pdf_password=args.pdf_password,
        )
    except FileNotFoundError:
        print(f"\n\033[31mCould not find that statement:\033[0m {args.statement}")
        print("Check the path, or run without --statement to use the built-in demo.\n")
        return 1
    except ImportError as exc:
        print(f"\n\033[31mMissing an optional dependency:\033[0m {exc}\n")
        return 1
    except ValueError as exc:
        print(f"\n\033[31mThat statement could not be read:\033[0m {exc}")
        print(
            "\nBookworths expects a Safaricom M-Pesa export with the columns:\n"
            "  Receipt No., Completion Time, Details, Transaction Status,\n"
            "  Paid In, Withdrawn, Balance\n"
            "Export it from the M-Pesa app or the Safaricom statement email.\n"
        )
        return 1

    _rule("CATEGORIZATION ENGINE")
    for key, value in result.engine_stats.items():
        print(f"  {key:<34} {value}")
    print(f"  {'Transactions processed':<34} {len(result.transactions)}")

    if not args.quiet:
        _rule("MODULE A — VENDOR PROFIT PACK")
        print(render_markdown(result.profit_pack))

        _rule("MODULE B — RESTOCK SAFETY BUDGET")
        print(render_restock_markdown(result.restock_budget))

        _rule("MODULE C — WHATSAPP EXCEPTION TEMPLATE")
        print(result.whatsapp_draft)

        if not args.no_personal:
            _rule("MODULE D — PERSONAL FINANCE ANALYSIS")
            print(render_personal_markdown(result.personal_report))

    paths = write_outputs(result, args.out)
    _rule("DELIVERABLES WRITTEN")
    for name, path in paths.items():
        print(f"  {name:<20} {path}")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
