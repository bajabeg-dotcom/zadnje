"""Status discipline for pa_tool.

Every finding the tool reports carries one of exactly three statuses. There is no
"probably": a claim is either measurable, evidenced-but-not-exclusive, or unknown.

    VERIFIED   - measured and reproducible (offset / length / hash / count)
    NEPOTVRDENO- evidence exists, exclusivity does not
    UNKNOWN    - no sample or no data; the tool reports UNKNOWN instead of guessing
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


class Status(str, Enum):
    VERIFIED = "VERIFIED"
    NEPOTVRDENO = "NEPOTVRDENO"
    UNKNOWN = "UNKNOWN"
    HIGH = "HIGH"  # used only where the RE notes already used HIGH (evidence, not proof)

    def __str__(self) -> str:  # pragma: no cover - cosmetic
        return self.value


@dataclass
class Finding:
    """One assertion, always carrying its status and its evidence."""

    key: str
    value: Any
    status: Status
    evidence: str = ""
    note: str = ""

    def is_blocking_unknown(self) -> bool:
        return self.status is Status.UNKNOWN

    def to_dict(self) -> dict:
        out = {
            "key": self.key,
            "value": self.value,
            "status": self.status.value,
        }
        if self.evidence:
            out["evidence"] = self.evidence
        if self.note:
            out["note"] = self.note
        return out


@dataclass
class Result:
    """A named group of findings, e.g. one check or one detection."""

    name: str
    findings: list[Finding] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def add(self, key: str, value: Any, status: Status, evidence: str = "", note: str = "") -> Finding:
        f = Finding(key, value, status, evidence, note)
        self.findings.append(f)
        return f

    @property
    def status(self) -> Status:
        """Worst status wins: UNKNOWN > NEPOTVRDENO > HIGH > VERIFIED."""
        order = [Status.UNKNOWN, Status.NEPOTVRDENO, Status.HIGH, Status.VERIFIED]
        for s in order:
            if any(f.status is s for f in self.findings):
                return s
        return Status.VERIFIED

    def decision(self, verdict_key: str = "decision") -> str:
        """PASS / FAIL / UNKNOWN derived from findings, never invented."""
        for f in self.findings:
            if f.key == verdict_key:
                v = str(f.value).upper()
                if v in ("FAIL", "PASS", "UNKNOWN"):
                    return v
        return self.status.value

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "status": self.status.value,
            "findings": [f.to_dict() for f in self.findings],
            "errors": list(self.errors),
        }


def worst(statuses: list[Status]) -> Status:
    for s in (Status.UNKNOWN, Status.NEPOTVRDENO, Status.HIGH, Status.VERIFIED):
        if s in statuses:
            return s
    return Status.VERIFIED
