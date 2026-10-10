#!/usr/bin/env python3
"""Inspect, validate, and losslessly repack PA800-style STY containers."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any


PAGE_SIZE = 0x40000
RESOURCE_IDS = {0x06, 0x09}
INDEXED_IDS = {0x05, 0x06, 0x09}
TRACK_ROLES = ("drum", "perc", "bass", "acc1", "acc2", "acc3", "acc4", "acc5")
PATTERN_TAG_CLASSES = {0x02: 0, 0x03: 1, 0x04: 2, 0x05: 3}
V5_EXTENSION_DEFAULT = 4
TRACK_DATA_96_EXTENSION_DEFAULT = 0
ELEMENT_INFO_V5_EXTENSION = bytes.fromhex(
    "00 00 0A 00 00 00 00 00 00 00 00 00 00 00 00 01"
)
STYLE_INFO_V4_TAIL_SIZE = 16
STYLE_INFO_V5_TAIL_SIZE = 18


class StyError(ValueError):
    pass


def be32(data: bytes, offset: int) -> int:
    if offset < 0 or offset + 4 > len(data):
        raise StyError(f"BE32 outside buffer at {offset:#x}")
    return int.from_bytes(data[offset:offset + 4], "big")


def xor_checksum(data: bytes) -> int:
    checksum = 0
    for value in data:
        checksum ^= value
    return checksum


def barc_decompress(stream: bytes, capacity: int, require_terminator: bool) -> tuple[bytes, int]:
    source = 0
    output = bytearray()
    terminated = False

    def take_byte() -> int:
        nonlocal source
        if source >= len(stream):
            raise StyError("truncated CBarc byte")
        value = stream[source]
        source += 1
        return value

    def take_be16() -> int:
        return (take_byte() << 8) | take_byte()

    def reserve(count: int) -> None:
        if count < 0 or len(output) + count > capacity:
            raise StyError("CBarc output exceeds declared size")

    while source < len(stream) and len(output) < capacity:
        control = take_byte()
        if control < 16:
            if control >= 2:
                count = control + 2
            elif control == 0:
                count = take_byte() + 18
            else:
                count = take_be16()
                if count == 0:
                    terminated = True
                    break
            reserve(count)
            if source + count > len(stream):
                raise StyError("truncated CBarc literal")
            output.extend(stream[source:source + count])
            source += count
            continue

        if control >= 64:
            packed = (control << 8) | take_byte()
            count = (packed >> 13) + 1
            match = len(output) - ((packed >> 2) & 0x7FF) - 1
        elif control >= 32:
            count = control & 0x1F
            if count >= 2:
                count += 2
            elif count == 0:
                count = take_byte() + 34
            else:
                count = take_be16()
            match = len(output) - (take_be16() >> 2) - 1
        else:
            count = control & 0x07
            if count >= 2:
                count += 2
            elif count == 0:
                count = take_byte() + 10
            else:
                count = take_be16()
            match = len(output) - (take_be16() >> 2) - 16385
            if control & 0x08:
                match -= 16384

        reserve(count)
        if match < 0:
            raise StyError("CBarc match precedes output buffer")
        for _ in range(count):
            if match >= len(output):
                raise StyError("invalid CBarc overlapping match")
            output.append(output[match])
            match += 1

        trailing = stream[source - 1] & 0x03
        reserve(trailing)
        if source + trailing > len(stream):
            raise StyError("truncated CBarc trailing literal")
        output.extend(stream[source:source + trailing])
        source += trailing

    if len(output) != capacity:
        raise StyError(f"CBarc produced {len(output)} bytes, expected {capacity}")
    if require_terminator and not terminated:
        if source + 3 > len(stream) or stream[source:source + 3] != b"\x01\x00\x00":
            raise StyError("CBarc stream lacks terminator")
        source += 3
        terminated = True
    if require_terminator and source != len(stream):
        raise StyError(f"CBarc left {len(stream) - source} compressed bytes unread")
    return bytes(output), source


def barc_literal_compress(data: bytes, terminate: bool) -> bytes:
    output = bytearray()
    position = 0
    remaining = len(data)
    while remaining > 0xFFFF:
        count = 0xFFFC
        output.extend(b"\x01\xFF\xFC")
        output.extend(data[position:position + count])
        position += count
        remaining -= count
    if remaining:
        if 4 <= remaining < 18:
            output.append(remaining - 2)
        elif 18 <= remaining < 272:
            output.extend((0, remaining - 18))
        else:
            output.append(1)
            output.extend(remaining.to_bytes(2, "big"))
        output.extend(data[position:position + remaining])
    if terminate:
        output.extend(b"\x01\x00\x00")
    return bytes(output)


def decode_oc20_pages(raw: bytes) -> tuple[bytes, list[dict[str, Any]]]:
    position = 0
    decoded = bytearray()
    pages = []
    while position < len(raw):
        if raw[position:position + 4] != b"OC20":
            raise StyError(f"expected OC20 page at {position:#x}")
        size = int.from_bytes(raw[position + 4:position + 8], "little")
        page, consumed = barc_decompress(raw[position + 8:], size, False)
        stream_end = position + 8 + consumed
        next_page = raw.find(b"OC20", stream_end)
        page_end = next_page if next_page >= 0 else len(raw)
        footer = raw[stream_end:page_end]
        if len(footer) not in {12, 17} or footer[:3] != b"\x01\x00\x00" or footer[4] != 0xFB:
            raise StyError(f"unrecognized OC20 footer at {stream_end:#x}")
        pages.append({
            "offset": position,
            "compressed_size": page_end - position,
            "decoded_size": size,
            "footer_size": len(footer),
            "footer": footer.hex(" "),
        })
        decoded.extend(page)
        position = page_end
    return bytes(decoded), pages


def encode_oc20_pages(data: bytes) -> bytes:
    output = bytearray()
    for position in range(0, len(data), PAGE_SIZE):
        page = data[position:position + PAGE_SIZE]
        output.extend(b"OC20")
        output.extend(len(page).to_bytes(4, "little"))
        output.extend(barc_literal_compress(page, False))
    return bytes(output)


def decode_input(raw: bytes) -> tuple[bytes, str, list[dict[str, Any]]]:
    if raw[:4] == bytes.fromhex("01 00 01 14"):
        return raw, "native", []
    if raw[:4] == b"OC20":
        native, pages = decode_oc20_pages(raw)
        return native, "oc20_factory_package", pages
    raise StyError("input is neither native STY nor OC20 factory package")


def decode_oc3x(payload: bytes) -> tuple[bytes, str]:
    decoded, metadata = decode_oc3x_generic(payload)
    if metadata["trailer_size"]:
        raise StyError(f"OC31/OC32 has {metadata['trailer_size']} trailing bytes")
    return decoded, str(metadata["wrapper"])


def decode_oc3x_generic(payload: bytes) -> tuple[bytes, dict[str, Any]]:
    if len(payload) < 12 or payload[:4] not in (b"OC31", b"OC32"):
        raise StyError("invalid OC31/OC32 wrapper")
    size = int.from_bytes(payload[4:8], "little")
    decoded, consumed = barc_decompress(payload[8:], size, False)
    stream_end = 8 + consumed
    if payload[stream_end:stream_end + 3] == b"\x01\x00\x00":
        stream_end += 3
    if stream_end >= len(payload):
        raise StyError("OC31/OC32 checksum is missing")
    checksum = payload[stream_end]
    if xor_checksum(decoded) != checksum:
        raise StyError("OC31/OC32 checksum mismatch")
    trailer = payload[stream_end + 1:]
    return decoded, {
        "wrapper": payload[:4].decode("ascii"),
        "stream_size": stream_end - 8,
        "checksum": checksum,
        "trailer_size": len(trailer),
        "trailer": trailer.hex(" "),
    }


def encode_oc3x(data: bytes, magic: str) -> bytes:
    if magic not in {"OC31", "OC32"}:
        raise StyError(f"unsupported resource wrapper {magic!r}")
    return (
        magic.encode("ascii")
        + len(data).to_bytes(4, "little")
        + barc_literal_compress(data, True)
        + bytes([xor_checksum(data)])
    )


def children(data: bytes, offset: int = 0) -> list[dict[str, int]]:
    if offset + 8 > len(data):
        raise StyError("container header outside buffer")
    end = offset + 8 + be32(data, offset + 4)
    if end > len(data):
        raise StyError("container exceeds buffer")
    result = []
    position = offset + 8
    while position < end:
        if position + 8 > end:
            raise StyError("truncated child header")
        length = be32(data, position + 4)
        child_end = position + 8 + length
        if child_end > end:
            raise StyError("child exceeds parent")
        result.append({
            "offset": position,
            "end": child_end,
            "id": data[position],
            "version": data[position + 1],
            "release": data[position + 2],
            "flags": data[position + 3],
            "length": length,
        })
        position = child_end
    if position != end:
        raise StyError("child traversal did not end at parent boundary")
    return result


def build_record(header: bytes, payload: bytes) -> bytes:
    if len(header) != 4:
        raise StyError("record header must contain four bytes")
    return header + len(payload).to_bytes(4, "big") + payload


def record_sequence(data: bytes) -> list[dict[str, int]]:
    result = []
    position = 0
    while position < len(data):
        if position + 8 > len(data):
            raise StyError("truncated record sequence header")
        length = be32(data, position + 4)
        end = position + 8 + length
        if end > len(data):
            raise StyError("record sequence exceeds buffer")
        result.append({
            "offset": position,
            "end": end,
            "id": data[position],
            "version": data[position + 1],
            "release": data[position + 2],
            "flags": data[position + 3],
            "length": length,
        })
        position = end
    return result


def record_payload(data: bytes, item: dict[str, int]) -> bytes:
    return data[item["offset"] + 8:item["end"]]


def record_tree_inventory(data: bytes, depth: int = 0) -> dict[str, Any]:
    records = record_sequence(data)
    header_counts: dict[str, int] = {}
    maximum_depth = depth
    total_records = len(records)
    for item in records:
        key = data[item["offset"]:item["offset"] + 4].hex(" ")
        header_counts[key] = header_counts.get(key, 0) + 1
        payload = data[item["offset"] + 8:item["end"]]
        try:
            nested = record_tree_inventory(payload, depth + 1)
        except StyError:
            continue
        maximum_depth = max(maximum_depth, nested["maximum_depth"])
        total_records += nested["total_records"]
        for nested_key, count in nested["header_counts"].items():
            header_counts[nested_key] = header_counts.get(nested_key, 0) + count
    return {
        "top_level_records": [
            {
                "header": data[item["offset"]:item["offset"] + 4].hex(" "),
                "length": item["length"],
            }
            for item in records
        ],
        "total_records": total_records,
        "maximum_depth": maximum_depth,
        "header_counts": header_counts,
    }


def sts_sound_record_model(record: bytes) -> dict[str, Any]:
    if len(record) != 10:
        raise StyError("STS sound record must contain 10 bytes")
    result: dict[str, Any] = {
        "raw_hex": record.hex(" "),
        "volume": record[3],
        "pan": record[4],
        "fx_c_send": record[5],
        "fx_d_send": record[6],
    }
    if record[:3] == b"\xff\xff\xff":
        if record[7:] != b"\0\0\0":
            raise StyError("legacy empty STS selector has nonzero reserved tail")
        result.update({
            "selector_kind": "empty",
            "selector_family": "empty",
        })
    elif record[:3] == b"\xff\xff\x02":
        result.update({
            "selector_kind": "user",
            "selector_family": "user_namespace_09",
            "user_namespace": record[7],
            "user_bank": record[8],
            "user_position": record[9],
        })
    else:
        result.update({
            "selector_kind": "native",
            "bank_msb": 121,
            "bank_lsb": record[0],
            "program": record[1] & 0x7F,
            "muted": bool(record[1] & 0x80),
            "status": record[2],
        })
    return result


def nested_sts_track_model(payload: bytes) -> dict[str, Any]:
    if len(payload) != 19:
        raise StyError("nested STS track record must contain 19 bytes")
    track_index = be32(payload, 0)
    if track_index not in range(4):
        raise StyError("nested STS track index must be in range 0..3")
    body = payload[4:]
    selector = body[:4]
    if body[2] != 0:
        raise StyError("nested STS selector reserved byte is not zero")
    if body[8:14] != b"\0" * 6:
        raise StyError("nested STS reserved tail is not zero")
    if body[14] != 0x5A:
        raise StyError("nested STS track terminator is not 0x5A")
    if selector == bytes.fromhex("FF FF 00 FF"):
        selector_family = "empty"
    elif selector[0] == 0x09:
        selector_family = "user_namespace_09"
    elif selector[0] == 0x00:
        selector_family = "native_reference_mode_00"
    elif selector == bytes.fromhex("FF 00 00 00"):
        selector_family = "native_reference_mode_ff"
    else:
        selector_family = "unknown"
    result = {
        "track": track_index,
        "role": ("lower", "upper1", "upper2", "upper3")[track_index],
        "semantic_level": "mixer_fields_decoded_selector_lossless",
        "selector_family": selector_family,
        "selector_raw_hex": selector.hex(" "),
        "selector_namespace": selector[0],
        "selector_bank": selector[1],
        "selector_reserved": selector[2],
        "selector_position": selector[3],
        "volume": body[4],
        "pan": body[5],
        "fx_c_send": body[6],
        "fx_d_send": body[7],
        "reserved_tail_hex": body[8:14].hex(" "),
        "terminator": body[14],
        "body_raw_hex": body.hex(" "),
        "raw_hex": payload.hex(" "),
        "raw_sha256": hashlib.sha256(payload).hexdigest(),
    }
    if selector_family == "user_namespace_09":
        result.update({
            "selector_kind": "user",
            "user_namespace": selector[0],
            "user_bank": selector[1],
            "user_position": selector[3],
        })
    elif selector_family in {"native_reference_mode_00", "native_reference_mode_ff"}:
        result.update({
            "selector_kind": "native_reference",
            "selector_contains_program_address": False,
        })
    return result


def nested_sts_detail_model(payload: bytes) -> dict[str, Any]:
    if len(payload) not in {242, 382}:
        raise StyError(f"unsupported nested STS detail length {len(payload)}")
    track_index = be32(payload, 0)
    if track_index not in range(4):
        raise StyError("nested STS detail track index must be in range 0..3")
    header_marker = payload[46]
    if header_marker not in {0x5A, 0x61}:
        raise StyError("unsupported nested STS detail header marker")
    first_block = payload[47:83]
    remaining_blocks = [
        payload[83 + index * 22:105 + index * 22]
        for index in range(7)
    ]
    parameter_blocks = [first_block, *remaining_blocks]
    if len(first_block) != 36 or any(len(block) != 22 for block in remaining_blocks):
        raise StyError("nested STS detail parameter block geometry mismatch")
    if any(block[-1] != 0x5A for block in parameter_blocks):
        raise StyError("nested STS detail parameter block marker missing")
    late_extensions = []
    if len(payload) == 382:
        for base in (242, 312):
            block = payload[base:base + 70]
            if block[67:] != bytes([track_index, 0x00, 0xFF]):
                raise StyError("nested STS late extension trailer mismatch")
            late_extensions.append({
                "data_nonzero": any(block[:67]),
                "data_sha256": hashlib.sha256(block[:67]).hexdigest(),
                "trailer_hex": block[67:].hex(" "),
                "raw_sha256": hashlib.sha256(block).hexdigest(),
            })
    return {
        "track": track_index,
        "role": ("lower", "upper1", "upper2", "upper3")[track_index],
        "record_size": len(payload),
        "header": {
            "size": 47,
            "marker": header_marker,
            "raw_sha256": hashlib.sha256(payload[:47]).hexdigest(),
        },
        "parameter_blocks": [
            {
                "index": index,
                "size": len(block),
                "marker": block[-1],
                "raw_sha256": hashlib.sha256(block).hexdigest(),
            }
            for index, block in enumerate(parameter_blocks)
        ],
        "base_tail_hex": payload[237:242].hex(" "),
        "late_extensions": late_extensions,
        "unknown_triplet_10_12": {
            "semantic_level": "raw_field_selector_hypothesis_rejected",
            "values": list(payload[10:13]),
            "raw_hex": payload[10:13].hex(" "),
        },
        "unknown_byte_13": payload[13],
        "raw_hex": payload.hex(" "),
        "raw_sha256": hashlib.sha256(payload).hexdigest(),
    }


def nested_sts_legacy_companion_model(payload: bytes) -> dict[str, Any]:
    if len(payload) != 78:
        raise StyError("nested STS legacy companion must contain 78 bytes")
    track_index = be32(payload, 0)
    if track_index not in range(4):
        raise StyError("nested STS legacy companion track index must be in range 0..3")
    if payload[38:] != b"\0" * 40:
        raise StyError("nested STS legacy companion reserved tail is not zero")
    return {
        "track": track_index,
        "role": ("lower", "upper1", "upper2", "upper3")[track_index],
        "core_size": 34,
        "core_raw_hex": payload[4:38].hex(" "),
        "core_sha256": hashlib.sha256(payload[4:38]).hexdigest(),
        "reserved_tail_size": 40,
        "raw_hex": payload.hex(" "),
        "raw_sha256": hashlib.sha256(payload).hexdigest(),
    }


def nested_sts_status_model(payload: bytes) -> dict[str, Any]:
    if len(payload) != 8:
        raise StyError("nested STS status record must contain 8 bytes")
    track_index = be32(payload, 0)
    if track_index not in range(4):
        raise StyError("nested STS status track index must be in range 0..3")
    if payload[4:7] != b"\0" * 3:
        raise StyError("nested STS status reserved prefix is not zero")
    return {
        "track": track_index,
        "role": ("lower", "upper1", "upper2", "upper3")[track_index],
        "unknown_status_code": payload[7],
        "raw_hex": payload.hex(" "),
        "raw_sha256": hashlib.sha256(payload).hexdigest(),
    }


def legacy_user_sts_to_nested(record: bytes, track_index: int) -> bytes:
    model = sts_sound_record_model(record)
    if model.get("selector_kind") != "user" or model["user_namespace"] != 0x09:
        raise StyError("legacy STS record is not a namespace-09 USER selector")
    if track_index not in range(4):
        raise StyError("nested STS track index must be in range 0..3")
    body = bytes([
        0x09,
        model["user_bank"],
        0x00,
        model["user_position"],
        model["volume"],
        model["pan"],
        model["fx_c_send"],
        model["fx_d_send"],
    ]) + b"\0" * 6 + b"\x5A"
    nested = track_index.to_bytes(4, "big") + body
    nested_sts_track_model(nested)
    return nested


def nested_user_sts_to_legacy(payload: bytes) -> bytes:
    model = nested_sts_track_model(payload)
    if model.get("selector_kind") != "user" or model["user_namespace"] != 0x09:
        raise StyError("nested STS record is not a namespace-09 USER selector")
    legacy = bytes.fromhex("FF FF 02") + bytes([
        model["volume"],
        model["pan"],
        model["fx_c_send"],
        model["fx_d_send"],
        0x09,
        model["user_bank"],
        model["user_position"],
    ])
    sts_sound_record_model(legacy)
    return legacy


def legacy_empty_sts_to_nested(record: bytes, track_index: int) -> bytes:
    model = sts_sound_record_model(record)
    if model.get("selector_kind") != "empty":
        raise StyError("legacy STS record is not an empty selector")
    if track_index not in range(4):
        raise StyError("nested STS track index must be in range 0..3")
    body = bytes.fromhex("FF FF 00 FF") + bytes([
        model["volume"],
        model["pan"],
        model["fx_c_send"],
        model["fx_d_send"],
    ]) + b"\0" * 6 + b"\x5A"
    nested = track_index.to_bytes(4, "big") + body
    nested_sts_track_model(nested)
    return nested


def nested_empty_sts_to_legacy(payload: bytes) -> bytes:
    model = nested_sts_track_model(payload)
    if model.get("selector_family") != "empty":
        raise StyError("nested STS record is not an empty selector")
    legacy = bytes.fromhex("FF FF FF") + bytes([
        model["volume"],
        model["pan"],
        model["fx_c_send"],
        model["fx_d_send"],
    ]) + b"\0" * 3
    sts_sound_record_model(legacy)
    return legacy


def legacy_type09_model(decoded: bytes, records: list[dict[str, int]]) -> dict[str, Any]:
    mixer_item = records[0]
    metadata_item = records[-1]
    mixer_header = decoded[mixer_item["offset"]:mixer_item["offset"] + 4]
    metadata_header = decoded[metadata_item["offset"]:metadata_item["offset"] + 4]
    if mixer_header not in {bytes.fromhex("00 00 00 08"), bytes.fromhex("00 01 00 08")}:
        raise StyError("unsupported legacy type-09 mixer header")
    if metadata_header != bytes.fromhex("02 00 01 08"):
        raise StyError("unsupported legacy type-09 metadata header")

    mixer = decoded[mixer_item["offset"] + 8:mixer_item["end"]]
    if len(mixer) < 17 or be32(mixer, 0) != len(mixer):
        raise StyError("invalid legacy type-09 mixer size")
    common_offset = be32(mixer, 4)
    slots_offset = be32(mixer, 8)
    slot_count = mixer[12]
    if mixer[-4:] != bytes.fromhex("DE AD BE EF"):
        raise StyError("legacy type-09 mixer marker missing")
    slot_bytes = len(mixer) - slots_offset - 4
    if not slot_count or slot_bytes % slot_count:
        raise StyError("legacy type-09 mixer slots do not divide evenly")
    slot_size = slot_bytes // slot_count
    if slot_size not in {261, 357}:
        raise StyError(f"unsupported legacy type-09 mixer slot size {slot_size}")
    mixer_slots = []
    for index in range(slot_count):
        start = slots_offset + index * slot_size
        slot = mixer[start:start + slot_size]
        mixer_slots.append({
            "physical_index": index,
            "slot_id": slot[0],
            "slot_size": slot_size,
            "settings16_hex": slot[1:17].hex(" "),
            "keyboard_tracks": [
                {
                    "track": track,
                    "role": ("lower", "upper1", "upper2", "upper3")[track],
                    **sts_sound_record_model(slot[17 + track * 10:27 + track * 10]),
                }
                for track in range(4)
            ],
            "raw_sha256": hashlib.sha256(slot).hexdigest(),
        })

    metadata = decoded[metadata_item["offset"] + 8:metadata_item["end"]]
    if len(metadata) < 20 or metadata[:4] != bytes.fromhex("AB CD EF 02"):
        raise StyError("invalid legacy type-09 metadata marker")
    if metadata[-4:] != bytes.fromhex("02 AB CD EF"):
        raise StyError("legacy type-09 metadata end marker missing")
    named_offset = be32(metadata, 12)
    named_count = metadata[16]
    named_bytes = len(metadata) - named_offset - 4
    if not named_count or named_bytes % named_count:
        raise StyError("legacy type-09 named slots do not divide evenly")
    named_slot_size = named_bytes // named_count
    if named_slot_size != 167:
        raise StyError(f"unsupported legacy named slot size {named_slot_size}")
    named_slots = []
    for index in range(named_count):
        start = named_offset + index * named_slot_size
        slot = metadata[start:start + named_slot_size]
        name = slot[1:18].split(b"\0", 1)[0].decode("latin1", "replace")
        named_slots.append({
            "physical_index": index,
            "slot_id": slot[0],
            "name": name,
            "raw_sha256": hashlib.sha256(slot).hexdigest(),
        })

    mixer_ids = [item["slot_id"] for item in mixer_slots]
    named_ids = [item["slot_id"] for item in named_slots]
    if len(set(mixer_ids)) != len(mixer_ids) or len(set(named_ids)) != len(named_ids):
        raise StyError("duplicate legacy type-09 slot ID")
    if not set(named_ids).issubset(mixer_ids):
        raise StyError("named type-09 slot ID is missing from mixer child")
    names_by_id = {item["slot_id"]: item["name"] for item in named_slots}
    for item in mixer_slots:
        item["name"] = names_by_id.get(item["slot_id"])

    return {
        "layout": "legacy_two_child",
        "mixer_header": mixer_header.hex(" "),
        "metadata_header": metadata_header.hex(" "),
        "common_offset": common_offset,
        "mixer_slots_offset": slots_offset,
        "mixer_slot_size": slot_size,
        "mixer_slot_count": slot_count,
        "named_slots_offset": named_offset,
        "named_slot_size": named_slot_size,
        "named_slot_count": named_count,
        "orphan_mixer_slot_ids": sorted(set(mixer_ids) - set(named_ids)),
        "middle_records": [
            {
                "header": decoded[item["offset"]:item["offset"] + 4].hex(" "),
                "length": item["length"],
            }
            for item in records[1:-1]
        ],
        "slots": mixer_slots,
    }


def neutral_legacy_type09(model: dict[str, Any]) -> dict[str, Any]:
    slots = []
    for slot in model["slots"]:
        slots.append({
            "physical_index": slot["physical_index"],
            "slot_id": slot["slot_id"],
            "name": slot["name"],
            "named": slot["name"] is not None,
            "semantic_level": "legacy_fields_decoded",
            "tracks": slot["keyboard_tracks"],
            "raw_sha256": slot["raw_sha256"],
        })
    return {
        "schema": "korg-style-performance-neutral-v1",
        "source_layout": "legacy_two_child",
        "slot_count": len(slots),
        "named_slot_count": sum(slot["named"] for slot in slots),
        "orphan_slot_ids": model["orphan_mixer_slot_ids"],
        "slots": slots,
    }


def nested_type09_model(decoded: bytes) -> dict[str, Any]:
    outer = record_sequence(decoded)
    if len(outer) != 1 or outer[0]["id"] != 0x00:
        raise StyError("nested type-09 must contain one outer ID-00 record")
    outer_payload = record_payload(decoded, outer[0])
    generation = record_sequence(outer_payload)
    if len(generation) != 1 or generation[0]["id"] != 0x01:
        raise StyError("nested type-09 must contain one generation ID-01 record")
    generation_payload = record_payload(outer_payload, generation[0])
    generation_records = record_sequence(generation_payload)
    slot_records = [item for item in generation_records if item["id"] == 0x03]
    if len(slot_records) != 4:
        raise StyError(f"nested type-09 expected four STS slots, found {len(slot_records)}")

    slots = []
    for physical_index, slot_item in enumerate(slot_records):
        slot_payload = record_payload(generation_payload, slot_item)
        wrappers = record_sequence(slot_payload)
        if len(wrappers) != 1 or wrappers[0]["id"] != 0x0A:
            raise StyError("nested STS slot must contain one ID-0A wrapper")
        content = record_payload(slot_payload, wrappers[0])
        children = record_sequence(content)
        name_records = [item for item in children if item["id"] == 0x0F]
        track_records = [item for item in children if item["id"] == 0x08]
        detail_records = [item for item in children if item["id"] == 0x09]
        status_records = [item for item in children if item["id"] == 0x15]
        legacy_companion_records = [item for item in children if item["id"] == 0x07]
        if (
            len(name_records) != 1
            or len(track_records) != 4
            or len(detail_records) != 4
            or len(status_records) != 4
            or len(legacy_companion_records) not in {0, 4}
        ):
            raise StyError(
                "nested STS slot requires one name, four track/detail/status "
                "records and zero or four legacy companion records"
            )

        name_payload = record_payload(content, name_records[0])
        if len(name_payload) < 5:
            raise StyError("nested STS name record is truncated")
        name = name_payload[4:].split(b"\0", 1)[0].decode("latin1", "replace")
        detail_models = {}
        status_by_track = {}
        legacy_companion_by_track = {}
        for detail_item in detail_records:
            detail = nested_sts_detail_model(record_payload(content, detail_item))
            if detail["track"] in detail_models:
                raise StyError("duplicate nested STS detail track index")
            detail_models[detail["track"]] = detail
        for status_item in status_records:
            payload = record_payload(content, status_item)
            status = nested_sts_status_model(payload)
            track_index = status["track"]
            if track_index in status_by_track:
                raise StyError("duplicate nested STS status track index")
            status_by_track[track_index] = status
        for companion_item in legacy_companion_records:
            payload = record_payload(content, companion_item)
            companion = nested_sts_legacy_companion_model(payload)
            track_index = companion["track"]
            if track_index in legacy_companion_by_track:
                raise StyError("duplicate nested STS legacy companion track index")
            legacy_companion_by_track[track_index] = companion
        if set(detail_models) != set(range(4)) or set(status_by_track) != set(range(4)):
            raise StyError("nested STS detail/status track set is incomplete")
        if legacy_companion_by_track and set(legacy_companion_by_track) != set(range(4)):
            raise StyError("nested STS legacy companion track set is incomplete")

        tracks = []
        seen_track_indexes = set()
        for track_item in track_records:
            payload = record_payload(content, track_item)
            track_model = nested_sts_track_model(payload)
            track_index = track_model["track"]
            if track_index not in range(4) or track_index in seen_track_indexes:
                raise StyError("nested STS track indexes must be unique values 0..3")
            seen_track_indexes.add(track_index)
            track_model["detail"] = detail_models[track_index]
            track_model["status"] = status_by_track[track_index]
            if track_index in legacy_companion_by_track:
                track_model["legacy_companion"] = legacy_companion_by_track[track_index]
            tracks.append(track_model)
        if seen_track_indexes != set(range(4)):
            raise StyError("nested STS track set is incomplete")
        tracks.sort(key=lambda item: item["track"])
        slots.append({
            "physical_index": physical_index,
            "slot_id": physical_index,
            "embedded_name_record_id": be32(name_payload, 0),
            "name": name,
            "named": True,
            "semantic_level": "nested_tracks_raw_preserved",
            "tracks": tracks,
            "child_headers": [
                content[item["offset"]:item["offset"] + 4].hex(" ")
                for item in children
            ],
            "raw_sha256": hashlib.sha256(slot_payload).hexdigest(),
        })

    return {
        "layout": "nested_new_generation",
        "inventory": record_tree_inventory(decoded),
        "neutral": {
            "schema": "korg-style-performance-neutral-v1",
            "source_layout": "nested_new_generation",
            "slot_count": len(slots),
            "named_slot_count": len(slots),
            "orphan_slot_ids": [],
            "slots": slots,
        },
    }


def type09_model(decoded: bytes) -> dict[str, Any]:
    records = record_sequence(decoded)
    if (
        len(records) >= 2
        and decoded[records[0]["offset"]:records[0]["offset"] + 4]
        in {bytes.fromhex("00 00 00 08"), bytes.fromhex("00 01 00 08")}
        and decoded[records[-1]["offset"]:records[-1]["offset"] + 4]
        == bytes.fromhex("02 00 01 08")
    ):
        model = legacy_type09_model(decoded, records)
        model["neutral"] = neutral_legacy_type09(model)
        return model
    return nested_type09_model(decoded)


STS_MIXER_FIELDS = {
    "volume": 0,
    "pan": 1,
    "fx_c_send": 2,
    "fx_d_send": 3,
}


def patch_type09_mixer_field(
    decoded: bytes,
    slot_index: int,
    track_index: int,
    field: str,
    value: int,
) -> tuple[bytes, dict[str, Any]]:
    if field not in STS_MIXER_FIELDS:
        raise StyError(f"unsupported STS mixer field {field!r}")
    if value not in range(128):
        raise StyError("STS mixer value must be in range 0..127")
    if track_index not in range(4):
        raise StyError("STS keyboard track must be in range 0..3")
    records = record_sequence(decoded)
    mutable = bytearray(decoded)
    legacy = (
        len(records) >= 2
        and decoded[records[0]["offset"]:records[0]["offset"] + 4]
        in {bytes.fromhex("00 00 00 08"), bytes.fromhex("00 01 00 08")}
        and decoded[records[-1]["offset"]:records[-1]["offset"] + 4]
        == bytes.fromhex("02 00 01 08")
    )
    if legacy:
        model = legacy_type09_model(decoded, records)
        if slot_index not in range(model["mixer_slot_count"]):
            raise StyError("legacy STS slot index is outside mixer slot range")
        mixer_item = records[0]
        slot_start = (
            mixer_item["offset"]
            + 8
            + model["mixer_slots_offset"]
            + slot_index * model["mixer_slot_size"]
        )
        record_start = slot_start + 17 + track_index * 10
        before_record = bytes(mutable[record_start:record_start + 10])
        field_offset = record_start + 3 + STS_MIXER_FIELDS[field]
        mutable[field_offset] = value
        after_record = bytes(mutable[record_start:record_start + 10])
        before = sts_sound_record_model(before_record)
        after = sts_sound_record_model(after_record)
        layout = "legacy_two_child"
    else:
        outer = record_sequence(decoded)
        if len(outer) != 1 or outer[0]["id"] != 0x00:
            raise StyError("unsupported nested type-09 outer structure")
        outer_base = outer[0]["offset"] + 8
        outer_payload = record_payload(decoded, outer[0])
        generation = record_sequence(outer_payload)
        if len(generation) != 1 or generation[0]["id"] != 0x01:
            raise StyError("unsupported nested type-09 generation structure")
        generation_base = outer_base + generation[0]["offset"] + 8
        generation_payload = record_payload(outer_payload, generation[0])
        slots = [item for item in record_sequence(generation_payload) if item["id"] == 0x03]
        if slot_index not in range(len(slots)):
            raise StyError("nested STS slot index is outside slot range")
        slot_item = slots[slot_index]
        slot_base = generation_base + slot_item["offset"] + 8
        slot_payload = record_payload(generation_payload, slot_item)
        wrappers = record_sequence(slot_payload)
        if len(wrappers) != 1 or wrappers[0]["id"] != 0x0A:
            raise StyError("unsupported nested STS slot wrapper")
        content_base = slot_base + wrappers[0]["offset"] + 8
        content = record_payload(slot_payload, wrappers[0])
        track_items = [item for item in record_sequence(content) if item["id"] == 0x08]
        selected = None
        for item in track_items:
            payload = record_payload(content, item)
            if be32(payload, 0) == track_index:
                selected = item
                break
        if selected is None:
            raise StyError("nested STS keyboard track is missing")
        payload_start = content_base + selected["offset"] + 8
        before_payload = bytes(mutable[payload_start:payload_start + 19])
        field_offset = payload_start + 8 + STS_MIXER_FIELDS[field]
        mutable[field_offset] = value
        after_payload = bytes(mutable[payload_start:payload_start + 19])
        before = nested_sts_track_model(before_payload)
        after = nested_sts_track_model(after_payload)
        layout = "nested_new_generation"
    patched = bytes(mutable)
    type09_model(patched)
    return patched, {
        "layout": layout,
        "slot_index": slot_index,
        "track": track_index,
        "role": ("lower", "upper1", "upper2", "upper3")[track_index],
        "field": field,
        "old_value": before[field],
        "new_value": after[field],
        "changed": before[field] != after[field],
        "before_track_sha256": hashlib.sha256(
            before_record if legacy else before_payload
        ).hexdigest(),
        "after_track_sha256": hashlib.sha256(
            after_record if legacy else after_payload
        ).hexdigest(),
    }


def parse_native(native: bytes) -> list[dict[str, int]]:
    if native[:4] != bytes.fromhex("01 00 01 14"):
        raise StyError("unexpected native root")
    if be32(native, 4) != len(native) - 8:
        raise StyError("native root length mismatch")
    outer = children(native)
    if not outer or outer[-1]["id"] != 0xFE:
        raise StyError("missing FE index")
    terminal = outer[-1]
    payload = native[terminal["offset"] + 8:terminal["end"]]
    if payload[:4] != b"KBEG" or payload[-8:-4] != b"KEND":
        raise StyError("invalid FE/KBEG/KEND index")
    offsets = [be32(payload, position) for position in range(4, len(payload) - 8, 4)]
    expected = [item["offset"] for item in outer if item["id"] in INDEXED_IDS]
    if offsets != expected or be32(payload, len(payload) - 4) != len(offsets):
        raise StyError("FE index does not match resource offsets")
    return outer


def parse_indexed_native(native: bytes) -> tuple[list[dict[str, int]], dict[str, Any]]:
    if native[:4] != bytes.fromhex("01 00 01 14"):
        raise StyError("unexpected native root")
    kbeg = native.rfind(b"KBEG")
    kend = native.rfind(b"KEND")
    if kbeg < 0 or kend <= kbeg or kend + 8 > len(native):
        raise StyError("missing terminal KBEG/KEND index")
    offsets = [be32(native, position) for position in range(kbeg + 4, kend, 4)]
    count = be32(native, kend + 4)
    if count != len(offsets):
        raise StyError("KEND count does not match index entries")
    if not offsets or offsets != sorted(offsets) or offsets[-1] >= kbeg:
        raise StyError("invalid indexed resource offsets")
    result = []
    length_mismatches = 0
    for index, offset in enumerate(offsets):
        end = offsets[index + 1] if index + 1 < len(offsets) else kbeg
        if offset + 8 > end:
            raise StyError("indexed resource header exceeds span")
        declared = be32(native, offset + 4)
        span_length = end - offset - 8
        if declared != span_length:
            length_mismatches += 1
        result.append({
            "offset": offset,
            "end": end,
            "id": native[offset],
            "version": native[offset + 1],
            "release": native[offset + 2],
            "flags": native[offset + 3],
            "length": span_length,
            "declared_length": declared,
        })
    return result, {
        "declared_root_length": be32(native, 4),
        "actual_root_length": len(native) - 8,
        "root_length_matches": be32(native, 4) == len(native) - 8,
        "index_offset": kbeg,
        "index_entries": len(offsets),
        "resource_length_mismatches": length_mismatches,
    }


def style_summary(decoded: bytes) -> dict[str, Any]:
    result: dict[str, Any] = {
        "decoded_size": len(decoded),
        "root": decoded[:4].hex(" "),
    }
    try:
        root_children = children(decoded)
    except StyError as error:
        result["structure_error"] = str(error)
        return result
    result["root_child_ids"] = [item["id"] for item in root_children]
    if root_children:
        info = root_children[0]
        start = info["offset"] + 8
        if start < info["end"]:
            name_length = decoded[start]
            if start + 1 + name_length <= info["end"]:
                result["name"] = decoded[start + 1:start + 1 + name_length].decode(
                    "latin1", "replace"
                )
                try:
                    result["style_info"] = style_info_model(
                        decoded[start:info["end"]], decoded[1]
                    )
                except StyError as error:
                    result["style_info_error"] = str(error)
    elements = [item for item in root_children if item["id"] == 0x03]
    result["style_elements"] = len(elements)
    geometries: dict[str, int] = {}
    for element in elements:
        try:
            element_children = children(decoded, element["offset"])
        except StyError:
            continue
        if len(element_children) >= 2:
            key = f"{element_children[0]['length']}/{element_children[1]['length']}"
            geometries[key] = geometries.get(key, 0) + 1
    result["element_track_geometries"] = geometries
    return result


def track_data_model(track_data: bytes) -> dict[str, Any]:
    if len(track_data) not in {48, 80, 88, 96}:
        raise StyError(f"unsupported TrackData length {len(track_data)}")
    tracks = []
    for track_index, role in enumerate(TRACK_ROLES):
        selector = track_data[track_index * 6:track_index * 6 + 6]
        row: dict[str, Any] = {
            "track": track_index,
            "role": role,
            "expression": selector[0],
            "bank_msb": selector[1],
            "bank_lsb": selector[2],
            "program": selector[3],
            "key_low": selector[4],
            "key_high": selector[5],
        }
        if len(track_data) >= 80:
            auxiliary = track_data[48 + track_index * 3:48 + track_index * 3 + 3]
            packed = track_data[72 + track_index]
            row.update({
                "rx_noise": auxiliary[0],
                "floating_trigger": auxiliary[1],
                "guitar_capo": auxiliary[2],
                "ntt_ntr_packed": packed,
                "ntt": packed >> 5,
                "ntr": packed & 0x1F,
            })
        if len(track_data) == 88:
            row["v5_extension"] = track_data[80 + track_index]
        elif len(track_data) == 96:
            row["v5_extension"] = track_data[80 + track_index]
            row["late_model_extension"] = track_data[88 + track_index]
        if len(track_data) >= 88:
            extension_value = track_data[80 + track_index]
            row["v5_extension_value"] = extension_value
            row["v5_extension_is_modal_default"] = (
                extension_value == V5_EXTENSION_DEFAULT
            )
        tracks.append(row)
    extension = list(track_data[80:88]) if len(track_data) >= 88 else None
    late_extension = list(track_data[88:96]) if len(track_data) == 96 else None
    return {
        "length": len(track_data),
        "layout": (
            "48-byte selectors only"
            if len(track_data) == 48
            else "48-byte selectors + 24-byte auxiliary triplets + 8-byte NTT/NTR"
            + (" + 8-byte v5 extension" if len(track_data) >= 88 else "")
            + (" + 8-byte late-model extension" if len(track_data) == 96 else "")
        ),
        "tracks": tracks,
        "missing_v4_fields": (
            ["rx_noise", "floating_trigger", "guitar_capo", "ntt", "ntr"]
            if len(track_data) == 48 else []
        ),
        "v5_extension": extension,
        "late_model_extension": late_extension,
        "safe_to_80_drop": extension is not None
        and all(value == V5_EXTENSION_DEFAULT for value in extension)
        and (
            late_extension is None
            or all(value == TRACK_DATA_96_EXTENSION_DEFAULT for value in late_extension)
        ),
    }


def pattern_payload_model(tag: int, payload: bytes) -> dict[str, Any]:
    if len(payload) < 8:
        raise StyError("pattern payload is shorter than its stream header")
    stream_length = be32(payload, 4)
    suffix_offset = 8 + stream_length
    if suffix_offset > len(payload):
        raise StyError("pattern stream exceeds payload")
    suffix = payload[suffix_offset:]
    result: dict[str, Any] = {
        "tag": tag,
        "stream_length": stream_length,
        "suffix_hex": suffix.hex(" "),
    }
    if tag == 0x02:
        result["track_family"] = "drum_percussion"
    elif tag == 0x03:
        result["track_family"] = "bass"
    elif tag in {0x04, 0x05}:
        result["track_family"] = "accompaniment"
        result["guitar_mode_tag"] = tag == 0x05
    return result


def pattern_pool_model(decoded: bytes) -> dict[str, Any]:
    root_children = children(decoded)
    pools = [item for item in root_children if item["id"] == 0x02]
    if len(pools) != 1:
        raise StyError(f"expected one pattern pool, found {len(pools)}")
    pool_children = children(decoded, pools[0]["offset"])
    if not pool_children or pool_children[0]["id"] != 0x01:
        raise StyError("pattern pool is missing its reference directory")

    directory_item = pool_children[0]
    directory = decoded[directory_item["offset"] + 8:directory_item["end"]]
    if len(directory) < 4:
        raise StyError("pattern reference directory is truncated")
    object_count = int.from_bytes(directory[0:2], "big")
    reference_count = int.from_bytes(directory[2:4], "big")
    reference_end = 4 + reference_count * 2
    if reference_end > len(directory):
        raise StyError("pattern reference table exceeds directory")
    references = [
        int.from_bytes(directory[position:position + 2], "big")
        for position in range(4, reference_end, 2)
    ]
    objects = pool_children[1:]
    if object_count != len(objects):
        raise StyError(
            f"pattern object count mismatch: {object_count} != {len(objects)}"
        )

    object_models = []
    tag_counts: dict[str, int] = {}
    for object_index, item in enumerate(objects, 1):
        payload = decoded[item["offset"] + 8:item["end"]]
        model = pattern_payload_model(item["id"], payload)
        model["object_index"] = object_index
        object_models.append(model)
        key = f"{item['id']:02x}"
        tag_counts[key] = tag_counts.get(key, 0) + 1

    element_models = []
    reference_cursor = 0
    elements = [item for item in root_children if item["id"] == 0x03]
    for element_index, element in enumerate(elements):
        element_children = children(decoded, element["offset"])
        chord_variations = []
        element_tag_counts: dict[str, int] = {}
        for cv_index, cv in enumerate(element_children[2:]):
            payload = decoded[cv["offset"] + 8:cv["end"]]
            if len(payload) < 8:
                raise StyError("chord variation payload is truncated")
            stream_length = be32(payload, 4)
            trailer_offset = 8 + stream_length
            if trailer_offset >= len(payload):
                raise StyError("chord variation is missing its reference count")
            local_reference_count = payload[trailer_offset]
            controls = payload[trailer_offset + 1:]
            if len(controls) != local_reference_count * 2:
                raise StyError(
                    "chord variation control-pair count does not match references"
                )
            reference_limit = reference_cursor + local_reference_count
            if reference_limit > len(references):
                raise StyError("chord variation consumes references past directory end")
            local_references = references[reference_cursor:reference_limit]
            resolved = []
            cv_tag_counts: dict[str, int] = {}
            for local_id, object_index in enumerate(local_references):
                if object_index < 1 or object_index > len(object_models):
                    raise StyError("pattern reference points outside object table")
                pattern = object_models[object_index - 1]
                cv_class = controls[local_id * 2]
                track_index = controls[local_id * 2 + 1]
                if track_index >= len(TRACK_ROLES):
                    raise StyError("chord variation track index exceeds TrackData")
                expected_class = PATTERN_TAG_CLASSES.get(pattern["tag"])
                if expected_class is not None and cv_class != expected_class:
                    raise StyError(
                        f"pattern tag/class mismatch: {pattern['tag']:02x}/{cv_class}"
                    )
                tag_key = f"{pattern['tag']:02x}"
                cv_tag_counts[tag_key] = cv_tag_counts.get(tag_key, 0) + 1
                element_tag_counts[tag_key] = element_tag_counts.get(tag_key, 0) + 1
                resolved.append({
                    "local_id": local_id,
                    "object_index": object_index,
                    "tag": pattern["tag"],
                    "cv_class": cv_class,
                    "track_index": track_index,
                    "track_role": TRACK_ROLES[track_index],
                    "track_family": pattern.get("track_family"),
                    "guitar_mode_tag": pattern.get("guitar_mode_tag"),
                    "suffix_hex": pattern["suffix_hex"],
                })
            chord_variations.append({
                "index": cv_index,
                "stream_length": stream_length,
                "reference_start": reference_cursor,
                "reference_count": local_reference_count,
                "control_pair_count": local_reference_count,
                "tag_counts": cv_tag_counts,
                "references": resolved,
                "trailer_hex": payload[trailer_offset:].hex(" "),
            })
            reference_cursor = reference_limit
        element_models.append({
            "index": element_index,
            "tag_counts": element_tag_counts,
            "chord_variations": chord_variations,
        })

    if reference_cursor != len(references):
        raise StyError(
            f"pattern references not fully consumed: {reference_cursor} != {len(references)}"
        )
    return {
        "object_count": object_count,
        "reference_count": reference_count,
        "directory_trailing_hex": directory[reference_end:].hex(" "),
        "tag_counts": tag_counts,
        "all_references_consumed": True,
        "objects": object_models,
        "elements": element_models,
    }


def convert_track_data(track_data: bytes, target_length: int, allow_lossy: bool = False) -> tuple[bytes, list[str]]:
    if len(track_data) == target_length:
        return track_data, []
    if len(track_data) == 80 and target_length == 88:
        return track_data + bytes([V5_EXTENSION_DEFAULT] * 8), [
            "v5 extension initialized to corpus modal value 4 for all tracks"
        ]
    if len(track_data) == 80 and target_length == 96:
        return track_data + bytes([V5_EXTENSION_DEFAULT] * 8) + bytes(8), [
            "v5 extension initialized to corpus modal value 4 for all tracks",
            "late-model extension initialized to corpus-exact value 0 for all tracks",
        ]
    if len(track_data) == 88 and target_length == 96:
        return track_data + bytes(8), [
            "late-model extension initialized to corpus-exact value 0 for all tracks"
        ]
    if len(track_data) == 96 and target_length == 88:
        extension = track_data[88:96]
        if any(extension) and not allow_lossy:
            raise StyError(
                "refusing lossy TrackData 96->88 conversion with non-default late-model extension"
            )
        warnings = []
        if any(extension):
            warnings.append(f"discarded non-default late-model extension {extension.hex(' ')}")
        return track_data[:88], warnings
    if len(track_data) in {88, 96} and target_length == 80:
        extension = track_data[80:88]
        late_extension = track_data[88:96] if len(track_data) == 96 else b""
        if any(value != V5_EXTENSION_DEFAULT for value in extension) and not allow_lossy:
            raise StyError(
                f"refusing lossy TrackData {len(track_data)}->80 conversion with non-default v5 extension data"
            )
        if any(late_extension) and not allow_lossy:
            raise StyError(
                "refusing lossy TrackData 96->80 conversion with non-default late-model extension"
            )
        warnings = []
        if any(value != V5_EXTENSION_DEFAULT for value in extension):
            warnings.append(f"discarded non-default v5 extension {extension.hex(' ')}")
        if any(late_extension):
            warnings.append(
                f"discarded non-default late-model extension {late_extension.hex(' ')}"
            )
        return track_data[:80], warnings
    raise StyError(f"unsupported TrackData conversion {len(track_data)}->{target_length}")


def element_info_model(element_info: bytes) -> dict[str, Any]:
    if len(element_info) not in {27, 28, 44}:
        raise StyError(f"unsupported ElementInfo length {len(element_info)}")
    extension = element_info[28:44] if len(element_info) == 44 else None
    return {
        "length": len(element_info),
        "core_hex": element_info[:28].hex(" "),
        "missing_v4_byte_count": 1 if len(element_info) == 27 else 0,
        "v5_extension_hex": extension.hex(" ") if extension is not None else None,
        "safe_v5_to_v4_drop": extension == ELEMENT_INFO_V5_EXTENSION,
    }


def convert_element_info(
    element_info: bytes,
    target_length: int,
    allow_lossy: bool = False,
) -> tuple[bytes, list[str]]:
    if len(element_info) == target_length:
        return element_info, []
    if len(element_info) == 28 and target_length == 44:
        return element_info + ELEMENT_INFO_V5_EXTENSION, [
            "appended corpus-exact canonical v5 ElementInfo extension"
        ]
    if len(element_info) == 44 and target_length == 28:
        extension = element_info[28:44]
        if extension != ELEMENT_INFO_V5_EXTENSION and not allow_lossy:
            raise StyError(
                "refusing lossy ElementInfo 44->28 conversion with non-default extension"
            )
        warnings = []
        if extension != ELEMENT_INFO_V5_EXTENSION:
            warnings.append(f"discarded non-default ElementInfo extension {extension.hex(' ')}")
        return element_info[:28], warnings
    raise StyError(f"unsupported ElementInfo conversion {len(element_info)}->{target_length}")


def style_info_model(payload: bytes, root_version: int) -> dict[str, Any]:
    if not payload:
        raise StyError("empty StyleInfo payload")
    name_length = payload[0]
    if 1 + name_length > len(payload):
        raise StyError("StyleInfo name exceeds payload")
    tail = payload[1 + name_length:]
    expected = (
        STYLE_INFO_V5_TAIL_SIZE
        if root_version == 5
        else STYLE_INFO_V4_TAIL_SIZE
    )
    if root_version not in {2, 4, 5} or len(tail) != expected:
        raise StyError(
            f"unsupported StyleInfo root/tail combination v{root_version}/{len(tail)}"
        )
    content_mask = int.from_bytes(tail[3:5], "big")
    index_mask = (
        content_mask
        if root_version in {2, 4}
        else int.from_bytes(tail[16:18], "big")
    )
    if content_mask & ~index_mask:
        raise StyError("StyleInfo content bitmap is not a subset of index bitmap")
    return {
        "name": payload[1:1 + name_length].decode("latin1", "replace"),
        "tail_length": len(tail),
        "tail_hex": tail.hex(" "),
        "content_bitmap": f"{content_mask:04x}",
        "content_element_indices": [
            index for index in range(15) if content_mask & (1 << index)
        ],
        "index_bitmap": f"{index_mask:04x}",
        "stored_element_indices": [
            index for index in range(15) if index_mask & (1 << index)
        ],
    }


def convert_style_info(payload: bytes, source_version: int, target_version: int) -> bytes:
    model = style_info_model(payload, source_version)
    name_length = payload[0]
    prefix = payload[:1 + name_length]
    tail = bytearray(payload[1 + name_length:])
    if source_version == target_version:
        return payload
    if source_version == 4 and target_version == 5:
        tail.extend(int(model["index_bitmap"], 16).to_bytes(2, "big"))
        return prefix + tail
    if source_version == 5 and target_version == 4:
        return prefix + tail[:STYLE_INFO_V4_TAIL_SIZE]
    raise StyError(f"unsupported inner STYLE conversion v{source_version}->v{target_version}")


def convert_style_payload(
    decoded: bytes,
    target_version: int,
    allow_lossy: bool = False,
    expand_element_info: bool = False,
) -> tuple[bytes, list[str]]:
    if len(decoded) < 8 or decoded[0] != 1:
        raise StyError("invalid inner STYLE root")
    source_version = decoded[1]
    if source_version not in {4, 5} or target_version not in {4, 5}:
        raise StyError(f"unsupported inner STYLE conversion v{source_version}->v{target_version}")
    root_children = children(decoded)
    if not root_children or root_children[0]["id"] != 1:
        raise StyError("inner STYLE lacks leading StyleInfo")

    style_info = root_children[0]
    style_info_payload = decoded[style_info["offset"] + 8:style_info["end"]]
    source_info = style_info_model(style_info_payload, source_version)
    stored_indices = source_info["stored_element_indices"]
    content_indices = set(source_info["content_element_indices"])
    element_children = [item for item in root_children if item["id"] == 3]
    if len(element_children) != len(stored_indices):
        raise StyError(
            "StyleInfo index bitmap does not match stored Style Element count"
        )
    element_indices = dict(zip((item["offset"] for item in element_children), stored_indices))

    warnings: list[str] = []
    rebuilt_children = []
    for item in root_children:
        header = bytearray(decoded[item["offset"]:item["offset"] + 4])
        payload = decoded[item["offset"] + 8:item["end"]]
        if item is style_info:
            header[2] = 3 if target_version == 5 else 2
            payload = convert_style_info(payload, source_version, target_version)
        elif item["id"] == 3:
            element_index = element_indices[item["offset"]]
            if target_version == 4 and element_index not in content_indices:
                warnings.append(
                    f"removed v5 Style Element index {element_index} marked empty"
                )
                continue
            element_parts = children(decoded, item["offset"])
            rebuilt_parts = []
            for part_index, part in enumerate(element_parts):
                part_header = decoded[part["offset"]:part["offset"] + 4]
                part_payload = decoded[part["offset"] + 8:part["end"]]
                if part_index == 0:
                    target_length = 28 if target_version == 4 else (
                        44 if expand_element_info else len(part_payload)
                    )
                    part_payload, part_warnings = convert_element_info(
                        part_payload, target_length, allow_lossy
                    )
                    warnings.extend(part_warnings)
                elif part_index == 1:
                    target_length = 80 if target_version == 4 else 88
                    part_payload, part_warnings = convert_track_data(
                        part_payload, target_length, allow_lossy
                    )
                    warnings.extend(part_warnings)
                rebuilt_parts.append(build_record(part_header, part_payload))
            payload = b"".join(rebuilt_parts)
        rebuilt_children.append(build_record(bytes(header), payload))

    root_header = bytearray(decoded[:4])
    root_header[1] = target_version
    converted = build_record(bytes(root_header), b"".join(rebuilt_children))
    style_info_model(
        converted[children(converted)[0]["offset"] + 8:children(converted)[0]["end"]],
        target_version,
    )
    return converted, warnings


def neutral_style(decoded: bytes) -> dict[str, Any]:
    result = style_summary(decoded)
    result["decoded_sha256"] = hashlib.sha256(decoded).hexdigest()
    if "structure_error" in result:
        return result
    root_children = children(decoded)
    try:
        pool_model = pattern_pool_model(decoded)
        result["pattern_pool"] = {
            key: value
            for key, value in pool_model.items()
            if key != "elements"
        }
        pool_elements = pool_model["elements"]
    except StyError as error:
        result["pattern_pool_error"] = str(error)
        pool_elements = []
    elements = [item for item in root_children if item["id"] == 0x03]
    neutral_elements = []
    for index, element in enumerate(elements):
        element_children = children(decoded, element["offset"])
        row: dict[str, Any] = {"index": index, "child_count": len(element_children)}
        if element_children:
            info = element_children[0]
            info_data = decoded[info["offset"] + 8:info["end"]]
            row["element_info"] = {
                "length": len(info_data),
                "sha256": hashlib.sha256(info_data).hexdigest(),
                "raw_hex": info_data.hex(),
            }
            if len(info_data) in {27, 28, 44}:
                row["element_info"]["model"] = element_info_model(info_data)
                if len(info_data) == 27:
                    row["element_info"]["conversion_hint"] = {
                        "target_length": 28,
                        "safe_without_loss": False,
                        "blocked_reason": "v2 missing byte has no proven canonical value",
                    }
                else:
                    target_length = 44 if len(info_data) == 28 else 28
                    row["element_info"]["conversion_hint"] = {
                        "target_length": target_length,
                        "safe_without_loss": (
                            True
                            if len(info_data) == 28
                            else info_data[28:44] == ELEMENT_INFO_V5_EXTENSION
                        ),
                    }
        if len(element_children) >= 2:
            track = element_children[1]
            track_data = decoded[track["offset"] + 8:track["end"]]
            lanes = [
                list(track_data[position:position + 8])
                for position in range(0, len(track_data), 8)
            ]
            track_record: dict[str, Any] = {
                "length": len(track_data),
                "sha256": hashlib.sha256(track_data).hexdigest(),
                "lanes_of_8": lanes,
                "raw_hex": track_data.hex(),
            }
            if len(track_data) in {48, 80, 88, 96}:
                track_record["model"] = track_data_model(track_data)
                if len(track_data) == 48:
                    track_record["conversion_hint"] = {
                        "target_length": 80,
                        "safe_without_loss": False,
                        "blocked_reason": "v2 lacks 24 auxiliary and 8 NTT/NTR bytes",
                    }
                elif len(track_data) == 80:
                    converted, warnings = convert_track_data(track_data, 88)
                    track_record["conversion_hint"] = {
                        "target_length": 88,
                        "result_sha256": hashlib.sha256(converted).hexdigest(),
                        "warnings": warnings,
                    }
                else:
                    model = track_record["model"]
                    track_record["conversion_hint"] = {
                        "target_length": 80,
                        "safe_without_loss": model["safe_to_80_drop"],
                        "extension_hex": track_data[80:].hex(" "),
                    }
            row["track_data"] = track_record
        row["chord_variation_count"] = max(0, len(element_children) - 2)
        if index < len(pool_elements):
            row["pattern_usage"] = pool_elements[index]
        neutral_elements.append(row)
    result["elements"] = neutral_elements
    return result


def neutral_export(raw: bytes, source: str) -> dict[str, Any]:
    native, container, pages = decode_input(raw)
    outer, framing = parse_indexed_native(native)
    styles = []
    style_performances = []
    for position, item in enumerate(outer):
        if item["id"] not in {0x06, 0x09}:
            continue
        payload = native[item["offset"] + 8:item["end"]]
        row: dict[str, Any] = {
            "resource_position": position,
            "offset": item["offset"],
            "outer_version": item["version"],
            "outer_release": item["release"],
        }
        try:
            decoded, metadata = decode_oc3x_generic(payload)
            row["codec"] = metadata
            if item["id"] == 0x06:
                row["style"] = neutral_style(decoded)
            else:
                row["style_performance"] = type09_model(decoded)["neutral"]
        except StyError as error:
            row["decode_error"] = str(error)
        if item["id"] == 0x06:
            styles.append(row)
        else:
            style_performances.append(row)
    return {
        "source": source,
        "input_container": container,
        "oc20_pages": pages,
        "framing": framing,
        "style_count": len(styles),
        "decoded_style_count": sum("style" in item for item in styles),
        "styles": styles,
        "style_performance_count": len(style_performances),
        "decoded_style_performance_count": sum(
            "style_performance" in item for item in style_performances
        ),
        "style_performances": style_performances,
    }


def conversion_preview(
    raw: bytes,
    source: str,
    target_version: int,
    allow_lossy: bool = False,
    expand_element_info: bool = False,
) -> dict[str, Any]:
    native, container, pages = decode_input(raw)
    outer, framing = parse_indexed_native(native)
    styles = []
    for position, item in enumerate(outer):
        if item["id"] != 0x06:
            continue
        row: dict[str, Any] = {
            "resource_position": position,
            "offset": item["offset"],
        }
        try:
            decoded, _ = decode_oc3x_generic(native[item["offset"] + 8:item["end"]])
            row["source_version"] = decoded[1] if len(decoded) > 1 else None
            row["source"] = style_summary(decoded)
            converted, warnings = convert_style_payload(
                decoded,
                target_version,
                allow_lossy=allow_lossy,
                expand_element_info=expand_element_info,
            )
            row["target"] = style_summary(converted)
            row["target_decoded_size"] = len(converted)
            row["target_sha256"] = hashlib.sha256(converted).hexdigest()
            row["warnings"] = warnings
        except StyError as error:
            row["conversion_error"] = str(error)
        styles.append(row)
    return {
        "source": source,
        "input_container": container,
        "oc20_pages": pages,
        "framing": framing,
        "target_inner_version": target_version,
        "allow_lossy": allow_lossy,
        "expand_element_info": expand_element_info,
        "style_count": len(styles),
        "convertible_style_count": sum("target" in item for item in styles),
        "conversion_error_count": sum("conversion_error" in item for item in styles),
        "styles": styles,
    }


def inspect_bytes(raw: bytes, source: str) -> dict[str, Any]:
    native, container, pages = decode_input(raw)
    outer, framing = parse_indexed_native(native)
    resources = []
    decode_errors = 0
    for item in outer:
        if item["id"] not in RESOURCE_IDS:
            continue
        payload = native[item["offset"] + 8:item["end"]]
        resource: dict[str, Any] = {
            "offset": item["offset"],
            "id": item["id"],
            "version": item["version"],
            "release": item["release"],
            "flags": item["flags"],
            "compressed_size": len(payload),
        }
        try:
            decoded, metadata = decode_oc3x_generic(payload)
            resource.update(metadata)
            resource.update({
                "decoded_size": len(decoded),
                "decoded_root": decoded[:4].hex(" "),
                "sha256": hashlib.sha256(decoded).hexdigest(),
            })
            if item["id"] == 0x06:
                resource["style"] = style_summary(decoded)
            elif item["id"] == 0x09:
                resource["style_performance"] = type09_model(decoded)
        except StyError as error:
            decode_errors += 1
            resource["wrapper"] = payload[:4].decode("ascii", "replace")
            resource["decode_error"] = str(error)
        resources.append(resource)
    return {
        "source": source,
        "input_size": len(raw),
        "input_sha256": hashlib.sha256(raw).hexdigest(),
        "input_container": container,
        "native_size": len(native),
        "native_sha256": hashlib.sha256(native).hexdigest(),
        "oc20_pages": pages,
        "framing": framing,
        "indexed_entries": len(outer),
        "resource_count": len(resources),
        "decoded_resource_count": len(resources) - decode_errors,
        "decode_error_count": decode_errors,
        "resources": resources,
    }


def rebuild_native(native: bytes) -> bytes:
    outer = parse_native(native)
    rebuilt_children = []
    indexed_offsets = []
    current_offset = 8
    terminal_header = None
    for item in outer:
        header = native[item["offset"]:item["offset"] + 4]
        payload = native[item["offset"] + 8:item["end"]]
        if item["id"] == 0xFE:
            terminal_header = header
            continue
        if item["id"] in RESOURCE_IDS:
            decoded, magic = decode_oc3x(payload)
            payload = encode_oc3x(decoded, magic)
        child = header + len(payload).to_bytes(4, "big") + payload
        if item["id"] in INDEXED_IDS:
            indexed_offsets.append(current_offset)
        rebuilt_children.append(child)
        current_offset += len(child)
    if terminal_header is None:
        raise StyError("missing terminal FE header")
    index_payload = (
        b"KBEG"
        + b"".join(offset.to_bytes(4, "big") for offset in indexed_offsets)
        + b"KEND"
        + len(indexed_offsets).to_bytes(4, "big")
    )
    terminal = terminal_header + len(index_payload).to_bytes(4, "big") + index_payload
    body = b"".join(rebuilt_children) + terminal
    return native[:4] + len(body).to_bytes(4, "big") + body


def replace_native_resource(native: bytes, target_offset: int, payload: bytes) -> bytes:
    outer = parse_native(native)
    rebuilt_children = []
    indexed_offsets = []
    current_offset = 8
    terminal_header = None
    replaced = False
    for item in outer:
        header = native[item["offset"]:item["offset"] + 4]
        if item["id"] == 0xFE:
            terminal_header = header
            continue
        if item["offset"] == target_offset:
            child = header + len(payload).to_bytes(4, "big") + payload
            replaced = True
        else:
            child = native[item["offset"]:item["end"]]
        if item["id"] in INDEXED_IDS:
            indexed_offsets.append(current_offset)
        rebuilt_children.append(child)
        current_offset += len(child)
    if not replaced:
        raise StyError("target resource offset was not found")
    if terminal_header is None:
        raise StyError("missing terminal FE header")
    index_payload = (
        b"KBEG"
        + b"".join(offset.to_bytes(4, "big") for offset in indexed_offsets)
        + b"KEND"
        + len(indexed_offsets).to_bytes(4, "big")
    )
    terminal = terminal_header + len(index_payload).to_bytes(4, "big") + index_payload
    body = b"".join(rebuilt_children) + terminal
    rebuilt = native[:4] + len(body).to_bytes(4, "big") + body
    parse_native(rebuilt)
    return rebuilt


def convert_style_bytes(
    raw: bytes,
    target_version: int,
    allow_lossy: bool = False,
    expand_element_info: bool = False,
    resource_positions: list[int] | None = None,
) -> tuple[bytes, dict[str, Any]]:
    native, input_container, pages = decode_input(raw)
    indexed, framing = parse_indexed_native(native)
    original_indexed_payloads = [
        native[item["offset"] + 8:item["end"]]
        for item in indexed
    ]
    selected_positions = set(resource_positions or [])
    if len(selected_positions) != len(resource_positions or []):
        raise StyError("duplicate --resource-position value")
    if selected_positions and not selected_positions.issubset(range(len(indexed))):
        raise StyError("resource position is outside indexed resource range")
    conversions = []
    for resource_position, item in enumerate(indexed):
        if item["id"] != 0x06:
            continue
        if selected_positions and resource_position not in selected_positions:
            continue
        compressed = original_indexed_payloads[resource_position]
        decoded, metadata = decode_oc3x_generic(compressed)
        converted, warnings = convert_style_payload(
            decoded,
            target_version,
            allow_lossy=allow_lossy,
            expand_element_info=expand_element_info,
        )
        trailer = bytes.fromhex(metadata["trailer"])
        encoded = encode_oc3x(converted, str(metadata["wrapper"])) + trailer
        verified, verified_metadata = decode_oc3x_generic(encoded)
        if verified != converted:
            raise StyError(
                f"STYLE resource {resource_position} failed encode/decode verification"
            )
        if verified_metadata["trailer"] != metadata["trailer"]:
            raise StyError(f"STYLE resource {resource_position} trailer changed")
        conversions.append({
            "resource_position": resource_position,
            "source_decoded": decoded,
            "target_decoded": converted,
            "encoded": encoded,
            "warnings": warnings,
            "wrapper": metadata["wrapper"],
            "trailer_hex": metadata["trailer"],
        })
    if not conversions:
        raise StyError("input contains no indexed ID-06 STYLE resources")
    if selected_positions != {
        item["resource_position"] for item in conversions
    } and selected_positions:
        raise StyError("one or more selected resources are not ID-06 STYLE resources")

    rebuilt = native
    for conversion in conversions:
        current_indexed, _ = parse_indexed_native(rebuilt)
        resource_position = conversion["resource_position"]
        if resource_position >= len(current_indexed):
            raise StyError("indexed resource count changed during STYLE conversion")
        current_item = current_indexed[resource_position]
        if current_item["id"] != 0x06:
            raise StyError("indexed resource identity changed during STYLE conversion")
        rebuilt = replace_native_resource(
            rebuilt,
            current_item["offset"],
            conversion["encoded"],
        )

    rebuilt_indexed, rebuilt_framing = parse_indexed_native(rebuilt)
    if len(rebuilt_indexed) != len(indexed):
        raise StyError("indexed resource count changed after STYLE conversion")
    converted_by_position = {
        item["resource_position"]: item for item in conversions
    }
    for resource_position, (before_item, after_item) in enumerate(
        zip(indexed, rebuilt_indexed)
    ):
        if before_item["id"] != after_item["id"]:
            raise StyError("indexed resource type changed after STYLE conversion")
        after_payload = rebuilt[after_item["offset"] + 8:after_item["end"]]
        conversion = converted_by_position.get(resource_position)
        if conversion is None:
            if after_payload != original_indexed_payloads[resource_position]:
                raise StyError(
                    f"non-STYLE indexed resource {resource_position} changed"
                )
            continue
        verified, _ = decode_oc3x_generic(after_payload)
        if verified != conversion["target_decoded"]:
            raise StyError(
                f"STYLE resource {resource_position} changed after native rebuild"
            )

    style_reports = []
    for conversion in conversions:
        source_decoded = conversion["source_decoded"]
        target_decoded = conversion["target_decoded"]
        style_reports.append({
            "resource_position": conversion["resource_position"],
            "wrapper": conversion["wrapper"],
            "trailer_hex": conversion["trailer_hex"],
            "source": style_summary(source_decoded),
            "target": style_summary(target_decoded),
            "source_sha256": hashlib.sha256(source_decoded).hexdigest(),
            "target_sha256": hashlib.sha256(target_decoded).hexdigest(),
            "warnings": conversion["warnings"],
        })
    return rebuilt, {
        "input_container": input_container,
        "output_container": "native",
        "oc20_pages": len(pages),
        "input_framing": framing,
        "output_framing": rebuilt_framing,
        "target_inner_version": target_version,
        "allow_lossy": allow_lossy,
        "expand_element_info": expand_element_info,
        "selected_resource_positions": sorted(selected_positions),
        "converted_style_count": len(conversions),
        "all_non_style_indexed_resources_byte_exact": True,
        "all_converted_styles_verified_round_trip": True,
        "styles": style_reports,
    }


def patch_sts_bytes(
    raw: bytes,
    resource_position: int,
    slot_index: int,
    track_index: int,
    field: str,
    value: int,
) -> tuple[bytes, dict[str, Any]]:
    native, input_container, pages = decode_input(raw)
    indexed, _ = parse_indexed_native(native)
    if resource_position not in range(len(indexed)):
        raise StyError("resource position is outside indexed resource range")
    item = indexed[resource_position]
    if item["id"] != 0x09:
        raise StyError("selected resource is not an ID-09 Style Performance resource")
    compressed = native[item["offset"] + 8:item["end"]]
    decoded, metadata = decode_oc3x_generic(compressed)
    patched, change = patch_type09_mixer_field(
        decoded,
        slot_index,
        track_index,
        field,
        value,
    )
    decoded_differences = [
        index for index, (before, after) in enumerate(zip(decoded, patched))
        if before != after
    ]
    expected_differences = 1 if change["changed"] else 0
    if len(decoded_differences) != expected_differences:
        raise StyError("STS patch changed an unexpected number of decoded bytes")
    trailer = bytes.fromhex(metadata["trailer"])
    encoded = encode_oc3x(patched, str(metadata["wrapper"])) + trailer
    rebuilt = replace_native_resource(native, item["offset"], encoded)
    rebuilt_indexed, _ = parse_indexed_native(rebuilt)
    rebuilt_item = rebuilt_indexed[resource_position]
    verified, _ = decode_oc3x_generic(
        rebuilt[rebuilt_item["offset"] + 8:rebuilt_item["end"]]
    )
    if verified != patched:
        raise StyError("patched STS resource failed encode/decode verification")
    return rebuilt, {
        "input_container": input_container,
        "output_container": "native",
        "oc20_pages": len(pages),
        "resource_position": resource_position,
        "resource_offset_before": item["offset"],
        "resource_offset_after": rebuilt_item["offset"],
        "decoded_sha256_before": hashlib.sha256(decoded).hexdigest(),
        "decoded_sha256_after": hashlib.sha256(patched).hexdigest(),
        "decoded_byte_differences": decoded_differences,
        "change": change,
        "selector_unchanged": True,
        "verified_round_trip": True,
    }


def repack_bytes(raw: bytes) -> tuple[bytes, dict[str, Any]]:
    original_native, input_container, original_pages = decode_input(raw)
    rebuilt_native = rebuild_native(original_native)
    parse_native(rebuilt_native)
    original_resources = inspect_bytes(raw, "original")["resources"]
    rebuilt_resources = inspect_bytes(rebuilt_native, "rebuilt")["resources"]
    if [item["sha256"] for item in original_resources] != [
        item["sha256"] for item in rebuilt_resources
    ]:
        raise StyError("resource payload changed during native rebuild")
    return rebuilt_native, {
        "input_container": input_container,
        "output_container": "native",
        "original_native_sha256": hashlib.sha256(original_native).hexdigest(),
        "rebuilt_native_sha256": hashlib.sha256(rebuilt_native).hexdigest(),
        "original_pages": len(original_pages),
        "original_size": len(raw),
        "rebuilt_size": len(rebuilt_native),
        "semantic_resource_count": sum(
            item["id"] in RESOURCE_IDS for item in parse_native(rebuilt_native)
        ),
        "all_decoded_resources_preserved": True,
    }


def semantic_diff(left_raw: bytes, right_raw: bytes, left_name: str, right_name: str) -> dict[str, Any]:
    left = inspect_bytes(left_raw, left_name)
    right = inspect_bytes(right_raw, right_name)
    pairs = []
    changed = 0
    maximum = max(len(left["resources"]), len(right["resources"]))
    for index in range(maximum):
        left_item = left["resources"][index] if index < len(left["resources"]) else None
        right_item = right["resources"][index] if index < len(right["resources"]) else None
        same = left_item is not None and right_item is not None and (
            left_item["id"] == right_item["id"]
            and (
                (
                    left_item.get("sha256") is not None
                    and left_item.get("sha256") == right_item.get("sha256")
                )
                or (
                    left_item.get("decode_error") is not None
                    and left_item.get("decode_error") == right_item.get("decode_error")
                    and left_item.get("wrapper") == right_item.get("wrapper")
                )
            )
        )
        if not same:
            changed += 1
        row: dict[str, Any] = {"index": index, "same_decoded_resource": same}
        for label, item in (("left", left_item), ("right", right_item)):
            if item is None:
                row[label] = None
                continue
            summary: dict[str, Any] = {
                "id": item["id"],
                "version": item["version"],
                "release": item["release"],
                "decoded_size": item.get("decoded_size"),
                "decoded_root": item.get("decoded_root"),
                "sha256": item.get("sha256"),
            }
            if "decode_error" in item:
                summary["decode_error"] = item["decode_error"]
            if "style" in item:
                summary["style"] = item["style"]
            row[label] = summary
        pairs.append(row)
    return {
        "left": {
            "source": left_name,
            "container": left["input_container"],
            "resource_count": left["resource_count"],
            "native_sha256": left["native_sha256"],
        },
        "right": {
            "source": right_name,
            "container": right["input_container"],
            "resource_count": right["resource_count"],
            "native_sha256": right["native_sha256"],
        },
        "changed_resource_positions": changed,
        "resource_pairs": pairs,
    }


def type09_resource_models(raw: bytes) -> tuple[str, list[dict[str, Any]]]:
    native, container, _ = decode_input(raw)
    indexed, _ = parse_indexed_native(native)
    style_names = []
    for item in indexed:
        if item["id"] != 0x06:
            continue
        payload = native[item["offset"] + 8:item["end"]]
        try:
            decoded, _ = decode_oc3x_generic(payload)
            style_names.append(style_summary(decoded).get("name"))
        except StyError:
            style_names.append(None)
    resources = []
    for indexed_position, item in enumerate(indexed):
        if item["id"] != 0x09:
            continue
        ordinal = len(resources)
        payload = native[item["offset"] + 8:item["end"]]
        row: dict[str, Any] = {
            "id09_ordinal": ordinal,
            "indexed_position": indexed_position,
            "offset": item["offset"],
            "companion_style_name": (
                style_names[ordinal] if ordinal < len(style_names) else None
            ),
        }
        try:
            decoded, metadata = decode_oc3x_generic(payload)
            row.update({
                "decoded_sha256": hashlib.sha256(decoded).hexdigest(),
                "wrapper": metadata["wrapper"],
                "model": type09_model(decoded),
            })
        except StyError as error:
            row["decode_error"] = str(error)
        resources.append(row)
    return container, resources


def normalized_sts_pair_name(value: str | None) -> str:
    if not value:
        return ""
    return re.sub(r"\s+", " ", value.strip()).casefold()


def named_type09_resources(
    resources: list[dict[str, Any]],
) -> tuple[dict[tuple[str, int], dict[str, Any]], list[dict[str, Any]]]:
    occurrences: dict[str, int] = {}
    named = {}
    unnamed = []
    for resource in resources:
        normalized = normalized_sts_pair_name(resource.get("companion_style_name"))
        if not normalized:
            unnamed.append(resource)
            continue
        occurrence = occurrences.get(normalized, 0)
        occurrences[normalized] = occurrence + 1
        named[(normalized, occurrence)] = resource
    return named, unnamed


def sts_track_projection(track: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {
        "role": track["role"],
        "volume": track["volume"],
        "pan": track["pan"],
        "fx_c_send": track["fx_c_send"],
        "fx_d_send": track["fx_d_send"],
    }
    if track.get("selector_kind") == "user":
        result["selector"] = {
            "kind": "user",
            "namespace": track["user_namespace"],
            "bank": track["user_bank"],
            "position": track["user_position"],
        }
    elif track.get("selector_kind") == "empty":
        result["selector"] = {
            "kind": "empty",
        }
    elif "bank_msb" in track:
        result["selector"] = {
            "kind": "native_legacy",
            "bank_msb": track["bank_msb"],
            "bank_lsb": track["bank_lsb"],
            "program": track["program"],
            "muted": track["muted"],
            "status": track["status"],
        }
    else:
        result["selector"] = {
            "kind": track["selector_family"],
            "raw_hex": track["selector_raw_hex"],
        }
    preserved = {}
    if "detail" in track:
        preserved["detail_sha256"] = track["detail"]["raw_sha256"]
    if isinstance(track.get("status"), dict):
        preserved["status_sha256"] = track["status"]["raw_sha256"]
    if "legacy_companion" in track:
        preserved["legacy_companion_sha256"] = track["legacy_companion"][
            "raw_sha256"
        ]
    if preserved:
        result["preserved_companions"] = preserved
    return result


def raw_hex_byte_diff(left_hex: str, right_hex: str) -> dict[str, Any]:
    left = bytes.fromhex(left_hex)
    right = bytes.fromhex(right_hex)
    common_size = min(len(left), len(right))
    changes = [
        {
            "offset": offset,
            "left": left[offset],
            "right": right[offset],
        }
        for offset in range(common_size)
        if left[offset] != right[offset]
    ]
    if len(left) != len(right):
        changes.extend(
            {
                "offset": offset,
                "left": left[offset] if offset < len(left) else None,
                "right": right[offset] if offset < len(right) else None,
            }
            for offset in range(common_size, max(len(left), len(right)))
        )
    return {
        "left_size": len(left),
        "right_size": len(right),
        "changed_byte_count": len(changes),
        "byte_changes": changes,
    }


def sts_track_diff(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    left_projection = sts_track_projection(left)
    right_projection = sts_track_projection(right)
    fields = ("volume", "pan", "fx_c_send", "fx_d_send", "selector")
    changes = {
        field: {
            "left": left_projection[field],
            "right": right_projection[field],
        }
        for field in fields
        if left_projection[field] != right_projection[field]
    }
    left_preserved = left_projection.get("preserved_companions", {})
    right_preserved = right_projection.get("preserved_companions", {})
    companion_changes = {}
    companion_fields = {
        "detail_sha256": "detail",
        "status_sha256": "status",
        "legacy_companion_sha256": "legacy_companion",
    }
    for key in sorted(set(left_preserved) | set(right_preserved)):
        if left_preserved.get(key) == right_preserved.get(key):
            continue
        change: dict[str, Any] = {
            "left": left_preserved.get(key),
            "right": right_preserved.get(key),
        }
        model_key = companion_fields.get(key)
        left_model = left.get(model_key) if model_key else None
        right_model = right.get(model_key) if model_key else None
        if (
            isinstance(left_model, dict)
            and isinstance(right_model, dict)
            and "raw_hex" in left_model
            and "raw_hex" in right_model
        ):
            change.update(raw_hex_byte_diff(
                left_model["raw_hex"],
                right_model["raw_hex"],
            ))
        companion_changes[key] = change
    return {
        "track": left["track"],
        "role": left["role"],
        "changed": bool(changes or companion_changes),
        "field_changes": changes,
        "companion_changes": companion_changes,
    }


def style_performance_diff(
    left_raw: bytes,
    right_raw: bytes,
    left_name: str,
    right_name: str,
    pair_by: str = "ordinal",
) -> dict[str, Any]:
    left_container, left_resources = type09_resource_models(left_raw)
    right_container, right_resources = type09_resource_models(right_raw)
    resource_pairs = []
    changed_resources = 0
    changed_slots = 0
    changed_tracks = 0
    if pair_by == "ordinal":
        maximum_resources = max(len(left_resources), len(right_resources))
        paired_resources = [
            (
                f"ordinal:{ordinal}",
                left_resources[ordinal] if ordinal < len(left_resources) else None,
                right_resources[ordinal] if ordinal < len(right_resources) else None,
            )
            for ordinal in range(maximum_resources)
        ]
        unnamed_left = []
        unnamed_right = []
    elif pair_by == "style-name":
        left_named, unnamed_left = named_type09_resources(left_resources)
        right_named, unnamed_right = named_type09_resources(right_resources)
        keys = list(left_named)
        keys.extend(key for key in right_named if key not in left_named)
        paired_resources = [
            (
                f"style:{key[0]}#{key[1]}",
                left_named.get(key),
                right_named.get(key),
            )
            for key in keys
        ]
        paired_resources.extend(
            (f"left-unpaired:{item['id09_ordinal']}", item, None)
            for item in unnamed_left
        )
        paired_resources.extend(
            (f"right-unpaired:{item['id09_ordinal']}", None, item)
            for item in unnamed_right
        )
    else:
        raise StyError(f"unsupported STS resource pairing mode {pair_by!r}")
    for pairing_key, left_resource, right_resource in paired_resources:
        row: dict[str, Any] = {
            "pairing_key": pairing_key,
            "left_id09_ordinal": left_resource and left_resource["id09_ordinal"],
            "right_id09_ordinal": right_resource and right_resource["id09_ordinal"],
            "left_style_name": left_resource and left_resource["companion_style_name"],
            "right_style_name": right_resource and right_resource["companion_style_name"],
        }
        if left_resource is None or right_resource is None:
            row.update({
                "changed": True,
                "left": left_resource,
                "right": right_resource,
                "reason": "missing_resource",
            })
            changed_resources += 1
            resource_pairs.append(row)
            continue
        row["left_indexed_position"] = left_resource["indexed_position"]
        row["right_indexed_position"] = right_resource["indexed_position"]
        if "decode_error" in left_resource or "decode_error" in right_resource:
            same_error = left_resource.get("decode_error") == right_resource.get(
                "decode_error"
            )
            row.update({
                "changed": not same_error,
                "left_decode_error": left_resource.get("decode_error"),
                "right_decode_error": right_resource.get("decode_error"),
                "reason": "decode_error",
            })
            changed_resources += not same_error
            resource_pairs.append(row)
            continue
        left_model = left_resource["model"]
        right_model = right_resource["model"]
        left_neutral = left_model["neutral"]
        right_neutral = right_model["neutral"]
        row["left_layout"] = left_neutral["source_layout"]
        row["right_layout"] = right_neutral["source_layout"]
        slots = []
        maximum_slots = max(len(left_neutral["slots"]), len(right_neutral["slots"]))
        resource_changed = left_resource["decoded_sha256"] != right_resource[
            "decoded_sha256"
        ]
        for slot_index in range(maximum_slots):
            left_slot = (
                left_neutral["slots"][slot_index]
                if slot_index < len(left_neutral["slots"])
                else None
            )
            right_slot = (
                right_neutral["slots"][slot_index]
                if slot_index < len(right_neutral["slots"])
                else None
            )
            slot_row: dict[str, Any] = {"slot_index": slot_index}
            if left_slot is None or right_slot is None:
                slot_row.update({
                    "changed": True,
                    "left_name": left_slot and left_slot["name"],
                    "right_name": right_slot and right_slot["name"],
                    "reason": "missing_slot",
                })
                changed_slots += 1
                slots.append(slot_row)
                continue
            track_rows = [
                sts_track_diff(left_track, right_track)
                for left_track, right_track in zip(
                    left_slot["tracks"], right_slot["tracks"]
                )
            ]
            name_changed = left_slot["name"] != right_slot["name"]
            slot_changed = name_changed or any(item["changed"] for item in track_rows)
            changed_slots += slot_changed
            changed_tracks += sum(item["changed"] for item in track_rows)
            slot_row.update({
                "changed": slot_changed,
                "left_name": left_slot["name"],
                "right_name": right_slot["name"],
                "name_changed": name_changed,
                "tracks": track_rows,
            })
            slots.append(slot_row)
        row["changed"] = resource_changed or any(item["changed"] for item in slots)
        changed_resources += row["changed"]
        row["slots"] = slots
        resource_pairs.append(row)
    return {
        "schema": "korg-style-performance-diff-v1",
        "pairing_policy": pair_by,
        "left": {
            "source": left_name,
            "container": left_container,
            "id09_resources": len(left_resources),
        },
        "right": {
            "source": right_name,
            "container": right_container,
            "id09_resources": len(right_resources),
        },
        "changed_resources": changed_resources,
        "changed_slots": changed_slots,
        "changed_tracks": changed_tracks,
        "unpaired_unnamed_left": len(unnamed_left),
        "unpaired_unnamed_right": len(unnamed_right),
        "resource_pairs": resource_pairs,
    }


def changed_sts_track_locations(report: dict[str, Any]) -> list[dict[str, Any]]:
    locations = []
    for resource in report["resource_pairs"]:
        for slot in resource.get("slots", []):
            for track in slot.get("tracks", []):
                if not track.get("changed"):
                    continue
                locations.append({
                    "pairing_key": resource["pairing_key"],
                    "left_id09_ordinal": resource.get("left_id09_ordinal"),
                    "right_id09_ordinal": resource.get("right_id09_ordinal"),
                    "left_style_name": resource.get("left_style_name"),
                    "right_style_name": resource.get("right_style_name"),
                    "slot_index": slot["slot_index"],
                    "left_slot_name": slot.get("left_name"),
                    "right_slot_name": slot.get("right_name"),
                    "track": track["track"],
                    "role": track["role"],
                    "field_changes": track["field_changes"],
                    "companion_changes": track["companion_changes"],
                })
    return locations


def controlled_sts_evidence_audit(
    baseline_raw: bytes,
    repeat_raw: bytes,
    modified_raw: bytes,
    baseline_name: str,
    repeat_name: str,
    modified_name: str,
    pair_by: str = "style-name",
) -> dict[str, Any]:
    repeat_diff = style_performance_diff(
        baseline_raw,
        repeat_raw,
        baseline_name,
        repeat_name,
        pair_by=pair_by,
    )
    modified_diff = style_performance_diff(
        baseline_raw,
        modified_raw,
        baseline_name,
        modified_name,
        pair_by=pair_by,
    )
    repeat_stable = (
        repeat_diff["changed_resources"] == 0
        and repeat_diff["changed_slots"] == 0
        and repeat_diff["changed_tracks"] == 0
    )
    changed_tracks = changed_sts_track_locations(modified_diff)
    one_factor = len(changed_tracks) == 1
    return {
        "schema": "korg-controlled-sts-evidence-v1",
        "pairing_policy": pair_by,
        "baseline_repeat_stable": repeat_stable,
        "modified_one_factor_track": one_factor,
        "accepted": repeat_stable and one_factor,
        "changed_track_count": len(changed_tracks),
        "changed_tracks": changed_tracks,
        "baseline_repeat_summary": {
            "changed_resources": repeat_diff["changed_resources"],
            "changed_slots": repeat_diff["changed_slots"],
            "changed_tracks": repeat_diff["changed_tracks"],
        },
        "baseline_modified_summary": {
            "changed_resources": modified_diff["changed_resources"],
            "changed_slots": modified_diff["changed_slots"],
            "changed_tracks": modified_diff["changed_tracks"],
        },
        "rejection_reasons": [
            reason
            for condition, reason in (
                (repeat_stable, "baseline repeat is not byte-semantically stable"),
                (one_factor, "modified file does not change exactly one STS track"),
            )
            if not condition
        ],
    }


def write_json(value: Any, output: Path | None) -> None:
    rendered = json.dumps(value, indent=2, ensure_ascii=False) + "\n"
    if output is None:
        print(rendered, end="")
    else:
        output.write_text(rendered, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    inspect_parser = subparsers.add_parser("inspect")
    inspect_parser.add_argument("input", type=Path)
    inspect_parser.add_argument("--json", type=Path)

    repack_parser = subparsers.add_parser("repack")
    repack_parser.add_argument("input", type=Path)
    repack_parser.add_argument("output", type=Path)
    repack_parser.add_argument("--report", type=Path)

    diff_parser = subparsers.add_parser("diff")
    diff_parser.add_argument("left", type=Path)
    diff_parser.add_argument("right", type=Path)
    diff_parser.add_argument("--json", type=Path)

    sts_diff_parser = subparsers.add_parser("sts-diff")
    sts_diff_parser.add_argument("left", type=Path)
    sts_diff_parser.add_argument("right", type=Path)
    sts_diff_parser.add_argument(
        "--pair-by",
        choices=("ordinal", "style-name"),
        default="ordinal",
    )
    sts_diff_parser.add_argument("--json", type=Path)

    evidence_parser = subparsers.add_parser("evidence-audit")
    evidence_parser.add_argument("baseline", type=Path)
    evidence_parser.add_argument("repeat", type=Path)
    evidence_parser.add_argument("modified", type=Path)
    evidence_parser.add_argument(
        "--pair-by",
        choices=("ordinal", "style-name"),
        default="style-name",
    )
    evidence_parser.add_argument("--json", type=Path)

    neutral_parser = subparsers.add_parser("neutral")
    neutral_parser.add_argument("input", type=Path)
    neutral_parser.add_argument("--json", type=Path)

    preview_parser = subparsers.add_parser("preview-convert")
    preview_parser.add_argument("input", type=Path)
    preview_parser.add_argument("--target-version", type=int, choices=(4, 5), required=True)
    preview_parser.add_argument("--allow-lossy", action="store_true")
    preview_parser.add_argument("--expand-element-info", action="store_true")
    preview_parser.add_argument("--json", type=Path)

    convert_style_parser = subparsers.add_parser("convert-style")
    convert_style_parser.add_argument("input", type=Path)
    convert_style_parser.add_argument("output", type=Path)
    convert_style_parser.add_argument(
        "--target-version", type=int, choices=(4, 5), required=True
    )
    convert_style_parser.add_argument("--allow-lossy", action="store_true")
    convert_style_parser.add_argument("--expand-element-info", action="store_true")
    convert_style_parser.add_argument(
        "--resource-position", type=int, action="append", default=[]
    )
    convert_style_parser.add_argument("--report", type=Path)

    patch_sts_parser = subparsers.add_parser("patch-sts")
    patch_sts_parser.add_argument("input", type=Path)
    patch_sts_parser.add_argument("output", type=Path)
    patch_sts_parser.add_argument("--resource-position", type=int, required=True)
    patch_sts_parser.add_argument("--slot-index", type=int, required=True)
    patch_sts_parser.add_argument(
        "--track",
        choices=("lower", "upper1", "upper2", "upper3"),
        required=True,
    )
    patch_sts_parser.add_argument(
        "--field",
        choices=tuple(STS_MIXER_FIELDS),
        required=True,
    )
    patch_sts_parser.add_argument("--value", type=int, required=True)
    patch_sts_parser.add_argument("--report", type=Path)

    arguments = parser.parse_args()
    try:
        if arguments.command == "diff":
            report = semantic_diff(
                arguments.left.read_bytes(),
                arguments.right.read_bytes(),
                str(arguments.left),
                str(arguments.right),
            )
            write_json(report, arguments.json)
            return 0
        if arguments.command == "sts-diff":
            report = style_performance_diff(
                arguments.left.read_bytes(),
                arguments.right.read_bytes(),
                str(arguments.left),
                str(arguments.right),
                pair_by=arguments.pair_by,
            )
            write_json(report, arguments.json)
            return 0
        if arguments.command == "evidence-audit":
            report = controlled_sts_evidence_audit(
                arguments.baseline.read_bytes(),
                arguments.repeat.read_bytes(),
                arguments.modified.read_bytes(),
                str(arguments.baseline),
                str(arguments.repeat),
                str(arguments.modified),
                pair_by=arguments.pair_by,
            )
            write_json(report, arguments.json)
            return 0
        if arguments.command == "neutral":
            write_json(
                neutral_export(arguments.input.read_bytes(), str(arguments.input)),
                arguments.json,
            )
            return 0
        if arguments.command == "preview-convert":
            write_json(
                conversion_preview(
                    arguments.input.read_bytes(),
                    str(arguments.input),
                    arguments.target_version,
                    allow_lossy=arguments.allow_lossy,
                    expand_element_info=arguments.expand_element_info,
                ),
                arguments.json,
            )
            return 0
        if arguments.command == "convert-style":
            converted, report = convert_style_bytes(
                arguments.input.read_bytes(),
                arguments.target_version,
                allow_lossy=arguments.allow_lossy,
                expand_element_info=arguments.expand_element_info,
                resource_positions=arguments.resource_position,
            )
            arguments.output.write_bytes(converted)
            write_json(report, arguments.report)
            return 0
        if arguments.command == "patch-sts":
            track_index = ("lower", "upper1", "upper2", "upper3").index(
                arguments.track
            )
            patched, report = patch_sts_bytes(
                arguments.input.read_bytes(),
                arguments.resource_position,
                arguments.slot_index,
                track_index,
                arguments.field,
                arguments.value,
            )
            arguments.output.write_bytes(patched)
            write_json(report, arguments.report)
            return 0
        raw = arguments.input.read_bytes()
        if arguments.command == "inspect":
            write_json(inspect_bytes(raw, str(arguments.input)), arguments.json)
            return 0
        packed, report = repack_bytes(raw)
        arguments.output.write_bytes(packed)
        write_json(report, arguments.report)
        return 0
    except (OSError, StyError) as error:
        parser.error(str(error))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())