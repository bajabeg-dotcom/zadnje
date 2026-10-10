from __future__ import annotations

from typing import Any

from .signatures import load_korg_signatures


def detect_korg(data: Any) -> list[dict[str, Any]]:
    findings = []
    for signature in load_korg_signatures():
        try:
            pattern = bytes.fromhex(signature["bytes"])
        except (KeyError, ValueError, TypeError):
            continue
        offset = data.find(pattern)
        if offset >= 0:
            findings.append({
                "name": signature.get("name", "unknown"), "offset": offset,
                "status": signature.get("status", "CANDIDATE"), "confidence": signature.get("confidence", 0.3),
                "source": signature.get("source", "unspecified"), "evidence": ["configured Korg signature matched"],
            })
    return findings