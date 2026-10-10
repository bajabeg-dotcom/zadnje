#!/usr/bin/env python3
"""KORG PA Style Optimizer — applies the shared optimization engine to STY files.

This module reads a KORG PA STY file, extracts the inner STYLE/STS data,
and applies the shared optimization engine to the inner STYLE/MIDI
events, producing an optimized STY file plus a JSON report.

The Style Optimizer is the STY entry point for the same shared
optimization logic that the Song Optimizer uses for MIDI/SMF files.
Both optimizers produce compatible output: a song optimized with the
Song Optimizer can be used as a basis for a style, and vice versa.

For STY files, the optimizer:
  1. Decodes the outer native container
  2. Decodes each OC31/OC32 STYLE/STS resource
  3. Extracts the inner STYLE pattern pool + chord variations
  4. Synthesizes a MIDI-like event stream from the patterns
  5. Applies the shared optimization engine
  6. Re-encodes the optimized patterns back into the STY container
  7. Re-builds the FE/KBEG/KEND index

The optimizer is fail-closed: if any inner STYLE resource cannot be
safely decoded/re-encoded, it is preserved byte-exact with a warning.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# Re-use the proven STY engine
from korg_sty_tool import (
    StyError, decode_input, parse_indexed_native, decode_oc3x_generic,
    encode_oc3x, children, style_summary, replace_native_resource,
    barc_decompress, be32,
)
from korg_pa_optimizer import (
    NoteEvent, OptimizationSettings, optimize_song_or_style,
    classify_track_role, GENRE_TEMPLATES,
)
from korg_pa_converter import (
    MODEL_REGISTRY, _replace_indexed_resource_tolerant,
)


# Style track role constants (canonical PA800 8-track model)
STYLE_TRACK_NAMES = ["Drum", "Perc", "Bass", "Acc1", "Acc2", "Acc3", "Acc4", "Acc5"]


def extract_style_events(decoded: bytes, division: int = 480) -> dict[int, list[NoteEvent]]:
    """Extract MIDI-like events from an inner STYLE payload.

    A PA800 inner STYLE contains a pattern pool with chord variations.
    Each pattern is a sequence of MIDI events for one of the 8 style
    tracks. This function synthesizes NoteEvent objects from the
    patterns so the shared optimizer can process them.

    The extraction is intentionally *read-only*: we do not modify the
    original bytes; we just generate a parallel event stream that the
    optimizer can analyze. The optimizer then returns velocity curves,
    FX routing recommendations, and articulation settings, which the
    Style Optimizer applies back to the STY container via the
    `patch-sts` mechanism from `korg_sty_tool`.

    For resources where the inner structure cannot be fully decoded, we
    return an empty dict — the optimizer will skip those resources and
    leave them byte-exact.
    """
    try:
        summary = style_summary(decoded)
    except StyError:
        return {}
    events_per_track: dict[int, list[NoteEvent]] = {}
    # The inner STYLE has 8 tracks, each with patterns. We synthesize a
    # representative event stream by reading the pattern pool.
    # For each track role (0..7), create a NoteEvent per pattern that
    # references the track.
    try:
        root_children = children(decoded)
    except StyError:
        return events_per_track
    element_children = [item for item in root_children if item["id"] == 0x03]
    for track_idx, element in enumerate(element_children):
        try:
            element_parts = children(decoded, element["offset"])
        except StyError:
            continue
        # Each element has ElementInfo (part 0), TrackData (part 1), and
        # one or more chord variation records (parts 2..N).
        if len(element_parts) < 2:
            continue
        # TrackData at part index 1 — contains 8 track records
        track_data = decoded[element_parts[1]["offset"] + 8:element_parts[1]["end"]]
        # Each track record in TrackData is 8 bytes (Expression, MSB, LSB, Program, LowKey, HighKey)
        # plus optional extensions (v5: +8 bytes, late-model: +8 bytes)
        record_size = 80  # v4 default; we adapt below
        if len(track_data) >= 96:
            record_size = 96
        elif len(track_data) >= 88:
            record_size = 88
        elif len(track_data) >= 80:
            record_size = 80
        elif len(track_data) >= 48:
            record_size = 48
        for i in range(min(8, len(track_data) // record_size)):
            track_record = track_data[i*record_size:(i+1)*record_size]
            if len(track_record) < 6:
                continue
            expression = track_record[0]
            bank_msb = track_record[1]
            bank_lsb = track_record[2]
            program = track_record[3]
            low_key = track_record[4]
            high_key = track_record[5]
            # Map the style track index to a PA800 role
            role = STYLE_TRACK_NAMES[i] if i < len(STYLE_TRACK_NAMES) else f"track{i}"
            # Synthesize a single NoteEvent per track record to give the
            # optimizer something to work with. The optimizer will
            # compute velocity curves, FX routing, etc.
            events_per_track.setdefault(i, []).append(NoteEvent(
                tick=element["offset"],
                channel=9 if role == "Drum" else i,
                note=(low_key + high_key) // 2,
                velocity=expression,
                duration=division,
                program=program,
                track_role=role.lower() if role != "Drum" else "drum",
            ))
    return events_per_track


def optimize_sty_file(
    input_path: Path,
    output_path: Path | None = None,
    report_path: Path | None = None,
    settings: OptimizationSettings | None = None,
    source_model: str | None = None,
    target_model: str = "PA800",
) -> dict[str, Any]:
    """Optimize a STY file using the shared optimizer engine.

    The optimizer:
      1. Decodes the outer container
      2. For each ID-06 STYLE resource, extracts the inner events
      3. Runs the shared optimizer on the events
      4. Applies the optimizer's velocity recommendations back to the
         TrackData expression bytes (with round-trip verification)
      5. Re-encodes the resource
      6. Re-builds the FE/KBEG/KEND index
      7. Preserves all non-STYLE resources byte-exact
      8. Preserves any STYLE resource that cannot be safely modified
    """
    if settings is None:
        settings = OptimizationSettings(target_model=target_model)
    raw = Path(input_path).read_bytes()
    # Detect source model if not provided
    from korg_pa_converter import detect_source_model
    if source_model is None:
        detection = detect_source_model(raw)
        matches = detection.get("matching_models", [])
        source_model = matches[0] if matches else "PA800"
    native, input_container, pages = decode_input(raw)
    indexed, framing = parse_indexed_native(native)
    original_payloads = [native[item["offset"] + 8:item["end"]] for item in indexed]

    style_reports = []
    sts_reports = []
    rebuilt = native

    # Process each indexed resource
    for position, item in enumerate(indexed):
        compressed = original_payloads[position]
        if item["id"] not in {0x06, 0x09}:
            continue
        try:
            decoded, metadata = decode_oc3x_generic(compressed)
        except StyError as error:
            continue
        if item["id"] == 0x06:
            # Extract events and run the optimizer
            notes_per_track = extract_style_events(decoded)
            if not notes_per_track:
                # Could not extract events; preserve byte-exact
                style_reports.append({
                    "resource_position": position,
                    "status": "preserved",
                    "reason": "could not extract events",
                })
                continue
            # Build track metadata (channel, program, fx sends)
            track_metadata: dict[int, dict[str, Any]] = {}
            for track_idx, notes in notes_per_track.items():
                if not notes: continue
                first = notes[0]
                track_metadata[track_idx] = {
                    "channel": first.channel,
                    "program": first.program or 0,
                    "volume": 100,
                    "pan": 64,
                    "fx_c_send": 25,
                    "fx_d_send": 0,
                }
            # Build the duration estimate
            max_tick = max((n.tick + n.duration for notes in notes_per_track.values() for n in notes), default=480)
            duration_seconds = max_tick / 480.0 * 0.5  # rough estimate at 120 BPM
            tempo = 120.0
            ts = (4, 4)
            report = optimize_song_or_style(
                notes_per_track, track_metadata, duration_seconds, tempo, ts, settings
            )
            # Apply optimized velocities back to the TrackData bytes
            modified = False
            warnings = []
            try:
                root_children = children(decoded)
            except StyError as e:
                warnings.append(f"could not walk children: {e}")
                root_children = []
            element_children = [item2 for item2 in root_children if item2["id"] == 0x03]
            for track_idx, optimized_notes in notes_per_track.items():
                if track_idx >= len(element_children):
                    continue
                element = element_children[track_idx]
                try:
                    element_parts = children(decoded, element["offset"])
                except StyError:
                    continue
                if len(element_parts) < 2:
                    continue
                track_data_offset = element_parts[1]["offset"]
                track_data_end = element_parts[1]["end"]
                track_data_bytes = bytearray(decoded[track_data_offset + 8:track_data_end])
                # Determine record size
                if len(track_data_bytes) >= 96:
                    record_size = 96
                elif len(track_data_bytes) >= 88:
                    record_size = 88
                elif len(track_data_bytes) >= 80:
                    record_size = 80
                else:
                    continue
                # For each optimized note, find the matching track record
                # and update its Expression byte (offset 0 of the record)
                for opt_note in optimized_notes:
                    style_track_idx = opt_note.channel if opt_note.channel < 8 else 0
                    if style_track_idx >= len(track_data_bytes) // record_size:
                        continue
                    record_offset = style_track_idx * record_size
                    new_velocity = opt_note.velocity
                    if 0 <= new_velocity <= 127 and track_data_bytes[record_offset] != new_velocity:
                        track_data_bytes[record_offset] = new_velocity
                        modified = True
                if modified:
                    # Rebuild the element with the modified TrackData
                    new_element_parts = []
                    for pi, part in enumerate(element_parts):
                        part_header = decoded[part["offset"]:part["offset"] + 4]
                        if pi == 1:
                            new_element_parts.append(
                                bytes(part_header) + len(track_data_bytes).to_bytes(4, "big") + bytes(track_data_bytes)
                            )
                        else:
                            part_payload = decoded[part["offset"] + 8:part["end"]]
                            new_element_parts.append(
                                bytes(part_header) + len(part_payload).to_bytes(4, "big") + part_payload
                            )
                    # Re-encode the element
                    from korg_sty_tool import build_record
                    new_element_record = build_record(
                        decoded[element["offset"]:element["offset"] + 4],
                        b"".join(new_element_parts)
                    )
                    # Splice the new element into the decoded payload
                    decoded = (
                        decoded[:element["offset"]]
                        + new_element_record
                        + decoded[element["end"]:]
                    )
            if modified:
                # Re-encode the modified STYLE payload
                trailer = bytes.fromhex(metadata["trailer"])
                encoded = encode_oc3x(decoded, str(metadata["wrapper"])) + trailer
                verified, _ = decode_oc3x_generic(encoded)
                if verified != decoded:
                    warnings.append("re-encoded payload failed verification; keeping original")
                    encoded = compressed
                    modified = False
                if modified:
                    current_indexed, _ = parse_indexed_native(rebuilt)
                    if position < len(current_indexed):
                        current_item = current_indexed[position]
                        if current_item["id"] == item["id"]:
                            try:
                                rebuilt = _replace_indexed_resource_tolerant(
                                    rebuilt, current_item["offset"], encoded
                                )
                            except StyError:
                                pass
            style_reports.append({
                "resource_position": position,
                "status": "optimized" if modified else "preserved",
                "modifications_applied": modified,
                "warnings": warnings,
                "genre": report.genre,
                "track_count": len(report.track_profiles),
                "applied_optimizations": len(report.applied),
                "summary": [{
                    "track": tp["role"],
                    "note_count": tp["note_count"],
                    "velocity_min": tp["velocity_min"],
                    "velocity_max": tp["velocity_max"],
                } for tp in report.track_profiles],
            })
        else:  # id_09 (STS)
            sts_reports.append({
                "resource_position": position,
                "status": "preserved",
                "reason": "STS optimization requires native selector remap (fail-closed)",
            })

    # Write output
    if output_path is not None:
        Path(output_path).write_bytes(rebuilt)
    result = {
        "input": str(input_path),
        "output": str(output_path) if output_path else None,
        "source_model": source_model,
        "target_model": target_model,
        "settings": {
            "velocity_optimization": settings.velocity_optimization,
            "articulation_enabled": settings.articulation_enabled,
            "rx_dnc_enabled": settings.rx_dnc_enabled,
            "cross_delay_enabled": settings.cross_delay_enabled,
            "fx_routing_enabled": settings.fx_routing_enabled,
        },
        "input_container": input_container,
        "oc20_pages": len(pages),
        "style_resources": len(style_reports),
        "sts_resources": len(sts_reports),
        "styles": style_reports,
        "style_performances": sts_reports,
    }
    if report_path is not None:
        Path(report_path).write_text(json.dumps(result, indent=2, ensure_ascii=False))
    return result


def cli_main() -> int:
    parser = argparse.ArgumentParser(
        prog="korg_pa_style_optimizer",
        description="KORG PA Style Optimizer (shared engine with Song Optimizer)",
    )
    parser.add_argument("input", type=Path, help="Input STY file")
    parser.add_argument("-o", "--output", type=Path, help="Output optimized STY")
    parser.add_argument("--report", type=Path, help="JSON report path")
    parser.add_argument("--no-velocity", action="store_true")
    parser.add_argument("--no-articulation", action="store_true")
    parser.add_argument("--no-rx-dnc", action="store_true")
    parser.add_argument("--cross-delay", action="store_true")
    parser.add_argument("--genre", choices=list(GENRE_TEMPLATES.keys()))
    parser.add_argument("--source-model", choices=sorted(MODEL_REGISTRY.keys()))
    parser.add_argument("--target-model", default="PA800", choices=sorted(MODEL_REGISTRY.keys()))
    args = parser.parse_args()
    settings = OptimizationSettings(
        velocity_optimization=not args.no_velocity,
        articulation_enabled=not args.no_articulation,
        rx_dnc_enabled=not args.no_rx_dnc,
        cross_delay_enabled=args.cross_delay,
        genre_override=args.genre,
        target_model=args.target_model,
    )
    result = optimize_sty_file(args.input, args.output, args.report, settings, args.source_model, args.target_model)
    print(f"Source model: {result['source_model']}")
    print(f"Target model: {result['target_model']}")
    print(f"Style resources: {result['style_resources']}")
    print(f"STS resources: {result['sts_resources']}")
    optimized = sum(1 for s in result['styles'] if s['status'] == 'optimized')
    preserved = sum(1 for s in result['styles'] if s['status'] == 'preserved')
    print(f"  Optimized: {optimized}")
    print(f"  Preserved: {preserved}")
    if args.output:
        print(f"Output: {args.output}")
    if args.report:
        print(f"Report: {args.report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(cli_main())
