#!/usr/bin/env python3
"""KORG PA Song Optimizer — applies the shared optimization engine to MIDI/SMF files.

This module reads a Standard MIDI File (.mid) and applies the shared
optimization engine to it, producing an optimized MIDI file plus a JSON
report.

The Song Optimizer is the SMF/MIDI entry point for the same shared
optimization logic that the Style Optimizer uses for STY files. Both
optimizers produce compatible output that can be mixed and matched on a
PA800 arranger.
"""

from __future__ import annotations

import argparse
import json
import struct
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from korg_pa_optimizer import (
    NoteEvent, OptimizationSettings, optimize_song_or_style,
    classify_track_role, GENRE_TEMPLATES,
)


# ---------------------------------------------------------------------------
# SMF parser (Standard MIDI File)
# ---------------------------------------------------------------------------


def vlq(data: bytes, pos: int) -> tuple[int, int]:
    value = 0
    for _ in range(4):
        if pos >= len(data):
            raise ValueError("truncated VLQ")
        byte = data[pos]
        pos += 1
        value = (value << 7) | (byte & 0x7F)
        if not byte & 0x80:
            return value, pos
    raise ValueError("VLQ longer than four bytes")


def encode_vlq(value: int) -> bytes:
    if not 0 <= value <= 0x0FFFFFFF:
        raise ValueError("VLQ value out of range")
    result = bytearray([value & 0x7F])
    value >>= 7
    while value:
        result.insert(0, 0x80 | (value & 0x7F))
        value >>= 7
    return bytes(result)


def parse_smf(data: bytes) -> dict[str, Any]:
    """Parse a Standard MIDI File into tracks + metadata."""
    if data[:4] != b"MThd":
        raise ValueError("not a SMF (missing MThd)")
    header_len = struct.unpack(">I", data[4:8])[0]
    fmt, ntracks, division = struct.unpack(">HHH", data[8:14])
    if division & 0x8000:
        raise ValueError("SMPTE timing not supported")
    pos = 8 + header_len
    tracks: list[dict[str, Any]] = []
    for track_idx in range(ntracks):
        if data[pos:pos+4] != b"MTrk":
            raise ValueError(f"missing MTrk at offset {pos}")
        track_len = struct.unpack(">I", data[pos+4:pos+8])[0]
        track_data = data[pos+8:pos+8+track_len]
        tracks.append({
            "index": track_idx,
            "data": track_data,
            "events": [],
            "notes": [],
            "tempo": 500000,  # default 120 BPM
            "time_signature": (4, 4),
            "program_changes": {},
            "control_changes": {},
            "meta": {},
        })
        pos += 8 + track_len
    # Parse each track
    for track in tracks:
        _parse_track_events(track)
    return {
        "format": fmt,
        "ntracks": ntracks,
        "division": division,
        "tracks": tracks,
    }


def _parse_track_events(track: dict[str, Any]) -> None:
    data = track["data"]
    pos = 0
    tick = 0
    running: int | None = None
    notes_pending: dict[tuple[int, int], NoteEvent] = {}
    events: list[dict[str, Any]] = []
    while pos < len(data):
        delta, pos = vlq(data, pos)
        tick += delta
        if pos >= len(data):
            break
        lead = data[pos]
        if lead & 0x80:
            status = lead
            pos += 1
            if status < 0xF0:
                running = status
        elif running is not None:
            status = running
        else:
            raise ValueError(f"running status without prior status at {pos}")
        if status == 0xFF:
            meta_type = data[pos]
            pos += 1
            length, pos = vlq(data, pos)
            payload = data[pos:pos + length]
            pos += length
            events.append({"tick": tick, "kind": "meta", "type": meta_type, "data": payload})
            if meta_type == 0x51:  # tempo
                track["tempo"] = struct.unpack(">I", b'\x00' + payload[:3])[0]
            elif meta_type == 0x58:  # time signature
                if len(payload) >= 2:
                    track["time_signature"] = (payload[0], 1 << payload[1])
            elif meta_type == 0x2F:  # end of track
                track["meta"]["end_tick"] = tick
                break
        elif status in (0xF0, 0xF7):
            length, pos = vlq(data, pos)
            payload = data[pos:pos + length]
            pos += length
            events.append({"tick": tick, "kind": "sysex", "status": status, "data": payload})
        else:
            family = status & 0xF0
            length = 1 if family in (0xC0, 0xD0) else 2
            payload = data[pos:pos + length]
            pos += length
            channel = status & 0x0F
            events.append({
                "tick": tick, "kind": "channel", "status": status,
                "channel": channel, "data": payload,
            })
            if family == 0x90 and payload[1] > 0:  # note on
                note = NoteEvent(
                    tick=tick, channel=channel, note=payload[0],
                    velocity=payload[1], duration=0,
                )
                notes_pending[(channel, payload[0])] = note
            elif family == 0x80 or (family == 0x90 and payload[1] == 0):  # note off
                key = (channel, payload[0])
                if key in notes_pending:
                    note = notes_pending.pop(key)
                    note.duration = tick - note.tick
                    track["notes"].append(note)
            elif family == 0xC0:  # program change
                track["program_changes"][channel] = payload[0]
            elif family == 0xB0:  # control change
                cc_num = payload[0]
                cc_val = payload[1]
                track["control_changes"].setdefault(channel, {})[cc_num] = cc_val
    track["events"] = events


def build_track_metadata(parsed_smf: dict[str, Any]) -> dict[int, dict[str, Any]]:
    """Build track metadata dict for the optimizer."""
    metadata = {}
    for track_idx, track in enumerate(parsed_smf["tracks"]):
        for channel, program in track["program_changes"].items():
            cc = track["control_changes"].get(channel, {})
            metadata.setdefault(track_idx, {
                "channel": channel, "program": program,
                "volume": cc.get(7, 100), "pan": cc.get(10, 64),
                "fx_a_send": 0, "fx_b_send": 0,
                "fx_c_send": 25, "fx_d_send": 0,
            })
            # If we already have an entry for this track but a different
            # channel/program, skip; the optimizer handles per-track.
            if metadata[track_idx]["channel"] != channel:
                metadata[track_idx + 1000] = {
                    "channel": channel, "program": program,
                    "volume": cc.get(7, 100), "pan": cc.get(10, 64),
                    "fx_a_send": 0, "fx_b_send": 0,
                    "fx_c_send": 25, "fx_d_send": 0,
                }
    return metadata


def collect_notes_per_track(parsed_smf: dict[str, Any]) -> dict[int, list[NoteEvent]]:
    """Collect all notes organized by track index."""
    notes = {}
    for track_idx, track in enumerate(parsed_smf["tracks"]):
        # Tag each note with track_role based on its channel + program
        for channel, program in track["program_changes"].items():
            role = classify_track_role(channel, program)
            for n in track["notes"]:
                if n.channel == channel:
                    n.program = program
                    n.track_role = role
        notes[track_idx] = track["notes"]
    return notes


def get_tempo_and_time_signature(parsed_smf: dict[str, Any]) -> tuple[float, tuple[int, int]]:
    """Extract tempo (BPM) and time signature from the SMF."""
    for track in parsed_smf["tracks"]:
        if track["tempo"] != 500000 or track["time_signature"] != (4, 4):
            tempo_bpm = 60_000_000 / track["tempo"]
            return tempo_bpm, track["time_signature"]
    return 120.0, (4, 4)


def get_duration_seconds(parsed_smf: dict[str, Any], tempo: float) -> float:
    """Compute total duration in seconds from the highest tick."""
    max_tick = 0
    for track in parsed_smf["tracks"]:
        for ev in track["events"]:
            if ev["tick"] > max_tick:
                max_tick = ev["tick"]
        for note in track["notes"]:
            if note.tick + note.duration > max_tick:
                max_tick = note.tick + note.duration
    micros_per_tick = parsed_smf["tracks"][0]["tempo"] / parsed_smf["division"]
    return max_tick * micros_per_tick / 1_000_000


# ---------------------------------------------------------------------------
# SMF writer
# ---------------------------------------------------------------------------


def write_smf(parsed_smf: dict[str, Any]) -> bytes:
    """Re-encode a parsed SMF back to bytes."""
    out = bytearray()
    out.extend(b"MThd")
    out.extend((6).to_bytes(4, "big"))
    out.extend(parsed_smf["format"].to_bytes(2, "big"))
    out.extend(parsed_smf["ntracks"].to_bytes(2, "big"))
    out.extend(parsed_smf["division"].to_bytes(2, "big"))
    for track in parsed_smf["tracks"]:
        track_bytes = _encode_track(track)
        out.extend(b"MTrk")
        out.extend(len(track_bytes).to_bytes(4, "big"))
        out.extend(track_bytes)
    return bytes(out)


def _encode_track(track: dict[str, Any]) -> bytes:
    events = sorted(track["events"], key=lambda e: e["tick"])
    out = bytearray()
    last_tick = 0
    for ev in events:
        delta = ev["tick"] - last_tick
        last_tick = ev["tick"]
        out.extend(encode_vlq(delta))
        if ev["kind"] == "meta":
            out.append(0xFF)
            out.append(ev["type"])
            out.extend(encode_vlq(len(ev["data"])))
            out.extend(ev["data"])
        elif ev["kind"] == "sysex":
            out.append(ev["status"])
            out.extend(encode_vlq(len(ev["data"])))
            out.extend(ev["data"])
        else:  # channel
            out.append(ev["status"])
            out.extend(ev["data"])
    # End of track marker
    if not events or events[-1].get("kind") != "meta" or events[-1].get("type") != 0x2F:
        out.extend(encode_vlq(0))
        out.extend(b'\xFF\x2F\x00')
    return bytes(out)


# ---------------------------------------------------------------------------
# Song Optimizer public API
# ---------------------------------------------------------------------------


def optimize_midi_file(
    input_path: Path,
    output_path: Path | None = None,
    report_path: Path | None = None,
    settings: OptimizationSettings | None = None,
) -> dict[str, Any]:
    """Optimize a MIDI/SMF file using the shared optimizer engine."""
    if settings is None:
        settings = OptimizationSettings()
    data = Path(input_path).read_bytes()
    parsed = parse_smf(data)
    notes_per_track = collect_notes_per_track(parsed)
    track_metadata = build_track_metadata(parsed)
    tempo, ts = get_tempo_and_time_signature(parsed)
    duration = get_duration_seconds(parsed, tempo)
    report = optimize_song_or_style(
        notes_per_track, track_metadata, duration, tempo, ts, settings
    )
    # Apply optimized velocities back to the parsed SMF
    for track_idx, optimized_notes in notes_per_track.items():
        if track_idx >= len(parsed["tracks"]):
            continue
        track = parsed["tracks"][track_idx]
        # Replace note-on velocities
        note_map: dict[tuple[int, int], NoteEvent] = {(n.channel, n.note): n for n in track["notes"]}
        for opt_note in optimized_notes:
            key = (opt_note.channel, opt_note.note)
            if key in note_map and note_map[key].tick == opt_note.tick:
                note_map[key].velocity = opt_note.velocity
        # Update events: find note-on events and update their velocities
        for ev in track["events"]:
            if ev["kind"] == "channel" and (ev["status"] & 0xF0) == 0x90 and ev["data"][1] > 0:
                key = (ev["channel"], ev["data"][0])
                # Match by tick + channel + note
                for n in track["notes"]:
                    if n.channel == ev["channel"] and n.note == ev["data"][0] and n.tick == ev["tick"]:
                        ev["data"] = bytes([ev["data"][0], n.velocity])
                        break
    if output_path is not None:
        optimized_data = write_smf(parsed)
        Path(output_path).write_bytes(optimized_data)
    # Build report
    result = {
        "input": str(input_path),
        "output": str(output_path) if output_path else None,
        "settings": {
            "velocity_optimization": settings.velocity_optimization,
            "articulation_enabled": settings.articulation_enabled,
            "rx_dnc_enabled": settings.rx_dnc_enabled,
            "cross_delay_enabled": settings.cross_delay_enabled,
            "fx_routing_enabled": settings.fx_routing_enabled,
            "target_model": settings.target_model,
        },
        "profile": report.profile,
        "genre": report.genre,
        "track_profiles": report.track_profiles,
        "applied_optimizations": report.applied,
        "recommendations": report.recommendations,
        "warnings": report.warnings,
    }
    if report_path is not None:
        Path(report_path).write_text(json.dumps(result, indent=2, ensure_ascii=False))
    return result


def cli_main() -> int:
    parser = argparse.ArgumentParser(
        prog="korg_pa_song_optimizer",
        description="KORG PA Song Optimizer (shared engine with Style Optimizer)",
    )
    parser.add_argument("input", type=Path, help="Input MIDI/SMF file")
    parser.add_argument("-o", "--output", type=Path, help="Output optimized MIDI")
    parser.add_argument("--report", type=Path, help="JSON report path")
    parser.add_argument("--no-velocity", action="store_true", help="Disable velocity optimization")
    parser.add_argument("--no-articulation", action="store_true", help="Disable articulation engine")
    parser.add_argument("--no-rx-dnc", action="store_true", help="Disable RX/DNC remap")
    parser.add_argument("--cross-delay", action="store_true", help="Enable cross delay echo")
    parser.add_argument("--genre", choices=list(GENRE_TEMPLATES.keys()), help="Genre override")
    parser.add_argument("--target-model", default="PA800", help="Target KORG PA model")
    args = parser.parse_args()
    settings = OptimizationSettings(
        velocity_optimization=not args.no_velocity,
        articulation_enabled=not args.no_articulation,
        rx_dnc_enabled=not args.no_rx_dnc,
        cross_delay_enabled=args.cross_delay,
        genre_override=args.genre,
        target_model=args.target_model,
    )
    result = optimize_midi_file(args.input, args.output, args.report, settings)
    print(f"Genre: {result['genre']}")
    print(f"Tracks: {len(result['track_profiles'])}")
    print(f"Optimizations applied: {len(result['applied_optimizations'])}")
    if args.output:
        print(f"Output: {args.output}")
    if args.report:
        print(f"Report: {args.report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(cli_main())
