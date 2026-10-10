from __future__ import annotations

from typing import Any


def validate_korg_candidate(data: Any) -> dict[str, Any]:
    return {
        "status": "NOT_VALIDATED",
        "format": "UNKNOWN_KORG_CONTAINER",
        "evidence": ["No documented structure, checksum, or decoder contract is available for this candidate"],
    }