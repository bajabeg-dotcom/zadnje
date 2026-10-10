"""Real decoder for Korg Pa style containers (``.STY``).

A ``.STY`` file is a native Korg resource container whose terminal ``KBEG``/
``KEND`` index addresses a run of paired records: resource id ``0x06`` (the
style itself) immediately followed by resource id ``0x09`` (its
sound/global companion).  Every payload is an OC31/OC32 wrapped CBarc stream.

This decoder unwraps the container, decodes each resource with a declared-size
check plus an XOR checksum check, extracts the style name and structural
summary, and writes a manifest.  It never writes to the source.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..codec import NATIVE_ROOT
from ..containers import container_kind
from ..resources import scan_structured
from .base import KorgContainerDecoder

STYLE_ID = 0x06
COMPANION_ID = 0x09


class StyleDecoder(KorgContainerDecoder):
    name = "korg-style-sty"
    supported_inputs = ("KORG-STY",)
    container_label = "Korg Pa style container"
    output_suffix = ".native.bin"
    executable = True

    def unwrap(self, data: bytes) -> tuple[bytes, str, list[dict[str, Any]]]:
        kind = container_kind(data)
        if data[:4] != NATIVE_ROOT:
            raise ValueError(f"not a native Korg container (leading bytes {data[:4].hex(' ')})")
        return data, kind, ["input is already an unwrapped native container"]

    def detect(self, data: Any) -> dict[str, Any]:
        probe = super().detect(data)
        if not probe.get("matched"):
            return probe
        try:
            report = scan_structured(bytes(data))
        except Exception as error:  # pragma: no cover - defensive
            return {
                "matched": True,
                "confidence": 0.5,
                "evidence": [*probe["evidence"], f"index walk failed: {error}"],
            }
        resources = report.get("resources", [])
        style_count = sum(1 for item in resources if item["id"] == "0x06")
        if style_count == 0:
            return {"matched": False, "confidence": 0.0, "evidence": ["no style resource (id 06) present"]}
        return {
            "matched": True,
            "confidence": 0.97,
            "evidence": [
                *probe["evidence"],
                f"{style_count} style resource(s) with id 0x06 addressed by the terminal KBEG/KEND index",
                f"{len(resources)} resource(s) decoded with declared size + XOR checksum agreement",
            ],
        }

    def summarize(self, manifest: dict[str, Any]) -> dict[str, Any]:
        return {"style_names": manifest.get("style_names", [])}

    def describe(self) -> str:
        return "Real Korg Pa style decoder: OC31/OC32 + CBarc with size and checksum validation."


DECODER = StyleDecoder()
