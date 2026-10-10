from __future__ import annotations


def unknown_container(label: str = "Korg-related indicator") -> dict[str, object]:
    return {
        "format": "UNKNOWN_KORG_CONTAINER",
        "label": label,
        "status": "UNKNOWN",
        "confidence": 0.0,
        "evidence": ["No verified Korg format definition is configured"],
    }