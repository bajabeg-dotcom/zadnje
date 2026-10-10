"""PA800 Factory Sound name/selector catalog from the user's 2026-09-22 evidence.

Names are a lookup aid for known factory selectors; a custom user Sound may not
appear, and a name match never authorizes an unverified PCG write.
"""
from __future__ import annotations

import re
from pathlib import Path

LINE = re.compile(r"^(.+?)\s{2,}(\d{1,3})\s+(\d{1,3})\s+(\d{1,3})$")
# pa_tool keeps evidence tables at the project-level data/ directory, matching
# the existing models/manager data layer.  parents[2] is the project root when
# running from the source tree (pa_tool/native/sounds.py).
CATALOG = Path(__file__).resolve().parents[2] / "data" / "PA800_FACTORY_SOUND_LIST_CC00_CC32_PC.txt"


def load_sound_catalog() -> tuple[list[dict], dict[tuple[int, int, int], str]]:
    entries = []
    lookup = {}
    for line in CATALOG.read_text(encoding="utf-8").splitlines():
        match = LINE.match(line)
        if not match:
            continue
        name = match.group(1).strip()
        msb, lsb, pc = (int(match.group(i)) for i in (2, 3, 4))
        if any(not 0 <= value <= 127 for value in (msb, lsb, pc)):
            raise ValueError(f"Invalid sound selector: {line}")
        key = (msb, lsb, pc)
        if key in lookup:
            raise ValueError(f"Duplicate sound selector: {key}")
        entries.append({"name": name, "msb": msb, "lsb": lsb, "pc": pc})
        lookup[key] = name
    if len(entries) != 1003:
        raise ValueError(f"Expected 1003 proven factory sound bindings, found {len(entries)}")
    return entries, lookup
