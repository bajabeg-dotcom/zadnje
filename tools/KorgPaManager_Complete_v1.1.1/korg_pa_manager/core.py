"""Write-capable Standard MIDI core for KorgPaManager.

All edits are made to an in-memory :class:`mido.MidiFile`.  The source path is
never overwritten by Save As.  Output is written to a sibling temporary file,
parsed and compared with the intended in-memory event stream, then installed
with ``os.replace``.
"""

from __future__ import annotations

import copy
import csv
import hashlib
import io
import json
import math
import os
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

from mido import Message, MetaMessage, MidiFile, MidiTrack

from .gm_drums import gm_reference_label, midi_note_name
from .markers import TEXT_META_TYPES, classify_marker, is_marker_like, text_from_meta
from .models import (
    ChannelSummary,
    EventRef,
    MarkerEvent,
    NoteActivity,
    NoteHit,
    ProgramAssignment,
    SaveResult,
    TimeSliceReport,
)

STYLE_CHANNELS: dict[int, str] = {
    8: "Bass",
    9: "Drums",
    10: "Percussion",
    11: "Acc 1",
    12: "Acc 2",
    13: "Acc 3",
    14: "Acc 4",
    15: "Acc 5",
}
DRUM_CHANNELS = frozenset({9, 10})
LABEL_SCHEMA = "KorgPaManager labels v1"


class MidiDocumentError(Exception):
    """Raised when MIDI loading, editing, validation, or saving fails."""


@dataclass
class _Snapshot:
    data: bytes
    labels: dict[tuple[int, int], str]
    dirty: bool
    description: str


@dataclass
class _AbsoluteEvent:
    tick: int
    order: float
    message: Any


def _serialize_midi(midi: MidiFile) -> bytes:
    output = io.BytesIO()
    midi.save(file=output)
    return output.getvalue()


def _deserialize_midi(data: bytes) -> MidiFile:
    return MidiFile(file=io.BytesIO(data), clip=False)


def _track_as_absolute(track: MidiTrack) -> list[_AbsoluteEvent]:
    tick = 0
    events: list[_AbsoluteEvent] = []
    for index, message in enumerate(track):
        tick += int(message.time)
        events.append(_AbsoluteEvent(tick=tick, order=float(index), message=message))
    return events


def _events_to_track(events: Iterable[_AbsoluteEvent]) -> MidiTrack:
    ordered = sorted(events, key=lambda event: (event.tick, event.order))
    result = MidiTrack()
    previous = 0
    for event in ordered:
        if event.tick < previous:
            raise MidiDocumentError(
                "Internal error: event ordering produced a negative delta"
            )
        result.append(event.message.copy(time=event.tick - previous))
        previous = event.tick
    return result


def _message_signature(message: Any) -> tuple[tuple[str, Any], ...]:
    values = message.dict()
    # JSON-like sorting makes a stable semantic signature after re-parsing.
    return tuple(sorted(values.items(), key=lambda item: item[0]))


def _midi_signature(midi: MidiFile) -> tuple[Any, ...]:
    return (
        midi.type,
        midi.ticks_per_beat,
        tuple(
            tuple(_message_signature(message) for message in track)
            for track in midi.tracks
        ),
    )


def _message_count(midi: MidiFile) -> int:
    return sum(len(track) for track in midi.tracks)


class MidiDocument:
    """Editable MIDI document with undo/redo and validated atomic Save As."""

    def __init__(self, undo_limit: int = 50) -> None:
        self.midi: Optional[MidiFile] = None
        self.source_path: Optional[Path] = None
        self.last_output_path: Optional[Path] = None
        self.source_sha256: Optional[str] = None
        self.custom_labels: dict[tuple[int, int], str] = {}
        self.dirty = False
        self.undo_limit = max(1, undo_limit)
        self._undo: list[_Snapshot] = []
        self._redo: list[_Snapshot] = []

    @property
    def is_loaded(self) -> bool:
        return self.midi is not None

    @property
    def can_undo(self) -> bool:
        return bool(self._undo)

    @property
    def can_redo(self) -> bool:
        return bool(self._redo)

    def _require_midi(self) -> MidiFile:
        if self.midi is None:
            raise MidiDocumentError("No MIDI file is loaded")
        return self.midi

    def load(self, path: str | os.PathLike[str]) -> None:
        """Load and validate a Standard MIDI File with contextual errors."""

        source = Path(path).expanduser()
        try:
            if not source.is_file():
                raise FileNotFoundError(f"File does not exist: {source}")
            midi = MidiFile(str(source), clip=False)
            self._validate_midi(midi)
            source_bytes = source.read_bytes()
        except (OSError, EOFError, ValueError, TypeError) as exc:
            raise MidiDocumentError(
                f"Could not load MIDI file '{source}': {exc}"
            ) from exc
        except Exception as exc:
            raise MidiDocumentError(
                f"The file could not be parsed as a Standard MIDI File: {exc}"
            ) from exc

        self.midi = midi
        self.source_path = source.resolve()
        self.last_output_path = None
        self.source_sha256 = hashlib.sha256(source_bytes).hexdigest()
        self.custom_labels = {}
        self.dirty = False
        self._undo.clear()
        self._redo.clear()
        self._load_labels_if_present()

    @staticmethod
    def _validate_midi(midi: MidiFile) -> None:
        if midi.type not in (0, 1, 2):
            raise MidiDocumentError(f"Unsupported SMF type: {midi.type}")
        if midi.type == 0 and len(midi.tracks) != 1:
            raise MidiDocumentError("SMF type 0 must contain exactly one track")
        if not 1 <= midi.ticks_per_beat <= 0x7FFF:
            raise MidiDocumentError("Invalid ticks-per-quarter-note value")
        for track_index, track in enumerate(midi.tracks):
            for message_index, message in enumerate(track):
                if not isinstance(message.time, int) or message.time < 0:
                    raise MidiDocumentError(
                        "Invalid delta time at "
                        f"track {track_index}, message {message_index}: {message.time!r}"
                    )

    def _snapshot(self, description: str) -> _Snapshot:
        midi = self._require_midi()
        return _Snapshot(
            data=_serialize_midi(midi),
            labels=copy.deepcopy(self.custom_labels),
            dirty=self.dirty,
            description=description,
        )

    def _restore(self, snapshot: _Snapshot) -> None:
        self.midi = _deserialize_midi(snapshot.data)
        self.custom_labels = copy.deepcopy(snapshot.labels)
        self.dirty = snapshot.dirty

    def _apply_edit(self, description: str, mutator: Callable[[], Any]) -> Any:
        before = self._snapshot(description)
        try:
            result = mutator()
            self._validate_midi(self._require_midi())
            # A round-trip catches structures that Mido accepts in memory but
            # cannot encode as a valid SMF.
            _deserialize_midi(_serialize_midi(self._require_midi()))
        except Exception:
            self._restore(before)
            raise
        self._undo.append(before)
        if len(self._undo) > self.undo_limit:
            del self._undo[0]
        self._redo.clear()
        self.dirty = True
        return result

    def undo(self) -> str:
        if not self._undo:
            raise MidiDocumentError("Nothing to undo")
        target = self._undo.pop()
        self._redo.append(self._snapshot(target.description))
        self._restore(target)
        return target.description

    def redo(self) -> str:
        if not self._redo:
            raise MidiDocumentError("Nothing to redo")
        target = self._redo.pop()
        self._undo.append(self._snapshot(target.description))
        self._restore(target)
        return target.description

    def track_length_ticks(self, track_index: int) -> int:
        midi = self._require_midi()
        self._check_track(track_index)
        return sum(int(message.time) for message in midi.tracks[track_index])

    def total_length_ticks(self) -> int:
        midi = self._require_midi()
        return max(
            (sum(int(msg.time) for msg in track) for track in midi.tracks), default=0
        )

    def _check_track(self, track_index: int) -> None:
        midi = self._require_midi()
        if not 0 <= track_index < len(midi.tracks):
            raise MidiDocumentError(f"Track index must be 0..{len(midi.tracks) - 1}")

    @staticmethod
    def _check_tick(tick: int) -> None:
        if not isinstance(tick, int) or tick < 0:
            raise MidiDocumentError("Absolute tick must be a non-negative integer")

    @staticmethod
    def _check_channel(channel: int) -> None:
        if not isinstance(channel, int) or not 0 <= channel <= 15:
            raise MidiDocumentError("Mido channel must be in the range 0..15")

    @staticmethod
    def _check_data_byte(value: int, name: str) -> None:
        if not isinstance(value, int) or not 0 <= value <= 127:
            raise MidiDocumentError(f"{name} must be in the range 0..127")

    def _get_message(self, ref: EventRef) -> Any:
        midi = self._require_midi()
        self._check_track(ref.track_index)
        track = midi.tracks[ref.track_index]
        if not 0 <= ref.message_index < len(track):
            raise MidiDocumentError(
                "The selected event is no longer available; refresh the view"
            )
        return track[ref.message_index]

    def markers(self) -> list[MarkerEvent]:
        """Return all marker-like textual events with exact absolute ticks."""

        midi = self._require_midi()
        found: list[MarkerEvent] = []
        for track_index, track in enumerate(midi.tracks):
            tick = 0
            for message_index, message in enumerate(track):
                tick += int(message.time)
                if not message.is_meta or message.type not in TEXT_META_TYPES:
                    continue
                text = text_from_meta(message)
                if not is_marker_like(message.type, text):
                    continue
                family, element, cv, recognized = classify_marker(text)
                found.append(
                    MarkerEvent(
                        ref=EventRef(track_index, message_index),
                        tick=tick,
                        meta_type=message.type,
                        text=text,
                        family=family,
                        element_number=element,
                        chord_variation=cv,
                        recognized=recognized,
                    )
                )
        found.sort(
            key=lambda item: (item.tick, item.ref.track_index, item.ref.message_index)
        )
        return found

    def _section_at_tick(self, tick: int, markers: list[MarkerEvent]) -> str:
        section = "Before first marker"
        for marker in markers:
            if marker.tick > tick:
                break
            section = marker.section_name
        return section

    def add_marker(
        self,
        tick: int,
        text: str,
        track_index: int = 0,
        meta_type: str = "marker",
    ) -> None:
        self._check_tick(tick)
        self._check_track(track_index)
        if meta_type not in {"marker", "cue_marker", "text"}:
            raise MidiDocumentError("Marker type must be marker, cue_marker, or text")
        if not text:
            raise MidiDocumentError("Marker text cannot be empty")

        def mutate() -> None:
            midi = self._require_midi()
            events = _track_as_absolute(midi.tracks[track_index])
            eot_events = [
                event for event in events if event.message.type == "end_of_track"
            ]
            same_tick_non_eot = [
                event.order
                for event in events
                if event.tick == tick and event.message.type != "end_of_track"
            ]
            if same_tick_non_eot:
                order = max(same_tick_non_eot) + 0.25
            else:
                same_tick_eot = [
                    event.order for event in eot_events if event.tick == tick
                ]
                order = (
                    (min(same_tick_eot) - 0.25) if same_tick_eot else len(events) + 0.25
                )
            if eot_events and tick > max(event.tick for event in eot_events):
                for event in eot_events:
                    event.tick = tick
            events.append(
                _AbsoluteEvent(tick, order, MetaMessage(meta_type, text=text, time=0))
            )
            midi.tracks[track_index] = _events_to_track(events)

        self._apply_edit(f"Add marker {text!r}", mutate)

    def edit_marker(self, ref: EventRef, tick: int, text: str) -> None:
        self._check_tick(tick)
        if not text:
            raise MidiDocumentError("Marker text cannot be empty")
        message = self._get_message(ref)
        if not message.is_meta or message.type not in TEXT_META_TYPES:
            raise MidiDocumentError("The selected event is not a textual meta event")

        def mutate() -> None:
            midi = self._require_midi()
            events = _track_as_absolute(midi.tracks[ref.track_index])
            target = events[ref.message_index]
            if hasattr(target.message, "text"):
                changed = target.message.copy(text=text, time=0)
            elif hasattr(target.message, "name"):
                changed = target.message.copy(name=text, time=0)
            else:
                raise MidiDocumentError("The selected event has no editable text field")
            target.message = changed
            target.tick = tick
            eot_events = [
                event for event in events if event.message.type == "end_of_track"
            ]
            if eot_events and tick > max(event.tick for event in eot_events):
                for event in eot_events:
                    event.tick = tick
                    event.order = max(event.order, target.order + 0.5)
            midi.tracks[ref.track_index] = _events_to_track(events)

        self._apply_edit(f"Edit marker {text!r}", mutate)

    def delete_marker(self, ref: EventRef) -> None:
        message = self._get_message(ref)
        if not message.is_meta or message.type not in TEXT_META_TYPES:
            raise MidiDocumentError("The selected event is not a textual meta event")
        old_text = text_from_meta(message)

        def mutate() -> None:
            midi = self._require_midi()
            events = _track_as_absolute(midi.tracks[ref.track_index])
            del events[ref.message_index]
            midi.tracks[ref.track_index] = _events_to_track(events)

        self._apply_edit(f"Delete marker {old_text!r}", mutate)

    def channel_summaries(self) -> list[ChannelSummary]:
        midi = self._require_midi()
        summaries = {
            channel: ChannelSummary(channel=channel, role=role)
            for channel, role in STYLE_CHANNELS.items()
        }
        for track in midi.tracks:
            tick = 0
            for message in track:
                tick += int(message.time)
                if message.is_meta or not hasattr(message, "channel"):
                    continue
                summary = summaries.get(message.channel)
                if summary is None:
                    continue
                summary.message_count += 1
                if message.type == "note_on" and message.velocity > 0:
                    summary.note_on_count += 1
                if message.type == "program_change":
                    summary.program_count += 1
                if summary.first_tick is None or tick < summary.first_tick:
                    summary.first_tick = tick
                if summary.last_tick is None or tick > summary.last_tick:
                    summary.last_tick = tick
        return list(summaries.values())

    def program_assignments(self) -> list[ProgramAssignment]:
        midi = self._require_midi()
        assignments: list[ProgramAssignment] = []
        for track_index, track in enumerate(midi.tracks):
            bank: dict[int, list[Optional[int]]] = {
                channel: [None, None] for channel in range(16)
            }
            tick = 0
            for message_index, message in enumerate(track):
                tick += int(message.time)
                if message.is_meta or not hasattr(message, "channel"):
                    continue
                channel = message.channel
                if message.type == "control_change" and message.control in (0, 32):
                    bank[channel][0 if message.control == 0 else 1] = message.value
                elif message.type == "program_change" and channel in STYLE_CHANNELS:
                    assignments.append(
                        ProgramAssignment(
                            ref=EventRef(track_index, message_index),
                            tick=tick,
                            channel=channel,
                            role=STYLE_CHANNELS[channel],
                            bank_msb=bank[channel][0],
                            bank_lsb=bank[channel][1],
                            program=message.program,
                        )
                    )
        assignments.sort(
            key=lambda item: (item.tick, item.ref.track_index, item.ref.message_index)
        )
        return assignments

    def edit_program_assignment(
        self,
        ref: EventRef,
        program: int,
        bank_msb: Optional[int] = None,
        bank_lsb: Optional[int] = None,
    ) -> None:
        self._check_data_byte(program, "Program")
        if bank_msb is not None:
            self._check_data_byte(bank_msb, "Bank MSB (CC00)")
        if bank_lsb is not None:
            self._check_data_byte(bank_lsb, "Bank LSB (CC32)")
        message = self._get_message(ref)
        if message.type != "program_change" or message.channel not in STYLE_CHANNELS:
            raise MidiDocumentError(
                "The selected event is not a Korg style program change"
            )

        def mutate() -> None:
            midi = self._require_midi()
            events = _track_as_absolute(midi.tracks[ref.track_index])
            program_event = events[ref.message_index]
            channel = program_event.message.channel
            tick = program_event.tick
            program_event.message = program_event.message.copy(program=program, time=0)

            requested = ((0, bank_msb), (32, bank_lsb))
            missing: list[tuple[int, int]] = []
            for control, value in requested:
                if value is None:
                    continue
                candidate: Optional[_AbsoluteEvent] = None
                for event in events[: ref.message_index]:
                    current = event.message
                    if (
                        event.tick == tick
                        and not current.is_meta
                        and current.type == "control_change"
                        and current.channel == channel
                        and current.control == control
                    ):
                        candidate = event
                if candidate is not None:
                    candidate.message = candidate.message.copy(value=value, time=0)
                else:
                    missing.append((control, value))

            for offset, (control, value) in enumerate(missing):
                # Keep CC00 before CC32 and both before the selected PC.
                order = program_event.order - 0.20 + offset * 0.05
                events.append(
                    _AbsoluteEvent(
                        tick,
                        order,
                        Message(
                            "control_change",
                            channel=channel,
                            control=control,
                            value=value,
                            time=0,
                        ),
                    )
                )
            midi.tracks[ref.track_index] = _events_to_track(events)

        self._apply_edit(f"Set program to {program}", mutate)

    def add_program_assignment(
        self,
        track_index: int,
        tick: int,
        channel: int,
        program: int,
        bank_msb: Optional[int] = None,
        bank_lsb: Optional[int] = None,
    ) -> None:
        self._check_track(track_index)
        self._check_tick(tick)
        self._check_channel(channel)
        if channel not in STYLE_CHANNELS:
            raise MidiDocumentError(
                "Assignment channel must be a Korg style channel (MIDI 9..16)"
            )
        self._check_data_byte(program, "Program")
        if bank_msb is not None:
            self._check_data_byte(bank_msb, "Bank MSB (CC00)")
        if bank_lsb is not None:
            self._check_data_byte(bank_lsb, "Bank LSB (CC32)")

        def mutate() -> None:
            midi = self._require_midi()
            events = _track_as_absolute(midi.tracks[track_index])
            first_channel_order = min(
                (
                    event.order
                    for event in events
                    if event.tick == tick
                    and not event.message.is_meta
                    and getattr(event.message, "channel", -1) == channel
                ),
                default=float(len(events) + 1),
            )
            same_tick_eot = [
                event.order
                for event in events
                if event.tick == tick and event.message.type == "end_of_track"
            ]
            boundary = min(
                [first_channel_order, *same_tick_eot]
                if same_tick_eot
                else [first_channel_order]
            )
            new_messages: list[Any] = []
            if bank_msb is not None:
                new_messages.append(
                    Message(
                        "control_change", channel=channel, control=0, value=bank_msb
                    )
                )
            if bank_lsb is not None:
                new_messages.append(
                    Message(
                        "control_change", channel=channel, control=32, value=bank_lsb
                    )
                )
            new_messages.append(
                Message("program_change", channel=channel, program=program)
            )
            start_order = boundary - 0.10 * len(new_messages)
            for index, new_message in enumerate(new_messages):
                events.append(
                    _AbsoluteEvent(tick, start_order + index * 0.05, new_message)
                )
            eot_events = [
                event for event in events if event.message.type == "end_of_track"
            ]
            if eot_events and tick > max(event.tick for event in eot_events):
                for event in eot_events:
                    event.tick = tick
                    event.order = max(event.order, boundary + 1.0)
            midi.tracks[track_index] = _events_to_track(events)

        self._apply_edit(
            f"Add {STYLE_CHANNELS[channel]} program assignment at tick {tick}", mutate
        )

    def delete_program_assignment(self, ref: EventRef) -> None:
        message = self._get_message(ref)
        if message.type != "program_change" or message.channel not in STYLE_CHANNELS:
            raise MidiDocumentError(
                "The selected event is not a Korg style program change"
            )

        def mutate() -> None:
            midi = self._require_midi()
            events = _track_as_absolute(midi.tracks[ref.track_index])
            del events[ref.message_index]
            midi.tracks[ref.track_index] = _events_to_track(events)

        self._apply_edit("Delete program assignment", mutate)

    def remap_channels(self, source: int, target: int, swap: bool = False) -> int:
        """Move or swap all channel messages while preserving event ticks."""

        self._check_channel(source)
        self._check_channel(target)
        if source not in STYLE_CHANNELS or target not in STYLE_CHANNELS:
            raise MidiDocumentError(
                "Both channels must be Korg style channels (MIDI 9..16)"
            )
        if source == target:
            raise MidiDocumentError("Source and target channels are the same")
        count = 0

        def mutate() -> None:
            nonlocal count
            midi = self._require_midi()
            for track in midi.tracks:
                for index, message in enumerate(track):
                    if message.is_meta or not hasattr(message, "channel"):
                        continue
                    replacement: Optional[int] = None
                    if message.channel == source:
                        replacement = target
                    elif swap and message.channel == target:
                        replacement = source
                    if replacement is not None:
                        track[index] = message.copy(channel=replacement)
                        count += 1
            if count == 0:
                raise MidiDocumentError(
                    "No channel events matched the selected operation"
                )
            return count

        action = "Swap" if swap else "Move"
        return self._apply_edit(
            f"{action} MIDI channel {source + 1} and {target + 1}", mutate
        )

    def note_activity(
        self, channels: Iterable[int] = DRUM_CHANNELS
    ) -> list[NoteActivity]:
        midi = self._require_midi()
        selected = set(channels)
        markers = self.markers()
        activity: dict[tuple[int, int], NoteActivity] = {}
        for track in midi.tracks:
            tick = 0
            banks: dict[int, list[Optional[int]]] = {
                channel: [None, None] for channel in range(16)
            }
            programs: dict[int, Optional[int]] = {
                channel: None for channel in range(16)
            }
            for message in track:
                tick += int(message.time)
                if message.is_meta or not hasattr(message, "channel"):
                    continue
                if message.type == "control_change" and message.control in (0, 32):
                    banks[message.channel][0 if message.control == 0 else 1] = (
                        message.value
                    )
                    continue
                if message.type == "program_change":
                    programs[message.channel] = message.program
                    continue
                if (
                    message.type != "note_on"
                    or message.velocity <= 0
                    or message.channel not in selected
                ):
                    continue
                key = (message.channel, message.note)
                item = activity.setdefault(
                    key, NoteActivity(channel=message.channel, note=message.note)
                )
                item.hit_count += 1
                item.velocity_min = min(item.velocity_min, message.velocity)
                item.velocity_max = max(item.velocity_max, message.velocity)
                if item.first_tick is None or tick < item.first_tick:
                    item.first_tick = tick
                if item.last_tick is None or tick > item.last_tick:
                    item.last_tick = tick
                item.sections.add(self._section_at_tick(tick, markers))
                msb, lsb = banks[message.channel]
                program = programs[message.channel]
                identity = ".".join(
                    "—" if value is None else str(value)
                    for value in (msb, lsb, program)
                )
                item.assignments.add(identity)
        return [activity[key] for key in sorted(activity)]

    def note_hits(self, channel: int, note: Optional[int] = None) -> list[NoteHit]:
        self._check_channel(channel)
        midi = self._require_midi()
        markers = self.markers()
        hits: list[NoteHit] = []
        for track_index, track in enumerate(midi.tracks):
            tick = 0
            bank_msb: Optional[int] = None
            bank_lsb: Optional[int] = None
            program: Optional[int] = None
            for message_index, message in enumerate(track):
                tick += int(message.time)
                if not message.is_meta and getattr(message, "channel", -1) == channel:
                    if message.type == "control_change" and message.control == 0:
                        bank_msb = message.value
                    elif message.type == "control_change" and message.control == 32:
                        bank_lsb = message.value
                    elif message.type == "program_change":
                        program = message.program
                if (
                    not message.is_meta
                    and message.type == "note_on"
                    and message.velocity > 0
                    and message.channel == channel
                    and (note is None or message.note == note)
                ):
                    hits.append(
                        NoteHit(
                            ref=EventRef(track_index, message_index),
                            tick=tick,
                            channel=channel,
                            note=message.note,
                            velocity=message.velocity,
                            section=self._section_at_tick(tick, markers),
                            bank_msb=bank_msb,
                            bank_lsb=bank_lsb,
                            program=program,
                        )
                    )
        hits.sort(
            key=lambda item: (item.tick, item.ref.track_index, item.ref.message_index)
        )
        return hits

    def get_custom_label(self, channel: int, note: int) -> str:
        return self.custom_labels.get((channel, note), "")

    def set_custom_label(self, channel: int, note: int, label: str) -> None:
        self._check_channel(channel)
        self._check_data_byte(note, "Note")
        cleaned = label.strip()

        def mutate() -> None:
            key = (channel, note)
            if cleaned:
                self.custom_labels[key] = cleaned
            else:
                self.custom_labels.pop(key, None)

        self._apply_edit(
            f"Set custom label for MIDI channel {channel + 1}, note {note}", mutate
        )

    def remap_note(self, channel: int, source_note: int, target_note: int) -> int:
        self._check_channel(channel)
        if channel not in DRUM_CHANNELS:
            raise MidiDocumentError(
                "Note remapping is limited to Drum and Percussion channels"
            )
        self._check_data_byte(source_note, "Source note")
        self._check_data_byte(target_note, "Target note")
        if source_note == target_note:
            raise MidiDocumentError("Source and target notes are the same")
        count = 0

        def mutate() -> None:
            nonlocal count
            midi = self._require_midi()
            for track in midi.tracks:
                for index, message in enumerate(track):
                    if (
                        not message.is_meta
                        and message.channel == channel
                        and message.type in {"note_on", "note_off", "polytouch"}
                        and message.note == source_note
                    ):
                        track[index] = message.copy(note=target_note)
                        count += 1
            if count == 0:
                raise MidiDocumentError(
                    "No matching note-on or note-off events were found"
                )
            old_key = (channel, source_note)
            new_key = (channel, target_note)
            if old_key in self.custom_labels and new_key not in self.custom_labels:
                self.custom_labels[new_key] = self.custom_labels[old_key]
            self.custom_labels.pop(old_key, None)
            return count

        return self._apply_edit(
            f"Remap MIDI channel {channel + 1} note {source_note} to {target_note}",
            mutate,
        )

    def transform_timeslice_range(
        self,
        channel: int,
        note_min: int,
        note_max: int,
        note_offset: int = 0,
        tick_offset: int = 0,
        velocity_percent: int = 100,
    ) -> int:
        """Transform a note range without claiming that it is TimeSlice data.

        Note-on, note-off and poly-pressure keys are shifted together.  Exact
        absolute ticks are reconstructed after an optional timing offset.
        Velocity scaling applies only to sounding note-on events.
        """

        self._check_channel(channel)
        if channel not in DRUM_CHANNELS:
            raise MidiDocumentError(
                "TimeSlice tools are limited to Drum and Percussion"
            )
        self._check_data_byte(note_min, "First note")
        self._check_data_byte(note_max, "Last note")
        if note_min > note_max:
            raise MidiDocumentError("First note must not be greater than last note")
        if not isinstance(note_offset, int) or not -127 <= note_offset <= 127:
            raise MidiDocumentError("Note offset must be in the range -127..127")
        if note_min + note_offset < 0 or note_max + note_offset > 127:
            raise MidiDocumentError(
                "The note offset would produce notes outside 0..127"
            )
        if not isinstance(tick_offset, int):
            raise MidiDocumentError("Tick offset must be an integer")
        if not isinstance(velocity_percent, int) or not 1 <= velocity_percent <= 400:
            raise MidiDocumentError("Velocity percent must be in the range 1..400")
        changed = 0

        def mutate() -> None:
            nonlocal changed
            midi = self._require_midi()
            for track_index, track in enumerate(midi.tracks):
                events = _track_as_absolute(track)
                track_changed = False
                for event in events:
                    message = event.message
                    if (
                        message.is_meta
                        or not hasattr(message, "channel")
                        or message.channel != channel
                        or message.type not in {"note_on", "note_off", "polytouch"}
                        or not note_min <= message.note <= note_max
                    ):
                        continue
                    new_tick = event.tick + tick_offset
                    if new_tick < 0:
                        raise MidiDocumentError(
                            "The tick offset would move at least one event before tick 0"
                        )
                    changes: dict[str, int] = {"note": message.note + note_offset}
                    if message.type == "note_on" and message.velocity > 0:
                        scaled = round(message.velocity * velocity_percent / 100)
                        changes["velocity"] = min(127, max(1, scaled))
                    event.tick = new_tick
                    event.message = message.copy(time=0, **changes)
                    changed += 1
                    track_changed = True
                if track_changed:
                    non_eot_max = max(
                        (
                            event.tick
                            for event in events
                            if event.message.type != "end_of_track"
                        ),
                        default=0,
                    )
                    for event in events:
                        if (
                            event.message.type == "end_of_track"
                            and event.tick < non_eot_max
                        ):
                            event.tick = non_eot_max
                            event.order = max(item.order for item in events) + 1.0
                    midi.tracks[track_index] = _events_to_track(events)
            if changed == 0:
                raise MidiDocumentError("No note events matched the selected range")
            return changed

        return self._apply_edit(
            f"Transform possible TimeSlice triggers on MIDI channel {channel + 1}",
            mutate,
        )

    def timeslice_report(self, channel: int) -> TimeSliceReport:
        """Return exact trigger statistics, without asserting data provenance."""

        self._check_channel(channel)
        hits = self.note_hits(channel)
        ticks = [hit.tick for hit in hits]
        intervals = [
            later - earlier
            for earlier, later in zip(ticks, ticks[1:])
            if later > earlier
        ]
        gcd_value: Optional[int] = None
        for interval in intervals:
            gcd_value = interval if gcd_value is None else math.gcd(gcd_value, interval)
        velocities = [hit.velocity for hit in hits]
        notes = [hit.note for hit in hits]
        return TimeSliceReport(
            channel=channel,
            hit_count=len(hits),
            unique_notes=len(set(notes)),
            note_min=min(notes) if notes else None,
            note_max=max(notes) if notes else None,
            first_tick=min(ticks) if ticks else None,
            last_tick=max(ticks) if ticks else None,
            shortest_start_interval=min(intervals) if intervals else None,
            interval_gcd=gcd_value,
            velocity_min=min(velocities) if velocities else None,
            velocity_max=max(velocities) if velocities else None,
        )

    def bar_beat_tick(self, absolute_tick: int) -> str:
        """Format tick position using the active time signature when possible."""

        midi = self._require_midi()
        self._check_tick(absolute_tick)
        signatures: list[tuple[int, int, int]] = [(0, 4, 4)]
        for track in midi.tracks:
            tick = 0
            for message in track:
                tick += int(message.time)
                if message.is_meta and message.type == "time_signature":
                    signatures.append((tick, message.numerator, message.denominator))
        signatures.sort(key=lambda item: item[0])
        current_tick = 0
        bar = 1
        for index, (change_tick, numerator, denominator) in enumerate(signatures):
            if change_tick < current_tick:
                continue
            next_tick = (
                signatures[index + 1][0]
                if index + 1 < len(signatures)
                else absolute_tick
            )
            segment_end = min(absolute_tick, next_tick)
            if segment_end < change_tick:
                break
            ticks_per_beat = midi.ticks_per_beat * 4 // denominator
            ticks_per_bar = max(1, ticks_per_beat * numerator)
            if absolute_tick >= change_tick:
                elapsed = segment_end - change_tick
                if segment_end == absolute_tick:
                    bar_offset, in_bar = divmod(elapsed, ticks_per_bar)
                    beat_offset, remainder = divmod(in_bar, ticks_per_beat)
                    return f"{bar + bar_offset}:{beat_offset + 1}:{remainder}"
                bar += elapsed // ticks_per_bar
                current_tick = segment_end
        # Defensive fallback for malformed or unusual signatures.
        beat, remainder = divmod(absolute_tick, midi.ticks_per_beat)
        return f"{beat // 4 + 1}:{beat % 4 + 1}:{remainder}"

    def build_report(self) -> dict[str, Any]:
        midi = self._require_midi()
        markers = self.markers()
        assignments = self.program_assignments()
        notes = self.note_activity()
        return {
            "application": "KorgPaManager",
            "source_path": str(self.source_path) if self.source_path else None,
            "source_sha256": self.source_sha256,
            "last_output_path": str(self.last_output_path)
            if self.last_output_path
            else None,
            "midi": {
                "type": midi.type,
                "tracks": len(midi.tracks),
                "ticks_per_quarter": midi.ticks_per_beat,
                "length_ticks": self.total_length_ticks(),
                "message_count": _message_count(midi),
            },
            "channel_mapping": [
                {
                    "midi_channel": channel + 1,
                    "mido_channel": channel,
                    "role": role,
                }
                for channel, role in STYLE_CHANNELS.items()
            ],
            "channel_summary": [asdict(item) for item in self.channel_summaries()],
            "markers": [
                {
                    **asdict(item),
                    "ref": asdict(item.ref),
                    "bar_beat_tick": self.bar_beat_tick(item.tick),
                    "section_name": item.section_name,
                }
                for item in markers
            ],
            "program_assignments": [
                {**asdict(item), "ref": asdict(item.ref)} for item in assignments
            ],
            "drum_percussion_activity": [
                {
                    **asdict(item),
                    "sections": sorted(item.sections),
                    "assignments": sorted(item.assignments),
                    "note_name": midi_note_name(item.note),
                    "gm_reference": gm_reference_label(item.note),
                    "custom_label": self.get_custom_label(item.channel, item.note),
                }
                for item in notes
            ],
            "timeslice_note": (
                "Statistics report trigger evidence only. Standard MIDI does not prove "
                "that a sequence was generated by Korg Time Slice."
            ),
            "timeslice_reports": [
                asdict(self.timeslice_report(channel))
                for channel in sorted(DRUM_CHANNELS)
            ],
            "sound_mapping_note": (
                "CC00, CC32, and Program are preserved as exact 0..127 MIDI values. "
                "No Pa4X-to-Pa800 sound conversion is inferred."
            ),
            "drum_label_note": (
                "GM percussion labels are optional references, not proven Korg Drum Kit "
                "sample assignments. Actual sound depends on the selected kit, key, and "
                "velocity layer."
            ),
        }

    def export_report_json(self, path: str | os.PathLike[str]) -> Path:
        destination = Path(path).expanduser()
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(
                json.dumps(self.build_report(), indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except OSError as exc:
            raise MidiDocumentError(f"Could not write report: {exc}") from exc
        return destination

    def export_note_csv(self, path: str | os.PathLike[str]) -> Path:
        destination = Path(path).expanduser()
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            with destination.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.writer(handle)
                writer.writerow(
                    [
                        "midi_channel",
                        "role",
                        "note",
                        "note_name",
                        "gm_reference_only",
                        "custom_label",
                        "hit_count",
                        "velocity_min",
                        "velocity_max",
                        "first_tick",
                        "last_tick",
                        "effective_cc00_cc32_program",
                        "sections",
                    ]
                )
                for item in self.note_activity():
                    writer.writerow(
                        [
                            item.midi_channel,
                            STYLE_CHANNELS[item.channel],
                            item.note,
                            midi_note_name(item.note),
                            gm_reference_label(item.note),
                            self.get_custom_label(item.channel, item.note),
                            item.hit_count,
                            item.velocity_min,
                            item.velocity_max,
                            item.first_tick,
                            item.last_tick,
                            " | ".join(sorted(item.assignments)),
                            " | ".join(sorted(item.sections)),
                        ]
                    )
        except OSError as exc:
            raise MidiDocumentError(f"Could not write CSV: {exc}") from exc
        return destination

    def _labels_path_for(self, midi_path: Path) -> Path:
        return midi_path.with_name(midi_path.name + ".kpm-labels.json")

    def _load_labels_if_present(self) -> None:
        if self.source_path is None:
            return
        labels_path = self._labels_path_for(self.source_path)
        if not labels_path.is_file():
            return
        try:
            payload = json.loads(labels_path.read_text(encoding="utf-8"))
            if payload.get("schema") != LABEL_SCHEMA:
                return
            values = payload.get("labels", {})
            loaded: dict[tuple[int, int], str] = {}
            for key, label in values.items():
                channel_text, note_text = key.split(":", 1)
                channel = int(channel_text) - 1
                note = int(note_text)
                if 0 <= channel <= 15 and 0 <= note <= 127 and isinstance(label, str):
                    loaded[(channel, note)] = label
            self.custom_labels = loaded
        except (OSError, ValueError, TypeError, AttributeError):
            # A broken optional sidecar must never block loading the MIDI.
            self.custom_labels = {}

    def _write_labels(self, midi_path: Path) -> Optional[Path]:
        if not self.custom_labels:
            return None
        destination = self._labels_path_for(midi_path)
        payload = {
            "schema": LABEL_SCHEMA,
            "midi_file": midi_path.name,
            "source_sha256": self.source_sha256,
            "labels": {
                f"{channel + 1}:{note}": label
                for (channel, note), label in sorted(self.custom_labels.items())
            },
        }
        temporary: Optional[Path] = None
        try:
            descriptor, name = tempfile.mkstemp(
                prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent
            )
            temporary = Path(name)
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2, ensure_ascii=False)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, destination)
        except OSError as exc:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
            raise MidiDocumentError(
                f"MIDI saved, but labels sidecar failed: {exc}"
            ) from exc
        return destination

    def save_as(self, path: str | os.PathLike[str]) -> SaveResult:
        """Atomically save a validated copy; never overwrite the loaded source."""

        midi = self._require_midi()
        destination = Path(path).expanduser()
        if destination.suffix.lower() not in {".mid", ".midi"}:
            destination = destination.with_suffix(".mid")
        try:
            resolved_destination = destination.resolve()
            if (
                self.source_path is not None
                and resolved_destination == self.source_path
            ):
                raise MidiDocumentError(
                    "Refusing to overwrite the loaded source. Choose a new output name."
                )
            if (
                self.source_path is not None
                and destination.exists()
                and os.path.samefile(destination, self.source_path)
            ):
                raise MidiDocumentError(
                    "Refusing to overwrite the loaded source through an alias or link."
                )
            destination.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise MidiDocumentError(f"Could not prepare output path: {exc}") from exc

        intended_signature = _midi_signature(midi)
        temporary: Optional[Path] = None
        try:
            descriptor, name = tempfile.mkstemp(
                prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent
            )
            os.close(descriptor)
            temporary = Path(name)
            midi.save(str(temporary))
            # Windows' CRT rejects os.fsync() on a read-only descriptor with
            # EBADF (Errno 9). Reopen the completed temp file as writable;
            # bytes are not changed, but the handle is valid for a durable
            # flush on Windows, Linux, and macOS.
            with temporary.open("r+b") as handle:
                handle.flush()
                os.fsync(handle.fileno())
            reopened = MidiFile(str(temporary), clip=False)
            self._validate_midi(reopened)
            if _midi_signature(reopened) != intended_signature:
                raise MidiDocumentError(
                    "Validation failed: the saved event stream differs from memory"
                )
            os.replace(temporary, destination)
            temporary = None
        except MidiDocumentError:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
            raise
        except (OSError, EOFError, ValueError, TypeError) as exc:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
            raise MidiDocumentError(
                f"Could not save validated MIDI copy: {exc}"
            ) from exc
        except Exception as exc:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
            raise MidiDocumentError(f"Unexpected MIDI save failure: {exc}") from exc

        labels_path = self._write_labels(destination)
        self.last_output_path = destination.resolve()
        self.dirty = False
        return SaveResult(
            path=destination.resolve(),
            file_size=destination.stat().st_size,
            track_count=len(reopened.tracks),
            message_count=_message_count(reopened),
            labels_path=labels_path,
        )
