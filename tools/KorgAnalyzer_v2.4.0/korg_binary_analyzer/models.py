from __future__ import annotations

from typing import Any


def finding(
    kind: str,
    fact: str,
    evidence: list[str],
    confidence: float,
    status: str = "CANDIDATE",
    **details: Any,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "kind": kind,
        "fact": fact,
        "evidence": evidence,
        "confidence": round(max(0.0, min(1.0, confidence)), 3),
        "status": status,
    }
    result.update(details)
    return result