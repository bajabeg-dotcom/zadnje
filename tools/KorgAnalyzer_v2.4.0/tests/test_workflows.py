from __future__ import annotations

import gzip
import io
import json
import tempfile
import unittest
import zipfile
from contextlib import redirect_stdout
from pathlib import Path

from korg_binary_analyzer.agent_context import build_agent_context
from korg_binary_analyzer.binary import MappedInput
from korg_binary_analyzer.cli import main
from korg_binary_analyzer.compare import compare_files
from korg_binary_analyzer.decoder_engine import DecoderRegistry
from korg_binary_analyzer.korg.signatures import load_korg_signatures
from korg_binary_analyzer.reports import write_bundle
from korg_binary_analyzer.scanner import analyze_file, scan_directory


class WorkflowTests(unittest.TestCase):
    def test_gzip_decodes_and_validates_without_mutating_input(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "payload.gz"
            original = b"validated content " * 100
            source.write_bytes(gzip.compress(original))
            before = source.read_bytes()
            workspace = root / "workspace"
            with MappedInput(source) as mapped:
                results = DecoderRegistry(include_korg=False).run(source, workspace, mapped.data)
            self.assertEqual(results[0]["status"], "DECODED_VALIDATED")
            self.assertEqual(Path(results[0]["output"]).read_bytes(), original)
            self.assertEqual(source.read_bytes(), before)

    def test_corrupt_gzip_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "bad.gz"
            source.write_bytes(gzip.compress(b"payload")[:-4])
            with MappedInput(source) as mapped:
                results = DecoderRegistry(include_korg=False).run(source, Path(temporary) / "workspace", mapped.data)
            self.assertEqual(results[0]["status"], "ATTEMPTED_FAILED")
            self.assertEqual(results[0]["validation"]["status"], "REJECTED")

    def test_zip_extraction_validates_crc_and_stays_within_root(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "safe.zip"
            with zipfile.ZipFile(source, "w") as archive:
                archive.writestr("folder/file.txt", "hello")
            with MappedInput(source) as mapped:
                results = DecoderRegistry(include_korg=False).run(source, root / "workspace", mapped.data)
            self.assertEqual(results[0]["status"], "DECODED_VALIDATED")
            self.assertEqual(Path(results[0]["outputs"][0]["path"]).read_text(), "hello")

    def test_zip_path_traversal_is_rejected_and_rolled_back(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "unsafe.zip"
            with zipfile.ZipFile(source, "w") as archive:
                archive.writestr("../escape.txt", "no")
            workspace = root / "workspace"
            with MappedInput(source) as mapped:
                results = DecoderRegistry(include_korg=False).run(source, workspace, mapped.data)
            self.assertEqual(results[0]["status"], "ATTEMPTED_FAILED")
            self.assertFalse((root / "escape.txt").exists())
            self.assertFalse((workspace / "extracted" / "unsafe").exists())

    def test_high_entropy_candidate_does_not_abort_analysis(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "high-entropy.bin"
            source.write_bytes(bytes(range(256)) * 32)
            report = analyze_file(source, root / "workspace")
            self.assertTrue(report["evidence"]["entropy"]["high_entropy_regions"])
            self.assertTrue(any("high-entropy" in action["action"] for action in report["next_actions"]))
            self.assertIn("not proof", report["evidence"]["entropy"]["high_entropy_regions"][0]["interpretation"])

    def test_analysis_bundle_and_agent_context(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "unknown.bin"
            source.write_bytes(b"KORG COMP16 candidate text\x00" + bytes(range(64)))
            workspace = root / "workspace"
            report = analyze_file(source, workspace)
            self.assertEqual(report["schema_version"], "1.0")
            self.assertTrue(report["candidates"]["korg_relevance"])
            context = build_agent_context(report)
            self.assertNotIn("binary", context)
            output = write_bundle(report, workspace, "analyze")
            self.assertTrue((output / "report.json").exists())
            self.assertTrue((output / "agent_prompt.txt").exists())
            report_text = (output / "report.txt").read_text()
            for heading in ("FACTS:", "EVIDENCE:", "CANDIDATES:", "DECODED:", "VALIDATED:", "UNKNOWN:", "NEXT ACTIONS:"):
                self.assertIn(heading, report_text)
            manifest = json.loads((output / "artifact_manifest.json").read_text())
            self.assertIn("report.json", manifest["artifacts"])

    def test_korg_slots_are_candidates_not_decoders(self) -> None:
        probes = DecoderRegistry().probe(b"container COMP16 LFO-PKG")
        matches = [item for item in probes if item["implementation"] == "PLACEHOLDER_NOT_IMPLEMENTED"]
        self.assertTrue(any("COMP16" in item["name"] for item in matches))
        self.assertTrue(any("LFO-PKG" in item["name"] for item in matches))
        self.assertTrue(all(item["status"] == "CANDIDATE_ONLY" for item in matches))
        self.assertEqual(load_korg_signatures(), [])

    def test_agent_context_respects_compact_size_budget(self) -> None:
        source_strings = [{"offset": index, "length": 512, "encoding": "ASCII", "content": "KORG " + "x" * 500, "categories": ["KORG"]} for index in range(500)]
        report = {
            "schema_version": "1.0", "facts": {"identity": {"filename": "large.bin", "hashes": {"sha256": "a" * 64}}, "formats": []},
            "evidence": {"strings": source_strings, "signatures": [], "entropy": {"high_entropy_regions": []}, "pointer_tables": {"tables": []}, "xrefs": [], "embedded_objects": []},
            "candidates": {"korg_relevance": [], "compression": [], "crypto": {"evidence": []}},
            "decoded": {"results": [], "probes": []}, "next_actions": [], "unknown": [],
        }
        context = build_agent_context(report, max_chars=3000)
        self.assertLessEqual(len(json.dumps(context, ensure_ascii=False, separators=(",", ":"))), 3000)
        self.assertTrue(context.get("context_truncated"))

    def test_directory_scan_groups_duplicates_and_continues(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "input"
            root.mkdir()
            (root / "a.bin").write_bytes(b"same data")
            nested = root / "nested"
            nested.mkdir()
            (nested / "b.dat").write_bytes(b"same data")
            (nested / "c.unknown").write_bytes(b"unique raw")
            result = scan_directory(root, Path(temporary) / "workspace")
            self.assertEqual(result["files_scanned"], 3)
            self.assertEqual(len(result["duplicate_groups"]), 1)
            self.assertEqual(result["duplicate_groups"][0]["paths"], ["a.bin", "nested/b.dat"])

    def test_compare_groups_changed_blocks(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = root / "a.bin"
            second = root / "b.bin"
            first.write_bytes(b"A" * 8192)
            second.write_bytes(b"B" + b"A" * 8191)
            result = compare_files(first, second, root / "workspace")
            self.assertFalse(result["comparison"]["same_sha256"])
            self.assertEqual(result["comparison"]["changed_region_count"], 1)
            self.assertEqual(result["comparison"]["changed_regions"][0]["start"], 0)

    def test_cli_decode_extract_scan_compare_report_and_hex(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = root / "workspace"
            folder = root / "folder"
            folder.mkdir()
            first = folder / "first.bin"
            second = folder / "second.bin"
            first.write_bytes(b"A" * 8192)
            second.write_bytes(b"B" + b"A" * 8191)
            compressed = root / "file.gz"
            compressed.write_bytes(gzip.compress(b"decoder CLI payload"))
            archive = root / "archive.zip"
            with zipfile.ZipFile(archive, "w") as output:
                output.writestr("inside.txt", "extracted")
            sink = io.StringIO()
            with redirect_stdout(sink):
                self.assertEqual(main(["scan", str(folder), "--output", str(workspace)]), 0)
                self.assertEqual(main(["compare", str(first), str(second), "--output", str(workspace)]), 0)
                self.assertEqual(main(["decode", str(compressed), "--output", str(workspace)]), 0)
                self.assertEqual(main(["extract", str(archive), "--output", str(workspace)]), 0)
                self.assertEqual(main(["hex", str(first), "0x0", "--output", str(workspace)]), 0)
            report_json = sorted((workspace / "reports").glob("decode-*/report.json"))[-1]
            report = json.loads(report_json.read_text())
            self.assertEqual(report["decoded"]["status"], "DECODED_VALIDATED")
            self.assertEqual(report["source_integrity_check"]["status"], "UNCHANGED")
            self.assertEqual(report["next_actions"][0]["action"], "Review the validated decoded output")
            with redirect_stdout(sink):
                self.assertEqual(main(["report", str(report_json), "--output", str(workspace)]), 0)

    def test_cli_analyze_smoke(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "input.bin"
            source.write_bytes(b"unknown bytes")
            with redirect_stdout(io.StringIO()):
                status = main(["analyze", str(source), "--output", str(root / "workspace")])
            self.assertEqual(status, 0)
            reports = list((root / "workspace" / "reports").glob("analyze-*/report.json"))
            self.assertEqual(len(reports), 1)


if __name__ == "__main__":
    unittest.main()