"""Tests for the real Korg CBarc / OCxx codec layer.

These run against synthetic containers built by the package's own self-test
builder, so the suite needs no commercial sample files.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from korg_binary_analyzer.decoder_engine import DecoderRegistry
from korg_binary_analyzer.korg.codec import (
    KorgCodecError,
    barc_decompress,
    barc_literal_compress,
    decode_oc3x_generic,
    encode_oc3x,
    xor_checksum,
)
from korg_binary_analyzer.korg.containers import (
    children,
    parse_indexed_native,
    parse_name_directory,
    style_name,
)
from korg_binary_analyzer.korg.resources import scan_buffer, scan_structured
from korg_binary_analyzer.selftest import SYNTHETIC_NAMES, build_synthetic_container, run_selftest


class BarcTests(unittest.TestCase):
    def test_literal_round_trip_across_token_boundaries(self) -> None:
        for length in (0, 1, 3, 4, 17, 18, 19, 271, 272, 273, 4096, 70000):
            payload = bytes((index * 7 + 3) & 0xFF for index in range(length))
            wrapped = encode_oc3x(payload, "OC31")
            decoded, metadata = decode_oc3x_generic(wrapped)
            self.assertEqual(decoded, payload, f"length {length}")
            self.assertEqual(metadata["declared_size"], length)
            self.assertEqual(metadata["produced_size"], length)

    def test_declared_size_mismatch_is_rejected(self) -> None:
        wrapped = bytearray(encode_oc3x(b"A" * 64, "OC31"))
        wrapped[4:8] = (65).to_bytes(4, "little")
        with self.assertRaises(KorgCodecError):
            decode_oc3x_generic(bytes(wrapped))

    def test_checksum_mismatch_is_rejected(self) -> None:
        wrapped = bytearray(encode_oc3x(b"Korg" * 32, "OC31"))
        wrapped[-1] ^= 0xFF
        with self.assertRaises(KorgCodecError):
            decode_oc3x_generic(bytes(wrapped))

    def test_unsupported_magic_is_refused(self) -> None:
        with self.assertRaises(KorgCodecError):
            decode_oc3x_generic(b"OC99" + bytes(64))

    def test_truncated_stream_is_refused(self) -> None:
        wrapped = encode_oc3x(b"payload" * 100, "OC31")
        with self.assertRaises(KorgCodecError):
            decode_oc3x_generic(wrapped[:20])

    def test_xor_checksum_matches_manual_computation(self) -> None:
        data = bytes(range(256))
        expected = 0
        for value in data:
            expected ^= value
        self.assertEqual(xor_checksum(data), expected)

    def test_barc_terminator_is_honoured(self) -> None:
        stream = barc_literal_compress(b"KORG" * 8, terminate=True)
        self.assertTrue(stream.endswith(b"\x01\x00\x00"))
        decoded, consumed = barc_decompress(stream, 32, require_terminator=True)
        self.assertEqual(decoded, b"KORG" * 8)
        self.assertEqual(consumed, len(stream))


class ContainerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.container = build_synthetic_container()

    def test_root_and_terminal_records(self) -> None:
        root = children(self.container)
        self.assertEqual(root[-1]["id"], 0xFE)
        self.assertEqual(root[0]["id"], 0x02)

    def test_index_addresses_only_resource_ids(self) -> None:
        indexed, framing = parse_indexed_native(self.container)
        self.assertTrue(framing["root_length_matches"])
        self.assertEqual(framing["resource_length_mismatches"], 0)
        self.assertEqual({item["id"] for item in indexed}, {0x05, 0x06, 0x09})

    def test_name_directory_layout(self) -> None:
        root = children(self.container)
        record = next(item for item in root if item["id"] == 0x05)
        directory = parse_name_directory(self.container[record["offset"] + 8:record["end"]])
        self.assertIsNotNone(directory)
        assert directory is not None
        self.assertEqual(directory["entry_size"], 48)
        self.assertEqual(
            [item["name"] for item in directory["names"]],
            list(SYNTHETIC_NAMES),
        )

    def test_name_directory_rejects_coincidental_blocks(self) -> None:
        self.assertIsNone(parse_name_directory(bytes(1920)))
        self.assertIsNone(parse_name_directory(b"\x01" * 47))

    def test_style_name_is_read_from_the_decoded_stream(self) -> None:
        report = scan_structured(self.container)
        self.assertEqual(report["status"], "DECODED_VALIDATED")
        style = next(item for item in report["resources"] if item["id"] == "0x06")
        blob = self.container[style["payload_offset"]:style["payload_offset"] + style["payload_size"]]
        decoded, _ = decode_oc3x_generic(blob)
        self.assertEqual(style_name(decoded), SYNTHETIC_NAMES[0])

    def test_scan_reports_raw_resources_separately(self) -> None:
        report = scan_structured(self.container)
        self.assertEqual(len(report["raw_resources"]), 1)
        self.assertEqual(report["raw_resources"][0]["id"], "0x05")
        self.assertIsNotNone(report["name_directory"])

    def test_raw_magic_inside_decoded_spans_is_not_re_accepted(self) -> None:
        """Magic bytes inside an already validated resource are not new resources."""
        probe = scan_buffer(self.container)["raw_magic_probe"]
        self.assertEqual(probe["magic_hits"], 6)
        self.assertEqual(probe["accepted_count"], 0)
        self.assertEqual(probe["rejected_count"], 6)

    def test_raw_magic_outside_the_index_is_accepted_when_it_validates(self) -> None:
        """A stream the index does not address is a real resource candidate."""
        orphan = encode_oc3x(b"orphan payload" * 16, "OC32")
        hybrid = bytes(self.container) + orphan
        probe = scan_buffer(hybrid)["raw_magic_probe"]
        self.assertGreaterEqual(probe["accepted_count"], 1)
        accepted_offsets = [item["offset"] for item in probe["accepted"]]
        self.assertIn(len(self.container), accepted_offsets)


class DecoderPipelineTests(unittest.TestCase):
    def test_registry_decodes_a_style_container(self) -> None:
        container = build_synthetic_container()
        with TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "sample.STY"
            source.write_bytes(container)
            result = DecoderRegistry().run(source, root / "out", container)[0]
            self.assertEqual(result["status"], "DECODED_VALIDATED")
            self.assertEqual(result["validation"]["status"], "VALIDATED")
            self.assertEqual(result["style_count"], len(SYNTHETIC_NAMES))
            self.assertEqual(result["name_agreement"]["disagreed"], 0)
            self.assertTrue(Path(result["manifest"]).is_file())
            manifest = json.loads(Path(result["manifest"]).read_text())
            self.assertEqual(manifest["container_kind"], "native-container")

    def test_source_file_is_never_modified(self) -> None:
        container = build_synthetic_container()
        with TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "sample.STY"
            source.write_bytes(container)
            DecoderRegistry().run(source, root / "out", container)
            self.assertEqual(source.read_bytes(), container)

    def test_oc20_wrapped_container_is_decoded(self) -> None:
        from korg_binary_analyzer.korg.codec import barc_literal_compress

        container = build_synthetic_container()
        footer = b"\x01\x00\x00\x00\xfb" + bytes(7)  # 12-byte OC20 footer
        page = (
            b"OC20"
            + len(container).to_bytes(4, "little")
            + barc_literal_compress(container)
            + footer
        )
        with TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "sample.BKP"
            source.write_bytes(page)
            probes = DecoderRegistry().probe(page)
            self.assertTrue(any(item["name"] == "korg-oc20-package" for item in probes))
            result = DecoderRegistry().run(source, root / "out", page)[0]
            self.assertEqual(result["status"], "DECODED_VALIDATED")
            self.assertEqual(result["style_count"], len(SYNTHETIC_NAMES))

    def test_unknown_input_reports_no_decoder(self) -> None:
        data = b"not a korg container at all" * 8
        probes = DecoderRegistry().probe(data)
        self.assertFalse(any(item["implementation"] == "SUPPORTED" for item in probes))

    def test_lexical_korg_slots_stay_candidates(self) -> None:
        probes = DecoderRegistry().probe(b"container COMP16 LFO-PKG")
        matches = [item for item in probes if item["implementation"] == "PLACEHOLDER_NOT_IMPLEMENTED"]
        self.assertTrue(any("COMP16" in item["name"] for item in matches))
        self.assertTrue(any("LFO-PKG" in item["name"] for item in matches))
        self.assertTrue(all(item["status"] == "CANDIDATE_ONLY" for item in matches))


class SelfTestTests(unittest.TestCase):
    def test_packaged_selftest_passes(self) -> None:
        self.assertEqual(run_selftest(verbose=False), 0)


if __name__ == "__main__":
    unittest.main()


class AgentContextTests(unittest.TestCase):
    """Regression: the shrink loop must always terminate (issue found on real data)."""

    def test_context_shrinks_to_budget_with_single_element_lists(self) -> None:
        from korg_binary_analyzer.agent_context import build_agent_context

        # One entry per list, each entry huge: the old 3//4 cut deleted nothing
        # and reported progress, so this call never returned.
        report = {
            "schema_version": "1.0",
            "facts": {"identity": {"filename": "x.bin", "hashes": {"sha256": "a" * 64}}, "formats": []},
            "evidence": {
                "strings": [],
                "signatures": [{"blob": "s" * 50000}],
                "entropy": {"high_entropy_regions": [{"blob": "e" * 50000}]},
                "pointer_tables": {"tables": [{"blob": "p" * 50000}]},
                "xrefs": [{"blob": "x" * 50000}],
                "embedded_objects": [{"blob": "o" * 50000}],
            },
            "candidates": {"korg_relevance": [{"blob": "k" * 50000}], "compression": [], "crypto": {"evidence": []}},
            "decoded": {"results": [{"decoder": "korg-style-sty", "status": "DECODED_VALIDATED", "outputs": [{"blob": "d" * 50000}]}], "probes": []},
            "next_actions": [{"blob": "n" * 50000}],
            "unknown": [{"blob": "u" * 50000}],
        }
        context = build_agent_context(report, max_chars=2000)
        self.assertLessEqual(len(json.dumps(context, ensure_ascii=False, separators=(",", ":"))), 4000)

    def test_decoder_bulk_is_not_copied_into_the_context(self) -> None:
        from korg_binary_analyzer.agent_context import build_agent_context

        report = {
            "schema_version": "1.0",
            "facts": {"identity": {"filename": "s.sty", "hashes": {"sha256": "b" * 64}}, "formats": []},
            "evidence": {"strings": [], "signatures": [], "entropy": {}, "pointer_tables": {}, "xrefs": [], "embedded_objects": []},
            "candidates": {"korg_relevance": [], "compression": [], "crypto": {"evidence": []}},
            "decoded": {
                "results": [{
                    "decoder": "korg-style-sty",
                    "status": "DECODED_VALIDATED",
                    "resource_count": 3,
                    "style_names": ["A", "B"],
                    "slots": [{"slot": index, "blob": "z" * 10000} for index in range(40)],
                    "outputs": [{"blob": "y" * 10000} for _ in range(40)],
                }],
                "probes": [],
            },
            "next_actions": [],
            "unknown": [],
        }
        context = build_agent_context(report, max_chars=12000)
        encoded = json.dumps(context, ensure_ascii=False)
        self.assertIn("korg-style-sty", encoded)
        self.assertNotIn("slots", encoded)
        self.assertNotIn("outputs", encoded)
