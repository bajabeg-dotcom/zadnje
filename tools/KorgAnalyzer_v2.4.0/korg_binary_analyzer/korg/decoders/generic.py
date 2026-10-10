"""Real decoder for OC20 factory packages (``.BKP`` / ``.pkg`` flash page runs).

A ``.BKP`` backup or a Pa3X-era ``.pkg`` update stores the resource container as
a run of ``OC20`` pages, each being ``"OC20" + u32LE decoded size + CBarc
stream`` followed by a 12 or 17 byte footer.  Page payloads of 256 KiB
(``0x40000``) are the packer's standard chunk size.

This decoder concatenates the pages back into the native container and then
hands the result to the same validated resource pipeline the native decoder
uses, so both entry points produce identical evidence.
"""

from __future__ import annotations

from typing import Any

from ..codec import is_oc20_pages
from .base import KorgContainerDecoder, Oc20PackageDecoder as _Oc20Base


class Oc20PackageDecoder(_Oc20Base):
    name = "korg-oc20-package"
    supported_inputs = ("KORG-BKP", "KORG-PKG")
    container_label = "OC20 factory package (BKP/pkg flash pages)"
    output_suffix = ".native.bin"
    executable = True

    def detect(self, data: Any) -> dict[str, Any]:
        if not is_oc20_pages(bytes(data)):
            return {"matched": False, "confidence": 0.0, "evidence": ["input does not begin with an OC20 page"]}
        probe = super().detect(data)
        if probe.get("matched"):
            probe["evidence"] = [
                *probe["evidence"],
                "OC20 page framing present at offset 0 (BKP/pkg flash page run)",
            ]
        return probe

    def describe(self) -> str:
        return "Real OC20 factory-package decoder (BKP/pkg flash pages -> native container)."


DECODER = Oc20PackageDecoder()
