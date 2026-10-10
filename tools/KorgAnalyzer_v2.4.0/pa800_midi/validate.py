"""Validation -- prove that nothing moved.

A fingerprint of every note is taken before and after the transformation:

    (track, order, tick, pitch)

The rule (§16):

    BEFORE tick/pitch/order == AFTER tick/pitch/order

If it does not hold the result is VALIDATION_FAILED and the export is not
valid.  When the user explicitly allowed note ADDITIONS (ghost notes), the
original notes must still appear as an unchanged subsequence; additions are
counted and reported separately -- they can never silently replace a note.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Tuple

from pa800_midi._mido import MidiFile

NoteKey = Tuple[int, int, int, int]     # track, order, tick, pitch

VALIDATION_OK = "VALIDATION_OK"
VALIDATION_FAILED = "VALIDATION_FAILED"


@dataclass
class NoteFingerprint:
    keys: List[NoteKey] = field(default_factory=list)

    def __len__(self) -> int:
        return len(self.keys)

    def summary(self) -> str:
        if not self.keys:
            return "nema nota"
        low = min(k[3] for k in self.keys)
        high = max(k[3] for k in self.keys)
        last = max(k[2] for k in self.keys)
        return (f"{len(self.keys)} nota, pitch {low}-{high}, "
                f"zadnji tick {last}")


@dataclass
class ValidationResult:
    status: str = VALIDATION_OK
    before_count: int = 0
    after_count: int = 0
    moved: List[Tuple[NoteKey, NoteKey]] = field(default_factory=list)
    missing: List[NoteKey] = field(default_factory=list)
    added: List[NoteKey] = field(default_factory=list)
    additions_allowed: bool = False

    @property
    def ok(self) -> bool:
        return self.status == VALIDATION_OK

    def describe(self) -> str:
        if self.ok:
            base = (f"{self.status}: {self.before_count} nota -- tick, pitch i "
                    f"redoslijed su identični")
            if self.added:
                base += (f"; dodano {len(self.added)} nota (dozvoljeno "
                         f"korisničkim pravilom)")
            return base
        return (f"{self.status}: {len(self.moved)} nota pomjereno, "
                f"{len(self.missing)} izgubljeno, {len(self.added)} dodano "
                f"-- izvoz se ne smatra validnim")

    def to_dict(self):
        return {
            "status": self.status,
            "before_count": self.before_count,
            "after_count": self.after_count,
            "moved": [{"before": list(b), "after": list(a)} for b, a in self.moved],
            "missing": [list(m) for m in self.missing],
            "added": [list(a) for a in self.added],
            "additions_allowed": self.additions_allowed,
            "description": self.describe(),
        }


def fingerprint(mid: MidiFile) -> NoteFingerprint:
    keys: List[NoteKey] = []
    for track_index, track in enumerate(mid.tracks):
        tick = 0
        order = 0
        for msg in track:
            tick += msg.time
            if msg.type == "note_on" and msg.velocity > 0:
                keys.append((track_index, order, tick, msg.note))
                order += 1
    return NoteFingerprint(keys)


def _is_subsequence(before: List[NoteKey], after: List[NoteKey]) -> bool:
    it = iter(after)
    return all(item in it for item in before)


def compare(before: NoteFingerprint, after: NoteFingerprint,
            additions_allowed: bool = False) -> ValidationResult:
    result = ValidationResult(
        before_count=len(before),
        after_count=len(after),
        additions_allowed=additions_allowed,
    )

    if before.keys == after.keys:
        return result

    before_set = set(before.keys)
    after_set = set(after.keys)
    result.added = sorted(after_set - before_set)
    result.missing = sorted(before_set - after_set)

    if additions_allowed and _is_subsequence(before.keys, after.keys):
        result.status = VALIDATION_OK
        return result

    # notes that kept their identity but moved in tick or order
    by_pitch_before = sorted(before.keys, key=lambda k: (k[3], k[2]))
    by_pitch_after = sorted(after.keys, key=lambda k: (k[3], k[2]))
    for old, new in zip(by_pitch_before, by_pitch_after):
        if old != new:
            result.moved.append((old, new))
    result.status = VALIDATION_FAILED
    return result


def validate_files(original: str, transformed: str,
                   additions_allowed: bool = False) -> ValidationResult:
    return compare(fingerprint(MidiFile(original)),
                   fingerprint(MidiFile(transformed)),
                   additions_allowed=additions_allowed)
