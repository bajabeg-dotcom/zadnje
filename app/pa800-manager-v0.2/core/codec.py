"""Bounded readers for the PA800 resource compression families.

Evidence: user's 2026-09-22 PA800 reverse-engineering archive, especially
06_TOOLS_SCRIPTS/native_style_verifier_and_oc31.py and oc31_port.py.
No OC20 writer is provided: its final-page trailer has not been validated here.
"""
from __future__ import annotations

from dataclasses import dataclass

MAX_PAGE = 4 * 1024 * 1024
MAX_RESOURCE = 16 * 1024 * 1024
MAX_FILE = 64 * 1024 * 1024


class FormatError(ValueError):
    """The input is truncated, outside bounds, or contradicts its own metadata."""


@dataclass(frozen=True)
class Page:
    compressed_at: int
    decoded_size: int
    stream_end: int
    footer: bytes


def cbarc_decode(source: bytes, position: int, capacity: int) -> tuple[bytes, int]:
    """Decode one Korg CBarc stream, stopping at its explicit 01 00 00 tag.

    Returns (decoded bytes, first offset after the terminator). Calls bound both
    the declared output size and all input/backreference accesses. The caller
    is responsible for interpreting bytes following the stream.
    """
    if not 0 <= capacity <= MAX_RESOURCE:
        raise FormatError("Deklarisana veličina dekompresije je izvan dozvoljenih granica")
    out = bytearray()

    def take() -> int:
        nonlocal position
        if position >= len(source):
            raise FormatError("Prekinut kompresovani tok")
        value = source[position]
        position += 1
        return value

    def be16() -> int:
        return (take() << 8) | take()

    def room(count: int) -> None:
        if count < 0 or len(out) + count > capacity:
            raise FormatError("Kompresovani tok prekoračuje deklarisanu veličinu")

    def literal(count: int) -> None:
        nonlocal position
        room(count)
        if position + count > len(source):
            raise FormatError("Prekinut niz literalnih bajtova")
        out.extend(source[position:position + count])
        position += count

    while True:
        control = take()
        if control < 0x10:
            if control >= 2:
                count = control + 2
            elif control == 0:
                count = take() + 18
            else:
                count = be16()
                if count == 0:
                    break
            literal(count)
            continue

        if control >= 0x40:
            packed = (control << 8) | take()
            count = (packed >> 13) + 1
            match = len(out) - ((packed >> 2) & 0x7FF) - 1
        elif control >= 0x20:
            count = control & 0x1F
            if count >= 2:
                count += 2
            elif count == 0:
                count = take() + 34
            else:
                count = be16()
            match = len(out) - (be16() >> 2) - 1
        else:
            count = control & 0x07
            if count >= 2:
                count += 2
            elif count == 0:
                count = take() + 10
            else:
                count = be16()
            match = len(out) - (be16() >> 2) - 16385
            if control & 0x08:
                match -= 16384

        room(count)
        if match < 0:
            raise FormatError("Neispravna povratna referenca u kompresiji")
        for _ in range(count):
            if match >= len(out):
                raise FormatError("Povratna referenca izlazi izvan dekompresovanog bloka")
            out.append(out[match])
            match += 1
        trailing = source[position - 1] & 0x03
        literal(trailing)

    if len(out) != capacity:
        raise FormatError(f"Dekomprimovano {len(out)} umjesto {capacity} bajtova")
    return bytes(out), position


def decode_oc3(payload: bytes) -> tuple[bytes, str]:
    """Decode OC31/OC32 and validate the XOR of decoded bytes."""
    if len(payload) < 12 or payload[:4] not in (b"OC31", b"OC32"):
        raise FormatError("Nedostaje valjan OC31/OC32 omotač")
    size = int.from_bytes(payload[4:8], "little")
    if size > MAX_RESOURCE:
        raise FormatError("OC3x objekt je prevelik")
    decoded, after = cbarc_decode(payload, 8, size)
    if after != len(payload) - 1:
        raise FormatError("Neočekivani bajtovi poslije OC3x kompresovanog toka")
    xor = 0
    for value in decoded:
        xor ^= value
    if xor != payload[-1]:
        raise FormatError("OC3x XOR kontrolni bajt se ne podudara")
    return decoded, payload[:4].decode("ascii")


def encode_oc31_literal(data: bytes) -> bytes:
    """Software-only OC31 encoder for inner-object round-trip tests.

    This does NOT create an instrument-safe OC20/STY file.
    """
    if len(data) > MAX_RESOURCE:
        raise FormatError("Prevelik objekt za testni OC31 enkoder")
    packed = bytearray(b"OC31" + len(data).to_bytes(4, "little"))
    position = 0
    while position < len(data):
        part = data[position:position + 0xFFFC]
        packed.extend(b"\x01" + len(part).to_bytes(2, "big") + part)
        position += len(part)
    packed.extend(b"\x01\x00\x00")
    xor = 0
    for value in data:
        xor ^= value
    packed.append(xor)
    return bytes(packed)


def decode_oc20_file(data: bytes) -> tuple[bytes, list[Page]]:
    """Read one or more OC20 pages up to the root's declared size.

    Final-page footer semantics are not proven: this reader validates page
    lengths and the resource tree but never writes/repacks an OC20 file.
    """
    if len(data) > MAX_FILE:
        raise FormatError("Fajl prelazi limit od 64 MiB")
    position = 0
    parts: list[bytes] = []
    pages: list[Page] = []
    root_size: int | None = None
    for _ in range(256):
        if data[position:position + 4] != b"OC20":
            raise FormatError("Nedostaje OC20 zaglavlje stranice")
        if position + 8 > len(data):
            raise FormatError("Prekinuto OC20 zaglavlje")
        size = int.from_bytes(data[position + 4:position + 8], "little")
        if not 0 < size <= MAX_PAGE:
            raise FormatError("Nevažeća veličina OC20 stranice")
        page, after = cbarc_decode(data, position + 8, size)
        parts.append(page)
        if sum(map(len, parts)) > MAX_FILE:
            raise FormatError("Dekodirani fajl prelazi limit od 64 MiB")
        decoded = b"".join(parts)
        if root_size is None and len(decoded) >= 8:
            if decoded[:4] != bytes.fromhex("01 00 01 14"):
                raise FormatError("Neočekivana korijenska oznaka Korg resursa")
            root_size = 8 + int.from_bytes(decoded[4:8], "big")
            if root_size < 16 or root_size > MAX_FILE:
                raise FormatError("Nevažeća veličina Korg korijena")
        if root_size is not None and len(decoded) > root_size:
            raise FormatError("OC20 stranice prelaze deklarisani korijen")
        if root_size == len(decoded):
            footer = data[after:]
            # Corpus-derived page check: first footer byte is the sum modulo 256
            # of that page's OC20 header plus compressed CBarc bytes. The other
            # five final payload bytes are still preserve-only and not authored.
            if len(footer) != 14 or footer[1:5] != b"\xfb\x00\x00\x18":
                raise FormatError("Neprepoznat završni OC20 okvir")
            if footer[0] != sum(data[position:after]) & 0xFF:
                raise FormatError("Nevažeća OC20 kontrolna suma stranice")
            pages.append(Page(position, size, after, footer))
            return decoded, pages
        # Continuation uses an observed 9-byte page terminator followed by OC20.
        next_position = after + 9
        footer = data[after:next_position]
        if len(footer) != 9 or footer[1:5] != b"\xfb\x00\x008" or data[next_position:next_position + 4] != b"OC20":
            raise FormatError("Neprepoznata granica između OC20 stranica")
        if footer[0] != sum(data[position:after]) & 0xFF:
            raise FormatError("Nevažeća OC20 kontrolna suma stranice")
        pages.append(Page(position, size, after, footer))
        position = next_position
    raise FormatError("Previše OC20 stranica")
