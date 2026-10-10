from __future__ import annotations

import hashlib
import json
import struct
import tempfile
import unittest
import zlib
from pathlib import Path

from korg_binary_analyzer.binary import (
    MappedInput, detect_formats, entropy_analysis, entropy_value, identity,
    korg_string_candidates, scan_pointer_tables, scan_signatures, scan_strings,
)
from korg_binary_analyzer.embedded import extract_validated_embedded, find_embedded
from korg_binary_analyzer.pe import PEParseError, parse_pe
from korg_binary_analyzer.pe_extra import parse_pe_extensions
from korg_binary_analyzer.binary import xref_candidates
from korg_binary_analyzer.signals import structure_candidates, transform_candidates


def make_pe32() -> bytes:
    data = bytearray(0x400)
    data[:2] = b"MZ"
    struct.pack_into("<I", data, 0x3C, 0x80)
    data[0x80:0x84] = b"PE\0\0"
    coff = 0x84
    struct.pack_into("<HHIIIHH", data, coff, 0x14C, 1, 0x65000000, 0, 0, 224, 0x0102)
    optional = coff + 20
    struct.pack_into("<H", data, optional, 0x10B)
    struct.pack_into("<I", data, optional + 16, 0x1000)
    struct.pack_into("<I", data, optional + 28, 0x400000)
    struct.pack_into("<II", data, optional + 32, 0x1000, 0x200)
    struct.pack_into("<II", data, optional + 56, 0x2000, 0x200)
    struct.pack_into("<HH", data, optional + 68, 3, 0x40)
    struct.pack_into("<I", data, optional + 92, 16)
    section = optional + 224
    data[section:section + 8] = b".text\0\0\0"
    struct.pack_into("<IIII", data, section + 8, 0x10, 0x1000, 0x200, 0x200)
    struct.pack_into("<I", data, section + 36, 0x60000020)
    data[0x200:0x210] = b"\x90\x90\x90\xc3" + b"\0" * 12
    return bytes(data)


def png_chunk(kind: bytes, content: bytes) -> bytes:
    return struct.pack(">I", len(content)) + kind + content + struct.pack(">I", zlib.crc32(kind + content) & 0xFFFFFFFF)


def make_png() -> bytes:
    header = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + png_chunk(b"IHDR", header) + png_chunk(b"IDAT", zlib.compress(b"\0\xff\0\0")) + png_chunk(b"IEND", b"")


class BinaryCoreTests(unittest.TestCase):
    def test_identity_and_hashes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "sample.bin"
            path.write_bytes(b"ABCD" * 8)
            with MappedInput(path) as mapped:
                result = identity(path, mapped.data)
            self.assertEqual(result["hashes"]["sha256"], hashlib.sha256(path.read_bytes()).hexdigest())
            self.assertEqual(result["hashes"]["sha1"], hashlib.sha1(path.read_bytes()).hexdigest())
            self.assertEqual(result["size"], 32)
            self.assertTrue(result["last_256_hex"].startswith("41 42 43 44"))

    def test_empty_file_identity_and_entropy(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "empty"
            path.write_bytes(b"")
            with MappedInput(path) as mapped:
                self.assertEqual(identity(path, mapped.data)["size"], 0)
                report = entropy_analysis(mapped.data)
            self.assertEqual(report["whole_file"]["value"], 0.0)
        self.assertEqual(entropy_value(b"\x00" * 64), 0.0)

    def test_ascii_utf8_and_utf16_strings(self) -> None:
        data = b"prefix KORG PA5X\0" + "LFO package".encode("utf-16le") + b"\0\0" + "café".encode("utf-8")
        strings = scan_strings(data)
        encodings = {item["encoding"] for item in strings}
        self.assertIn("ASCII", encodings)
        self.assertIn("UTF16LE", encodings)
        self.assertIn("UTF8", encodings)
        self.assertTrue(korg_string_candidates(strings))

    def test_entropy_windows_and_regions(self) -> None:
        noisy = bytes(range(256)) * 32
        report = entropy_analysis(noisy)
        self.assertAlmostEqual(report["whole_file"]["value"], 8.0, places=3)
        self.assertEqual(len(report["high_entropy_regions"]), 1)
        self.assertIn("not proof", report["high_entropy_regions"][0]["interpretation"])

    def test_signature_searches_nonzero_offsets(self) -> None:
        signatures = [{"name": "TEST", "category": "test", "raw": b"ABCD", "mask_bytes": b"\xff" * 4}]
        found = scan_signatures(b"xxABCDyyABCD", signatures)
        self.assertEqual([item["offset"] for item in found], [2, 8])

    def test_pointer_table_candidate_is_not_fact(self) -> None:
        data = bytearray(1024)
        struct.pack_into("<III", data, 128, 256, 260, 264)
        result = scan_pointer_tables(bytes(data))
        candidate = next(item for item in result["tables"] if item["source_offset"] == 128 and item["endian"] == "little")
        self.assertEqual(candidate["status"], "CANDIDATE")
        self.assertEqual(candidate["targets"], [256, 260, 264])
        self.assertEqual(structure_candidates(result)[0]["status"], "CANDIDATE")

    def test_pe32_headers_and_entry_point(self) -> None:
        parsed = parse_pe(make_pe32())
        self.assertEqual(parsed["coff_header"]["machine_name"], "x86")
        self.assertEqual(parsed["optional_header"]["entry_point_rva"], 0x1000)
        self.assertEqual(parsed["entry_point"]["file_offset"], 0x200)
        self.assertEqual(parsed["sections"][0]["permissions"], "RX")
        self.assertEqual(parsed["entry_point"]["section"], ".text")

    def test_pe_import_export_and_delay_imports(self) -> None:
        data = bytearray(0x800)
        data[:0x400] = make_pe32()
        section = 0x178
        struct.pack_into("<II", data, section + 8, 0x600, 0x1000)
        struct.pack_into("<I", data, section + 16, 0x600)
        optional = 0x98
        struct.pack_into("<II", data, optional + 96, 0x1200, 40)
        struct.pack_into("<II", data, optional + 104, 0x1100, 40)
        struct.pack_into("<II", data, optional + 96 + 13 * 8, 0x1300, 64)
        struct.pack_into("<IIIII", data, 0x300, 0x1180, 0, 0, 0x1150, 0x1190)
        data[0x350:0x35d] = b"KERNEL32.dll\0"
        struct.pack_into("<II", data, 0x380, 0x11C0, 0)
        struct.pack_into("<H", data, 0x3C0, 0)
        data[0x3C2:0x3CE] = b"ExitProcess\0"
        struct.pack_into("<IIHHIIIIIII", data, 0x400, 0, 0, 0, 0, 0x1240, 1, 1, 1, 0x1250, 0x1254, 0x1258)
        data[0x440:0x44B] = b"sample.dll\0"
        struct.pack_into("<I", data, 0x450, 0x1000)
        struct.pack_into("<I", data, 0x454, 0x1260)
        struct.pack_into("<H", data, 0x458, 0)
        data[0x460:0x46B] = b"PublicFunc\0"
        struct.pack_into("<IIIIIIII", data, 0x500, 1, 0x1340, 0, 0x1380, 0x1360, 0, 0, 0)
        data[0x540:0x54A] = b"delay.dll\0"
        struct.pack_into("<II", data, 0x560, 0x13A0, 0)
        struct.pack_into("<H", data, 0x5A0, 3)
        data[0x5A2:0x5ac] = b"DelayFunc\0"
        parsed = parse_pe(bytes(data))
        parsed.update(parse_pe_extensions(bytes(data), parsed))
        self.assertEqual(parsed["imports"][0]["dll"], "KERNEL32.dll")
        self.assertEqual(parsed["imports"][0]["symbols"][0]["name"], "ExitProcess")
        self.assertEqual(parsed["exports"]["symbols"][0]["name"], "PublicFunc")
        self.assertEqual(parsed["delay_imports"][0]["dll"], "delay.dll")
        self.assertEqual(parsed["delay_imports"][0]["symbols"][0]["name"], "DelayFunc")

    def test_xref_pointer_target_candidate(self) -> None:
        tables = {"tables": [{"source_offset": 4, "width_bits": 32, "targets": [32], "confidence": 0.6}]}
        strings = [{"offset": 32, "content": "marker"}]
        xrefs = xref_candidates(tables, strings, [])
        self.assertEqual(xrefs[0]["target_type"], "string")
        self.assertEqual(xrefs[0]["status"], "CANDIDATE")

    def test_truncated_pe_is_rejected(self) -> None:
        with self.assertRaises(PEParseError):
            parse_pe(b"MZ" + b"\0" * 20)

    def test_embedded_png_is_bounded_and_crc_validated(self) -> None:
        payload = b"parent-prefix" + make_png() + b"tail"
        signatures = scan_signatures(payload)
        embedded = find_embedded(payload, signatures)
        png = next(item for item in embedded if item["embedded_signature"] == "PNG")
        self.assertEqual(png["status"], "BOUNDED_VALIDATED")
        with tempfile.TemporaryDirectory() as temporary:
            outputs = extract_validated_embedded(payload, embedded, Path(temporary), "a" * 64)
            self.assertEqual(len(outputs), 1)
            self.assertEqual(Path(outputs[0]["path"]).read_bytes(), make_png())

    def test_embedded_json_and_xml_are_bounded_before_extraction(self) -> None:
        payload = b"parent-prefix" + b'{"name":"sample","items":[1,2]}' + b"tail" + b"<?xml version=\"1.0\"?><root><item>ok</item></root>tail"
        embedded = find_embedded(payload, [])
        formats = {item["embedded_signature"] for item in embedded if item["status"] == "BOUNDED_VALIDATED"}
        self.assertIn("JSON", formats)
        self.assertIn("XML", formats)
        with tempfile.TemporaryDirectory() as temporary:
            outputs = extract_validated_embedded(payload, embedded, Path(temporary), "b" * 64)
            self.assertEqual({item["format"] for item in outputs}, {"JSON", "XML"})
            self.assertTrue(all(Path(item["path"]).is_file() for item in outputs))

    def test_transform_probe_requires_known_magic(self) -> None:
        key = 0x37
        encoded = bytes(byte ^ key for byte in b"\x7fELF" + b"payload")
        candidate = transform_candidates(encoded)["candidates"]
        self.assertTrue(any(item["key_hex"] == "37" and item["matched_magic"] == "ELF" for item in candidate))
        self.assertEqual(transform_candidates(b"random bytes")["candidates"], [])

    def test_format_detection_raw_fallback(self) -> None:
        result = detect_formats(b"\x01\x02\x03")
        self.assertEqual(result[0]["fact"], "RAW_BINARY")
        self.assertEqual(result[0]["status"], "OBSERVED")


if __name__ == "__main__":
    unittest.main()