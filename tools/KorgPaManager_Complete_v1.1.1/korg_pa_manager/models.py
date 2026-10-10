"""Typed data models used by the KorgPaManager core and GUI."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass(frozen=True)
class EventRef:
    """Stable reference to an event until the next structural edit."""

    track_index: int
    message_index: int

    @property
    def key(self) -> str:
        return f"{self.track_index}:{self.message_index}"


@dataclass(frozen=True)
class MarkerEvent:
    """A textual MIDI event that can delimit a Korg style section."""

    ref: EventRef
    tick: int
    meta_type: str
    text: str
    family: str
    element_number: Optional[int]
    chord_variation: Optional[int]
    recognized: bool

    @property
    def section_name(self) -> str:
        if self.recognized:
            bits = [self.family]
            if self.element_number is not None:
                bits.append(str(self.element_number))
            if self.chord_variation is not None:
                bits.append(f"CV{self.chord_variation}")
            return " ".join(bits)
        return self.text or "(unnamed marker)"


@dataclass(frozen=True)
class ProgramAssignment:
    """One program-change event and bank state effective at that position."""

    ref: EventRef
    tick: int
    channel: int
    role: str
    bank_msb: Optional[int]
    bank_lsb: Optional[int]
    program: int

    @property
    def midi_channel(self) -> int:
        return self.channel + 1


@dataclass
class ChannelSummary:
    """Counts and timing boundaries for one Korg style MIDI channel."""

    channel: int
    role: str
    message_count: int = 0
    note_on_count: int = 0
    program_count: int = 0
    first_tick: Optional[int] = None
    last_tick: Optional[int] = None

    @property
    def midi_channel(self) -> int:
        return self.channel + 1


@dataclass
class NoteActivity:
    """Aggregated trigger activity for a note on Drum or Percussion."""

    channel: int
    note: int
    hit_count: int = 0
    velocity_min: int = 127
    velocity_max: int = 0
    first_tick: Optional[int] = None
    last_tick: Optional[int] = None
    sections: set[str] = field(default_factory=set)
    assignments: set[str] = field(default_factory=set)

    @property
    def midi_channel(self) -> int:
        return self.channel + 1


@dataclass(frozen=True)
class NoteHit:
    """One sounding note-on event with its exact MIDI location."""

    ref: EventRef
    tick: int
    channel: int
    note: int
    velocity: int
    section: str
    bank_msb: Optional[int]
    bank_lsb: Optional[int]
    program: Optional[int]

    @property
    def midi_channel(self) -> int:
        return self.channel + 1


@dataclass(frozen=True)
class TimeSliceReport:
    """Evidence-only statistics for possible sliced-groove trigger data."""

    channel: int
    hit_count: int
    unique_notes: int
    note_min: Optional[int]
    note_max: Optional[int]
    first_tick: Optional[int]
    last_tick: Optional[int]
    shortest_start_interval: Optional[int]
    interval_gcd: Optional[int]
    velocity_min: Optional[int]
    velocity_max: Optional[int]


@dataclass(frozen=True)
class SaveResult:
    """Result returned after an atomic, validated Save As operation."""

    path: Path
    file_size: int
    track_count: int
    message_count: int
    labels_path: Optional[Path]
