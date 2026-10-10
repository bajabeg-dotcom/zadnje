from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def load_korg_signatures() -> list[dict[str, Any]]:
    package_data = Path(__file__).resolve().parents[1] / "data"
    data_root = package_data if package_data.is_dir() else Path(__file__).resolve().parents[2] / "data"
    path = data_root / "korg_signatures.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload.get("signatures", []) if isinstance(payload, dict) else payload
    except (OSError, json.JSONDecodeError):
        return []