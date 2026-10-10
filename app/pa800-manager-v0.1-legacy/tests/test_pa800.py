"""Offline regression tests based on the owner's 2026-09-22 evidence fixtures.

Run: python -m unittest discover -s tests -v
Optional full-corpus run: PA800_FACTORY_CORPUS=/path/to/ALL python -m unittest ...
"""
from __future__ import annotations

from io import BytesIO
import os
from pathlib import Path
import sys
import unittest
from zipfile import ZipFile, ZIP_DEFLATED

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core.codec import FormatError, decode_oc20_file, decode_oc3, encode_oc31_literal
from core.format import Bank
from core.sounds import load_sound_catalog
from server import Workspace, UserError

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"


class CorpusTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sounds, cls.lookup = load_sound_catalog()

    def bank(self, name):
        return Bank(name, (FIXTURES / name).read_bytes(), self.lookup)

    def test_factory_catalog_exact_selectors(self):
        self.assertEqual(len(self.sounds), 1003)
        self.assertEqual(self.lookup[(121, 0, 0)], "AcousticPiano GM")
        self.assertEqual(len(self.lookup), len(self.sounds))

    def test_style_bank_and_true_inner_patterns(self):
        bank = self.bank("BANK01.STY")
        self.assertEqual(bank.count_by_tag, {"06": 18, "09": 18})
        self.assertEqual(bank.base_info()["indexValid"], True)
        for resource in bank.resource_listing():
            detail = bank.resource_detail(resource["index"])
            self.assertEqual(detail["codec"], "OC31")
            if resource["family"] == "Style":
                self.assertEqual(detail["elementCount"], 13)
                self.assertEqual(len(detail["elements"]), 13)
                self.assertEqual(len(detail["elements"][0]["tracks"]), 8)
                self.assertGreater(detail["patterns"], 1)
                self.assertGreater(detail["cvCount"], 0)

    def test_perf_pad_and_global(self):
        prf = self.bank("BANK01.PRF")
        self.assertEqual(len(prf.resources), 15)
        self.assertEqual(prf.resource_detail(0)["variant"], "Release 3")
        pad = self.bank("Bank01.pad")
        self.assertEqual(len(pad.resources), 30)
        self.assertEqual(pad.resource_detail(0)["objectName"], "88 Cowbell")
        self.assertEqual(len(pad.resource_detail(0)["elements"][0]["tracks"]), 8)
        self.assertEqual(len(self.bank("SETUP.GBL").resources), 19)
        self.assertEqual(len(self.bank("PRGMAPS.TBL").resources), 6)

    def test_pcg_multpage(self):
        bank = self.bank("ROM.PCG")
        self.assertEqual(bank.base_info()["pages"], 2)
        self.assertEqual(len(bank.resources), 1071)
        self.assertEqual(bank.resource_detail(0)["family"], "Sound resource")

    def test_codec_roundtrip_and_checksum(self):
        for raw in (b"", b"hello", bytes(range(256)) * 32):
            self.assertEqual(decode_oc3(encode_oc31_literal(raw))[0], raw)
        original = bytearray(encode_oc31_literal(b"just a test"))
        original[-1] ^= 1
        with self.assertRaises(FormatError):
            decode_oc3(bytes(original))

    def test_corrupt_oc20_page_checksum_rejected(self):
        raw = bytearray((FIXTURES / "BANK01.PRF").read_bytes())
        raw[-14] ^= 1
        with self.assertRaisesRegex(FormatError, "kontrolna suma"):
            decode_oc20_file(bytes(raw))

    def test_corrupt_index_rejected_and_original_preserved(self):
        source = (FIXTURES / "BANK01.PRF").read_bytes()
        decoded, _ = decode_oc20_file(source)
        edited = bytearray(decoded)
        kbeg = edited.rfind(b"KBEG")
        self.assertGreater(kbeg, 0)
        edited[kbeg + 4] ^= 1
        with self.assertRaisesRegex(FormatError, "KBEG"):
            Bank("BANK01.PRF", bytes(edited))
        self.assertEqual(Bank("BANK01.PRF", source).source, source)

    def test_demo_audit(self):
        ws = Workspace()
        result = ws.audit()
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["checkedResources"], 1177)
        self.assertEqual(result["failures"], [])
        self.assertEqual(result["codecs"]["OC31"] + result["codecs"]["OC32"] + result["codecs"]["RAW"], 1177)

    def test_upload_zip_and_path_traversal(self):
        data = BytesIO()
        with ZipFile(data, "w", ZIP_DEFLATED) as z:
            z.writestr("My.set/STYLE/BANK01.STY", (FIXTURES / "BANK01.STY").read_bytes())
            z.writestr("My.set/PAD/Bank01.pad", (FIXTURES / "Bank01.pad").read_bytes())
            z.writestr("My.set/README.txt", "not parsed")
        ws = Workspace()
        result = ws.import_file("my-set.zip", data.getvalue())
        self.assertEqual(result["counts"]["files"], 2)
        self.assertEqual(result["counts"]["styles"], 18)
        self.assertEqual(result["counts"]["pads"], 30)
        self.assertEqual(ws.audit()["status"], "PASS")
        evil = BytesIO()
        with ZipFile(evil, "w") as z:
            z.writestr("../escape.STY", b"x")
        with self.assertRaisesRegex(UserError, "nesigurno"):
            ws.import_file("evil.zip", evil.getvalue())
        self.assertEqual(ws.overview()["counts"]["files"], 2)

    @unittest.skipUnless(os.getenv("PA800_FACTORY_CORPUS"), "Set PA800_FACTORY_CORPUS for full-archive audit")
    def test_full_factory_corpus(self):
        root = Path(os.environ["PA800_FACTORY_CORPUS"])
        totals = {"Style": 0, "PAD": 0, "Performance": 0}
        failed = []
        for file in root.rglob("*"):
            if not file.is_file() or file.suffix.upper() not in {".STY", ".PAD", ".PRF", ".GBL", ".PCG", ".TBL"}:
                continue
            bank = Bank(file.name, file.read_bytes(), self.lookup)
            for entry in bank.resource_listing():
                if entry["family"] in totals:
                    try:
                        bank.resource_detail(entry["index"])
                        totals[entry["family"]] += 1
                    except (FormatError, ValueError) as exc:
                        failed.append(f"{file.name}:{entry['name']}: {exc}")
        self.assertEqual(failed, [])
        self.assertEqual(totals, {"Style": 435, "PAD": 411, "Performance": 264})


if __name__ == "__main__":
    unittest.main()
