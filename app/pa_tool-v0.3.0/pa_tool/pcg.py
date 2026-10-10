"""Read-only KORG .PCG dialect classifier and chunk-token audit.

KORG uses more than one unrelated binary layout under the .PCG extension:

* PA arranger files in the local evidence corpus are KORF containers beginning
  ``01 00 01 14`` with ``KORF`` at offset 0x17.
* Publicly documented workstation files (Triton/OASYS family) begin ``KORG`` and
  carry a ``PCG1`` chunk, with ``PRG1``/``PBK1`` below it.

The classifier deliberately keeps these dialects separate.  Token searches are
reported as observations, not accepted as structural parses merely because four
ASCII bytes occur somewhere inside compressed data.
"""
from __future__ import annotations

import json
import struct
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Optional

from .status import Status

DATA_PATH = Path(__file__).resolve().parent.parent / "data" / "pcg_chunks.json"
PA_MAGIC = b"\x01\x00\x01\x14"
TOKENS = (b"KORF", b"KBEG", b"KEND", b"OC20", b"OC31", b"OC32",
          b"KORG", b"PCG1", b"PRG1", b"PBK1", b"RLP1")


@lru_cache(maxsize=1)
def evidence_data() -> dict:
    with DATA_PATH.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def find_offsets(data: bytes, marker: bytes) -> list[int]:
    """Return every offset of *marker*, including overlapping occurrences."""
    out: list[int] = []
    start = 0
    while True:
        pos = data.find(marker, start)
        if pos < 0:
            return out
        out.append(pos)
        start = pos + 1


@dataclass
class PcgReport:
    path: Path
    size: int = 0
    dialect: str = "unknown"
    label: str = "Unknown PCG dialect"
    status: Status = Status.UNKNOWN
    declared_length: Optional[int] = None
    declared_ok: Optional[bool] = None
    token_offsets: dict[str, list[int]] = field(default_factory=dict)
    evidence: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    error: str = ""

    def to_dict(self) -> dict:
        return {
            "path": str(self.path),
            "size": self.size,
            "dialect": self.dialect,
            "label": self.label,
            "status": self.status.value,
            "declared_length": self.declared_length,
            "declared_ok": self.declared_ok,
            "token_offsets": self.token_offsets,
            "evidence": self.evidence,
            "notes": self.notes,
            "error": self.error,
        }


def classify_bytes(data: bytes, path: str | Path = "<bytes>") -> PcgReport:
    """Classify bytes without modifying them.

    ``VERIFIED`` is used for the PA KORF signature because four real local PCG
    samples carry it and two complete USER samples also satisfy the length field.
    The KORG/PCG1 branch stays ``HIGH``: its structure is publicly documented, but
    this project does not bundle a local binary sample of that dialect.
    """
    p = Path(path)
    offsets = {t.decode("ascii"): find_offsets(data, t) for t in TOKENS}
    offsets = {key: value for key, value in offsets.items() if value}
    rep = PcgReport(path=p, size=len(data), token_offsets=offsets)

    if data.startswith(PA_MAGIC) and data[23:27] == b"KORF":
        rep.dialect = "pa_korf"
        rep.label = "KORG PA KORF container"
        rep.status = Status.VERIFIED
        rep.evidence.append("magic 01 00 01 14 @0x0; KORF @0x17")
        if len(data) >= 8:
            rep.declared_length = struct.unpack_from(">I", data, 4)[0]
            rep.declared_ok = rep.declared_length == len(data) - 8
            rep.evidence.append(
                f"u32BE length @0x4={rep.declared_length}; file_size-8={len(data) - 8}"
            )
            if not rep.declared_ok:
                rep.notes.append(
                    "declared length does not match this byte buffer; it may be a fragment "
                    "or an aggregate containing several KORF containers"
                )
        if "PBK1" not in offsets and "PRG1" not in offsets:
            rep.notes.append(
                "PBK1/PRG1 absent: expected for the verified PA KORF corpus; those are "
                "KORG/PCG1 workstation chunk IDs, not universal PA signatures"
            )
        return rep

    if data.startswith(b"KORG") and data[16:20] == b"PCG1":
        rep.dialect = "korg_riff_pcg"
        rep.label = "KORG workstation RIFF-like PCG"
        rep.status = Status.HIGH
        rep.evidence.append("KORG @0x0; PCG1 @0x10")
        rep.notes.append(
            "PRG1 groups program banks; PBK1 is a PCM program-bank chunk in this dialect"
        )
        return rep

    if data.startswith(PA_MAGIC):
        rep.dialect = "pa_container_unconfirmed"
        rep.label = "KORG PA container without KORF@0x17"
        rep.status = Status.NEPOTVRDENO
        rep.evidence.append("magic 01 00 01 14 @0x0")
        rep.notes.append("container magic exists, but the verified PCG KORF marker is absent")
        return rep

    if data.startswith(b"KORG"):
        rep.dialect = "korg_container_unconfirmed"
        rep.label = "KORG container without PCG1@0x10"
        rep.status = Status.NEPOTVRDENO
        rep.evidence.append("KORG @0x0")
        rep.notes.append("KORG header exists, but this is not enough to claim the PCG1 dialect")
        return rep

    rep.notes.append("neither the PA magic nor the documented KORG/PCG1 header was found")
    return rep


def classify_pcg(path: str | Path) -> PcgReport:
    """Read and classify one file. Errors are returned, never raised."""
    p = Path(path)
    try:
        data = p.read_bytes()
    except OSError as exc:
        return PcgReport(path=p, error=f"cannot read: {exc}")
    return classify_bytes(data, p)


def chunk_semantics(token: str) -> Optional[dict]:
    """Evidence-backed meaning for PRG1, PBK1 or RLP1."""
    return evidence_data().get("chunk_semantics", {}).get(token.upper())


def reload_data() -> None:
    evidence_data.cache_clear()
