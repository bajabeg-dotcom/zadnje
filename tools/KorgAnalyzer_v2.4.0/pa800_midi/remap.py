"""GM -> Pa800 identity mapping, reused from the existing optimizer
(``midi_optimizer/services/remap_service.py``) and upgraded with a confidence
level so that only proven targets are applied automatically.
"""
from __future__ import annotations

from typing import Dict, Optional, Tuple

from pa800_midi.data.drum_kits import PA800_DRUM_KITS, get_drum_name
from pa800_midi.data.pa800_sounds import PA800_SOUNDS_FULL, get_sound_name
from pa800_midi.evidence import Confidence

#: Curated GM -> (cc00, cc32, program) table from the existing optimizer.
GM_TO_PA800_REMAP: Dict[int, Tuple[int, int, int]] = {
    0: (121, 3, 0),
    1: (121, 5, 1),
    2: (121, 2, 2),
    4: (121, 10, 4),
    16: (121, 4, 16),
    21: (121, 8, 21),
    24: (121, 2, 24),
    25: (121, 2, 25),
    27: (121, 4, 27),
    29: (121, 0, 29),
    30: (121, 0, 30),
    33: (121, 5, 33),
    48: (121, 7, 48),
    56: (121, 3, 56),
    57: (121, 1, 57),
    65: (121, 1, 65),
    66: (121, 1, 66),
    71: (121, 1, 71),
    73: (121, 1, 73),
}

DEFAULT_DRUM_REMAP: Tuple[int, int, int] = (120, 0, 0)

RX_SUFFIXES = (" RX", " RX1", " RX2", " RX3", " DNC")
GM_SUFFIX = " GM"


def pa800_target(cc00: int, cc32: int, program: int,
                 is_drum: bool = False) -> Tuple[Tuple[int, int, int],
                                                 Confidence, str]:
    """Where this sound should live on a Pa800, and how sure we are."""
    if is_drum:
        if (cc00, cc32, program) in PA800_DRUM_KITS:
            return ((cc00, cc32, program), Confidence.DOCUMENTED,
                    f"drum kit '{get_drum_name(cc00, cc32, program)}' je već "
                    f"fabricki kit")
        if DEFAULT_DRUM_REMAP in PA800_DRUM_KITS:
            return (DEFAULT_DRUM_REMAP, Confidence.DOCUMENTED,
                    "GM drum kanal -> fabricki 'Standard Kit GM' (remap tabela)")
        return ((cc00, cc32, program), Confidence.UNKNOWN,
                "nema dokazanog drum kita")

    if (cc00, cc32, program) in PA800_SOUNDS_FULL:
        return ((cc00, cc32, program), Confidence.DOCUMENTED,
                f"'{get_sound_name(cc00, cc32, program)}' je već fabricki zvuk")

    mapped = GM_TO_PA800_REMAP.get(program)
    if mapped and mapped in PA800_SOUNDS_FULL:
        target_name = get_sound_name(*mapped)
        current_name = get_sound_name(cc00, cc32, program)
        if _names_agree(current_name, target_name):
            return (mapped, Confidence.STRONG_EVIDENCE,
                    f"GM program {program} -> '{target_name}' "
                    f"(remap tabela + zvuk postoji u fabrickoj tabeli)")
        return (mapped, Confidence.CANDIDATE,
                f"remap tabela predlaže '{target_name}', ali se porodica imena "
                f"ne poklapa s '{current_name}' -- samo predlog, korisnik "
                f"potvrđuje")

    fallback = (121, 0, program)
    if fallback in PA800_SOUNDS_FULL:
        return (fallback, Confidence.DOCUMENTED,
                f"GM program {program} -> '{get_sound_name(*fallback)}' "
                f"(GM banka 121.0.{program} postoji u fabrickoj tabeli)")

    if mapped:
        return (mapped, Confidence.CANDIDATE,
                f"remap tabela predlaže {mapped}, ali zvuk nije potvrđen u "
                f"fabrickoj tabeli")
    return ((cc00, cc32, program), Confidence.UNKNOWN,
            f"nema dokazanog Pa800 ekvivalenta za program {program}")


_NAME_STOPWORDS = {"gm", "the", "and", "kit", "1", "2", "3", "rx1", "rx2",
                   "rx3", "dnc", "rx"}


def _words(name: str) -> set:
    import re
    raw = _family(name).casefold()
    return {w for w in re.split(r"[^0-9a-z]+", raw)
            if len(w) >= 3 and w not in _NAME_STOPWORDS}


def _names_agree(current: str, target: str) -> bool:
    """At least one shared significant word between the two sound families."""
    left, right = _words(current), _words(target)
    if not left or not right:
        return True          # nothing to compare -- do not downgrade blindly
    return bool(left & right)


def _family(name: str) -> str:
    for suffix in RX_SUFFIXES:
        if name.endswith(suffix):
            name = name[: -len(suffix)]
    if name.endswith(GM_SUFFIX):
        name = name[: -len(GM_SUFFIX)]
    return name.strip()


def rx_variant_for(cc00: int, cc32: int, program: int
                   ) -> Optional[Tuple[Tuple[int, int, int], str]]:
    """The RX/DNC variant of this sound, if the factory table has one.

    Deterministic: the closest variant by name inside the same family wins;
    ties are broken by program number, then bank.
    """
    current = PA800_SOUNDS_FULL.get((cc00, cc32, program))
    if not current:
        return None
    family = _family(current)
    if not family or family == current:
        return None

    candidates = []
    for key, name in PA800_SOUNDS_FULL.items():
        if key == (cc00, cc32, program):
            continue
        if _family(name) != family:
            continue
        if not (name.endswith("RX") or " RX" in name or "DNC" in name):
            continue
        candidates.append((name, key))
    if not candidates:
        return None

    def rank(item):
        name, key = item
        exact = 0 if name in (f"{family} RX", f"{family} RX1") else 1
        return (exact, key[2], key[1], name)

    name, key = sorted(candidates, key=rank)[0]
    return (key, name)
