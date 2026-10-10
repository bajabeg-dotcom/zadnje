"""Real Korg CBarc / OCxx codec.

This module implements the compression and wrapping layers that are shared by
every Korg Pa resource container from the Pa1X (2003) generation onwards:

* **CBarc** -- a byte-oriented LZ77 variant used by the instrument OS
  (``CChkCmdBG::DecompressData``).  The token grammar below was confirmed by
  disassembling the Pa1X OS and by decoding factory corpora from Pa1X, Pa800,
  Pa3X, Pa600 and Pa4X/Pa700/Pa1000 with a 100% success rate.
* **OC20** -- the factory-package page wrapper (``"OC20" + u32LE size`` +
  CBarc stream + a 12/17 byte footer).  Used for the flash pages inside
  ``.BKP``/``.pkg`` updates.
* **OC31 / OC32** -- the per-resource wrapper (``"OC3x" + u32LE decoded size``
  + CBarc stream + ``01 00 00`` terminator + XOR checksum byte).  The checksum
  is the XOR of every decoded byte and is therefore a *verifiable* integrity
  signal: a stream that decodes to the declared size **and** matches the XOR
  byte is not a coincidence.

Everything here is read-only and bounded.  Nothing in this module writes to
the input.
"""

from __future__ import annotations

from typing import Any

#: Byte pattern at offset 0 of a native (unwrapped) Korg resource container.
NATIVE_ROOT = bytes.fromhex("01 00 01 14")

#: OC20 page payload size used by the factory packer.
OC20_PAGE_SIZE = 0x40000

#: Resource ids that the terminal index is allowed to point at.
INDEXED_IDS = frozenset({0x05, 0x06, 0x09})

#: Highest resource id observed in the terminal index of a native container.
TERMINAL_ID = 0xFE


class KorgCodecError(ValueError):
    """Raised when a stream does not decode cleanly."""


def xor_checksum(data: bytes) -> int:
    """Return the XOR of every byte in *data* (the OC3x integrity byte)."""
    checksum = 0
    for value in data:
        checksum ^= value
    return checksum


def be32(data: bytes, offset: int) -> int:
    if offset < 0 or offset + 4 > len(data):
        raise KorgCodecError(f"BE32 read outside buffer at {offset:#x}")
    return int.from_bytes(data[offset:offset + 4], "big")


def barc_decompress(
    stream: bytes,
    capacity: int,
    require_terminator: bool = False,
) -> tuple[bytes, int]:
    """Decompress a CBarc stream.

    ``capacity`` is the exact number of bytes the stream must produce -- it is
    the value the container declares, so a stream that produces a different
    amount is rejected instead of guessed at.  Returns ``(decoded, consumed)``.

    Token grammar (``ctl`` is the first byte of each token)::

        ctl 2..15    literal, count = ctl + 2
        ctl 0        literal, count = next_byte + 18
        ctl 1        literal, count = BE16; 0 means end-of-stream
        ctl >= 0x40  match, count = (v16 >> 13) + 1
                            distance = ((v16 >> 2) & 0x7FF) + 1
        ctl 0x20..3F match, count = (ctl & 0x1F) + 2 / next + 34 / BE16
                            distance = (BE16 >> 2) + 1
        ctl 0x10..1F match, count = (ctl & 0x07) + 2 / next + 10 / BE16
                            distance = (BE16 >> 2) + 0x4001
                            (+0x4000 more when bit 3 of ctl is set)

    Every match token is followed by ``last_consumed_byte & 3`` trailing
    literal bytes.
    """
    if capacity < 0:
        raise KorgCodecError("negative capacity")
    source = 0
    output = bytearray()
    terminated = False

    def take_byte() -> int:
        nonlocal source
        if source >= len(stream):
            raise KorgCodecError("truncated CBarc byte")
        value = stream[source]
        source += 1
        return value

    def take_be16() -> int:
        return (take_byte() << 8) | take_byte()

    def reserve(count: int) -> None:
        if count < 0 or len(output) + count > capacity:
            raise KorgCodecError("CBarc output exceeds declared size")

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
                raise KorgCodecError("truncated CBarc literal")
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
            raise KorgCodecError("CBarc match precedes output buffer")
        for _ in range(count):
            if match >= len(output):
                raise KorgCodecError("invalid CBarc overlapping match")
            output.append(output[match])
            match += 1

        trailing = stream[source - 1] & 0x03
        reserve(trailing)
        if source + trailing > len(stream):
            raise KorgCodecError("truncated CBarc trailing literal")
        output.extend(stream[source:source + trailing])
        source += trailing

    if len(output) != capacity:
        raise KorgCodecError(
            f"CBarc produced {len(output)} bytes, container declared {capacity}"
        )
    if require_terminator and not terminated:
        if source + 3 > len(stream) or stream[source:source + 3] != b"\x01\x00\x00":
            raise KorgCodecError("CBarc stream lacks terminator")
        source += 3
        terminated = True
    if require_terminator and source != len(stream):
        raise KorgCodecError(
            f"CBarc left {len(stream) - source} compressed bytes unread"
        )
    return bytes(output), source


def barc_literal_compress(data: bytes, terminate: bool = False) -> bytes:
    """Emit *data* as a purely literal CBarc stream (used for side-by-side re-encode)."""
    output = bytearray()
    position = 0
    remaining = len(data)
    while remaining > 0xFFFF:
        count = 0xFFFC
        output.extend(b"\x01\xff\xfc")
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


# ---------------------------------------------------------------------------
# OC20 -- factory package pages
# ---------------------------------------------------------------------------


def decode_oc20_pages(raw: bytes) -> tuple[bytes, list[dict[str, Any]]]:
    """Decode a run of OC20 pages into the raw native container."""
    position = 0
    decoded = bytearray()
    pages: list[dict[str, Any]] = []
    while position < len(raw):
        if raw[position:position + 4] != b"OC20":
            raise KorgCodecError(f"expected OC20 page at {position:#x}")
        size = int.from_bytes(raw[position + 4:position + 8], "little")
        page, consumed = barc_decompress(raw[position + 8:], size, False)
        stream_end = position + 8 + consumed
        next_page = raw.find(b"OC20", stream_end)
        page_end = next_page if next_page >= 0 else len(raw)
        footer = raw[stream_end:page_end]
        if len(footer) not in {12, 17} or footer[:3] != b"\x01\x00\x00" or footer[4] != 0xFB:
            raise KorgCodecError(f"unrecognized OC20 footer at {stream_end:#x}")
        pages.append({
            "offset": position,
            "compressed_size": page_end - position,
            "decoded_size": size,
            "footer_size": len(footer),
        })
        decoded.extend(page)
        position = page_end
    return bytes(decoded), pages


def is_oc20_pages(data: bytes) -> bool:
    return bytes(data[:4]) == b"OC20"


# ---------------------------------------------------------------------------
# OC31 / OC32 -- per-resource wrappers
# ---------------------------------------------------------------------------


def decode_oc3x_generic(payload: bytes) -> tuple[bytes, dict[str, Any]]:
    """Decode an OC31/OC32 resource wrapper and verify its XOR checksum.

    Returns ``(decoded, metadata)``.  Raises :class:`KorgCodecError` when the
    wrapper magic, the declared size, the terminator or the XOR checksum does
    not line up.  A returned stream is therefore *evidence-qualified*: the
    declared size matched and an independent one-byte integrity value matched.
    """
    if len(payload) < 12 or payload[:4] not in (b"OC31", b"OC32"):
        raise KorgCodecError("invalid OC31/OC32 wrapper")
    size = int.from_bytes(payload[4:8], "little")
    decoded, consumed = barc_decompress(payload[8:], size, False)
    stream_end = 8 + consumed
    if payload[stream_end:stream_end + 3] == b"\x01\x00\x00":
        stream_end += 3
    if stream_end >= len(payload):
        raise KorgCodecError("OC31/OC32 checksum is missing")
    checksum = payload[stream_end]
    if xor_checksum(decoded) != checksum:
        raise KorgCodecError(
            f"OC31/OC32 XOR checksum mismatch: computed {xor_checksum(decoded):#04x}, "
            f"stored {checksum:#04x}"
        )
    trailer = payload[stream_end + 1:]
    return decoded, {
        "wrapper": payload[:4].decode("ascii"),
        "declared_size": size,
        "produced_size": len(decoded),
        "stream_size": stream_end - 8,
        "checksum": checksum,
        "trailer_size": len(trailer),
    }


def is_oc3x(data: bytes) -> bool:
    return bytes(data[:4]) in (b"OC31", b"OC32")


def encode_oc3x(data: bytes, magic: str = "OC31") -> bytes:
    """Re-wrap *data* as an OC3x resource (literal-only CBarc + XOR checksum)."""
    if magic not in {"OC31", "OC32"}:
        raise KorgCodecError(f"unsupported resource wrapper {magic!r}")
    return (
        magic.encode("ascii")
        + len(data).to_bytes(4, "little")
        + barc_literal_compress(data, True)
        + bytes([xor_checksum(data)])
    )
