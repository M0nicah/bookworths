"""Layer 2 — persistent entity memory backed by SQLite.

The whole point of Bookworths is that a seller confirms a counterparty once and
never again. Every confirmed mapping (phone / till / paybill -> account) is
written here, and the confidence rises each time the mapping is reinforced.
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterator, Optional

from ..schema import Account

DEFAULT_DB_PATH = Path("data/bookworths.db")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS entity_map (
    entity_identifier TEXT PRIMARY KEY,
    entity_kind       TEXT NOT NULL,
    display_name      TEXT NOT NULL,
    account_code      TEXT NOT NULL,
    confidence        REAL NOT NULL DEFAULT 0.90,
    source            TEXT NOT NULL DEFAULT 'seed',
    times_seen        INTEGER NOT NULL DEFAULT 1,
    last_seen         TEXT,
    notes             TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS name_hint (
    pattern      TEXT PRIMARY KEY,
    account_code TEXT NOT NULL,
    confidence   REAL NOT NULL DEFAULT 0.88,
    label        TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS classification_log (
    transaction_id TEXT PRIMARY KEY,
    account_code   TEXT NOT NULL,
    confidence     REAL NOT NULL,
    layer          TEXT NOT NULL,
    decided_at     TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_entity_account ON entity_map(account_code);
"""

# Seeded from a typical Nairobi IG-vendor book. These are the counterparties a
# seller would otherwise re-classify every single month.
_SEED_ENTITIES: list[tuple[str, str, str, Account, float, str]] = [
    # --- Restock & inventory (Gikomba / Eastleigh / Kamukunji) ---
    ("254701447788", "PHONE", "Mama Bale — Gikomba", Account.COGS_RESTOCK, 0.97, "Weekly bale supplier"),
    ("254798332211", "PHONE", "Kamau — sourcing matatu", Account.COGS_SOURCING_TRANSPORT, 0.93, "Gikomba fare + porter"),
    # --- Logistics & fulfilment ---
    ("254733889012", "PHONE", "Boniface — boda rider", Account.LOGISTICS, 0.97, "Primary Nairobi rider"),
    ("254790556677", "PHONE", "Easy Coach parcel office", Account.LOGISTICS, 0.95, "Upcountry parcels"),
    ("254773558899", "PHONE", "Fargo Courier Eldoret", Account.LOGISTICS, 0.95, ""),
    ("254781334455", "PHONE", "G4S Courier Nakuru", Account.LOGISTICS, 0.95, ""),
    ("4033012", "PAYBILL", "CBD pickup shelf rent", Account.LOGISTICS, 0.96, "Stall 22 monthly rent"),
    # --- Packaging & branding ---
    ("4477201", "TILL", "Mailer Bags KE", Account.PACKAGING, 0.95, ""),
    ("8890124", "TILL", "Packaging Box Hub", Account.PACKAGING, 0.94, ""),
    ("254745998877", "PHONE", "Sticker & label printer", Account.PACKAGING, 0.92, "Tags and thank-you cards"),
    # --- Marketing & visibility ---
    ("5296000", "PAYBILL", "Meta / Facebook Payments", Account.MARKETING, 0.98, "IG boosts"),
    ("254755667711", "PHONE", "Aisha — model / content shoot", Account.MARKETING, 0.91, ""),
    # --- Owner personal ---
    ("5423119", "TILL", "Naivas Supermarket", Account.OWNER_DRAWINGS, 0.94, "Household groceries"),
    ("3320088", "TILL", "Naivas Supermarket", Account.OWNER_DRAWINGS, 0.94, "Household groceries"),
    ("7781140", "TILL", "Quickmart", Account.OWNER_DRAWINGS, 0.93, "Household groceries"),
    ("6612300", "TILL", "Java House", Account.OWNER_DRAWINGS, 0.90, "Personal dining"),
    ("254768223311", "PHONE", "Family — school fees", Account.OWNER_DRAWINGS, 0.92, "Family support"),
    ("888880", "PAYBILL", "KPLC prepaid tokens", Account.OWNER_DRAWINGS, 0.86, "Home meter, not the stall"),
]

# Name-pattern fallbacks for counterparties whose number changes but whose
# business does not (rider pools, parcel offices, market suppliers).
_SEED_HINTS: list[tuple[str, Account, float, str]] = [
    ("BODA", Account.LOGISTICS, 0.90, "Boda rider payout"),
    ("RIDER", Account.LOGISTICS, 0.90, "Rider payout"),
    ("PIKIPIKI", Account.LOGISTICS, 0.89, "Rider payout"),
    ("PARCEL", Account.LOGISTICS, 0.90, "Parcel office"),
    ("COURIER", Account.LOGISTICS, 0.90, "Courier"),
    ("SHELF RENT", Account.LOGISTICS, 0.93, "CBD pickup shelf"),
    ("EASY COACH", Account.LOGISTICS, 0.92, "Upcountry parcel"),
    ("2NK", Account.LOGISTICS, 0.90, "Upcountry parcel"),
    ("GIKOMBA", Account.COGS_RESTOCK, 0.94, "Gikomba supplier"),
    ("EASTLEIGH", Account.COGS_RESTOCK, 0.93, "Eastleigh supplier"),
    ("KAMUKUNJI", Account.COGS_RESTOCK, 0.93, "Kamukunji supplier"),
    ("BALE", Account.COGS_RESTOCK, 0.94, "Bale purchase"),
    ("MITUMBA", Account.COGS_RESTOCK, 0.92, "Thrift stock"),
    ("SUPPLIER", Account.COGS_RESTOCK, 0.90, "Stock supplier"),
    ("MAILER", Account.PACKAGING, 0.92, "Mailer bags"),
    ("PACKAGING", Account.PACKAGING, 0.92, "Packaging"),
    ("STICKER", Account.PACKAGING, 0.90, "Branding stickers"),
    ("BOX HUB", Account.PACKAGING, 0.90, "Custom boxes"),
    ("SHOOT", Account.MARKETING, 0.88, "Content shoot"),
    ("INFLUENCER", Account.MARKETING, 0.90, "Influencer collab"),
    ("MODEL", Account.MARKETING, 0.87, "Model fee"),
    ("SUPERMARKET", Account.OWNER_DRAWINGS, 0.90, "Household groceries"),
    ("NAIVAS", Account.OWNER_DRAWINGS, 0.92, "Household groceries"),
    ("QUICKMART", Account.OWNER_DRAWINGS, 0.92, "Household groceries"),
    ("CARREFOUR", Account.OWNER_DRAWINGS, 0.92, "Household groceries"),
    ("SCHOOL FEES", Account.OWNER_DRAWINGS, 0.93, "Family support"),
    ("SALON", Account.OWNER_DRAWINGS, 0.90, "Personal grooming"),
]


@dataclass(frozen=True)
class EntityRecord:
    entity_identifier: str
    entity_kind: str
    display_name: str
    account: Account
    confidence: float
    source: str
    times_seen: int


class EntityMemory:
    """Persistent counterparty memory. Safe to construct repeatedly."""

    def __init__(self, db_path: str | Path = DEFAULT_DB_PATH, seed: bool = True):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()
        if seed:
            self.seed_defaults()

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _init_db(self) -> None:
        with self._conn() as conn:
            conn.executescript(_SCHEMA)

    def seed_defaults(self) -> None:
        """Insert the starter book. Existing rows are never overwritten."""
        now = datetime.now().isoformat(timespec="seconds")
        with self._conn() as conn:
            conn.executemany(
                """INSERT OR IGNORE INTO entity_map
                   (entity_identifier, entity_kind, display_name, account_code,
                    confidence, source, times_seen, last_seen, notes)
                   VALUES (?,?,?,?,?, 'seed', 1, ?, ?)""",
                [
                    (ident, kind, name, acct.value, conf, now, notes)
                    for ident, kind, name, acct, conf, notes in _SEED_ENTITIES
                ],
            )
            conn.executemany(
                """INSERT OR IGNORE INTO name_hint (pattern, account_code, confidence, label)
                   VALUES (?,?,?,?)""",
                [
                    (pattern, acct.value, conf, label)
                    for pattern, acct, conf, label in _SEED_HINTS
                ],
            )

    # --- lookups -----------------------------------------------------------

    def lookup(self, identifier: Optional[str]) -> Optional[EntityRecord]:
        if not identifier:
            return None
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM entity_map WHERE entity_identifier = ?", (identifier,)
            ).fetchone()
        if row is None:
            return None
        return EntityRecord(
            entity_identifier=row["entity_identifier"],
            entity_kind=row["entity_kind"],
            display_name=row["display_name"],
            account=Account(row["account_code"]),
            confidence=float(row["confidence"]),
            source=row["source"],
            times_seen=int(row["times_seen"]),
        )

    def match_name_hint(self, name: str) -> Optional[tuple[Account, float, str]]:
        """Longest-pattern-wins substring match against the hint table."""
        haystack = (name or "").upper()
        with self._conn() as conn:
            rows = conn.execute("SELECT * FROM name_hint").fetchall()
        best: Optional[tuple[Account, float, str]] = None
        best_len = 0
        for row in rows:
            pattern = row["pattern"].upper()
            if pattern in haystack and len(pattern) > best_len:
                best_len = len(pattern)
                best = (
                    Account(row["account_code"]),
                    float(row["confidence"]),
                    row["label"] or row["pattern"].title(),
                )
        return best

    # --- writes ------------------------------------------------------------

    def remember(
        self,
        identifier: Optional[str],
        entity_kind: str,
        display_name: str,
        account: Account,
        confidence: float = 0.9,
        source: str = "llm",
        notes: str = "",
    ) -> None:
        """Record or reinforce a mapping.

        Re-seeing a counterparty nudges confidence upward (capped at 0.99), so a
        supplier confirmed three months running stops being questioned.
        """
        if not identifier:
            return
        now = datetime.now().isoformat(timespec="seconds")
        with self._conn() as conn:
            existing = conn.execute(
                "SELECT confidence, times_seen FROM entity_map WHERE entity_identifier = ?",
                (identifier,),
            ).fetchone()
            if existing is None:
                conn.execute(
                    """INSERT INTO entity_map
                       (entity_identifier, entity_kind, display_name, account_code,
                        confidence, source, times_seen, last_seen, notes)
                       VALUES (?,?,?,?,?,?,1,?,?)""",
                    (identifier, entity_kind, display_name, account.value,
                     confidence, source, now, notes),
                )
            else:
                boosted = min(0.99, max(float(existing["confidence"]), confidence) + 0.01)
                conn.execute(
                    """UPDATE entity_map
                       SET account_code = ?, confidence = ?, display_name = ?,
                           times_seen = times_seen + 1, last_seen = ?, source = ?
                       WHERE entity_identifier = ?""",
                    (account.value, boosted, display_name, now, source, identifier),
                )

    def confirm(self, identifier: str, account: Account, display_name: str = "") -> None:
        """Apply a seller's WhatsApp reply — the highest-trust signal there is."""
        record = self.lookup(identifier)
        self.remember(
            identifier=identifier,
            entity_kind=record.entity_kind if record else "UNKNOWN",
            display_name=display_name or (record.display_name if record else identifier),
            account=account,
            confidence=0.99,
            source="seller_confirmed",
        )

    def log_decision(
        self, transaction_id: str, account: Account, confidence: float, layer: str
    ) -> None:
        with self._conn() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO classification_log
                   (transaction_id, account_code, confidence, layer, decided_at)
                   VALUES (?,?,?,?,?)""",
                (transaction_id, account.value, confidence, layer,
                 datetime.now().isoformat(timespec="seconds")),
            )

    def stats(self) -> dict[str, int]:
        with self._conn() as conn:
            entities = conn.execute("SELECT COUNT(*) c FROM entity_map").fetchone()["c"]
            hints = conn.execute("SELECT COUNT(*) c FROM name_hint").fetchone()["c"]
            logged = conn.execute("SELECT COUNT(*) c FROM classification_log").fetchone()["c"]
        return {"entities": entities, "name_hints": hints, "decisions_logged": logged}
