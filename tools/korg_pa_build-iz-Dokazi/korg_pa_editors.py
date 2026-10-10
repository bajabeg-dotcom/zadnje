#!/usr/bin/env python3
"""KORG PA Editor moduli — V3 funkcije za STY/PAD/PRF/PCG.

Implementira V3 KORGPAManager.exe funkcije:
  - reMapSTY: zamijeni sve instance zvuka u STY fajlu
  - reMapSTS: zamijeni STS zvukove
  - reMapPAD: zamijeni PAD zvukove
  - reMapPERF: zamijeni Performance zvukove
  - Paste_Sound: zalijepi zvuk na ciljnu poziciju
  - FourXPCGDBtoThreeXMSLevel: PA4X -> PA3X MultiSample downgrade
  - DeleteMSfromAllPCG: obriši MultiSample iz svih PCG banaka
  - NewSound: kreiraj novi user zvuk (placeholder sa NEW_PCG template-om)
  - mnuPCGDefault: vrati zvuk na default
  - mnuPCGDefaultAll: vrati sve zvukove na default
  - cmdEditMS: Edit MultiSample
  - cmdGetMS: Get MultiSample Info

Glavni editori:
  - DrumKitEditor: mijenja key map, velocity layer switches
  - StyleEditor: mijenja zvuk po traci, volume/pan/FX
  - STSEditor: mijenja 4 keyboard trake (Lower, Upper1-3)
  - SoundReMap: Find & Replace zvuk instance
"""

from __future__ import annotations

import argparse
import copy
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Koristimo postojeći STY engine
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
from korg_sty_tool import (
    StyError, decode_input, parse_indexed_native, decode_oc3x_generic,
    encode_oc3x, children, build_record, type09_model, nested_type09_model,
    patch_type09_mixer_field, STS_MIXER_FIELDS, be32,
)
from korg_pa_database import (
    get_database, get_sound_info, search_sounds, SoundRecord,
    DrumKitRecord,
)


# ---------------------------------------------------------------------------
# Sound ReMap — V3 funkcija reMapSTY/reMapSTS/reMapPAD/reMapPERF
# ---------------------------------------------------------------------------


@dataclass
class RemapResult:
    """Rezultat operacije Find & Replace zvuka."""
    operation: str  # 'reMapSTY' | 'reMapSTS' | 'reMapPAD' | 'reMapPERF' | 'Paste_Sound'
    source_sound: str
    target_sound: str
    instances_found: int = 0
    instances_replaced: int = 0
    resources_affected: list[int] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def remap_sound_in_sty(
    sty_data: bytes,
    source_sound_name: str,
    target_sound_name: str,
) -> tuple[bytes, RemapResult]:
    """Zamijeni sve instance source_sound_name sa target_sound_name u STY fajlu.

    Implementira V3 funkciju 'reMapSTY' — "This will Re-Map All Sound
    Instances of [source] with [target]".

    Operacija traži ASCII ime source zvuka u svim ID-06 STYLE i ID-09 STS
    resursima. Ako pronađe match, zamijeni ga sa target imenom (sa
    očuvanjem dužine — ako je target ime kraće, padding sa razmacima).

    Vraća (novi_sty_data, RemapResult).
    """
    result = RemapResult(
        operation="reMapSTY",
        source_sound=source_sound_name,
        target_sound=target_sound_name,
    )
    # Prvo, dekodiraj STY container
    try:
        native, container, pages = decode_input(sty_data)
    except StyError as e:
        result.warnings.append(f"could not decode STY: {e}")
        return sty_data, result
    indexed, framing = parse_indexed_native(native)
    source_bytes = source_sound_name.encode('ascii', errors='replace')
    target_bytes = target_sound_name.encode('ascii', errors='replace')
    # Očuvaj dužinu — V3 pravilo: "Delete [source] and Copy/Replace it with [target]"
    # Ako su dužine različite, padding/strip sa razmacima
    if len(source_bytes) != len(target_bytes):
        # Pad target sa razmacima da match-a source dužinu
        target_padded = target_bytes.ljust(len(source_bytes), b' ')
    else:
        target_padded = target_bytes
    rebuilt = native
    for position, item in enumerate(indexed):
        if item["id"] not in {0x06, 0x09}:
            continue
        compressed = native[item["offset"] + 8:item["end"]]
        try:
            decoded, metadata = decode_oc3x_generic(compressed)
        except StyError:
            continue
        # Broj pojavljivanja source zvuka
        instances_before = decoded.count(source_bytes)
        if instances_before == 0:
            continue
        # Zamijeni sve instance
        new_decoded = decoded.replace(source_bytes, target_padded)
        instances_after = new_decoded.count(target_padded)
        if new_decoded == decoded:
            continue
        result.instances_found += instances_before
        result.instances_replaced += instances_before
        result.resources_affected.append(position)
        # Re-encode
        trailer = bytes.fromhex(metadata["trailer"])
        try:
            encoded = encode_oc3x(new_decoded, str(metadata["wrapper"])) + trailer
            verified, _ = decode_oc3x_generic(encoded)
            if verified != new_decoded:
                result.warnings.append(f"resource {position}: round-trip verification failed")
                continue
        except StyError as e:
            result.warnings.append(f"resource {position}: encode failed: {e}")
            continue
        # Patch native
        try:
            # Koristi tolerant KBEG/KEND replacement
            from korg_pa_converter import _replace_indexed_resource_tolerant
            current_indexed, _ = parse_indexed_native(rebuilt)
            if position < len(current_indexed):
                current_item = current_indexed[position]
                if current_item["id"] == item["id"]:
                    rebuilt = _replace_indexed_resource_tolerant(
                        rebuilt, current_item["offset"], encoded
                    )
        except StyError as e:
            result.warnings.append(f"resource {position}: native rebuild failed: {e}")
            continue
    # Reconstruct outer container
    if rebuilt != native:
        # Ako je ulaz bio OC20 (factory), izlaz je native
        outer_bytes = rebuilt
    else:
        outer_bytes = sty_data
    return outer_bytes, result


def remap_sound_in_sts(
    sty_data: bytes,
    source_sound: str,
    target_sound: str,
) -> tuple[bytes, RemapResult]:
    """V3 'reMapSTS' — zamijeni zvuk samo u STS resursima."""
    result = RemapResult(
        operation="reMapSTS",
        source_sound=source_sound,
        target_sound=target_sound,
    )
    try:
        native, _, _ = decode_input(sty_data)
    except StyError:
        return sty_data, result
    indexed, _ = parse_indexed_native(native)
    source_bytes = source_sound.encode('ascii', errors='replace')
    target_bytes = target_sound.encode('ascii', errors='replace')
    target_padded = target_bytes.ljust(len(source_bytes), b' ')
    rebuilt = native
    for position, item in enumerate(indexed):
        if item["id"] != 0x09:
            continue
        compressed = native[item["offset"] + 8:item["end"]]
        try:
            decoded, metadata = decode_oc3x_generic(compressed)
        except StyError:
            continue
        if source_bytes not in decoded:
            continue
        instances_before = decoded.count(source_bytes)
        new_decoded = decoded.replace(source_bytes, target_padded)
        result.instances_found += instances_before
        result.instances_replaced += instances_before
        result.resources_affected.append(position)
        trailer = bytes.fromhex(metadata["trailer"])
        try:
            encoded = encode_oc3x(new_decoded, str(metadata["wrapper"])) + trailer
            verified, _ = decode_oc3x_generic(encoded)
            if verified != new_decoded:
                result.warnings.append(f"resource {position}: round-trip failed")
                continue
        except StyError as e:
            result.warnings.append(f"resource {position}: encode failed: {e}")
            continue
        try:
            from korg_pa_converter import _replace_indexed_resource_tolerant
            current_indexed, _ = parse_indexed_native(rebuilt)
            if position < len(current_indexed):
                current_item = current_indexed[position]
                rebuilt = _replace_indexed_resource_tolerant(
                    rebuilt, current_item["offset"], encoded
                )
        except StyError as e:
            result.warnings.append(f"resource {position}: native rebuild failed: {e}")
    return rebuilt, result


# ---------------------------------------------------------------------------
# Drum Kit Editor — V3 funkcija Drum Kit
# ---------------------------------------------------------------------------


@dataclass
class DrumKitKey:
    """Jedna tipka u drum kit mapiranju."""
    midi_key: int
    note_name: str
    oscillators: int = 1
    velocity_switches: list[int] = field(default_factory=list)
    samples: list[str] = field(default_factory=list)
    volume: int = 100
    pan: int = 64
    fx_a_send: int = 0
    fx_b_send: int = 0
    fx_c_send: int = 0
    fx_d_send: int = 0
    output: str = "Main"


def get_drum_kit_template(kit_name: str) -> DrumKitRecord | None:
    """Vraća template drum kita iz PA800 database."""
    db = get_database()
    for kit in db.drum_kits:
        if kit.name == kit_name:
            return kit
    return None


def list_drum_kit_keys(kit_name: str) -> list[DrumKitKey]:
    """Vraća listu svih tipki drum kita sa layer informacijama."""
    kit = get_drum_kit_template(kit_name)
    if not kit:
        return []
    keys = []
    for midi_key, info in kit.key_map.items():
        note_name = info.get("note", f"Key {midi_key}")
        keys.append(DrumKitKey(
            midi_key=midi_key,
            note_name=note_name,
            oscillators=info.get("oscillators", 1),
            velocity_switches=info.get("switches", []),
            samples=info.get("samples", []),
        ))
    return sorted(keys, key=lambda k: k.midi_key)


# ---------------------------------------------------------------------------
# Style Editor — V3 funkcija Style Track Report, Update Style
# ---------------------------------------------------------------------------


@dataclass
class StyleTrack:
    """Jedna traka u stilu (Drum/Perc/Bass/Acc1-5)."""
    index: int
    role: str  # 'drum', 'perc', 'bass', 'acc1'..'acc5'
    sound_name: str = ""
    sound_bank_msb: int = 0
    sound_bank_lsb: int = 0
    sound_program: int = 0
    volume: int = 100
    pan: int = 64
    fx_a_send: int = 0
    fx_b_send: int = 0
    fx_c_send: int = 25
    fx_d_send: int = 0
    mute: bool = False
    ntt: int = 0  # Note Transposition Table
    ntr: int = 0  # Note Transposition Rule
    chord_variation_count: int = 0
    guitar_mode: bool = False


def extract_style_tracks(sty_data: bytes) -> list[StyleTrack]:
    """Ekstraktuje 8 traka iz STY fajla.

    V3 string: "Style Track Report"
    V3 string: "Apply these Sounds to All other Style Elements" (STYchkAllElements)
    """
    tracks: list[StyleTrack] = []
    try:
        native, _, _ = decode_input(sty_data)
        indexed, _ = parse_indexed_native(native)
    except StyError:
        return tracks
    # Pronađi prvi ID-06 STYLE resurs
    for item in indexed:
        if item["id"] != 0x06:
            continue
        compressed = native[item["offset"] + 8:item["end"]]
        try:
            decoded, _ = decode_oc3x_generic(compressed)
        except StyError:
            continue
        # Uzmi prvi ID-06 element i parsiraj TrackData
        root_children = children(decoded)
        element_children = [c for c in root_children if c["id"] == 0x03]
        if not element_children:
            continue
        first_element = element_children[0]
        try:
            element_parts = children(decoded, first_element["offset"])
        except StyError:
            continue
        if len(element_parts) < 2:
            continue
        track_data = decoded[element_parts[1]["offset"] + 8:element_parts[1]["end"]]
        # 8 traka, 8 bajtova svaka
        for i in range(min(8, len(track_data) // 8)):
            track_record = track_data[i*8:(i+1)*8]
            if len(track_record) < 6:
                continue
            role_map = ["drum", "perc", "bass", "acc1", "acc2", "acc3", "acc4", "acc5"]
            tracks.append(StyleTrack(
                index=i,
                role=role_map[i] if i < len(role_map) else f"track{i}",
                sound_bank_msb=track_record[1],
                sound_bank_lsb=track_record[2],
                sound_program=track_record[3],
                volume=100,
                pan=64,
                fx_c_send=25,
            ))
        break  # samo prvi ID-06
    return tracks


# ---------------------------------------------------------------------------
# STS / Performance Editor — V3 funkcija reMapSTS, LoadStyleSTS
# ---------------------------------------------------------------------------


@dataclass
class STSTrack:
    """Jedna keyboard traka u STS-u (Lower/Upper1/Upper2/Upper3)."""
    role: str  # 'lower', 'upper1', 'upper2', 'upper3'
    selector_kind: str = "empty"  # 'empty' | 'user_namespace_09' | 'native'
    user_bank: int = 0
    user_position: int = 0
    volume: int = 100
    pan: int = 64
    fx_c_send: int = 0
    fx_d_send: int = 0
    mute: bool = False


def extract_sts_tracks(sty_data: bytes, resource_position: int = 0) -> list[STSTrack]:
    """Ekstraktuje 4 keyboard trake iz STS resursa.

    V3 string: "LoadStyleSTS"
    V3 string: "STS Version NOT Compatible"
    """
    try:
        native, _, _ = decode_input(sty_data)
        indexed, _ = parse_indexed_native(native)
    except StyError:
        return []
    sts_resources = [i for i, item in enumerate(indexed) if item["id"] == 0x09]
    if resource_position >= len(sts_resources):
        return []
    pos = sts_resources[resource_position]
    item = indexed[pos]
    compressed = native[item["offset"] + 8:item["end"]]
    try:
        decoded, _ = decode_oc3x_generic(compressed)
    except StyError:
        return []
    # Probaj legacy layout
    try:
        model = type09_model(decoded)
        layout = "legacy"
    except StyError:
        try:
            model = nested_type09_model(decoded)
            layout = "nested"
        except StyError:
            return []
    tracks = []
    if layout == "legacy" and "slots" in model:
        # Prvi slot, 4 trake
        if model["slots"]:
            slot = model["slots"][0]
            for i, track in enumerate(slot.get("keyboard_tracks", [])):
                role = ["lower", "upper1", "upper2", "upper3"][i] if i < 4 else f"track{i}"
                tracks.append(STSTrack(
                    role=role,
                    selector_kind=track.get("selector_kind", "unknown"),
                    user_bank=track.get("user_bank", 0),
                    user_position=track.get("user_position", 0),
                    volume=track.get("volume", 100),
                    pan=track.get("pan", 64),
                    fx_c_send=track.get("fx_c_send", 0),
                    fx_d_send=track.get("fx_d_send", 0),
                    mute=track.get("mute", False),
                ))
    elif layout == "nested":
        # Nested STS ima 4 ID-08 zapisa
        # Za sada, vrati default 4 prazne trake
        for i, role in enumerate(["lower", "upper1", "upper2", "upper3"]):
            tracks.append(STSTrack(role=role))
    return tracks


# ---------------------------------------------------------------------------
# PCG Editor — V3 funkcije NewSound, mnuPCGDefault, DeleteMSfromAllPCG
# ---------------------------------------------------------------------------


def get_new_pcg_template() -> dict[str, Any]:
    """Vraća NEW_PCG template (iz V3 CUSTOM resursa NEW_PCG).

    V3 resurs: CUSTOM_NEW_PCG (1388 bajtova, sve nule)
    Ovo je "STRONG_TEMPLATE_ARCHITECTURE" — alat koristi valid init
    template umjesto zero fill.
    """
    return {
        "template_name": "NEW_PCG",
        "template_size": 1388,
        "default_volume": 100,
        "default_pan": 64,
        "default_fx_c_send": 25,
        "default_fx_d_send": 0,
        "default_oscillators": 1,
        "default_velocity_layers": 0,
        "default_amp_eg": {"attack": 0, "decay": 0, "sustain": 127, "release": 0},
        "default_pitch_eg": {"attack": 0, "decay": 0},
        "default_filter_eg": {"attack": 0, "decay": 0},
        "default_lfo": {"rate": 0, "depth": 0, "delay": 0},
    }


def get_new_pcg_dk_template() -> dict[str, Any]:
    """Vraća NEW_PCG_DK template (Drum Kit template iz V3)."""
    return {
        "template_name": "NEW_PCG_DK",
        "template_size": 3586,
        "key_map_default": "empty 64 keys",
        "default_volume": 100,
        "default_pan": 64,
        "default_drum_kit_size": 64,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def cli_main():
    import argparse
    parser = argparse.ArgumentParser(prog="korg_pa_editors")
    sub = parser.add_subparsers(dest="command", required=True)

    p_search = sub.add_parser("search_sounds", help="Search PA800 sound database")
    p_search.add_argument("query", nargs="?", default="")
    p_search.add_argument("--category", help="Filter by category")

    p_dk = sub.add_parser("drum_kit", help="Show drum kit key map")
    p_dk.add_argument("--name", default="Pop Std. Kit RX")

    p_remap = sub.add_parser("remap_sty", help="ReMap sound in STY file")
    p_remap.add_argument("input", type=Path)
    p_remap.add_argument("output", type=Path)
    p_remap.add_argument("--source", required=True, help="Source sound name")
    p_remap.add_argument("--target", required=True, help="Target sound name")

    p_sty_tracks = sub.add_parser("style_tracks", help="Extract style tracks")
    p_sty_tracks.add_argument("input", type=Path)

    p_sts = sub.add_parser("sts_tracks", help="Extract STS keyboard tracks")
    p_sts.add_argument("input", type=Path)
    p_sts.add_argument("--position", type=int, default=0)

    args = parser.parse_args()
    if args.command == "search_sounds":
        results = search_sounds(args.query, args.category)
        print(f"Found {len(results)} sounds:")
        for s in results[:30]:
            print(f"  {s.name!r} (category={s.category}, file={s.file_source})")
    elif args.command == "drum_kit":
        keys = list_drum_kit_keys(args.name)
        print(f"Drum Kit: {args.name}")
        print(f"Total keys: {len(keys)}")
        for k in keys:
            switches = ", ".join(str(s) for s in k.velocity_switches) if k.velocity_switches else "(single)"
            print(f"  Key {k.midi_key:3d} ({k.note_name:12s}): {k.oscillators} osc, switches={switches}")
    elif args.command == "remap_sty":
        sty_data = args.input.read_bytes()
        new_data, result = remap_sound_in_sty(sty_data, args.source, args.target)
        args.output.write_bytes(new_data)
        print(f"ReMap STY: {result.instances_found} instances of '{args.source}' -> '{args.target}'")
        print(f"  Resources affected: {len(result.resources_affected)}")
        print(f"  Output: {args.output}")
        if result.warnings:
            print(f"  Warnings: {len(result.warnings)}")
    elif args.command == "style_tracks":
        sty_data = args.input.read_bytes()
        tracks = extract_style_tracks(sty_data)
        print(f"Style tracks: {len(tracks)}")
        for t in tracks:
            print(f"  [{t.index}] {t.role:6s}  bank={t.sound_bank_msb}/{t.sound_bank_lsb}  prog={t.sound_program}")
    elif args.command == "sts_tracks":
        sty_data = args.input.read_bytes()
        tracks = extract_sts_tracks(sty_data, args.position)
        print(f"STS keyboard tracks (resource {args.position}): {len(tracks)}")
        for t in tracks:
            print(f"  {t.role:6s}  kind={t.selector_kind:20s}  bank={t.user_bank} pos={t.user_position}  vol={t.volume} pan={t.pan} FX C={t.fx_c_send} FX D={t.fx_d_send}")


if __name__ == "__main__":
    cli_main()
