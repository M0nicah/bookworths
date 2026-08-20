"""The 4-layer categorization engine.

Layer 1  deterministic regex + Safaricom tariffs + known paybills
Layer 2  persistent entity memory (SQLite), then name-pattern hints
Layer 3  LLM contextual disambiguation via structured outputs
Layer 4  low-confidence routing to the seller exception queue

Each layer only sees what the layer above could not settle. That ordering is
what keeps the LLM bill near zero on a steady-state book: month two mostly
resolves in Layers 1 and 2.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Optional

from ..schema import (
    CONFIDENCE_THRESHOLD,
    Account,
    Classification,
    ClassifiedTransaction,
    Direction,
    Transaction,
)
from .llm import LLMBackend, build_backend, verdict_to_account
from .memory import EntityMemory
from .tariffs import classify_layer1


@dataclass
class EngineStats:
    layer1: int = 0
    layer2_entity: int = 0
    layer2_hint: int = 0
    layer3: int = 0
    layer4_flagged: int = 0
    llm_calls: int = 0
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, object]:
        return {
            "Layer 1 — deterministic rules": self.layer1,
            "Layer 2 — entity memory": self.layer2_entity,
            "Layer 2 — name hints": self.layer2_hint,
            "Layer 3 — LLM disambiguation": self.layer3,
            "Layer 4 — flagged for review": self.layer4_flagged,
            "LLM calls made": self.llm_calls,
        }


class CategorizationEngine:
    def __init__(
        self,
        memory: Optional[EntityMemory] = None,
        backend: Optional[LLMBackend] = None,
        backend_preference: str = "auto",
        learn: bool = True,
    ):
        self.memory = memory or EntityMemory()
        self.backend = backend or build_backend(backend_preference)
        self.learn = learn
        self.stats = EngineStats()

    # --- individual layers -------------------------------------------------

    def _layer1(self, txn: Transaction) -> Optional[Classification]:
        hit = classify_layer1(txn)
        if hit is None:
            return None
        account, confidence, rationale, label = hit
        self.stats.layer1 += 1
        return Classification(
            account=account,
            confidence=confidence,
            layer="L1:deterministic",
            rationale=rationale,
            counterparty_label=label,
        )

    def _layer2(self, txn: Transaction) -> Optional[Classification]:
        record = self.memory.lookup(txn.entity_identifier)
        if record is not None:
            self.stats.layer2_entity += 1
            return Classification(
                account=record.account,
                confidence=record.confidence,
                layer="L2:entity-memory",
                rationale=(
                    f"Known counterparty '{record.display_name}' "
                    f"(seen {record.times_seen}x, source: {record.source})."
                ),
                counterparty_label=record.display_name,
            )

        hint = self.memory.match_name_hint(txn.entity_name)
        if hint is not None:
            account, confidence, label = hint
            self.stats.layer2_hint += 1
            return Classification(
                account=account,
                confidence=confidence,
                layer="L2:name-hint",
                rationale=f"Counterparty name matched the '{label}' pattern.",
                counterparty_label=label,
            )
        return None

    def _layer3(self, txn: Transaction) -> Classification:
        # Incoming money from a person is settled without an LLM call: on these
        # books it is a customer order, and paying for that inference monthly
        # would be pure waste.
        if txn.direction is Direction.IN and txn.entity_kind.value == "PHONE":
            return Classification(
                account=Account.REVENUE_ORDERS,
                confidence=0.92,
                layer="L3:revenue-default",
                rationale="Money received from an individual phone line — customer order.",
                counterparty_label=txn.entity_name.title(),
            )

        try:
            self.stats.llm_calls += 1
            verdict = self.backend.classify(txn)
        except Exception as exc:  # network/quota/schema failure must not abort the run
            self.stats.errors.append(f"{txn.transaction_id}: {type(exc).__name__}: {exc}")
            return Classification(
                account=Account.UNRESOLVED,
                confidence=0.0,
                layer="L3:error",
                rationale=f"Classifier unavailable ({type(exc).__name__}); routed for review.",
                counterparty_label=txn.entity_name.title(),
            )

        if verdict is None:
            return Classification(
                account=Account.UNRESOLVED,
                confidence=0.0,
                layer="L3:empty",
                rationale="Classifier returned no verdict.",
                counterparty_label=txn.entity_name.title(),
            )

        self.stats.layer3 += 1
        account = verdict_to_account(verdict)
        return Classification(
            account=account,
            confidence=float(verdict.confidence),
            layer=f"L3:{self.backend.name}",
            rationale=verdict.rationale,
            counterparty_label=verdict.counterparty_label or txn.entity_name.title(),
        )

    # --- orchestration -----------------------------------------------------

    def classify_one(self, txn: Transaction) -> ClassifiedTransaction:
        classification = self._layer1(txn) or self._layer2(txn) or self._layer3(txn)

        # Layer 4: anything under threshold becomes an exception, whatever the
        # account the earlier layer proposed.
        if classification.confidence < CONFIDENCE_THRESHOLD:
            self.stats.layer4_flagged += 1
            classification = classification.model_copy(
                update={
                    "layer": classification.layer + " -> L4:review",
                    "rationale": (
                        classification.rationale
                        + f" Confidence {classification.confidence:.2f} is below "
                        f"{CONFIDENCE_THRESHOLD:.2f}; awaiting seller confirmation."
                    ),
                }
            )

        # Feed confident LLM verdicts back into memory so the next statement
        # resolves them in Layer 2 for free.
        if (
            self.learn
            and classification.layer.startswith("L3:")
            and classification.confidence >= CONFIDENCE_THRESHOLD
            and txn.entity_identifier
        ):
            self.memory.remember(
                identifier=txn.entity_identifier,
                entity_kind=txn.entity_kind.value,
                display_name=classification.counterparty_label,
                account=classification.account,
                confidence=classification.confidence,
                source=f"learned:{self.backend.name}",
            )

        self.memory.log_decision(
            txn.transaction_id, classification.account, classification.confidence,
            classification.layer,
        )
        return ClassifiedTransaction(transaction=txn, classification=classification)

    def classify_all(self, transactions: Iterable[Transaction]) -> list[ClassifiedTransaction]:
        return [self.classify_one(t) for t in transactions]
