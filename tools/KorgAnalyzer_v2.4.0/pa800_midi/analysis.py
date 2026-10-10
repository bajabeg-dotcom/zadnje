"""Step 1-4 of the pipeline: read the MIDI, identify track / role /
instrument and check the Key Range hard limit.

Nothing here changes a single byte -- analysis is read-only.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from pa800_midi._mido import MetaMessage, MidiFile
from pa800_midi.data.categories import (
    CHANNEL_NAMES,
    get_instrument_category,
    note_name,
)
from pa800_midi.data.drum_kits import get_drum_name
from pa800_midi.data.instrument_profiles import get_instrument_profile
from pa800_midi.data.pa800_sounds import PA800_SOUNDS_FULL, get_sound_name

#: Roles that the accompaniment machine actually uses on a Pa800 style.
KORG_ACC_CHANNELS = {10: "Acc1", 11: "Acc2", 12: "Acc3", 13: "Acc4"}

#: Categories whose Korg role name differs from the GM family name.
ROLE_BY_CATEGORY = {
    "piano": "piano",
    "organ": "organ",
    "accordion": "accordion",
    "guitar": "rhythm_guitar",
    "bass": "bass",
    "strings": "strings",
    "choir": "choir",
    "brass": "brass",
    "sax": "sax",
    "woodwind": "woodwind",
    "synth": "synth",
    "ethnic": "ethnic",
    "fx": "fx",
    "drums": "drums",
    "other": "other",
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass
class NoteEvent:
    track: int
    channel: int
    tick: int
    pitch: int
    velocity: int
    duration: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "track": self.track,
            "channel": self.channel + 1,
            "tick": self.tick,
            "note": self.pitch,
            "note_name": note_name(self.pitch),
            "velocity": self.velocity,
            "duration": self.duration,
        }


@dataclass
class KeyRangeConflict:
    track: int
    channel: int
    note: int
    tick: int
    direction: str          # "below" / "above"
    limit_low: int
    limit_high: int
    instrument: str
    ticks: int = 1
    source: str = "measured"     # measured / type_rule / user_override
    rule_note: str = ""

    def describe(self) -> str:
        where = "ispod" if self.direction == "below" else "iznad"
        how = {
            "measured": "izmjereni opseg",
            "type_rule": "pravilo po tipu instrumenta",
            "user_override": "tvoje pravilo",
        }.get(self.source, "opseg")
        return (
            f"KEY_RANGE_CONFLICT track {self.track} kanal {self.channel + 1}: "
            f"{note_name(self.note)} je {where} dozvoljenog opsega "
            f"{note_name(self.limit_low)}-{note_name(self.limit_high)} "
            f"za '{self.instrument}' ({how}, {self.ticks} nota)"
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "track": self.track,
            "channel": self.channel + 1,
            "note": self.note,
            "note_name": note_name(self.note),
            "tick": self.tick,
            "direction": self.direction,
            "limit_low": self.limit_low,
            "limit_high": self.limit_high,
            "instrument": self.instrument,
            "ticks": self.ticks,
            "source": self.source,
            "rule_note": self.rule_note,
        }


@dataclass
class TrackFacts:
    index: int
    name: str
    channels: List[int] = field(default_factory=list)
    cc00: int = 121
    cc32: int = 0
    program: int = 0
    is_drum: bool = False
    category: str = "other"
    role: str = "other"
    key_rule_source: Optional[str] = None
    key_rule_note: str = ""
    sound_name: str = ""
    in_factory_table: bool = False
    profile_name: Optional[str] = None
    key_low: Optional[int] = None
    key_high: Optional[int] = None
    note_count: int = 0
    pitch_min: Optional[int] = None
    pitch_max: Optional[int] = None
    velocity_min: Optional[int] = None
    velocity_max: Optional[int] = None
    velocity_median: Optional[int] = None
    controller_counts: Dict[int, int] = field(default_factory=dict)
    program_changes: int = 0
    conflicts: List[KeyRangeConflict] = field(default_factory=list)

    @property
    def instrument_id(self) -> str:
        return f"{self.cc00}.{self.cc32}.{self.program}"

    @property
    def label(self) -> str:
        ch = f"ch{self.channels[0] + 1}" if self.channels else "meta"
        return f"{self.name} [{ch}] {self.sound_name}"

    def to_dict(self) -> Dict[str, Any]:
        data = {
            "index": self.index,
            "name": self.name,
            "channels": [c + 1 for c in self.channels],
            "role": self.role,
            "category": self.category,
            "is_drum": self.is_drum,
            "instrument_id": self.instrument_id,
            "cc00": self.cc00,
            "cc32": self.cc32,
            "program": self.program,
            "sound_name": self.sound_name,
            "in_factory_table": self.in_factory_table,
            "profile_name": self.profile_name,
            "key_range": ([self.key_low, self.key_high]
                          if self.key_low is not None else None),
            "key_range_source": self.key_rule_source,
            "key_range_note": self.key_rule_note,
            "note_count": self.note_count,
            "pitch_range": ([self.pitch_min, self.pitch_max]
                            if self.pitch_min is not None else None),
            "velocity": {
                "min": self.velocity_min,
                "median": self.velocity_median,
                "max": self.velocity_max,
            },
            "program_changes": self.program_changes,
            "controllers": {str(k): v for k, v in sorted(self.controller_counts.items())},
            "conflicts": [c.to_dict() for c in self.conflicts],
        }
        return data


@dataclass
class MidiFacts:
    path: str
    sha256: str
    size: int
    midi_type: int
    ticks_per_beat: int
    track_count: int
    tempo: int
    length_seconds: float
    tracks: List[TrackFacts] = field(default_factory=list)
    notes: List[NoteEvent] = field(default_factory=list)
    conflicts: List[KeyRangeConflict] = field(default_factory=list)

    @property
    def total_notes(self) -> int:
        return len(self.notes)

    def track(self, index: int) -> Optional[TrackFacts]:
        for item in self.tracks:
            if item.index == index:
                return item
        return None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "path": self.path,
            "sha256": self.sha256,
            "size": self.size,
            "midi_type": self.midi_type,
            "ticks_per_beat": self.ticks_per_beat,
            "track_count": self.track_count,
            "tempo_us_per_beat": self.tempo,
            "length_seconds": round(self.length_seconds, 3),
            "total_notes": self.total_notes,
            "tracks": [t.to_dict() for t in self.tracks],
            "conflicts": [c.to_dict() for c in self.conflicts],
        }


def _median(values: List[int]) -> Optional[int]:
    if not values:
        return None
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return int(round((ordered[mid - 1] + ordered[mid]) / 2))


def identify_role(channel: int, category: str, is_drum: bool) -> str:
    if is_drum:
        return "drums"
    return ROLE_BY_CATEGORY.get(category, "other")


def is_korg_accompaniment(channel: int) -> bool:
    return channel in KORG_ACC_CHANNELS


def analyze(path: str | Path, key_ranges=None) -> MidiFacts:
    """Read a MIDI file into facts.  Never writes anything.

    ``key_ranges`` is an optional :class:`pa800_midi.keyrange.KeyRangeTable`;
    without it the default table (measured profiles + per-type rules) is used.
    """
    target = Path(path)
    if key_ranges is None:
        from pa800_midi.keyrange import default_table
        key_ranges = default_table()
    mid = MidiFile(str(target))

    tempo = 500_000
    for track in mid.tracks:
        for msg in track:
            if isinstance(msg, MetaMessage) and msg.type == "set_tempo":
                tempo = msg.tempo
                break
        else:
            continue
        break

    facts = MidiFacts(
        path=str(target),
        sha256=sha256_file(target),
        size=target.stat().st_size,
        midi_type=mid.type,
        ticks_per_beat=mid.ticks_per_beat,
        track_count=len(mid.tracks),
        tempo=tempo,
        length_seconds=mid.length,
    )

    for track_index, track in enumerate(mid.tracks):
        channel_state: Dict[int, Dict[str, Any]] = {}
        abs_tick = 0
        track_name = ""
        pending: Dict[Tuple[int, int], List[NoteEvent]] = {}

        for msg in track:
            abs_tick += msg.time
            if msg.type == "track_name" and not track_name:
                track_name = msg.name

            channel = getattr(msg, "channel", None)
            if channel is None:
                continue

            state = channel_state.setdefault(channel, {
                "cc00": 120 if channel == 9 else 121,
                "cc32": 0,
                "program": 0,
                "controllers": {},
                "program_changes": 0,
            })

            if msg.type == "program_change":
                state["program"] = msg.program
                state["program_changes"] += 1
            elif msg.type == "control_change":
                if msg.control == 0:
                    state["cc00"] = msg.value
                elif msg.control == 32:
                    state["cc32"] = msg.value
                state["controllers"][msg.control] = \
                    state["controllers"].get(msg.control, 0) + 1
            elif msg.type == "note_on" and msg.velocity > 0:
                note = NoteEvent(track_index, channel, abs_tick, msg.note,
                                 msg.velocity)
                facts.notes.append(note)
                pending.setdefault((track_index, channel), []).append(note)
            elif msg.type == "note_off" or (msg.type == "note_on"
                                            and msg.velocity == 0):
                queue = pending.get((track_index, channel))
                if queue:
                    for candidate in queue:
                        if candidate.pitch == msg.note and candidate.duration is None:
                            candidate.duration = max(0, abs_tick - candidate.tick)
                            break

        for channel in sorted(channel_state):
            state = channel_state[channel]
            state["track_name"] = track_name
            state["index"] = track_index
            facts.tracks.append(_build_track_facts(facts, track_index, channel,
                                                   state, key_ranges))

    facts.tracks = [t for t in facts.tracks if t.note_count or t.is_drum]
    facts.conflicts = [c for t in facts.tracks for c in t.conflicts]
    return facts


def _build_track_facts(facts: MidiFacts, track_index: int, channel: int,
                       state: Dict[str, Any], key_ranges=None) -> TrackFacts:
    notes = [n for n in facts.notes
             if n.track == track_index and n.channel == channel]
    pitches = [n.pitch for n in notes]
    velocities = [n.velocity for n in notes]

    is_drum = channel == 9
    category = "drums" if is_drum else get_instrument_category(state["program"])
    cc00, cc32, program = state["cc00"], state["cc32"], state["program"]

    if is_drum:
        sound_name = get_drum_name(cc00, cc32, program)
        in_table = (cc00, cc32, program) in _DRUM_TABLE
    else:
        sound_name = get_sound_name(cc00, cc32, program)
        in_table = (cc00, cc32, program) in PA800_SOUNDS_FULL

    profile = None if is_drum else get_instrument_profile(cc00, cc32, program)

    track_name = state.get("track_name") or CHANNEL_NAMES.get(channel, f"Ch{channel + 1}")
    if facts.midi_type == 0:
        track_name = CHANNEL_NAMES.get(channel, f"Ch{channel + 1}")

    rule = key_ranges.resolve(category, ROLE_BY_CATEGORY.get(category, ""),
                              profile) if key_ranges else None

    info = TrackFacts(
        index=track_index,
        name=track_name,
        channels=[channel],
        cc00=cc00,
        cc32=cc32,
        program=program,
        is_drum=is_drum,
        category=category,
        role=identify_role(channel, category, is_drum),
        sound_name=sound_name,
        in_factory_table=in_table,
        profile_name=profile.name if profile else None,
        key_low=rule.low if rule else None,
        key_high=rule.high if rule else None,
        key_rule_source=rule.source if rule else None,
        key_rule_note=rule.explanation if rule else "",
        note_count=len(notes),
        pitch_min=min(pitches) if pitches else None,
        pitch_max=max(pitches) if pitches else None,
        velocity_min=min(velocities) if velocities else None,
        velocity_max=max(velocities) if velocities else None,
        velocity_median=_median(velocities),
        controller_counts=dict(state["controllers"]),
        program_changes=state["program_changes"],
    )

    # HARD LIMIT -- reported, never "repaired".
    if rule and notes:
        out_of_range = [n for n in notes
                        if n.pitch < rule.low or n.pitch > rule.high]
        label = profile.name if (profile and rule.source == "measured") \
            else f"{category} (tip instrumenta)"
        buckets: Dict[Tuple[int, str], KeyRangeConflict] = {}
        for note in out_of_range:
            direction = "below" if note.pitch < rule.low else "above"
            key = (note.pitch, direction)
            if key in buckets:
                buckets[key].ticks += 1
            else:
                buckets[key] = KeyRangeConflict(
                    track=track_index,
                    channel=channel,
                    note=note.pitch,
                    tick=note.tick,
                    direction=direction,
                    limit_low=rule.low,
                    limit_high=rule.high,
                    instrument=label,
                    source=rule.source,
                    rule_note=rule.explanation,
                )
        info.conflicts = [buckets[k] for k in sorted(buckets)]

    return info


from pa800_midi.data.drum_kits import PA800_DRUM_KITS as _DRUM_TABLE  # noqa: E402
