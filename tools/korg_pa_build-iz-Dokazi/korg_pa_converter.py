#!/usr/bin/env python3
"""KORG PA cross-model converter.

This module wraps the proven, evidence-based primitives in
``korg_sty_tool`` and adds a high-level *cross-model* orchestrator that
maps a source KORG PA arranger model onto a target KORG PA arranger model.

Evidence basis (preserved verbatim from the Phase-1/2/3 reverse-engineering
findings under ``prism-uploads/analysis``):

* Inner STYLE root versions observed in the corpus:
    - v2 root ``01 02 00 00``  -> StyleInfo tail 16 bytes,
                                 ElementInfo 27 bytes, TrackData 48 bytes
    - v4 root ``01 04 00 00``  -> StyleInfo tail 16 bytes,
                                 ElementInfo 28 bytes, TrackData 80 bytes
    - v5 root ``01 05 00 00``  -> StyleInfo tail 18 bytes,
                                 ElementInfo 28 or 44 bytes,
                                 TrackData 80, 88, or 96 bytes
* STS (ID-09) layout families:
    - ``legacy_two_child``  -> PA1 / PA80 / PA500 / PA600 / PA800 / PA2X
    - ``nested_new_generation`` -> PA3X / PA3XLE / PA4X / PA700 / PA900 / PA1000
* Native selector namespaces that are *proven* to round-trip:
    - empty selector (FF FF FF ...) <-> nested sentinel FF FF 00 FF ...
    - USER namespace-09 selector <-> nested 09 BANK 00 POSITION ... record
* Native selector namespaces that remain fail-closed:
    - native_reference_mode_00 (token 00 00 00 00)
    - native_reference_mode_FF (token FF 00 00 00)
  These tokens are constants and do not contain a bank/program address; the
  actual native selector lives in another companion field that we cannot
  derive without a controlled before/after experiment.

Cross-model conversion strategy
-------------------------------
1. The ``MODEL_REGISTRY`` maps each KORG PA model name to a ``ModelProfile``
   describing its inner STYLE version, ElementInfo length, TrackData length
   and STS layout family.
2. ``convert`` reads the source STY, decodes the outer container, then for
   every ID-06 STYLE resource and every ID-09 STS resource applies the
   appropriate transformation between the source and target model profiles.
3. The converter is *fail-closed*: any operation that is not backed by a
   proven transformation rule is rejected by default. The user may pass
   ``--allow-lossy`` to override the safe downgrade gates for the v5
   TrackData 88/96 -> 80 byte path and the 44 -> 28 byte ElementInfo
   downgrade.

The native selector remap (bank/program addresses) remains an external
evidence dependency: the controlled before/after pair documented in
``phase3-evidence-protocol.json`` must be supplied before the converter can
safely rewrite native select program addresses. Without that evidence the
converter preserves native select bytes verbatim.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from korg_sty_tool import (
    StyError,
    decode_input,
    parse_indexed_native,
    decode_oc3x_generic,
    encode_oc3x,
    build_record,
    children,
    style_summary,
    convert_style_payload,
    replace_native_resource,
    legacy_user_sts_to_nested,
    nested_user_sts_to_legacy,
    legacy_empty_sts_to_nested,
    nested_empty_sts_to_legacy,
    type09_model,
    neutral_legacy_type09,
    nested_type09_model,
    be32,
    INDEXED_IDS,
)


# ---------------------------------------------------------------------------
# Model registry
# ---------------------------------------------------------------------------

# Inner STYLE root version codes that are proven to occur in the corpus
INNER_VERSION_V2 = 2
INNER_VERSION_V4 = 4
INNER_VERSION_V5 = 5

# STS (ID-09) layout family identifiers
STS_LEGACY_TWO_CHILD = "legacy_two_child"
STS_NESTED_NEW_GENERATION = "nested_new_generation"


@dataclass(frozen=True)
class ModelProfile:
    """Documented format profile for a single KORG PA arranger model."""

    name: str
    inner_version: int
    element_info_length: int
    track_data_length: int
    sts_layout: str
    lineage: str
    notes: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "inner_version": self.inner_version,
            "element_info_length": self.element_info_length,
            "track_data_length": self.track_data_length,
            "sts_layout": self.sts_layout,
            "lineage": self.lineage,
            "notes": self.notes,
        }


# The registry below is grounded in:
#  * multi-model inspect report (``multi-model-sty-report.json``)
#  * inner-v2 structure map for PA800 factory corpus
#  * trackdata-conversion-map.json / elementinfo-conversion-map.json /
#    styleinfo-conversion-map.json confirmed geometries
#  * conversion-strings.json recovered from KORGPAManager.exe
#    (PA_OS_SOUND_PA80, PA_OS_SOUND_PA1X, PA_OS_SOUND_PA2X, PA_OS_SOUND_PA500,
#     PA_OS_SOUND_Pa600, PA_OS_SOUND_Pa3X, PA_OS_SOUND_Pa4X, PA_OS_SOUND_Pa900,
#     PA_OS_SOUND_PA1000)
MODEL_REGISTRY: dict[str, ModelProfile] = {
    "PA80": ModelProfile(
        name="PA80",
        inner_version=INNER_VERSION_V2,
        element_info_length=27,
        track_data_length=48,
        sts_layout=STS_LEGACY_TWO_CHILD,
        lineage="legacy",
        notes="Earliest generation; same outer/inner geometry as PA1X (legacy family).",
    ),
    "PA1X": ModelProfile(
        name="PA1X",
        inner_version=INNER_VERSION_V2,
        element_info_length=27,
        track_data_length=48,
        sts_layout=STS_LEGACY_TWO_CHILD,
        lineage="legacy",
        notes="Confirmed by multi-model inspect (Pa1.STY root 01 02).",
    ),
    "PA2X": ModelProfile(
        name="PA2X",
        inner_version=INNER_VERSION_V4,
        element_info_length=28,
        track_data_length=80,
        sts_layout=STS_LEGACY_TWO_CHILD,
        lineage="legacy",
        notes="Shares STY family with PA500/PA800 (Pa800-PA2-PA500.STY).",
    ),
    "PA500": ModelProfile(
        name="PA500",
        inner_version=INNER_VERSION_V4,
        element_info_length=28,
        track_data_length=80,
        sts_layout=STS_LEGACY_TWO_CHILD,
        lineage="legacy",
        notes="Confirmed by shared STY fixture with PA2X/PA800.",
    ),
    "PA600": ModelProfile(
        name="PA600",
        inner_version=INNER_VERSION_V4,
        element_info_length=28,
        track_data_length=80,
        sts_layout=STS_LEGACY_TWO_CHILD,
        lineage="legacy",
        notes="Recovered model token PA_OS_SOUND_Pa600; format follows PA500 lineage.",
    ),
    "PA800": ModelProfile(
        name="PA800",
        inner_version=INNER_VERSION_V4,
        element_info_length=28,
        track_data_length=80,
        sts_layout=STS_LEGACY_TWO_CHILD,
        lineage="legacy",
        notes="Phase-1 regression corpus (18 factory banks, 870 resources).",
    ),
    "PA3X": ModelProfile(
        name="PA3X",
        inner_version=INNER_VERSION_V5,
        element_info_length=28,
        track_data_length=88,
        sts_layout=STS_NESTED_NEW_GENERATION,
        lineage="nested",
        notes="Confirmed by Pa3x.STY root 01 05, EI 28, TD 88.",
    ),
    "PA3XLE": ModelProfile(
        name="PA3XLE",
        inner_version=INNER_VERSION_V5,
        element_info_length=28,
        track_data_length=88,
        sts_layout=STS_NESTED_NEW_GENERATION,
        lineage="nested",
        notes="Pa3xle.STY uses the same geometry as PA3X (sample fixture).",
    ),
    "PA4X": ModelProfile(
        name="PA4X",
        inner_version=INNER_VERSION_V5,
        element_info_length=44,
        track_data_length=88,
        sts_layout=STS_NESTED_NEW_GENERATION,
        lineage="nested",
        notes="Confirmed by Pa4x.STY root 01 05, EI 44, TD 88.",
    ),
    "PA700": ModelProfile(
        name="PA700",
        inner_version=INNER_VERSION_V5,
        element_info_length=44,
        track_data_length=96,
        sts_layout=STS_NESTED_NEW_GENERATION,
        lineage="nested",
        notes="Late model: TrackData 96 with extra 8-byte extension (all 00).",
    ),
    "PA900": ModelProfile(
        name="PA900",
        inner_version=INNER_VERSION_V5,
        element_info_length=28,
        track_data_length=88,
        sts_layout=STS_NESTED_NEW_GENERATION,
        lineage="nested",
        notes="Pa900.STY uses the same geometry as PA3X (sample fixture).",
    ),
    "PA1000": ModelProfile(
        name="PA1000",
        inner_version=INNER_VERSION_V5,
        element_info_length=44,
        track_data_length=96,
        sts_layout=STS_NESTED_NEW_GENERATION,
        lineage="nested",
        notes="Recovered model token PA_OS_SOUND_PA1000; follows PA700 family.",
    ),
}


def list_models() -> list[str]:
    return sorted(MODEL_REGISTRY.keys())


# ---------------------------------------------------------------------------
# STS (ID-09) cross-layout conversion
# ---------------------------------------------------------------------------

# Per-track selector namespaces that are *proven* to round-trip between the
# legacy and nested layouts. Anything outside this set stays fail-closed.
PROVEN_SELECTOR_KINDS = {
    "empty",
    "user_namespace_09",
}


def _split_id09_layout(decoded: bytes) -> tuple[str, dict[str, Any]]:
    """Return (layout, model) for a decoded ID-09 payload.

    Falls back to the structural model when the legacy/nested heuristic
    disagrees; the converter then refuses to touch the resource.
    """
    try:
        legacy = type09_model(decoded)
        return STS_LEGACY_TWO_CHILD, legacy
    except StyError:
        pass
    try:
        nested = nested_type09_model(decoded)
        return STS_NESTED_NEW_GENERATION, nested
    except StyError:
        pass
    raise StyError("ID-09 resource is neither legacy nor nested generation")


def _convert_legacy_to_nested_id09(
    decoded: bytes, target_layout: str
) -> tuple[bytes, list[str]]:
    if target_layout == STS_LEGACY_TWO_CHILD:
        return decoded, []
    # legacy -> nested
    legacy_model = type09_model(decoded)
    warnings: list[str] = []
    rebuilt_children: list[bytes] = []
    # We preserve every byte that is not the user/empty selector quartet.
    for slot in legacy_model["slots"]:
        # Re-emit each child (mixer + named slots) unchanged
        for child_id, child_payload in slot.get("raw_children", []):
            rebuilt_children.append(build_record(bytes([child_id & 0xFF, 0, 0, 0]), child_payload))
        # Translate the per-track sound records that have proven transform rules
        for track_index, track_record in enumerate(slot.get("tracks", [])):
            sel = track_record.get("selector_kind")
            raw = track_record.get("raw_record")
            if sel == "empty":
                rebuilt = legacy_empty_sts_to_nested(raw, track_index)
                rebuilt_children.append(built_id08_record(rebuilt))
            elif sel == "user_namespace_09":
                rebuilt = legacy_user_sts_to_nested(raw, track_index)
                rebuilt_children.append(built_id08_record(rebuilt))
            elif sel in {"native_reference_mode_00", "native_reference_mode_ff"}:
                warnings.append(
                    f"native selector at slot {slot.get('slot_index')} track {track_index} preserved raw; "
                    "cross-model native remap requires controlled before/after evidence"
                )
                # The legacy layout does not carry an ID-08 record we can re-emit
                # losslessly without rebuilding the nested container. We must fail
                # closed here to avoid inventing bytes.
                raise StyError(
                    f"native selector at slot {slot.get('slot_index')} track {track_index} "
                    "cannot be re-serialized in nested layout without controlled evidence"
                )
            else:
                raise StyError(
                    f"unknown selector kind {sel!r} at slot {slot.get('slot_index')} track {track_index}"
                )
    raise StyError("legacy-to-nested STS structural builder is not yet supported")


def built_id08_record(payload: bytes) -> bytes:
    """Build an ID-08 record wrapping the given payload."""
    return build_record(bytes([0x08, 0, 0, 0]), payload)


def _convert_nested_to_legacy_id09(
    decoded: bytes, target_layout: str
) -> tuple[bytes, list[str]]:
    if target_layout == STS_NESTED_NEW_GENERATION:
        return decoded, []
    raise StyError("nested-to-legacy STS structural builder is not yet supported")


def convert_id09_payload(
    decoded: bytes, source_layout: str, target_layout: str
) -> tuple[bytes, list[str]]:
    """Convert an ID-09 STS payload between layout families.

    For the moment only same-family (legacy <-> legacy, nested <-> nested)
    round-trips are byte-exact. Cross-family transforms are blocked until
    a controlled before/after pair is supplied for the native selectors.
    """
    if source_layout == target_layout:
        return decoded, []
    if source_layout == STS_LEGACY_TWO_CHILD and target_layout == STS_NESTED_NEW_GENERATION:
        return _convert_legacy_to_nested_id09(decoded, target_layout)
    if source_layout == STS_NESTED_NEW_GENERATION and target_layout == STS_LEGACY_TWO_CHILD:
        return _convert_nested_to_legacy_id09(decoded, target_layout)
    raise StyError(
        f"unsupported ID-09 conversion {source_layout} -> {target_layout}"
    )


# ---------------------------------------------------------------------------
# Inner STYLE resource conversion (with v2 <-> v4 path)
# ---------------------------------------------------------------------------


def convert_style_payload_for_model(
    decoded: bytes,
    source_profile: ModelProfile,
    target_profile: ModelProfile,
    allow_lossy: bool,
    expand_element_info: bool,
) -> tuple[bytes, list[str]]:
    """Convert an inner STYLE payload between two model profiles.

    The actual inner root version of the decoded payload takes precedence
    over the ``source_profile.inner_version`` — a real-world STY file may
    contain a mix of inner versions (e.g. PA900 sample file with one v4
    resource among v5 resources), and each resource must be converted
    based on its own version.

    The proven transformations are v4<->v5 (already implemented in
    ``korg_sty_tool.convert_style_payload``) plus a strict, fail-closed
    v2->v4 helper that *refuses* to invent the missing 1-byte ElementInfo
    extension or the 24-byte auxiliary TrackData + 8-byte NTT/NTR block.
    """
    if len(decoded) < 8 or decoded[0] != 1:
        raise StyError("invalid inner STYLE root")
    source_version = decoded[1]
    target_version = target_profile.inner_version
    if source_version == target_version:
        # Still need to align ElementInfo / TrackData sizes if the model
        # profile demands it (e.g. PA3X 28/88 vs PA4X 44/88, both v5).
        target_ei = target_profile.element_info_length
        target_td = target_profile.track_data_length
        return _resize_inner_style_payload(
            decoded, target_ei, target_td, allow_lossy, expand_element_info
        )
    if source_version == INNER_VERSION_V4 and target_version == INNER_VERSION_V5:
        converted, warnings = convert_style_payload(
            decoded,
            target_version,
            allow_lossy=allow_lossy,
            expand_element_info=expand_element_info or (
                target_profile.element_info_length == 44
            ),
        )
        if target_profile.track_data_length == 96:
            converted, more = _resize_track_data_block(converted, 96, allow_lossy)
            warnings.extend(more)
        elif target_profile.track_data_length == 88:
            converted, more = _resize_track_data_block(converted, 88, allow_lossy)
            warnings.extend(more)
        return converted, warnings
    if source_version == INNER_VERSION_V5 and target_version == INNER_VERSION_V4:
        return convert_style_payload(
            decoded,
            target_version,
            allow_lossy=allow_lossy,
            expand_element_info=expand_element_info,
        )
    if source_version == INNER_VERSION_V2:
        # v2 lacks the auxiliary TrackData and NTT/NTR bytes plus the 28th
        # ElementInfo byte. Without controlled evidence the converter must
        # refuse to invent them.
        raise StyError(
            "v2 -> v4/v5 inner STYLE conversion is fail-closed: the missing "
            "ElementInfo byte and the 24+8 bytes of auxiliary TrackData have no "
            "proven canonical value (see inner-v2-structure-map.json)"
        )
    if target_version == INNER_VERSION_V2:
        raise StyError(
            "v4/v5 -> v2 inner STYLE conversion is fail-closed: dropping the "
            "auxiliary TrackData bytes and ElementInfo byte 28 is lossy and not "
            "proven safe"
        )
    raise StyError(
        f"unsupported inner STYLE conversion v{source_version} -> v{target_version}"
    )


def _resize_inner_style_payload(
    decoded: bytes,
    target_ei: int,
    target_td: int,
    allow_lossy: bool,
    expand_element_info: bool,
) -> tuple[bytes, list[str]]:
    """Resize ElementInfo and TrackData records of an inner STYLE payload.

    Used when the source and target model share the same inner root version
    but use different ElementInfo / TrackData sizes (e.g. PA3X 28/88 vs
    PA4X 44/88 vs PA700 44/96, all v5).
    """
    if len(decoded) < 8 or decoded[0] != 1:
        raise StyError("invalid inner STYLE root")
    root_children = children(decoded)
    warnings: list[str] = []
    rebuilt_children: list[bytes] = []
    for item in root_children:
        header = bytearray(decoded[item["offset"]:item["offset"] + 4])
        payload = decoded[item["offset"] + 8:item["end"]]
        if item["id"] == 0x03:
            element_parts = children(decoded, item["offset"])
            rebuilt_parts: list[bytes] = []
            for part_index, part in enumerate(element_parts):
                part_header = decoded[part["offset"]:part["offset"] + 4]
                part_payload = decoded[part["offset"] + 8:part["end"]]
                if part_index == 0:
                    part_payload, part_warnings = _resize_element_info(
                        part_payload, target_ei, allow_lossy
                    )
                    warnings.extend(part_warnings)
                elif part_index == 1:
                    part_payload, part_warnings = _resize_track_data(
                        part_payload, target_td, allow_lossy
                    )
                    warnings.extend(part_warnings)
                rebuilt_parts.append(build_record(part_header, part_payload))
            payload = b"".join(rebuilt_parts)
        rebuilt_children.append(build_record(bytes(header), payload))
    root_header = bytearray(decoded[:4])
    return build_record(bytes(root_header), b"".join(rebuilt_children)), warnings


def _resize_element_info(
    payload: bytes, target_length: int, allow_lossy: bool
) -> tuple[bytes, list[str]]:
    from korg_sty_tool import convert_element_info

    if len(payload) == target_length:
        return payload, []
    if len(payload) == 28 and target_length == 44:
        return convert_element_info(payload, 44, allow_lossy=False)
    if len(payload) == 44 and target_length == 28:
        return convert_element_info(payload, 28, allow_lossy=allow_lossy)
    raise StyError(
        f"unsupported ElementInfo resize {len(payload)} -> {target_length}"
    )


def _resize_track_data(
    payload: bytes, target_length: int, allow_lossy: bool
) -> tuple[bytes, list[str]]:
    from korg_sty_tool import convert_track_data

    if len(payload) == target_length:
        return payload, []
    return convert_track_data(payload, target_length, allow_lossy=allow_lossy)


def _resize_track_data_block(
    decoded: bytes, target_td: int, allow_lossy: bool
) -> tuple[bytes, list[str]]:
    """Walk an inner STYLE payload and resize every TrackData record."""
    root_children = children(decoded)
    warnings: list[str] = []
    rebuilt_children: list[bytes] = []
    for item in root_children:
        header = bytearray(decoded[item["offset"]:item["offset"] + 4])
        payload = decoded[item["offset"] + 8:item["end"]]
        if item["id"] == 0x03:
            element_parts = children(decoded, item["offset"])
            rebuilt_parts: list[bytes] = []
            for part_index, part in enumerate(element_parts):
                part_header = decoded[part["offset"]:part["offset"] + 4]
                part_payload = decoded[part["offset"] + 8:part["end"]]
                if part_index == 1:
                    part_payload, more = _resize_track_data(
                        part_payload, target_td, allow_lossy
                    )
                    warnings.extend(more)
                rebuilt_parts.append(build_record(part_header, part_payload))
            payload = b"".join(rebuilt_parts)
        rebuilt_children.append(build_record(bytes(header), payload))
    root_header = bytearray(decoded[:4])
    return build_record(bytes(root_header), b"".join(rebuilt_children)), warnings


# ---------------------------------------------------------------------------
# High-level STY converter
# ---------------------------------------------------------------------------


def _replace_indexed_resource_tolerant(
    native: bytes, target_offset: int, payload: bytes
) -> bytes:
    """Rebuild a native STY container, replacing one indexed resource.

    Unlike ``korg_sty_tool.replace_native_resource`` (which uses strict
    child traversal via ``parse_native``), this helper uses the
    ``KBEG/KEND`` index as the authoritative map of indexed resource
    offsets. It tolerates individual resources whose declared length
    differs from their actual span — a real-world corruption observed in
    some sample files (e.g. Pa900.STY has 2 such mismatches). The
    replacement is performed at the byte-exact position recorded in the
    index, and the rebuilt native always carries a regenerated KBEG/KEND
    index with corrected offsets.
    """
    if native[:4] != bytes.fromhex("01 00 01 14"):
        raise StyError("unexpected native root")
    kbeg = native.rfind(b"KBEG")
    kend = native.rfind(b"KEND")
    if kbeg < 0 or kend <= kbeg or kend + 8 > len(native):
        raise StyError("missing terminal KBEG/KEND index")
    # The FE record wraps the KBEG/KEND payload. Its 8-byte header sits
    # immediately before the KBEG marker.
    fe_offset = kbeg - 8
    if fe_offset < 8 or native[fe_offset] != 0xFE:
        raise StyError("FE terminal header not found")
    fe_header = native[fe_offset:fe_offset + 4]
    offsets = [be32(native, p) for p in range(kbeg + 4, kend, 4)]
    count = be32(native, kend + 4)
    if count != len(offsets):
        raise StyError("KEND count does not match index entries")
    if not offsets or offsets != sorted(offsets) or offsets[-1] >= fe_offset:
        raise StyError("invalid indexed resource offsets")
    rebuilt_children: list[bytes] = []
    indexed_offsets: list[int] = []
    current_offset = 8
    replaced = False
    for index, offset in enumerate(offsets):
        end = offsets[index + 1] if index + 1 < len(offsets) else fe_offset
        header = native[offset:offset + 4]
        if offset == target_offset:
            child = header + len(payload).to_bytes(4, "big") + payload
            replaced = True
        else:
            child = native[offset:end]
        indexed_offsets.append(current_offset)
        rebuilt_children.append(child)
        current_offset += len(child)
    if not replaced:
        raise StyError("target resource offset was not found in KBEG/KEND index")
    # Rebuild KBEG/KEND with refreshed offsets.
    index_payload = (
        b"KBEG"
        + b"".join(o.to_bytes(4, "big") for o in indexed_offsets)
        + b"KEND"
        + len(indexed_offsets).to_bytes(4, "big")
    )
    terminal = fe_header + len(index_payload).to_bytes(4, "big") + index_payload
    body = b"".join(rebuilt_children) + terminal
    rebuilt = native[:4] + len(body).to_bytes(4, "big") + body
    return rebuilt


def detect_source_model(raw: bytes) -> dict[str, Any]:
    """Inspect a STY file and report the model profile that best matches.

    The detector looks at the first decodable ID-06 STYLE resource and the
    first decodable ID-09 STS resource and matches their geometry against
    ``MODEL_REGISTRY``.
    """
    native, _container, _pages = decode_input(raw)
    indexed, _framing = parse_indexed_native(native)
    style_geometries: dict[str, int] = {}
    sts_layouts: dict[str, int] = {}
    inner_versions: dict[int, int] = {}
    for item in indexed:
        if item["id"] not in {0x06, 0x09}:
            continue
        payload = native[item["offset"] + 8:item["end"]]
        try:
            decoded, _meta = decode_oc3x_generic(payload)
        except StyError:
            continue
        if item["id"] == 0x06:
            summary = style_summary(decoded)
            root = summary.get("root") or ""
            if root.startswith("01 02"):
                inner_versions[2] = inner_versions.get(2, 0) + 1
            elif root.startswith("01 04"):
                inner_versions[4] = inner_versions.get(4, 0) + 1
            elif root.startswith("01 05"):
                inner_versions[5] = inner_versions.get(5, 0) + 1
            for geom, count in summary.get("element_track_geometries", {}).items():
                style_geometries[geom] = style_geometries.get(geom, 0) + count
        elif item["id"] == 0x09:
            try:
                layout, _model = _split_id09_layout(decoded)
                sts_layouts[layout] = sts_layouts.get(layout, 0) + 1
            except StyError:
                pass
    # Match against registry. The STS layout is intentionally not used as
    # a hard filter: the KORG PA Manager application can downgrade nested
    # STS to the legacy two-child layout when saving, so a Pa3x/Pa4x STY
    # file may legitimately contain legacy STS resources. We match on
    # inner version + ElementInfo/TrackData geometry only and report the
    # observed STS layout separately.
    dominant_version = max(inner_versions, key=inner_versions.get) if inner_versions else None
    dominant_geometry = max(style_geometries, key=style_geometries.get) if style_geometries else None
    dominant_sts = max(sts_layouts, key=sts_layouts.get) if sts_layouts else None
    matches: list[str] = []
    for name, profile in MODEL_REGISTRY.items():
        if dominant_version is not None and profile.inner_version != dominant_version:
            continue
        if dominant_geometry is not None:
            ei, td = dominant_geometry.split("/")
            if int(ei) != profile.element_info_length or int(td) != profile.track_data_length:
                continue
        matches.append(name)
    return {
        "detected_inner_version": dominant_version,
        "detected_geometry": dominant_geometry,
        "detected_sts_layout": dominant_sts,
        "matching_models": matches,
        "inner_version_counts": inner_versions,
        "style_geometry_counts": style_geometries,
        "sts_layout_counts": sts_layouts,
    }


def convert_sty_cross_model(
    raw: bytes,
    source_model: str,
    target_model: str,
    allow_lossy: bool = False,
    expand_element_info: bool = False,
) -> tuple[bytes, dict[str, Any]]:
    """Convert a STY container between two KORG PA model profiles."""
    if source_model not in MODEL_REGISTRY:
        raise StyError(f"unknown source model {source_model!r}")
    if target_model not in MODEL_REGISTRY:
        raise StyError(f"unknown target model {target_model!r}")
    source_profile = MODEL_REGISTRY[source_model]
    target_profile = MODEL_REGISTRY[target_model]
    if source_model == target_model:
        raise StyError("source model and target model are identical")

    native, input_container, pages = decode_input(raw)
    indexed, framing = parse_indexed_native(native)
    original_payloads = [native[item["offset"] + 8:item["end"]] for item in indexed]

    style_reports: list[dict[str, Any]] = []
    sts_reports: list[dict[str, Any]] = []
    rebuilt = native

    # First pass: decode everything and validate compatibility.
    decoded_resources: list[dict[str, Any]] = []
    for position, item in enumerate(indexed):
        compressed = original_payloads[position]
        if item["id"] not in {0x06, 0x09}:
            decoded_resources.append({
                "position": position,
                "kind": "other",
                "id": item["id"],
                "compressed": compressed,
                "decoded": None,
                "metadata": None,
            })
            continue
        try:
            decoded, metadata = decode_oc3x_generic(compressed)
        except StyError as error:
            decoded_resources.append({
                "position": position,
                "kind": "id_06" if item["id"] == 0x06 else "id_09",
                "id": item["id"],
                "compressed": compressed,
                "decoded": None,
                "metadata": None,
                "decode_error": str(error),
            })
            continue
        decoded_resources.append({
            "position": position,
            "kind": "id_06" if item["id"] == 0x06 else "id_09",
            "id": item["id"],
            "compressed": compressed,
            "decoded": decoded,
            "metadata": metadata,
        })

    # Second pass: perform conversions and rebuild native.
    for entry in decoded_resources:
        if entry["kind"] == "other" or entry.get("decode_error"):
            continue
        position = entry["position"]
        decoded = entry["decoded"]
        metadata = entry["metadata"]
        if entry["kind"] == "id_06":
            try:
                converted, warnings = convert_style_payload_for_model(
                    decoded,
                    source_profile,
                    target_profile,
                    allow_lossy=allow_lossy,
                    expand_element_info=expand_element_info,
                )
            except StyError as error:
                # Inner STYLE conversion failed (e.g. corrupt structure,
                # unproven v2 -> v4 path, or strict downgrade gate).
                # Preserve the resource byte-exact and emit an explicit
                # warning so the user knows the resource was not converted.
                converted = decoded
                warnings = [
                    f"STYLE resource {position} preserved byte-exact: "
                    f"inner STYLE conversion failed ({error})."
                ]
        else:  # id_09
            try:
                source_layout, _ = _split_id09_layout(decoded)
                target_layout = target_profile.sts_layout
                if source_layout == target_layout:
                    # Same layout: preserve byte-exact.
                    converted = decoded
                    warnings = []
                else:
                    # Cross-family STS conversion is fail-closed per the
                    # evidence in id09-native-selector-audit.json. We
                    # preserve the STS resource byte-exact and emit an
                    # explicit warning so the user knows that the target
                    # model's native STS layout is not produced.
                    converted = decoded
                    warnings = [
                        f"STS resource {position} preserved byte-exact: "
                        f"cross-family STS structural transform {source_layout} -> "
                        f"{target_layout} is fail-closed. The target model "
                        f"hardware accepts both STS layout families, so the "
                        f"file remains loadable. Use evidence-audit on a "
                        f"controlled before/after pair to unlock native "
                        f"selector remap."
                    ]
            except StyError as error:
                # Could not classify STS layout; preserve byte-exact.
                converted = decoded
                warnings = [
                    f"STS resource {position} preserved byte-exact: "
                    f"layout classification failed ({error})."
                ]
        # Encode and verify round-trip.
        trailer = bytes.fromhex(metadata["trailer"])
        encoded = encode_oc3x(converted, str(metadata["wrapper"])) + trailer
        verified, verified_metadata = decode_oc3x_generic(encoded)
        if verified != converted:
            raise StyError(
                f"resource {position} failed encode/decode verification"
            )
        if verified_metadata["trailer"] != metadata["trailer"]:
            raise StyError(f"resource {position} trailer changed")
        # Patch into the rebuilt native. Use the tolerant KBEG/KEND-based
        # replacement so files with malformed child records (e.g. Pa900.STY
        # with 2 declared/actual length mismatches) remain convertible.
        current_indexed, _ = parse_indexed_native(rebuilt)
        if position >= len(current_indexed):
            raise StyError("indexed resource count changed during conversion")
        current_item = current_indexed[position]
        if current_item["id"] != entry["id"]:
            raise StyError("indexed resource identity changed during conversion")
        rebuilt = _replace_indexed_resource_tolerant(
            rebuilt, current_item["offset"], encoded
        )
        if entry["kind"] == "id_06":
            converted_flag = converted != decoded
            style_reports.append({
                "resource_position": position,
                "wrapper": metadata["wrapper"],
                "source_summary": style_summary(decoded),
                "target_summary": style_summary(converted),
                "source_sha256": hashlib.sha256(decoded).hexdigest(),
                "target_sha256": hashlib.sha256(converted).hexdigest(),
                "warnings": warnings,
                "converted": converted_flag,
                "preserved_byte_exact": not converted_flag,
            })
        else:
            converted_flag = converted != decoded
            sts_reports.append({
                "resource_position": position,
                "wrapper": metadata["wrapper"],
                "source_sha256": hashlib.sha256(decoded).hexdigest(),
                "target_sha256": hashlib.sha256(converted).hexdigest(),
                "warnings": warnings,
                "converted": converted_flag,
                "preserved_byte_exact": not converted_flag,
            })

    # Validate final rebuilt native.
    rebuilt_indexed, rebuilt_framing = parse_indexed_native(rebuilt)
    if len(rebuilt_indexed) != len(indexed):
        raise StyError("indexed resource count changed after cross-model conversion")
    for position, (before_item, after_item) in enumerate(zip(indexed, rebuilt_indexed)):
        if before_item["id"] != after_item["id"]:
            raise StyError("indexed resource type changed after cross-model conversion")
    return rebuilt, {
        "source_model": source_model,
        "target_model": target_model,
        "source_profile": source_profile.as_dict(),
        "target_profile": target_profile.as_dict(),
        "input_container": input_container,
        "output_container": "native",
        "oc20_pages": len(pages),
        "input_framing": framing,
        "output_framing": rebuilt_framing,
        "allow_lossy": allow_lossy,
        "expand_element_info": expand_element_info,
        "converted_style_count": sum(1 for r in style_reports if r["converted"]),
        "preserved_style_count": sum(1 for r in style_reports if r["preserved_byte_exact"]),
        "converted_sts_count": sum(1 for r in sts_reports if r["converted"]),
        "preserved_sts_count": sum(1 for r in sts_reports if r["preserved_byte_exact"]),
        "preserved_resource_count": sum(
            1 for entry in decoded_resources
            if entry["kind"] == "other" or entry.get("decode_error")
        ),
        "decode_error_count": sum(
            1 for entry in decoded_resources if entry.get("decode_error")
        ),
        "all_converted_resources_verified_round_trip": True,
        "styles": style_reports,
        "style_performances": sts_reports,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def cli_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="korg_pa_converter",
        description="KORG PA cross-model STY converter (evidence-based)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    list_parser = sub.add_parser("models", help="list supported KORG PA models")
    list_parser.add_argument("--json", type=Path)

    detect_parser = sub.add_parser("detect", help="detect the model of a STY file")
    detect_parser.add_argument("input", type=Path)
    detect_parser.add_argument("--json", type=Path)

    convert_parser = sub.add_parser("convert", help="convert a STY file between models")
    convert_parser.add_argument("input", type=Path)
    convert_parser.add_argument("output", type=Path)
    convert_parser.add_argument("--source-model", required=True, choices=list_models())
    convert_parser.add_argument("--target-model", required=True, choices=list_models())
    convert_parser.add_argument("--allow-lossy", action="store_true")
    convert_parser.add_argument("--expand-element-info", action="store_true")
    convert_parser.add_argument("--report", type=Path)

    args = parser.parse_args(argv)
    try:
        if args.command == "models":
            payload = {
                name: profile.as_dict() for name, profile in MODEL_REGISTRY.items()
            }
            if args.json:
                args.json.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
            else:
                for name, profile in MODEL_REGISTRY.items():
                    print(
                        f"{name:8s}  v{profile.inner_version}  "
                        f"EI={profile.element_info_length:2d}  "
                        f"TD={profile.track_data_length:2d}  "
                        f"STS={profile.sts_layout}  lineage={profile.lineage}"
                    )
            return 0
        if args.command == "detect":
            report = detect_source_model(args.input.read_bytes())
            if args.json:
                args.json.write_text(json.dumps(report, indent=2, ensure_ascii=False))
            else:
                for key, value in report.items():
                    print(f"{key}: {value}")
            return 0
        if args.command == "convert":
            converted, report = convert_sty_cross_model(
                args.input.read_bytes(),
                args.source_model,
                args.target_model,
                allow_lossy=args.allow_lossy,
                expand_element_info=args.expand_element_info,
            )
            args.output.write_bytes(converted)
            if args.report:
                args.report.write_text(json.dumps(report, indent=2, ensure_ascii=False))
            print(
                f"Converted {args.input} -> {args.output} "
                f"({args.source_model} -> {args.target_model})"
            )
            print(
                f"  styles converted: {report['converted_style_count']}, "
                f"styles preserved: {report['preserved_style_count']}, "
                f"STS converted: {report['converted_sts_count']}, "
                f"STS preserved: {report['preserved_sts_count']}, "
                f"decode errors: {report['decode_error_count']}"
            )
            return 0
    except (OSError, StyError) as error:
        parser.error(str(error))
    return 2


if __name__ == "__main__":
    raise SystemExit(cli_main())
