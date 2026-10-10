"""KORG resource container parser.

Verified layout (see data/korg_tokens.json):

    +0   magic            01 00 01 14
    +4   payload_length   u32 big-endian, expected file_size - 8
    +8   version/flags    not interpreted -> UNKNOWN
    +23  KORF             resource header
         KBEG ... KEND    resource chain start / terminator

Nothing here writes. Inputs are read as bytes and never modified.
"""
from __future__ import annotations

import re
import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from . import models
from .status import Status

MAGIC_LEN = 4


@dataclass
class ContainerHeader:
    magic: bytes
    payload_length: Optional[int]
    declared_ok: Optional[bool]
    actual_size: int
    delta: Optional[int]
    pass8: bytes = b""


@dataclass
class ResourceChain:
    korf_offsets: list[int] = field(default_factory=list)
    kbeg_offsets: list[int] = field(default_factory=list)
    kend_offsets: list[int] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return bool(self.korf_offsets) and bool(self.kend_offsets)

    @property
    def balanced(self) -> bool:
        """KBEG/KEND counts match and nest as proper open/close pairs in offset order.

        `ok` only proves the markers are present; `balanced` proves they interleave
        without an unclosed or orphaned KEND. Reported alongside `ok` so a file whose
        markers merely appear (e.g. inside a compressed blob) is not mistaken for a
        structurally sound chain.
        """
        if len(self.kbeg_offsets) != len(self.kend_offsets):
            return False
        events = sorted(
            [(o, 0) for o in self.kbeg_offsets] + [(o, 1) for o in self.kend_offsets]
        )
        depth = 0
        for _, kind in events:
            depth += 1 if kind == 0 else -1
            if depth < 0:
                return False
        return depth == 0


@dataclass
class CompressionSummary:
    oc20_offset: Optional[int] = None
    oc31_offset: Optional[int] = None
    oc32_offset: Optional[int] = None
    oc20: int = 0
    oc31: int = 0
    oc32: int = 0

    @property
    def any(self) -> bool:
        return bool(self.oc20 or self.oc31 or self.oc32)

    @property
    def first_offset(self) -> Optional[int]:
        offsets = [o for o in (self.oc20_offset, self.oc31_offset, self.oc32_offset)
                   if o is not None]
        return min(offsets) if offsets else None

    @property
    def first_magic(self) -> Optional[bytes]:
        pairs = [(self.oc20_offset, b"OC20"), (self.oc31_offset, b"OC31"),
                 (self.oc32_offset, b"OC32")]
        pairs = [(o, m) for o, m in pairs if o is not None]
        return min(pairs)[1] if pairs else None


@dataclass
class DecodeResult:
    """Outcome of decoding one OC31/OC32 payload."""

    magic: str
    capacity: Optional[int]
    decoded: Optional[bytes]
    trailer_bytes: Optional[int] = None
    xor_computed: Optional[str] = None
    xor_in_file: Optional[str] = None
    xor_match: Optional[bool] = None
    error: str = ""

    @property
    def ok(self) -> bool:
        return (self.decoded is not None and self.capacity is not None
                and len(self.decoded) == self.capacity)

    def to_dict(self) -> dict:
        return {
            "magic": self.magic,
            "capacity": self.capacity,
            "decoded_size": None if self.decoded is None else len(self.decoded),
            "ok": self.ok,
            "trailer_bytes": self.trailer_bytes,
            "xor_computed": self.xor_computed,
            "xor_in_file": self.xor_in_file,
            "xor_match": self.xor_match,
            "error": self.error,
        }


_CODEC = None
_CODEC_TRIED = False


def load_codec():
    """Load the bounded bundled CBarc codec, with legacy assets as fallback.

    Since v0.2.0 the strict PA800 forensic decoder lives in
    :mod:`pa_tool.native.codec`, so OC inspection is self-contained.  Older
    ``pa800_remap/native/codec.py`` assets remain a compatibility fallback, not
    a prerequisite.  If every implementation fails, callers report UNKNOWN
    rather than guessing.
    """
    global _CODEC, _CODEC_TRIED
    if _CODEC_TRIED:
        return _CODEC
    _CODEC_TRIED = True

    try:
        from .native import codec as bundled
        if hasattr(bundled, "_barc_decompress") and hasattr(bundled, "VerificationError"):
            _CODEC = bundled
            return _CODEC
    except Exception:  # noqa: BLE001 - report absence instead of breaking detection
        pass

    import importlib.util
    import os
    import sys as _sys
    candidates = []
    for name in ("PA_TOOL_NATIVE_PARENT", "PA_TOOL_ASSET_ROOT"):
        for raw in os.environ.get(name, "").split(os.pathsep):
            raw = raw.strip()
            if raw:
                candidates.append(Path(raw) / "pa800_remap" / "native" / "codec.py")
    candidates.append(
        Path(__file__).resolve().parent.parent.parent
        / "KORG_KOMPLET_2026-10-06" / "PA800" / "PA800_KOMPLET"
        / "PA800_Remapper" / "pa800_remap" / "native" / "codec.py"
    )
    for path in candidates:
        if not path.is_file():
            continue
        try:
            spec = importlib.util.spec_from_file_location("korg_codec", path)
            mod = importlib.util.module_from_spec(spec)
            _sys.modules["korg_codec"] = mod
            spec.loader.exec_module(mod)
            if hasattr(mod, "_barc_decompress") and hasattr(mod, "VerificationError"):
                _CODEC = mod
                return _CODEC
        except Exception:  # noqa: BLE001 - optional compatibility dependency
            continue
    return None


def decode_oc_payload(data: bytes, pos: int, codec=None) -> DecodeResult:
    """Decode an OC31/OC32 payload at `pos`.

    KORG appends a trailer the CBarc stream does not account for. The codec
    terminates on its own control code and then insists the whole stream was
    consumed, so its "left N compressed bytes unread" is exactly the trailer size:
    trim it, retry, then validate the documented XOR check byte.
    """
    codec = codec if codec is not None else load_codec()
    if pos < 0 or pos + 8 > len(data):
        return DecodeResult(magic="", capacity=None, decoded=None,
                            error="offset outside buffer")
    magic = data[pos:pos + 4].decode("ascii", "replace")
    capacity = struct.unpack_from("<I", data, pos + 4)[0]
    if codec is None:
        return DecodeResult(magic=magic, capacity=capacity, decoded=None,
                            error="CBarc codec not available")
    if capacity == 0 or capacity > 512 * 1024 * 1024:
        return DecodeResult(magic=magic, capacity=capacity, decoded=None,
                            error=f"declared size out of range: {capacity}")

    stream = data[pos + 8:]
    bound = min(len(stream), capacity + capacity // 8 + 4096)
    stream = stream[:bound]
    trim = 0
    for _ in range(6):
        window = stream[:len(stream) - trim] if trim else stream
        try:
            out = codec._barc_decompress(window, capacity)
        except codec.VerificationError as exc:
            m = re.search(r"left (\d+) compressed bytes unread", str(exc))
            if m:
                trim += int(m.group(1))
                continue
            return DecodeResult(magic=magic, capacity=capacity, decoded=None, error=str(exc))
        except Exception as exc:  # noqa: BLE001
            return DecodeResult(magic=magic, capacity=capacity, decoded=None,
                                error=f"{type(exc).__name__}: {exc}")
        end = pos + 8 + len(stream) - trim
        chk = 0
        for v in out:
            chk ^= v
        trailer = data[end:end + 1]
        return DecodeResult(
            magic=magic, capacity=capacity, decoded=out, trailer_bytes=len(data[end:]),
            xor_computed=f"0x{chk:02x}",
            xor_in_file=f"0x{trailer[0]:02x}" if trailer else None,
            xor_match=bool(trailer) and chk == trailer[0],
        )
    return DecodeResult(magic=magic, capacity=capacity, decoded=None,
                        error="trailer trim did not converge")


@dataclass
class ContainerReport:
    path: Path
    is_container: bool
    header: Optional[ContainerHeader] = None
    chain: ResourceChain = field(default_factory=ResourceChain)
    compression: CompressionSummary = field(default_factory=CompressionSummary)
    status: Status = Status.UNKNOWN
    error: str = ""

    def to_dict(self) -> dict:
        return {
            "path": str(self.path),
            "is_container": self.is_container,
            "status": self.status.value,
            "error": self.error,
            "header": None if self.header is None else {
                "magic": self.header.magic.hex(" "),
                "payload_length": self.header.payload_length,
                "declared_ok": self.header.declared_ok,
                "actual_size": self.header.actual_size,
                "delta": self.header.delta,
            },
            "resources": {
                "korf": self.chain.korf_offsets,
                "kbeg": self.chain.kbeg_offsets,
                "kend": self.chain.kend_offsets,
                "chain_ok": self.chain.ok,
                "chain_balanced": self.chain.balanced,
            },
            "compression": {
                "oc20": self.compression.oc20,
                "oc31": self.compression.oc31,
                "oc32": self.compression.oc32,
            },
        }


def is_korg_container(data: bytes) -> bool:
    """True when the buffer starts with the verified KORG container magic."""
    return len(data) >= MAGIC_LEN and data[:MAGIC_LEN] == models.container_magic()


def read_header(data: bytes) -> ContainerHeader:
    magic = data[:MAGIC_LEN]
    payload_length = struct.unpack_from(">I", data, 4)[0] if len(data) >= 8 else None
    actual = len(data)
    delta = None
    declared_ok = None
    if payload_length is not None:
        delta = payload_length - (actual - 8)
        declared_ok = delta == 0
    return ContainerHeader(
        magic=magic,
        payload_length=payload_length,
        declared_ok=declared_ok,
        actual_size=actual,
        delta=delta,
        pass8=data[8:24] if len(data) >= 24 else data[8:],
    )


def find_token(data: bytes, marker: bytes) -> list[int]:
    """All offsets of a marker. Deterministic, no overlap skipping."""
    out = []
    start = 0
    while True:
        pos = data.find(marker, start)
        if pos < 0:
            return out
        out.append(pos)
        start = pos + 1


def walk_resource_chain(data: bytes) -> ResourceChain:
    chain = ResourceChain()
    chain.korf_offsets = find_token(data, b"KORF")
    chain.kbeg_offsets = find_token(data, b"KBEG")
    chain.kend_offsets = find_token(data, b"KEND")
    return chain


def scan_compression(data: bytes) -> CompressionSummary:
    return CompressionSummary(
        oc20=data.count(b"OC20"),
        oc31=data.count(b"OC31"),
        oc32=data.count(b"OC32"),
        oc20_offset=(data.find(b"OC20") if data.count(b"OC20") else None),
        oc31_offset=(data.find(b"OC31") if data.count(b"OC31") else None),
        oc32_offset=(data.find(b"OC32") if data.count(b"OC32") else None),
    )


def parse_container(path: str | Path) -> ContainerReport:
    """Read a file and report its container structure. Never modifies the file."""
    p = Path(path)
    try:
        data = p.read_bytes()
    except OSError as exc:
        return ContainerReport(path=p, is_container=False, status=Status.UNKNOWN,
                               error=f"cannot read: {exc}")

    if not is_korg_container(data):
        return ContainerReport(
            path=p, is_container=False, status=Status.UNKNOWN,
            error="magic 01 00 01 14 not present",
        )

    header = read_header(data)
    chain = walk_resource_chain(data)
    comp = scan_compression(data)

    # VERIFIED when magic + declared length + KORF/KEND chain all hold.
    status = Status.VERIFIED
    if not chain.ok:
        status = Status.NEPOTVRDENO
    if header.declared_ok is False:
        # The two factory ROM containers inside Pa Manager show delta -1/-2.
        # That is documented, not a parser failure -> keep NEPOTVRDENO.
        status = Status.NEPOTVRDENO

    return ContainerReport(
        path=p, is_container=True, header=header, chain=chain,
        compression=comp, status=status,
    )


def split_declared_payload(data: bytes) -> tuple[bytes, bytes]:
    """Return (payload, trailing) using the declared length when it is sane.

    Six of seven containers measured inside Pa Manager declared exactly size-8; two
    factory ROM containers carried 5-6 trailing bytes past the declaration. The
    trailing bytes are returned rather than dropped, so the caller can report them.
    """
    if not is_korg_container(data):
        return data, b""
    header = read_header(data)
    if header.payload_length is None:
        return data, b""
    end = 8 + header.payload_length
    if end > len(data):
        return data, b""
    return data[:end], data[end:]
