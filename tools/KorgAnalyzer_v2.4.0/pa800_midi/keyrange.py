"""Key Range rules per instrument type.

The master logic says Key Range is a HARD LIMIT.  Until now the check could
only run for the handful of sounds that have a measured factory profile, so in
practice it almost never fired on an imported GM MIDI.  This module adds the
user's rule: every instrument TYPE has a playable range.

Resolution order -- the hardest evidence wins:

1. **MEASURED** -- the exact factory sound has a measured profile
   (``data/instrument_profiles.py``).  Evidence source: factory_sound.
2. **TYPE RULE** -- the instrument type has a rule (this table).
   Evidence source: user_rule (the user's own rule, applied deterministically).
3. **NONE** -- no claim is made; the track is reported as unchecked.

A rule never moves a note.  It only decides whether a note is reported as
``KEY_RANGE_CONFLICT``.

The user may override the table:

    --key-range "bass=E1-C4,guitar=E2-E6"      (CLI)
    --key-range-file moja_pravila.json

JSON file format::

    {"bass": "E1-C4", "trumpet": [52, 84]}
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from pa800_midi.data.categories import note_name

SOURCE_MEASURED = "measured"
SOURCE_TYPE_RULE = "type_rule"
SOURCE_USER_OVERRIDE = "user_override"
SOURCE_NONE = "none"

#: key -> (low note, high note, explanation)
#: Ranges follow the real playing range of each instrument family, and where a
#: Pa800 factory profile exists the measured value is used for that exact sound.
KEY_RANGE_RULES: Dict[str, Tuple[int, int, str]] = {
    "piano":      (21, 108, "A0-C8, klavirski opseg"),
    "organ":      (36,  96, "C2-C7, drawbar/pipe orgulje"),
    "accordion":  (41,  96, "F2-C7, harmonika"),
    "rhythm_guitar": (40, 88, "E2-E6, gitara (najniža i najviša žica)"),
    "lead_guitar": (40,  96, "E2-C7, solo gitara"),
    "guitar":     (40,  88, "E2-E6, gitara"),
    "bass":       (28,  67, "E1-G4, bas gitara (4 žice)"),
    "strings":    (36,  96, "C2-C7, gudači"),
    "choir":      (36,  84, "C2-C6, hor/vokali"),
    "brass":      (52,  84, "E3-C6, truba/limena sekcija"),
    "trumpet":    (52,  84, "E3-C6, truba"),
    "trombone":   (40,  72, "E2-C5, trombon"),
    "sax":        (46,  82, "A#2-A#5, tenor saksofon"),
    "woodwind":   (50,  96, "D3-C7, drveni puhači"),
    "flute":      (60,  96, "C4-C7, flauta"),
    "clarinet":   (50,  91, "D3-G6, klarinet"),
    "ethnic":     (40,  96, "E2-C7, etno instrumenti"),
    "fx":         (0,  127, "nema opsega -- efekti"),
    "drums":      (0,  127, "bubnjevi se ne provjeravaju po visini"),
    "other":      (0,  127, "nepoznat tip -- ne tvrdi se opseg"),
    "synth":      (0,  127, "sintisajzer nema fizički opseg"),
    "choir_aah":  (36,  84, "C2-C6, hor"),
    "pad":        (36,  96, "C2-C7, padovi"),
}

#: Types for which a range claim makes no sense -- never reported as conflict.
NO_CLAIM_TYPES = {"drums", "synth", "fx", "other"}

_NOTE_RE = re.compile(r"^\s*([A-Ga-g])([#b]?)(-?\d+)\s*$")
_SEMITONES = {"c": 0, "d": 2, "e": 4, "f": 5, "g": 7, "a": 9, "b": 11}


def parse_note(text: str) -> int:
    """``"E1"`` -> 28, ``"C#4"`` -> 61.  Tolerates lower case and flats."""
    match = _NOTE_RE.match(str(text))
    if not match:
        raise ValueError(f"ne mogu pročitati notu: {text!r}")
    letter, accidental, octave = match.groups()
    value = _SEMITONES[letter.lower()]
    if accidental == "#":
        value += 1
    elif accidental == "b":
        value -= 1
    return max(0, min(127, value + (int(octave) + 1) * 12))


def parse_range(text: str) -> Tuple[int, int]:
    if isinstance(text, (list, tuple)) and len(text) == 2:
        return int(text[0]), int(text[1])
    parts = re.split(r"\s*[-–]\s*", str(text).strip())
    if len(parts) != 2:
        raise ValueError(f"opseg mora biti 'E1-C4', dobio sam {text!r}")
    return parse_note(parts[0]), parse_note(parts[1])


@dataclass
class KeyRangeRule:
    instrument_type: str
    low: int
    high: int
    source: str
    explanation: str = ""

    def describe(self) -> str:
        return (f"{note_name(self.low)}-{note_name(self.high)} "
                f"({self.explanation or self.instrument_type})")

    def to_dict(self) -> Dict[str, object]:
        return {
            "instrument_type": self.instrument_type,
            "low": self.low,
            "high": self.high,
            "low_name": note_name(self.low),
            "high_name": note_name(self.high),
            "source": self.source,
            "explanation": self.explanation,
        }


class KeyRangeTable:
    """Resolves the range that applies to one track."""

    def __init__(self, overrides: Optional[Dict[str, Tuple[int, int]]] = None,
                 rules: Optional[Dict[str, Tuple[int, int, str]]] = None) -> None:
        self.rules = dict(rules or KEY_RANGE_RULES)
        self.overrides: Dict[str, Tuple[int, int]] = dict(overrides or {})
        self.gold_note: Dict[str, str] = {}

    # -------------------------------------------------------------- loading
    @classmethod
    def from_file(cls, path: str | Path) -> "KeyRangeTable":
        """Load either a plain ``{"bass": "E1-C4"}`` map or the full file with
        ``rules`` and ``overrides`` sections."""
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        while "rules" in data or "overrides" in data:
            overrides = {key: parse_range(value)
                         for key, value in (data.get("overrides") or {}).items()}
            rules = None
            if data.get("rules"):
                rules = {}
                for key, value in data["rules"].items():
                    if isinstance(value, dict):
                        low = value.get("low")
                        high = value.get("high")
                        if low is None or high is None:
                            low, high = parse_range(
                                f"{value.get('low_name', 'C-1')}-{value.get('high_name', 'G9')}")
                        rules[key] = (int(low), int(high),
                                      value.get("explanation", ""))
                    else:
                        low, high = parse_range(value)
                        rules[key] = (low, high, "")
            return cls(overrides=overrides, rules=rules or None)
        return cls(overrides={k: parse_range(v) for k, v in data.items()})

    # ------------------------------------------------------------ gold proof
    def apply_gold(self, gold_index, min_files: int = 3,
                   min_notes: int = 5, coverage: float = 98.0) -> List[str]:
        """Widen type rules where the user's GOLD corpus proves it.

        GOLD is concrete evidence about how the material really plays.  A rule
        is only ever widened, never narrowed, and the new edge is the pitch
        that still contains ``coverage`` % of everything the corpus plays on
        that instrument type -- one stray note never moves a hard limit.
        Every change is returned as a readable line so the report can show it.
        """
        changed: List[str] = []
        for key, stat in getattr(gold_index, "types", {}).items():
            if key in self.overrides:
                continue                       # tvoje pravilo je jače od GOLD-a
            current = self.rules.get(key)
            if current is None:
                continue                       # nema tvrdnje -> nema sta siriti
            low, high, explanation = current
            if (getattr(stat, "files", 0) or 0) < min_files:
                continue                       # premalo pjesama za dokaz
            span = stat.percentile()
            if span is None:
                continue                       # premalo nota za percentil
            gold_low, gold_high = span
            new_low = min(low, gold_low)
            new_high = max(high, gold_high)
            if (new_low, new_high) == (low, high):
                continue
            note = (f"prosireno GOLD dokazom: {coverage:.0f}% nota iz "
                    f"{stat.files} tvojih pjesama je u "
                    f"{note_name(gold_low)}-{note_name(gold_high)} "
                    f"| osnova: {explanation}")
            self.rules[key] = (new_low, new_high, note)
            self.gold_note[key] = note
            changed.append(f"{key}: {explanation} -> "
                           f"{note_name(new_low)}-{note_name(new_high)} "
                           f"(GOLD {stat.files} pj., {stat.notes} nota)")
        return changed

    @classmethod
    def load_default(cls, path: Optional[str | Path] = None,
                     gold_path: Optional[str | Path] = None,
                     use_gold: bool = True) -> "KeyRangeTable":
        """The table the program ships with, honouring an edited rules file and
        the GOLD corpus measurement (``gold_index.json``)."""
        target = Path(path) if path else DEFAULT_RULES_FILE
        table = None
        if target.is_file():
            try:
                table = cls.from_file(target)
            except (OSError, ValueError, KeyError):
                table = None
        if table is None:
            table = cls()
        if use_gold:
            table.apply_gold_file(gold_path if gold_path else DEFAULT_GOLD_FILE)
        return table

    def apply_gold_file(self, path: Optional[str | Path],
                        min_files: int = 3, min_notes: int = 5) -> List[str]:
        """Apply GOLD range evidence from ``gold_index.json`` if it is there."""
        target = Path(path) if path else DEFAULT_GOLD_FILE
        if not target.is_file():
            return []
        try:
            from pa800_midi.index import GoldIndex, GoldTypeRange

            data = json.loads(target.read_text(encoding="utf-8"))
            index = GoldIndex(source=data.get("source", ""))
            for key, raw in (data.get("types") or {}).items():
                stat = GoldTypeRange(instrument_type=key)
                for name, value in raw.items():
                    if name in ("instrument_type", "pitch_low_name",
                                "pitch_high_name", "rule_range", "played_range"):
                        continue
                    if hasattr(stat, name):
                        setattr(stat, name, value)
                index.types[key] = stat
            return self.apply_gold(index, min_files=min_files, min_notes=min_notes)
        except (OSError, ValueError, KeyError, TypeError):
            return []

    def add_overrides(self, spec: str) -> None:
        """``"bass=E1-C4,guitar=E2-E6"`` -> overrides."""
        for item in str(spec).split(","):
            item = item.strip()
            if not item:
                continue
            if "=" not in item:
                raise ValueError(f"pravilo mora biti 'tip=OPSEG', dobio sam {item!r}")
            key, _, value = item.partition("=")
            self.overrides[key.strip().casefold()] = parse_range(value)

    # ------------------------------------------------------------ resolving
    def resolve(self, instrument_type: str, role: str = "",
                profile=None) -> Optional[KeyRangeRule]:
        """Hardest evidence first: override -> measured profile -> type rule."""
        key = (instrument_type or "").casefold()
        role_key = (role or "").casefold()

        if key in self.overrides:
            low, high = self.overrides[key]
            return KeyRangeRule(instrument_type, low, high, SOURCE_USER_OVERRIDE,
                                "tvoje pravilo za tip instrumenta")
        if role_key and role_key in self.overrides:
            low, high = self.overrides[role_key]
            return KeyRangeRule(role_key, low, high, SOURCE_USER_OVERRIDE,
                                "tvoje pravilo za rolu")

        if profile is not None and getattr(profile, "playable_range_low", None) is not None:
            low = profile.playable_range_low
            high = profile.playable_range_high
            if (low, high) != (0, 127):
                return KeyRangeRule(key or profile.category, low, high,
                                    SOURCE_MEASURED,
                                    f"izmjereni profil '{profile.name}'")

        for candidate in (key, role_key):
            if candidate and candidate in NO_CLAIM_TYPES:
                return None
            if candidate and candidate in self.rules:
                low, high, explanation = self.rules[candidate]
                if (low, high) == (0, 127):
                    return None
                return KeyRangeRule(candidate, low, high, SOURCE_TYPE_RULE,
                                    explanation)
        return None

    def to_dict(self) -> Dict[str, object]:
        rules = {}
        for key, (low, high, explanation) in sorted(self.rules.items()):
            rules[key] = {"low": low, "high": high,
                          "low_name": note_name(low), "high_name": note_name(high),
                          "explanation": explanation}
        return {
            "rules": rules,
            "overrides": {k: [v[0], v[1]] for k, v in sorted(self.overrides.items())},
        }

    def save(self, path: str | Path) -> Path:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(self.to_dict(), indent=2, ensure_ascii=False),
                          encoding="utf-8")
        return target


DEFAULT_RULES_FILE = Path(__file__).resolve().parent / "data" / "key_range_rules.json"
DEFAULT_GOLD_FILE = Path(__file__).resolve().parent / "data" / "gold_index.json"


def default_table(rules_file: Optional[str | Path] = None,
                  use_gold: bool = True) -> KeyRangeTable:
    return KeyRangeTable.load_default(rules_file, use_gold=use_gold)


def gold_table(path: Optional[str | Path] = None) -> "KeyRangeTable":
    """Samo GOLD mjerenje, bez uklapanja u pravila -- za prikaz."""
    table = KeyRangeTable()
    table.apply_gold_file(path if path else DEFAULT_GOLD_FILE)
    return table


def describe_gold(path: Optional[str | Path] = None) -> List[str]:
    """Sta GOLD korpus stvarno svira, po tipu instrumenta."""
    import json as _json

    target = Path(path) if path else DEFAULT_GOLD_FILE
    if not target.is_file():
        return ["  (nema gold_index.json -- pokreni: python -m pa800_midi index gold <mapa>)"]
    data = _json.loads(target.read_text(encoding="utf-8"))
    if not data.get("types"):
        return [f"  korpus: {Path(data.get('source', '') or '').name} "
                f"({data.get('file_count', 0)} fajlova)",
                "  (indeks je stariji -- nema mjerenja opsega; pokreni:",
                "   python -m pa800_midi index gold <mapa>)"]
    source = data.get("source", "") or ""
    source = Path(source).name or source          # kratko ime mape, ne cijeli put
    lines = [f"  korpus: {source} ({data.get('file_count', 0)} fajlova)"]
    for key, raw in sorted((data.get("types") or {}).items(),
                           key=lambda kv: -kv[1].get("notes", 0)):
        played = raw.get("played_range", "?")
        rule = raw.get("rule_range", "?")
        bits = []
        below = sorted((raw.get("beyond_low") or {}).items(),
                       key=lambda kv: -kv[1][0])[:2]
        above = sorted((raw.get("beyond_high") or {}).items(),
                       key=lambda kv: -kv[1][0])[:2]
        for name, (notes, files) in below:
            if notes:
                bits.append(f"ispod: {name} {notes}x u {files} pj.")
        for name, (notes, files) in above:
            if notes:
                bits.append(f"iznad: {name} {notes}x u {files} pj.")
        lines.append(f"  {key:14s} svirano {played:>9s} | pravilo {rule:>9s} | "
                     f"{raw.get('files', 0):3d} pj. {raw.get('notes', 0):7d} nota"
                     + ("  -> " + "; ".join(bits) if bits else ""))
    return lines


def describe_table(table: Optional[KeyRangeTable] = None) -> List[str]:
    table = table or default_table()
    lines = []
    for key, (low, high, explanation) in sorted(table.rules.items()):
        mark = " *" if key in table.gold_note else "  "
        lines.append(f"{mark}{key:14s} {note_name(low):>4s}-{note_name(high):<4s} {explanation}")
    return lines
