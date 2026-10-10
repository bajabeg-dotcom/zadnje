"""Execution -- apply the plan to a private copy of the MIDI.

Only operations from ``plan.SAFE_OPERATIONS`` reach this module.  Tick and
pitch of existing notes are never recomputed: velocity operations rewrite one
field, CC/bank/program operations insert messages, nothing else is touched.
"""
from __future__ import annotations

from typing import Dict, List, Tuple

from pa800_midi._mido import Message, MidiFile, MidiTrack, MetaMessage
from pa800_midi.data.categories import clamp
from pa800_midi.plan import Operation, TransformationPlan

#: Meta/ordering priority when rebuilding a track from absolute events --
#: same approach as the existing optimizer (core/processor.py).
ORDER_RANK = {
    "set_tempo": -20,
    "time_signature": -19,
    "key_signature": -18,
    "track_name": -17,
    "control_change": -5,
    "program_change": -4,
    "pitchwheel": -3,
    "note_off": -2,
    "note_on": -1,
}


def _absolute(track) -> List[Tuple[int, object]]:
    events = []
    tick = 0
    for msg in track:
        tick += msg.time
        events.append((tick, msg))
    return events


def _rebuild(events: List[Tuple[int, object]]) -> MidiTrack:
    ordered = sorted(events, key=lambda item: (item[0],
                                               ORDER_RANK.get(item[1].type, 0)))
    track = MidiTrack()
    previous = 0
    for tick, msg in ordered:
        delta = max(0, tick - previous)
        previous = tick
        track.append(msg.copy(time=delta))
    return track


def _first_tick_for_channel(events: List[Tuple[int, object]], channel: int) -> int:
    for tick, msg in events:
        if getattr(msg, "channel", None) == channel:
            return tick
    return 0


def _set_identity(track_events, channel: int, cc00: int, cc32: int,
                  program: int) -> int:
    """Insert bank select + program change for a channel. Returns insertions."""
    tick = _first_tick_for_channel(track_events, channel)
    track_events.append((tick, Message("control_change", channel=channel,
                                       control=0, value=cc00, time=0)))
    track_events.append((tick, Message("control_change", channel=channel,
                                       control=32, value=cc32, time=0)))
    track_events.append((tick, Message("program_change", channel=channel,
                                       program=program, time=0)))
    return 3


def _apply_velocity(track_events, channel: int, offset: int = 0,
                    scale: float = 1.0) -> int:
    changed = 0
    for index, (tick, msg) in enumerate(track_events):
        if msg.type != "note_on" or getattr(msg, "channel", None) != channel:
            continue
        if msg.velocity == 0:
            continue
        value = int(round(msg.velocity * scale)) + offset
        new_value = clamp(value, 1, 127)
        if new_value != msg.velocity:
            track_events[index] = (tick, msg.copy(velocity=new_value))
            changed += 1
    return changed


def _insert_cc(track_events, channel: int, control: int, value: int) -> int:
    tick = _first_tick_for_channel(track_events, channel)
    track_events.append((tick, Message("control_change", channel=channel,
                                       control=control, value=clamp(value, 0, 127),
                                       time=0)))
    return 1


def apply_plan(mid: MidiFile, plan: TransformationPlan) -> Dict[str, int]:
    """Apply every APPLIED operation in place (on a copy owned by the caller)."""
    counters: Dict[str, int] = {}
    by_track: Dict[int, List[Operation]] = {}
    for op in plan.operations:
        if op.status != "APPLIED":
            continue
        by_track.setdefault(op.track, []).append(op)

    for track_index, operations in by_track.items():
        if track_index < 0 or track_index >= len(mid.tracks):
            continue
        events = _absolute(mid.tracks[track_index])

        for op in operations:
            if op.operation in ("program_change", "rx_remap", "bank_select"):
                parts = str(op.new_value).split()
                cc00, cc32, program = (int(x) for x in parts[0].split("."))
                n = _set_identity(events, op.channel, cc00, cc32, program)
                counters[op.operation] = counters.get(op.operation, 0) + 1
                if n:
                    continue
            elif op.operation == "velocity_offset":
                try:
                    offset = int(str(op.new_value)) - int(str(op.old_value))
                except (TypeError, ValueError):
                    offset = 0
                if offset:
                    _apply_velocity(events, op.channel, offset=offset)
                    counters["velocity_offset"] = counters.get("velocity_offset", 0) + 1
            elif op.operation == "velocity_scale":
                try:
                    scale = float(op.new_value)
                except (TypeError, ValueError):
                    scale = 1.0
                if scale != 1.0:
                    _apply_velocity(events, op.channel, scale=scale)
                    counters["velocity_scale"] = counters.get("velocity_scale", 0) + 1
            elif op.operation == "cc_set":
                text = str(op.new_value)
                if "=" in text:
                    number, _, value = text.partition("=")
                    try:
                        _insert_cc(events, op.channel, int(number.strip()[2:]),
                                   int(value))
                        counters["cc_set"] = counters.get("cc_set", 0) + 1
                    except ValueError:
                        continue
            elif op.operation == "add_ghost_note":
                # only reachable with --allow-note-additions
                payload = op.new_value if isinstance(op.new_value, dict) else {}
                tick = int(payload.get("tick", 0))
                pitch = clamp(int(payload.get("note", 60)), 0, 127)
                velocity = clamp(int(payload.get("velocity", 30)), 1, 127)
                events.append((tick, Message("note_on", channel=op.channel,
                                             note=pitch, velocity=velocity, time=0)))
                events.append((tick + 30, Message("note_off", channel=op.channel,
                                                  note=pitch, velocity=0, time=0)))
                counters["add_ghost_note"] = counters.get("add_ghost_note", 0) + 1

        mid.tracks[track_index] = _rebuild(events)

    return counters


def apply_plan_to_file(source: str, plan: TransformationPlan,
                       destination: str) -> Dict[str, int]:
    mid = MidiFile(source)
    counters = apply_plan(mid, plan)
    mid.save(destination)
    return counters
