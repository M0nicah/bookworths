"""Canonical Bookworths data model.

Every downstream layer — ingestion, categorization, reporting — speaks in the
types defined here. Nothing else in the codebase is allowed to invent its own
shape for a transaction.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, field_validator, model_validator


class Direction(str, Enum):
    IN = "IN"
    OUT = "OUT"


class Account(str, Enum):
    """Bookworths chart of accounts for Instagram micro-vendors.

    Values are the account codes; `.label` gives the human name used on the
    Profit Pack.
    """

    REVENUE_ORDERS = "4000"
    REVENUE_DELIVERY = "4010"
    COGS_RESTOCK = "5000"
    COGS_SOURCING_TRANSPORT = "5010"
    LOGISTICS = "6100"
    MARKETING = "6200"
    PACKAGING = "6300"
    FINANCIAL_FEES = "7000"
    OWNER_DRAWINGS = "3000"
    UNRESOLVED = "9999"

    @property
    def label(self) -> str:
        return _ACCOUNT_LABELS[self]

    @property
    def group(self) -> str:
        return _ACCOUNT_GROUPS[self]


_ACCOUNT_LABELS: dict[Account, str] = {
    Account.REVENUE_ORDERS: "Customer Orders",
    Account.REVENUE_DELIVERY: "Delivery Fees Collected",
    Account.COGS_RESTOCK: "Stock Restocks & Bale Purchases",
    Account.COGS_SOURCING_TRANSPORT: "Sourcing Transport",
    Account.LOGISTICS: "Logistics & Delivery",
    Account.MARKETING: "Marketing & Visibility",
    Account.PACKAGING: "Packaging & Operational",
    Account.FINANCIAL_FEES: "Transaction Tariffs & Financial Fees",
    Account.OWNER_DRAWINGS: "Owner Personal Drawings",
    Account.UNRESOLVED: "Unresolved — Needs Seller Confirmation",
}

_ACCOUNT_GROUPS: dict[Account, str] = {
    Account.REVENUE_ORDERS: "Revenue",
    Account.REVENUE_DELIVERY: "Revenue",
    Account.COGS_RESTOCK: "COGS",
    Account.COGS_SOURCING_TRANSPORT: "COGS",
    Account.LOGISTICS: "Operating Expense",
    Account.MARKETING: "Operating Expense",
    Account.PACKAGING: "Operating Expense",
    Account.FINANCIAL_FEES: "Operating Expense",
    Account.OWNER_DRAWINGS: "Equity / Non-Business",
    Account.UNRESOLVED: "Suspense",
}

#: Accounts that must never be netted into business profit.
NON_BUSINESS_ACCOUNTS = frozenset({Account.OWNER_DRAWINGS})

#: Confidence at or above which a classification is accepted without review.
CONFIDENCE_THRESHOLD = 0.85


class EntityKind(str, Enum):
    PHONE = "PHONE"
    TILL = "TILL"
    PAYBILL = "PAYBILL"
    AGENT = "AGENT"
    SYSTEM = "SYSTEM"
    UNKNOWN = "UNKNOWN"


class Transaction(BaseModel):
    """One normalized M-Pesa statement line.

    `gross_amount` is always positive and is the value that moved on the
    customer's side of the ledger. `fee_amount` is the Safaricom tariff levied
    on that same line, always positive. `net_amount` is signed relative to the
    seller's cash position: positive for IN, negative for OUT (fee included).
    """

    model_config = {"use_enum_values": False}

    transaction_id: str
    timestamp: datetime
    raw_text: str
    entity_name: str = "UNKNOWN"
    entity_identifier: Optional[str] = None
    entity_kind: EntityKind = EntityKind.UNKNOWN
    direction: Direction
    gross_amount: Decimal = Field(ge=0)
    fee_amount: Decimal = Field(default=Decimal("0"), ge=0)
    net_amount: Decimal = Decimal("0")
    balance_after: Optional[Decimal] = None
    status: str = "Completed"

    @field_validator("gross_amount", "fee_amount", "balance_after", mode="before")
    @classmethod
    def _to_decimal(cls, v: object) -> object:
        if v is None or isinstance(v, Decimal):
            return v
        if isinstance(v, str):
            v = v.replace(",", "").strip()
            if not v:
                return None
        return Decimal(str(v))

    @model_validator(mode="after")
    def _compute_net(self) -> "Transaction":
        sign = Decimal("1") if self.direction is Direction.IN else Decimal("-1")
        # Fees always reduce cash regardless of direction.
        object.__setattr__(
            self, "net_amount", sign * self.gross_amount - self.fee_amount
        )
        return self

    @property
    def cash_effect(self) -> Decimal:
        """Signed change to the seller's M-Pesa balance."""
        return self.net_amount


class Classification(BaseModel):
    """The categorization engine's verdict on one transaction."""

    account: Account
    confidence: float = Field(ge=0.0, le=1.0)
    layer: str
    rationale: str = ""
    counterparty_label: str = ""

    @property
    def needs_review(self) -> bool:
        return (
            self.confidence < CONFIDENCE_THRESHOLD
            or self.account is Account.UNRESOLVED
        )


class ClassifiedTransaction(BaseModel):
    transaction: Transaction
    classification: Classification

    @property
    def account(self) -> Account:
        return self.classification.account

    @property
    def needs_review(self) -> bool:
        return self.classification.needs_review


class LLMVerdict(BaseModel):
    """Structured-output contract handed to the LLM in Layer 3.

    Deliberately narrow: the model picks an account code and states how sure it
    is. It is never asked to invent amounts or dates.
    """

    account_code: str = Field(
        description="One chart-of-accounts code: 4000, 4010, 5000, 5010, 6100, 6200, 6300, 7000, or 3000."
    )
    counterparty_label: str = Field(
        description="Short human name for the counterparty, e.g. 'Gikomba bale supplier' or 'Naivas supermarket'."
    )
    is_personal: bool = Field(
        description="True if this is owner personal/household spending rather than a business expense."
    )
    confidence: float = Field(
        ge=0.0, le=1.0, description="0.0-1.0 certainty in the chosen account code."
    )
    rationale: str = Field(description="One sentence justifying the choice.")
