"""Fallback parser for fixed-width M-Pesa exports.

Some Safaricom statements — especially PDFs converted to XLSX/ODS by third-party
tools — arrive with every column crammed into a single spreadsheet cell as
space-padded text, several statement rows per cell, headers repeating at each
page break, and long Details values wrapped onto continuation lines.

`pandas.read_excel` cannot help with that: it sees one column. This module
recovers the real table from the text layout.
"""
from __future__ import annotations

import re
from typing import Iterable

import pandas as pd

#: A statement row starts with a receipt code: 10 uppercase alphanumerics.
_RECEIPT_RE = re.compile(r"^([A-Z0-9]{10})\s")
_HEADER_RE = re.compile(r"^\s*Receipt\s*No\.", re.IGNORECASE)
_TIMESTAMP_RE = re.compile(r"(\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2})")
#: Money: optional minus, digits with optional thousands separators/decimals.
_MONEY_RE = re.compile(r"-?\d[\d,]*\.?\d*")
_STATUS_RE = re.compile(
    r"\b(Completed|Confirmed|Failed|Reversed|Pending|Cancelled)\b", re.IGNORECASE
)

_CANONICAL_COLUMNS = [
    "Receipt No.", "Completion Time", "Details", "Transaction Status",
    "Paid In", "Withdrawn", "Balance",
]


def looks_fixed_width(df: pd.DataFrame) -> bool:
    """True if this frame is really one text column, not a parsed table."""
    if df.empty:
        return False
    # A genuine export has its columns split out; a mangled one has the whole
    # header sitting inside a single cell.
    joined = " ".join(str(c) for c in df.columns).lower()
    if "receipt" in joined and "details" in joined and len(df.columns) >= 5:
        return False
    # Read with header=None, a real table puts its header in row 0 instead.
    if len(df.columns) >= 5 and not df.empty:
        first = " ".join(str(v) for v in df.iloc[0].tolist()).lower()
        if "receipt" in first and "details" in first:
            return False
    sample = " ".join(
        str(v) for v in df.head(20).to_numpy().ravel() if not pd.isna(v)
    )
    return bool(_HEADER_RE.search(sample) or _RECEIPT_RE.search(sample.lstrip()))


def _physical_lines(df: pd.DataFrame) -> list[str]:
    """Flatten every cell into physical text lines, preserving order."""
    lines: list[str] = []
    for _, row in df.iterrows():
        for cell in row:
            if cell is None or (isinstance(cell, float) and pd.isna(cell)):
                continue
            text = str(cell)
            if text == "nan":
                continue
            lines.extend(text.split("\n"))
    return lines


def _split_numeric_tail(tail: str) -> tuple[str, str, str]:
    """Split the trailing numeric block into (paid_in, withdrawn, balance).

    Safaricom pads these columns inconsistently once a statement has been
    through a PDF converter, so position is unreliable. Sign is not: money out
    is negative, money in is positive, and the balance is always last.
    """
    amounts = _MONEY_RE.findall(tail)
    amounts = [a for a in amounts if a not in {"-", ""}]
    if not amounts:
        return "", "", ""
    balance = amounts[-1]
    movements = amounts[:-1]
    paid_in, withdrawn = "", ""
    for value in movements:
        if value.startswith("-"):
            withdrawn = value
        else:
            paid_in = value
    # A single unsigned movement with no sign information is ambiguous; treat a
    # lone value as paid in only when nothing else claimed it.
    if not paid_in and not withdrawn and movements:
        paid_in = movements[0]
    return paid_in, withdrawn, balance


def parse_fixed_width(df: pd.DataFrame) -> pd.DataFrame:
    """Recover a canonical seven-column statement from mangled text rows."""
    lines = _physical_lines(df)

    records: list[dict[str, str]] = []
    for line in lines:
        if _HEADER_RE.match(line):
            continue  # repeated page header
        match = _RECEIPT_RE.match(line)
        if not match:
            # Continuation of the previous row's wrapped Details text.
            fragment = line.strip()
            if records and fragment and not _MONEY_RE.fullmatch(fragment):
                records[-1]["Details"] = (records[-1]["Details"] + " " + fragment).strip()
            continue

        receipt = match.group(1)
        rest = line[match.end(1):]

        ts_match = _TIMESTAMP_RE.search(rest)
        if not ts_match:
            continue  # not a real transaction row
        timestamp = ts_match.group(1)

        after_ts = rest[ts_match.end():]
        status_match = _STATUS_RE.search(after_ts)
        if status_match:
            details = after_ts[: status_match.start()].strip()
            status = status_match.group(1)
            tail = after_ts[status_match.end():]
        else:
            details, status, tail = after_ts.strip(), "Completed", ""

        paid_in, withdrawn, balance = _split_numeric_tail(tail)

        records.append({
            "Receipt No.": receipt,
            "Completion Time": timestamp,
            "Details": re.sub(r"\s{2,}", " ", details),
            "Transaction Status": status,
            "Paid In": paid_in,
            "Withdrawn": withdrawn,
            "Balance": balance,
        })

    if not records:
        raise ValueError(
            "This looks like a fixed-width M-Pesa export, but no transaction "
            "rows could be recovered from it."
        )
    return pd.DataFrame(_merge_split_details(records), columns=_CANONICAL_COLUMNS)


#: Detail fragments that carry no counterparty information on their own.
_NOISE_DETAILS = re.compile(
    r"^(OverDraft of Credit Party|M-?Pesa|Pesa|OD Loan Repayment.*|MPESA)$",
    re.IGNORECASE,
)


def _merge_split_details(records: list[dict[str, str]]) -> list[dict[str, str]]:
    """Repair Details text split across a converter's page boundaries.

    These exports emit each receipt twice — the payment and its Fuliza
    counterpart — and wrap the counterparty name across both rows and any
    continuation lines. Pooling the fragments per receipt recovers the name for
    every row that shares it, which is what lets entity memory key on a real
    counterparty instead of a stray surname.
    """
    pooled: dict[str, list[str]] = {}
    for record in records:
        detail = record["Details"].strip()
        if not detail or _NOISE_DETAILS.match(detail):
            continue
        pooled.setdefault(record["Receipt No."], []).append(detail)

    for record in records:
        fragments = pooled.get(record["Receipt No."])
        if not fragments:
            continue
        # Longest fragment carries the most counterparty context; append the
        # rest so a name split mid-word is still recoverable.
        best = max(fragments, key=len)
        extras = [f for f in fragments if f is not best and f not in best]
        merged = " ".join([best, *extras]).strip()
        if len(merged) > len(record["Details"].strip()):
            record["Details"] = re.sub(r"\s{2,}", " ", merged)
    return records
