"""Ingestion: raw Safaricom export -> canonical `Transaction` list.

Safaricom bills tariffs on their own statement line (usually the parent receipt
suffixed `-FEE`, sometimes an adjacent "… Charge" row). Folding those onto the
parent is what lets the Profit Pack report a single honest cost per payout
while still totalling the leakage separately.
"""
from __future__ import annotations

import re
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Iterable

import pandas as pd

from ..schema import Direction, EntityKind, Transaction
from .fixedwidth import looks_fixed_width, parse_fixed_width

# --- Column aliasing -------------------------------------------------------
# Safaricom has shipped several header spellings over the years; map them all.
_COLUMN_ALIASES: dict[str, str] = {
    "receipt no.": "receipt",
    "receipt no": "receipt",
    "receipt": "receipt",
    "transaction id": "receipt",
    "completion time": "timestamp",
    "date": "timestamp",
    "details": "details",
    "description": "details",
    "transaction status": "status",
    "status": "status",
    "paid in": "paid_in",
    "credit": "paid_in",
    "withdrawn": "withdrawn",
    "debit": "withdrawn",
    "balance": "balance",
    "running balance": "balance",
}

_REQUIRED = {"receipt", "timestamp", "details"}

# --- Entity extraction patterns -------------------------------------------
_PHONE_RE = re.compile(r"\b(?:254|\+254|0)(7\d{8}|1\d{8})\b")
#: Safaricom masks phone numbers on some exports: 07******212 / 254******212.
#: The visible digits are stable per counterparty, so they still key memory
#: reliably when combined with the counterparty name.
_MASKED_PHONE_RE = re.compile(r"\b(?:\+?254|0)?(\d{0,3})\*{3,}(\d{2,4})\b")
#: "to 7384394 - DAMARIS MORAA" — a till/agent number without the usual prefix.
_BARE_TILL_RE = re.compile(r"\bto\s+(\d{5,7})\s*-", re.IGNORECASE)
_PAYBILL_RE = re.compile(r"pay\s*bill\s+(?:to\s+)?(\d{5,7})", re.IGNORECASE)
_TILL_RE = re.compile(
    r"merchant\s+payment\s+to\s+(\d{5,7})", re.IGNORECASE
)
_ACCOUNT_RE = re.compile(r"\bAcc\.?\s*([A-Za-z0-9\-_/]+)", re.IGNORECASE)

#: Lines that are a tariff rather than a payment in their own right.
_FEE_LINE_RE = re.compile(
    # Any "<something> Charge" line is a tariff, however the exporter spaced it
    # ("Customer Transfer of FundsCharge" loses its space in converted files).
    r"(charge\b|"
    r"fuliza\s*m-?pesa\s*loan\s*access\s*fee|access\s*fee|"
    r"transaction\s*fee|excise\s*duty|tariff)",
    re.IGNORECASE,
)

_NAME_CLEANUP_RE = re.compile(
    r"^(funds\s+received\s+from|customer\s+transfer\s+(?:fuliza\s+)?to|"
    r"customer\s+transfer\s+of\s+funds\s+charge|merchant\s+payment\s+to|"
    r"pay\s*bill\s+(?:to\s+)?|business\s+payment\s+from|"
    r"receive\s+money\s+from|send\s+money\s+to)\s*",
    re.IGNORECASE,
)


def _norm_headers(df: pd.DataFrame) -> pd.DataFrame:
    renamed = {}
    for col in df.columns:
        key = str(col).strip().lower()
        renamed[col] = _COLUMN_ALIASES.get(key, key.replace(" ", "_"))
    out = df.rename(columns=renamed)
    missing = _REQUIRED - set(out.columns)
    if missing:
        raise ValueError(
            f"Statement is missing required column(s): {sorted(missing)}. "
            f"Saw: {sorted(out.columns)}"
        )
    for optional in ("paid_in", "withdrawn", "balance", "status"):
        if optional not in out.columns:
            out[optional] = ""
    return out


def _money(value: object) -> Decimal:
    """Parse a Safaricom money cell to a non-negative Decimal. Blank -> 0."""
    if value is None:
        return Decimal("0")
    text = str(value).strip().replace(",", "").replace("KES", "").strip()
    if not text or text.lower() in {"nan", "none", "-"}:
        return Decimal("0")
    try:
        return abs(Decimal(text))
    except InvalidOperation:
        return Decimal("0")


def _optional_money(value: object) -> Decimal | None:
    text = str(value or "").strip()
    if not text or text.lower() in {"nan", "none"}:
        return None
    try:
        return Decimal(text.replace(",", ""))
    except InvalidOperation:
        return None


def _parse_timestamp(value: object) -> datetime:
    text = str(value).strip()
    for fmt in (
        "%Y-%m-%d %H:%M:%S",
        "%d-%m-%Y %H:%M:%S",
        "%d/%m/%Y %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%d-%m-%Y %H:%M",
    ):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    parsed = pd.to_datetime(text, errors="coerce", dayfirst=True)
    if pd.isna(parsed):
        raise ValueError(f"Unparseable Completion Time: {value!r}")
    return parsed.to_pydatetime()


def _extract_entity(details: str) -> tuple[str, str | None, EntityKind]:
    """Pull (name, identifier, kind) out of a Details string."""
    paybill = _PAYBILL_RE.search(details)
    till = _TILL_RE.search(details)
    phone = _PHONE_RE.search(details)

    bare_till = _BARE_TILL_RE.search(details)
    masked = _MASKED_PHONE_RE.search(details)

    if paybill:
        identifier, kind = paybill.group(1), EntityKind.PAYBILL
    elif till:
        identifier, kind = till.group(1), EntityKind.TILL
    elif phone:
        identifier, kind = "254" + phone.group(1), EntityKind.PHONE
    elif bare_till:
        identifier, kind = bare_till.group(1), EntityKind.TILL
    elif masked:
        # Key on the masked pattern itself so the same counterparty maps
        # consistently across statements, even without full digits.
        identifier = f"{masked.group(1)}*{masked.group(2)}"
        kind = EntityKind.PHONE
    else:
        identifier, kind = None, EntityKind.UNKNOWN

    name = _NAME_CLEANUP_RE.sub("", details).strip()
    name = _MASKED_PHONE_RE.sub("", name)
    name = re.sub(r"^\s*to\s+\d{5,7}\s*-\s*", "", name, flags=re.IGNORECASE)
    if identifier and "*" not in identifier:
        name = re.sub(rf"\b(?:\+?254|0)?{re.escape(identifier[-9:])}\b", "", name)
        name = name.replace(identifier, "")
    name = _ACCOUNT_RE.sub("", name)
    name = name.strip(" -–—,. ")
    name = re.sub(r"\s{2,}", " ", name)
    return (name.upper() or "UNKNOWN"), identifier, kind


def _is_fee_line(details: str) -> bool:
    return bool(_FEE_LINE_RE.search(details))


def _parent_receipt(receipt: str) -> str:
    """`TG71AA02C3-FEE` -> `TG71AA02C3`."""
    return re.sub(r"[-_](fee|charge)$", "", receipt, flags=re.IGNORECASE)


def load_statement_csv(path: str | Path) -> pd.DataFrame:
    """Read a Safaricom CSV/XLSX/ODS export into a raw DataFrame.

    Converted exports often arrive as fixed-width text in a single column; those
    are recovered by `parse_fixed_width` rather than rejected.
    """
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix in {".xlsx", ".xls", ".ods"}:
        # ODS needs the odfpy engine; pandas picks the right one for xlsx/xls.
        engine = "odf" if suffix == ".ods" else None
        raw = pd.read_excel(path, dtype=str, header=None, engine=engine).fillna("")
        if looks_fixed_width(raw):
            return parse_fixed_width(raw)
        # Re-read with the header row honoured now we know it is a real table.
        return pd.read_excel(path, dtype=str, engine=engine).fillna("")

    # Try a normal delimited read first — that is what almost every export is.
    try:
        table = pd.read_csv(path, dtype=str).fillna("")
        if len(table.columns) >= 5:
            return table
    except Exception:
        table = None
    # Single-column or unparseable: likely fixed-width text saved as .csv/.txt.
    raw = pd.read_csv(
        path, dtype=str, header=None, sep="\x00", engine="python",
        quoting=3, on_bad_lines="skip",
    ).fillna("")
    if looks_fixed_width(raw):
        return parse_fixed_width(raw)
    if table is not None:
        return table
    raise ValueError(f"Could not interpret {path.name} as an M-Pesa statement.")


def load_statement_pdf(path: str | Path, password: str | None = None) -> pd.DataFrame:
    """Read a password-protected Safaricom PDF statement.

    Requires `pdfplumber` (`pip install pdfplumber`), which is optional because
    the CSV path covers the common case. The PDF tables carry the same seven
    columns, so the extracted rows flow through `normalize_statement` unchanged.
    """
    try:
        import pdfplumber  # type: ignore
    except ImportError as exc:  # pragma: no cover - depends on optional dep
        raise ImportError(
            "PDF ingestion needs pdfplumber. Install it with: pip install pdfplumber"
        ) from exc

    rows: list[list[str]] = []
    header: list[str] | None = None
    text_lines: list[str] = []

    with pdfplumber.open(str(path), password=password) as pdf:
        for page in pdf.pages:
            for table in page.extract_tables() or []:
                for row in table:
                    cells = [(c or "").strip() for c in row]
                    if not any(cells):
                        continue
                    if header is None and "Receipt" in " ".join(cells):
                        header = cells
                        continue
                    if header and len(cells) == len(header):
                        rows.append(cells)
            page_text = page.extract_text() or ""
            text_lines.extend(page_text.split("\n"))

    if header and rows:
        return pd.DataFrame(rows, columns=header).fillna("")

    # Many Safaricom PDFs (and anything without ruled table borders) yield no
    # detectable table. The text layout is still fixed-width, so recover it the
    # same way a mangled spreadsheet is recovered.
    if text_lines:
        candidate = pd.DataFrame({0: text_lines})
        if looks_fixed_width(candidate):
            return parse_fixed_width(candidate)

    raise ValueError(
        f"No M-Pesa statement table could be read from {path.name}. "
        "If it is password protected, pass the password; otherwise export CSV."
    )


def normalize_statement(raw: pd.DataFrame) -> list[Transaction]:
    """Normalize a raw statement into canonical transactions.

    Fee lines are merged into their parent where a parent can be identified;
    orphan fees (Fuliza access fees, standalone charges) survive as their own
    transaction so the leakage total stays complete.
    """
    df = _norm_headers(raw)

    payments: dict[str, Transaction] = {}
    by_receipt: dict[str, list[str]] = {}
    ordered: list[str] = []
    orphan_fees: list[Transaction] = []
    pending_fees: list[tuple[str, Decimal, str, datetime, str]] = []

    for _, row in df.iterrows():
        receipt = str(row["receipt"]).strip()
        details = str(row["details"]).strip()
        status = str(row.get("status") or "Completed").strip() or "Completed"
        if status.lower() not in {"completed", "confirmed", ""}:
            continue  # skip reversed/failed lines entirely

        try:
            timestamp = _parse_timestamp(row["timestamp"])
        except ValueError:
            # Footers, disclaimers, page furniture and blank spacer rows all
            # land here. They are not transactions, so drop them silently
            # rather than failing the whole statement.
            continue
        paid_in = _money(row.get("paid_in"))
        withdrawn = _money(row.get("withdrawn"))
        balance = _optional_money(row.get("balance"))

        if _is_fee_line(details):
            pending_fees.append(
                (_parent_receipt(receipt), withdrawn or paid_in, details, timestamp, receipt)
            )
            continue

        direction = Direction.IN if paid_in > 0 else Direction.OUT
        gross = paid_in if direction is Direction.IN else withdrawn
        name, identifier, kind = _extract_entity(details)

        txn = Transaction(
            transaction_id=receipt,
            timestamp=timestamp,
            raw_text=details,
            entity_name=name,
            entity_identifier=identifier,
            entity_kind=kind,
            direction=direction,
            gross_amount=gross,
            fee_amount=Decimal("0"),
            balance_after=balance,
            status=status,
        )
        # A receipt is NOT unique in Safaricom exports: one receipt can cover a
        # payment, its charge, and a Fuliza drawdown. Key each row separately so
        # none of them overwrite each other.
        row_key = f"{receipt}#{len(ordered)}"
        payments[row_key] = txn
        by_receipt.setdefault(receipt, []).append(row_key)
        ordered.append(row_key)

    # Second pass: attach fees now that every parent is known.
    for parent, amount, details, timestamp, receipt in pending_fees:
        # Safaricom bills the charge either on a "<receipt>-FEE" line or on a
        # line reusing the parent's own receipt. Prefer the largest outflow on
        # that receipt — the charge belongs to the payment it funded, not to a
        # Fuliza drawdown that happens to share the receipt.
        candidates = by_receipt.get(parent) or by_receipt.get(receipt) or []
        outflows = [
            k for k in candidates if payments[k].direction is Direction.OUT
        ]
        pool = outflows or candidates
        if pool:
            best = max(pool, key=lambda k: payments[k].gross_amount)
            parent_txn = payments[best]
            merged = parent_txn.model_copy(
                update={"fee_amount": parent_txn.fee_amount + amount}
            )
            payments[best] = Transaction.model_validate(merged.model_dump())
            continue
        name, identifier, kind = _extract_entity(details)
        orphan = Transaction(
            transaction_id=receipt,
            timestamp=timestamp,
            raw_text=details,
            entity_name=name or "SAFARICOM",
            entity_identifier=identifier,
            entity_kind=kind if identifier else EntityKind.SYSTEM,
            direction=Direction.OUT,
            gross_amount=amount,
            fee_amount=Decimal("0"),
        )
        orphan_fees.append(orphan)

    result: list[Transaction] = [payments[r] for r in ordered] + orphan_fees
    result.sort(key=lambda t: (t.timestamp, t.transaction_id))
    return result


def to_dataframe(transactions: Iterable[Transaction]) -> pd.DataFrame:
    """Flatten transactions for ad-hoc pandas analysis."""
    return pd.DataFrame([t.model_dump() for t in transactions])
