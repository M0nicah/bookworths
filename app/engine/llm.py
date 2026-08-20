"""Layer 3 — LLM contextual disambiguation via structured outputs.

Only transactions that survive Layers 1 and 2 reach here, which on a typical
month is a handful of rows. Three backends are supported and tried in order of
what is actually configured:

  * ``anthropic``  — ``client.messages.parse`` with a Pydantic output format.
  * ``openai``     — ``responses.parse`` with the same Pydantic model.
  * ``heuristic``  — an offline keyword scorer so the pipeline runs with no keys.

The heuristic backend is not a toy fallback: it is what keeps the demo, the test
suite, and any air-gapped deployment fully functional.
"""
from __future__ import annotations

import json
import os
from typing import Optional, Protocol

from ..schema import Account, LLMVerdict, Transaction

CLAUDE_MODEL = "claude-opus-5"
OPENAI_MODEL = "gpt-4.1-mini"

_VALID_CODES = {
    a.value
    for a in Account
    if a not in {Account.UNRESOLVED}
}

SYSTEM_PROMPT = """\
You are the classification engine inside Bookworths, a bookkeeping service for \
Kenyan Instagram and social-commerce sellers (thrift/apparel, bedding, footwear, \
home decor). You read one M-Pesa transaction at a time and assign it a single \
chart-of-accounts code.

Chart of accounts:
  4000 Customer Orders            — money IN from a buyer paying for goods
  4010 Delivery Fees Collected    — money IN that is explicitly a delivery charge
  5000 Stock Restocks             — bales, mitumba, wholesale stock (Gikomba,
                                    Eastleigh, Kamukunji, Nyamakima suppliers)
  5010 Sourcing Transport         — fare/porter/handcart to collect stock
  6100 Logistics & Delivery       — boda riders, courier and parcel offices
                                    (Easy Coach, 2NK, Fargo, G4S), CBD pickup
                                    shelf rent
  6200 Marketing & Visibility     — Meta/IG boosts, content shoots, model and
                                    influencer fees, airtime/data used to sell
  6300 Packaging & Operational    — mailer bags, boxes, stickers, tags, tape
  7000 Financial Fees             — M-Pesa tariffs, Fuliza fees, bank charges
  3000 Owner Personal Drawings    — supermarket/household shopping, personal
                                    dining, salon, rent, school fees, family
                                    support, personal utilities

The single most important judgement you make is business vs personal. These \
sellers run the business from the same M-Pesa line they use for their household. \
When a payment looks like living expenses rather than something that moves stock \
or fulfils an order, code it 3000 and set is_personal true.

Rules:
  - Money IN from an individual person is almost always 4000 unless the text says
    otherwise.
  - Be honest about uncertainty. A vague counterparty like "UNKNOWN RECIPIENT" or
    a bare phone number with no context deserves confidence well below 0.85 so a
    human confirms it, rather than a confident guess.
  - Never invent a counterparty identity that is not supported by the text.
"""


def _build_user_prompt(txn: Transaction, peer_context: str = "") -> str:
    lines = [
        "Classify this M-Pesa transaction.",
        "",
        f"Direction:      {txn.direction.value} ({'money in' if txn.direction.value == 'IN' else 'money out'})",
        f"Amount:         KES {txn.gross_amount:,.2f}",
        f"Safaricom fee:  KES {txn.fee_amount:,.2f}",
        f"Counterparty:   {txn.entity_name}",
        f"Identifier:     {txn.entity_identifier or 'none'} ({txn.entity_kind.value})",
        f"Timestamp:      {txn.timestamp:%Y-%m-%d %H:%M} ({txn.timestamp:%A})",
        f"Raw statement:  {txn.raw_text}",
    ]
    if peer_context:
        lines += ["", "Prior behaviour with this counterparty:", peer_context]
    return "\n".join(lines)


class LLMBackend(Protocol):
    name: str

    def classify(self, txn: Transaction, peer_context: str = "") -> Optional[LLMVerdict]: ...


class AnthropicBackend:
    """Claude via structured outputs (`messages.parse`)."""

    name = "anthropic"

    def __init__(self, model: str = CLAUDE_MODEL):
        import anthropic  # imported lazily so the package works without it

        self.model = model
        self._client = anthropic.Anthropic()

    def classify(self, txn: Transaction, peer_context: str = "") -> Optional[LLMVerdict]:
        response = self._client.messages.parse(
            model=self.model,
            max_tokens=2000,
            system=SYSTEM_PROMPT,
            # Bookkeeping wants stable, repeatable calls rather than creative
            # ones. Sampling params were removed on current Claude models, so
            # determinism comes from low effort + a constrained output schema.
            output_config={"effort": "low"},
            messages=[{"role": "user", "content": _build_user_prompt(txn, peer_context)}],
            output_format=LLMVerdict,
        )
        return response.parsed_output


class OpenAIBackend:
    """OpenAI via structured outputs, low temperature."""

    name = "openai"

    def __init__(self, model: str = OPENAI_MODEL):
        from openai import OpenAI  # imported lazily

        self.model = model
        self._client = OpenAI()

    def classify(self, txn: Transaction, peer_context: str = "") -> Optional[LLMVerdict]:
        response = self._client.responses.parse(
            model=self.model,
            temperature=0.0,
            instructions=SYSTEM_PROMPT,
            input=_build_user_prompt(txn, peer_context),
            text_format=LLMVerdict,
        )
        return response.output_parsed


class HeuristicBackend:
    """Offline keyword scorer — no API key, no network, fully deterministic."""

    name = "heuristic"

    _PERSONAL = (
        "supermarket", "naivas", "quickmart", "carrefour", "chandarana", "tuskys",
        "java", "restaurant", "cafe", "bar ", "night out", "salon", "spa",
        "school fees", "fees", "family", "mama watoto", "rent house", "hospital",
        "chemist", "pharmacy", "church", "harambee", "shopping", "groceries",
    )
    _RESTOCK = (
        "gikomba", "eastleigh", "kamukunji", "nyamakima", "bale", "mitumba",
        "supplier", "wholesale", "stock", "camera", "godown",
    )
    _LOGISTICS = (
        "boda", "rider", "pikipiki", "courier", "parcel", "fargo", "g4s",
        "easy coach", "2nk", "shelf", "delivery", "dispatch", "matatu stage",
    )
    _PACKAGING = ("mailer", "packaging", "sticker", "label", "box", "tag", "wrapping", "tape")
    _MARKETING = (
        "boost", "advert", "ads", "facebook", "meta", "instagram", "influencer",
        "shoot", "model", "content", "promo", "airtime", "bundle",
    )
    _SOURCING = ("matatu", "fare", "porter", "handcart", "mkokoteni", "transport")

    @staticmethod
    def _hits(text: str, needles: tuple[str, ...]) -> int:
        return sum(1 for n in needles if n in text)

    def classify(self, txn: Transaction, peer_context: str = "") -> Optional[LLMVerdict]:
        text = f"{txn.entity_name} {txn.raw_text}".lower()

        if txn.direction.value == "IN":
            is_delivery = "delivery" in text or "shipping" in text
            account = Account.REVENUE_DELIVERY if is_delivery else Account.REVENUE_ORDERS
            return LLMVerdict(
                account_code=account.value,
                counterparty_label=txn.entity_name.title(),
                is_personal=False,
                confidence=0.93 if txn.entity_kind.value == "PHONE" else 0.88,
                rationale="Money received from an individual — treated as a customer order.",
            )

        scores: list[tuple[int, Account, str]] = [
            (self._hits(text, self._PERSONAL), Account.OWNER_DRAWINGS, "personal/household spending"),
            (self._hits(text, self._RESTOCK), Account.COGS_RESTOCK, "stock restock"),
            (self._hits(text, self._LOGISTICS), Account.LOGISTICS, "logistics/fulfilment"),
            (self._hits(text, self._PACKAGING), Account.PACKAGING, "packaging"),
            (self._hits(text, self._MARKETING), Account.MARKETING, "marketing"),
            (self._hits(text, self._SOURCING), Account.COGS_SOURCING_TRANSPORT, "sourcing transport"),
        ]
        best_score, account, reason = max(scores, key=lambda s: s[0])

        if best_score == 0:
            # Genuinely unknown — say so loudly instead of guessing.
            return LLMVerdict(
                account_code=Account.OWNER_DRAWINGS.value,
                counterparty_label=txn.entity_name.title(),
                is_personal=True,
                confidence=0.40,
                rationale="No recognisable business signal in the counterparty; needs seller confirmation.",
            )

        confidence = min(0.92, 0.72 + 0.08 * best_score)
        return LLMVerdict(
            account_code=account.value,
            counterparty_label=txn.entity_name.title(),
            is_personal=account is Account.OWNER_DRAWINGS,
            confidence=confidence,
            rationale=f"Counterparty text indicates {reason}.",
        )


def build_backend(preference: str = "auto") -> LLMBackend:
    """Pick a backend.

    ``auto`` prefers Anthropic, then OpenAI, then the offline heuristic — so the
    pipeline always runs, with or without credentials.
    """
    order = (
        ["anthropic", "openai", "heuristic"]
        if preference == "auto"
        else [preference]
    )
    for choice in order:
        try:
            if choice == "anthropic":
                if not (os.getenv("ANTHROPIC_API_KEY") or os.getenv("ANTHROPIC_AUTH_TOKEN")):
                    continue
                return AnthropicBackend()
            if choice == "openai":
                if not os.getenv("OPENAI_API_KEY"):
                    continue
                return OpenAIBackend()
            if choice == "heuristic":
                return HeuristicBackend()
        except Exception:  # missing SDK, bad credentials — fall through
            continue
    return HeuristicBackend()


def verdict_to_account(verdict: LLMVerdict) -> Account:
    """Map a model verdict onto a real account, defensively."""
    code = str(verdict.account_code).strip()
    if code not in _VALID_CODES:
        return Account.UNRESOLVED
    account = Account(code)
    # A model that flags personal spending but codes it as business is
    # contradicting itself; trust the explicit personal flag.
    if verdict.is_personal and account not in {Account.OWNER_DRAWINGS, Account.FINANCIAL_FEES}:
        return Account.OWNER_DRAWINGS
    return account
