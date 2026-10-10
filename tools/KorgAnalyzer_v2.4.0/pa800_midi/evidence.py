"""Evidence model -- what a decision is allowed to be based on.

Every conclusion the optimizer states carries:

    SOURCE, CONFIDENCE, EVIDENCE_TYPE, INSTRUMENT_ID,
    FACTORY_REFERENCE, GOLD_REFERENCE, OSC_REFERENCE, RX_REFERENCE,
    MANUAL_REFERENCE, DEMO_REFERENCE

Confidence levels (master logic §7 / §20):

    VERIFIED         several independent real sources agree
    DOCUMENTED       official/curated data states it (factory table, manual)
    STRONG_EVIDENCE  direct measured evidence (gold corpus, factory data)
    CANDIDATE        plausible, not proven -- proposed, never applied
    UNKNOWN          no evidence -- nothing is changed

Only VERIFIED / DOCUMENTED / STRONG_EVIDENCE may be applied automatically.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Dict, Iterable, List, Optional


class Confidence(str, Enum):
    VERIFIED = "VERIFIED"
    DOCUMENTED = "DOCUMENTED"
    STRONG_EVIDENCE = "STRONG_EVIDENCE"
    CANDIDATE = "CANDIDATE"
    UNKNOWN = "UNKNOWN"

    @property
    def rank(self) -> int:
        return {
            "VERIFIED": 0,
            "DOCUMENTED": 1,
            "STRONG_EVIDENCE": 2,
            "CANDIDATE": 3,
            "UNKNOWN": 4,
        }[self.value]

    @property
    def can_auto_apply(self) -> bool:
        return self in (Confidence.VERIFIED, Confidence.DOCUMENTED,
                        Confidence.STRONG_EVIDENCE)


#: Evidence sources, in the master-logic vocabulary.
SOURCE_FACTORY_STYLE = "factory_style"
SOURCE_FACTORY_SOUND = "factory_sound"
SOURCE_OSC = "osc"
SOURCE_GOLD = "gold"
SOURCE_RX = "rx"
SOURCE_MANUAL = "manual"
SOURCE_DEMO = "demo"
SOURCE_BINARY = "binary"
SOURCE_USER_RULE = "user_rule"
SOURCE_HARD_LIMIT = "hard_limit"
SOURCE_HEURISTIC = "heuristic"

SOURCES = (
    SOURCE_HARD_LIMIT, SOURCE_USER_RULE, SOURCE_FACTORY_STYLE, SOURCE_RX,
    SOURCE_FACTORY_SOUND, SOURCE_OSC, SOURCE_GOLD, SOURCE_MANUAL,
    SOURCE_DEMO, SOURCE_BINARY, SOURCE_HEURISTIC,
)

#: §14 -- the ranking is NOT a blind global order; it is judged per question.
QUESTION_PRIORITY: Dict[str, List[str]] = {
    "groove": [SOURCE_FACTORY_STYLE, SOURCE_GOLD, SOURCE_USER_RULE,
               SOURCE_HEURISTIC],
    "sound_identity": [SOURCE_FACTORY_SOUND, SOURCE_OSC, SOURCE_FACTORY_STYLE,
                       SOURCE_GOLD, SOURCE_HEURISTIC],
    "rx_mapping": [SOURCE_RX, SOURCE_FACTORY_SOUND, SOURCE_DEMO,
                   SOURCE_FACTORY_STYLE, SOURCE_MANUAL, SOURCE_HEURISTIC],
    "efx": [SOURCE_DEMO, SOURCE_MANUAL, SOURCE_HEURISTIC],
    "articulation": [SOURCE_DEMO, SOURCE_MANUAL, SOURCE_FACTORY_SOUND],
    "parameter_meaning": [SOURCE_MANUAL, SOURCE_FACTORY_SOUND],
    "velocity": [SOURCE_FACTORY_STYLE, SOURCE_FACTORY_SOUND, SOURCE_GOLD,
                 SOURCE_RX, SOURCE_HEURISTIC],
    "key_range": [SOURCE_HARD_LIMIT, SOURCE_FACTORY_SOUND, SOURCE_MANUAL],
}


@dataclass
class Evidence:
    source: str
    confidence: Confidence
    evidence_type: str
    detail: str
    instrument_id: Optional[str] = None
    factory_reference: Optional[str] = None
    gold_reference: Optional[str] = None
    osc_reference: Optional[str] = None
    rx_reference: Optional[str] = None
    manual_reference: Optional[str] = None
    demo_reference: Optional[str] = None

    @property
    def can_auto_apply(self) -> bool:
        return self.confidence.can_auto_apply

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["confidence"] = self.confidence.value
        data["can_auto_apply"] = self.can_auto_apply
        return data

    def line(self) -> str:
        refs = []
        for label, value in (
            ("MANUAL", self.manual_reference),
            ("FACTORY", self.factory_reference),
            ("GOLD", self.gold_reference),
            ("OSC", self.osc_reference),
            ("RX", self.rx_reference),
            ("DEMO", self.demo_reference),
        ):
            if value:
                refs.append(f"{label}={value}")
        tail = ("; " + ", ".join(refs)) if refs else ""
        return f"[{self.source}] {self.confidence.value} -- {self.detail}{tail}"


class EvidenceBook:
    """Collects evidence and answers 'which source wins for this question?'"""

    def __init__(self) -> None:
        self._items: List[Evidence] = []

    def add(self, evidence: Evidence) -> Evidence:
        signature = (evidence.source, evidence.confidence.value,
                     evidence.detail, evidence.evidence_type)
        for item in self._items:
            if (item.source, item.confidence.value, item.detail,
                    item.evidence_type) == signature:
                return item
        self._items.append(evidence)
        return evidence

    def extend(self, items: Iterable[Evidence]) -> None:
        for item in items:
            self.add(item)

    def __len__(self) -> int:
        return len(self._items)

    def __iter__(self):
        return iter(self._items)

    def of_source(self, source: str) -> List[Evidence]:
        return [e for e in self._items if e.source == source]

    def of_type(self, evidence_type: str) -> List[Evidence]:
        return [e for e in self._items if e.evidence_type == evidence_type]

    def best_for(self, question: str) -> Optional[Evidence]:
        """Strongest evidence for a question, following §14 priority."""
        order = QUESTION_PRIORITY.get(question)
        if order is None:
            order = list(SOURCES)
        candidates = [e for e in self._items if e.source in order]
        if not candidates:
            return None
        return sorted(
            candidates,
            key=lambda e: (order.index(e.source), e.confidence.rank),
        )[0]

    def gaps(self) -> List[str]:
        """Questions with no evidence at all -- reported as UNKNOWN."""
        missing = []
        for question in QUESTION_PRIORITY:
            if self.best_for(question) is None:
                missing.append(question)
        return missing

    def to_list(self) -> List[Dict[str, Any]]:
        return [e.to_dict() for e in self._items]

    def to_json(self) -> str:
        return json.dumps(self.to_list(), indent=2, ensure_ascii=False)
