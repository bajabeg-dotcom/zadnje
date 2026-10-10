"""Tests for pa_tool. Standard library only (no third-party test runner required).

Run:  python -m unittest discover -s tests -v
  or: python run_tests.py
"""
from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
WORKSPACE = ROOT.parent
sys.path.insert(0, str(ROOT))

from pa_tool import container, convert, detect, manager, models, pcg, setfolder, validate  # noqa: E402
from pa_tool.status import Result, Status  # noqa: E402

SAMPLE_SET = WORKSPACE / "samples" / "NEWNAME_SET" / "NEWNAME.SET"
_EXTERNAL_SAMPLE_PCG = SAMPLE_SET / "SOUND" / "USER01.PCG"
SAMPLE_PCG = (_EXTERNAL_SAMPLE_PCG if _EXTERNAL_SAMPLE_PCG.is_file()
              else HERE / "fixtures" / "USER01.PCG")
PA5X_KST = Path(r"E:\Pa5X_UNPACKED\omega_sys\omega_sys\FactoryBackup\User.KST")
PA5X_METADIR = Path(r"E:\Pa5X_UNPACKED\omega_sys\omega_sys\STARTUP\ALL\STYLE\FACTORY\Ballad")


class TestStatusDiscipline(unittest.TestCase):
    def test_result_status_is_worst(self):
        r = Result(name="t")
        r.add("a", 1, Status.VERIFIED)
        self.assertEqual(r.status, Status.VERIFIED)
        r.add("b", 2, Status.UNKNOWN)
        self.assertEqual(r.status, Status.UNKNOWN)

    def test_decision_reads_explicit_verdict(self):
        r = Result(name="t")
        r.add("decision", "FAIL", Status.VERIFIED)
        self.assertEqual(r.decision(), "FAIL")

    def test_decision_falls_back_to_status(self):
        r = Result(name="t")
        r.add("x", 1, Status.UNKNOWN)
        self.assertEqual(r.decision(), "UNKNOWN")

    def test_finding_keeps_evidence(self):
        r = Result(name="t")
        f = r.add("k", "v", Status.VERIFIED, evidence="0x1234")
        d = f.to_dict()
        self.assertEqual(d["evidence"], "0x1234")
        self.assertEqual(d["status"], "VERIFIED")


class TestDataLayer(unittest.TestCase):
    def test_all_data_files_load_and_are_valid_json(self):
        for name in ("models.json", "korg_tokens.json", "rejections.json", "conversions.json",
                     "pcg_chunks.json"):
            with self.subTest(data=name):
                p = ROOT / "data" / name
                self.assertTrue(p.is_file(), f"missing {p}")
                with p.open(encoding="utf-8") as fh:
                    payload = json.load(fh)
                self.assertIn("_meta", payload)

    def test_canonical_order_matches_documented_table(self):
        names = [m.name for m in models.canonical_models()]
        self.assertEqual(
            names,
            ["PA80", "PA1X", "PA2X", "Pa3X", "PA500", "Pa600", "Pa900", "Pa4X", "PA1000", "Pa5X"],
        )

    def test_pa4x_base_ram_not_the_os_next_variant(self):
        info = models.ram_for("Pa4X")
        self.assertEqual(info["size_mb"], 400)
        self.assertIn((1536, "OS Next"), info["variants"])

    def test_pa800_has_no_own_sub(self):
        self.assertIsNone(models.sus_for("PA800"))

    def test_container_magic_is_verified_bytes(self):
        self.assertEqual(models.container_magic(), b"\x01\x00\x01\x14")

    def test_osc_limit_is_candidates_only(self):
        info = models.osc_limits()
        self.assertEqual([c["value"] for c in info["candidates"]], [4, 5, 16, 24])
        self.assertIn("disproof", info)

    def test_pa588_variant_uses_pa500_sound_handler(self):
        info = models.variant_by_name("PA588")
        self.assertEqual(info["status"], "HIGH")
        self.assertIn("PA_OS_SOUND_PA500", info["meaning"])

    def test_pa3x_le_variant_uses_pa3x_sound_handler(self):
        info = models.variant_by_name("PA3X Le")
        self.assertEqual(info["status"], "HIGH")
        self.assertIn("PA_OS_SOUND_PA3X", info["meaning"])


class TestContainer(unittest.TestCase):
    def test_real_pcg_is_a_container(self):
        if not SAMPLE_PCG.is_file():
            self.skipTest("sample missing")
        data = SAMPLE_PCG.read_bytes()
        self.assertTrue(container.is_korg_container(data))
        header = container.read_header(data)
        self.assertEqual(header.payload_length, len(data) - 8)
        self.assertTrue(header.declared_ok)

    def test_chain_and_compression(self):
        if not SAMPLE_PCG.is_file():
            self.skipTest("sample missing")
        data = SAMPLE_PCG.read_bytes()
        chain = container.walk_resource_chain(data)
        self.assertTrue(chain.korf_offsets)
        self.assertTrue(chain.kend_offsets)

    def test_non_container_is_not_claimed(self):
        self.assertFalse(container.is_korg_container(b"not a korg file at all"))

    def test_balanced_chain_accepts_proper_pairs(self):
        data = b"KBEG" + b"." * 4 + b"KEND" + b"." * 4 + b"KBEG" + b"." * 4 + b"KEND"
        chain = container.walk_resource_chain(data)
        self.assertEqual(len(chain.kbeg_offsets), 2)
        self.assertEqual(len(chain.kend_offsets), 2)
        self.assertTrue(chain.balanced)

    def test_balanced_chain_rejects_orphan_kend(self):
        # a KEND with no matching KBEG before it must not pass as a sound chain
        data = b"KEND" + b"." * 4 + b"KBEG" + b"." * 4 + b"KEND"
        chain = container.walk_resource_chain(data)
        self.assertFalse(chain.balanced)

    def test_split_payload_keeps_trailing_bytes(self):
        # magic + declared length 10 + 10-byte body + 3 trailing bytes past the declaration
        data = b"\x01\x00\x01\x14" + (10).to_bytes(4, "big") + b"KORF" + b"ABCDEF" + b"XYZ"
        payload, trailing = container.split_declared_payload(data)
        self.assertEqual(trailing, b"XYZ")
        self.assertEqual(len(payload), 18)

    def test_split_payload_without_declaration_returns_everything(self):
        data = b"\x01\x00\x01\x14" + (999).to_bytes(4, "big") + b"KORF"
        payload, trailing = container.split_declared_payload(data)
        self.assertEqual(payload, data)
        self.assertEqual(trailing, b"")


class TestPcgDialect(unittest.TestCase):
    def test_pa_korf_dialect_uses_verified_signature(self):
        data = bytearray(96)
        data[:4] = b"\x01\x00\x01\x14"
        data[4:8] = (len(data) - 8).to_bytes(4, "big")
        data[23:27] = b"KORF"
        data[68:72] = b"OC31"
        rep = pcg.classify_bytes(bytes(data), "USER01.PCG")
        self.assertEqual(rep.dialect, "pa_korf")
        self.assertEqual(rep.status, Status.VERIFIED)
        self.assertTrue(rep.declared_ok)
        self.assertEqual(rep.token_offsets["KORF"], [23])

    def test_workstation_pcg1_dialect_is_not_confused_with_pa(self):
        data = bytearray(64)
        data[:4] = b"KORG"
        data[16:20] = b"PCG1"
        data[24:28] = b"PRG1"
        data[40:44] = b"PBK1"
        rep = pcg.classify_bytes(bytes(data), "WORKSTATION.PCG")
        self.assertEqual(rep.dialect, "korg_riff_pcg")
        self.assertEqual(rep.status, Status.HIGH)
        self.assertIn("PBK1", rep.token_offsets)

    def test_pcg_chunk_meanings_are_format_specific(self):
        self.assertEqual(pcg.chunk_semantics("PBK1")["format"],
                         "KORG/PCG1 workstation PCG")
        self.assertEqual(pcg.chunk_semantics("RLP1")["format"], "KORG KMP")

    def test_real_evidence_hashes_are_recorded(self):
        samples = pcg.evidence_data()["dialects"]["pa_korf"]["samples"]
        by_name = {entry["name"]: entry for entry in samples}
        self.assertEqual(by_name["USER01.PCG"]["size"], 493)
        self.assertEqual(
            by_name["USER01.PCG"]["sha256"],
            "accc652bfa0bcb81f5eb2f8cd0433fa26ccb95f7344e9196dd59c563cd35cc2b",
        )
        self.assertTrue(by_name["USERDK.PCG"]["declared_ok"])


class TestDetect(unittest.TestCase):
    def test_set_folder_detected(self):
        if not SAMPLE_SET.is_dir():
            self.skipTest("sample missing")
        prof = detect.detect(SAMPLE_SET)
        self.assertEqual(prof.kind, "set")
        self.assertEqual(prof.status, Status.VERIFIED)

    def test_set_model_stays_unknown(self):
        if not SAMPLE_SET.is_dir():
            self.skipTest("sample missing")
        prof = detect.detect(SAMPLE_SET)
        self.assertIsNone(prof.model)
        self.assertEqual(prof.model_status, Status.UNKNOWN)

    def test_pcg_detected_as_container_kind(self):
        if not SAMPLE_PCG.is_file():
            self.skipTest("sample missing")
        prof = detect.detect(SAMPLE_PCG)
        self.assertEqual(prof.kind, "pcg")
        self.assertIsNotNone(prof.container)

    def test_percent_name_roundtrip(self):
        raw = "6%2F8 Ballad.stg"
        self.assertEqual(detect.decode_percent_name(raw), "6/8 Ballad.stg")
        self.assertEqual(detect.encode_percent_name("6/8 Ballad.stg"), raw)

    def test_missing_path_is_unknown_not_an_exception(self):
        prof = detect.detect(WORKSPACE / "definitely-not-here-12345")
        self.assertEqual(prof.status, Status.UNKNOWN)


class TestSetFolder(unittest.TestCase):
    def test_inventory_counts_match_disk(self):
        if not SAMPLE_SET.is_dir():
            self.skipTest("sample missing")
        rep = setfolder.inventory_set(SAMPLE_SET)
        real = sum(1 for f in SAMPLE_SET.rglob("*") if f.is_file())
        self.assertEqual(rep.lo_files, real)
        self.assertGreater(rep.total_bytes, 0)

    def test_metadir_parser_reports_schema(self):
        if not PA5X_METADIR.is_dir():
            self.skipTest("Pa5X sample missing")
        info = setfolder.parse_metadir(PA5X_METADIR)
        self.assertTrue(info["found"])
        self.assertTrue(info["has_files"])
        self.assertEqual(info["file_count"], 70)
        self.assertEqual(info["default_file_icon"], "sknIcoStyle_Ballad1")

    def test_metadir_decodes_percent_names(self):
        if not PA5X_METADIR.is_dir():
            self.skipTest("Pa5X sample missing")
        info = setfolder.parse_metadir(PA5X_METADIR)
        joined = " ".join(e["file"] for e in info["files_decoded"])
        self.assertIn("6/8", joined)
        self.assertNotIn("%2F", joined)


class TestValidate(unittest.TestCase):
    def test_osc_limit_is_always_unknown(self):
        for model in ("PA80", "Pa5X", "Pa4X"):
            with self.subTest(model=model):
                res = validate.check_osc_limit(model)
                self.assertEqual(res.status, Status.UNKNOWN)
                self.assertEqual(res.decision(), "UNKNOWN")

    def test_pa5x_sound_import_is_refused(self):
        d = validate.check_pa5x_sound_import("Pa5X")
        self.assertEqual(d.verdict, "FAIL")
        self.assertEqual(d.evidence, "0x002DD968")

    def test_locked_style_is_refused(self):
        d = validate.check_locked("STYLE")
        self.assertEqual(d.verdict, "FAIL")
        self.assertEqual(d.evidence, "0x0024AD90")

    def test_factory_rom_is_refused(self):
        d = validate.check_unsupported("FACTORY_ROM")
        self.assertEqual(d.verdict, "FAIL")
        self.assertEqual(d.evidence, "0x002DD868")

    def test_ram_fit_and_overflow(self):
        ok = validate.check_ram("Pa5X", required_mb=2048)
        self.assertIn("yes", [f.value for f in ok.findings])
        bad = validate.check_ram("PA80", required_mb=99999)
        self.assertIn("no", [f.value for f in bad.findings])

    def test_canonical_sample_format_flags_deviation(self):
        res = validate.check_canonical_sample_format(48000, 24, 2)
        notes = " ".join(f.note for f in res.findings)
        self.assertIn("must be converted", notes)

    def test_container_check_passes_on_real_sample(self):
        if not SAMPLE_PCG.is_file():
            self.skipTest("sample missing")
        res = validate.check_container(SAMPLE_PCG)
        self.assertEqual(res.decision(), "PASS")

    def test_hard_limits_detects_excess(self):
        res = validate.check_hard_limits({"foldera u SET-u": 999})
        self.assertIn("exceeds the documented limit",
                      " ".join(f.note for f in res.findings))


class TestInputImmutability(unittest.TestCase):
    """The core promise: inputs are never modified."""

    def _hash_tree(self, root: Path) -> dict:
        import hashlib
        out = {}
        if root.is_file():
            out[str(root)] = hashlib.sha256(root.read_bytes()).hexdigest()
            return out
        for f in sorted(root.rglob("*")):
            if f.is_file():
                out[str(f)] = hashlib.sha256(f.read_bytes()).hexdigest()
        return out

    def test_detect_and_inventory_do_not_touch_the_sample(self):
        if not SAMPLE_SET.is_dir():
            self.skipTest("sample missing")
        before = self._hash_tree(SAMPLE_SET)
        detect.detect(SAMPLE_SET)
        setfolder.inventory_set(SAMPLE_SET)
        validate.build_import_report(SAMPLE_PCG, model="Pa5X")
        after = self._hash_tree(SAMPLE_SET)
        self.assertEqual(before, after)


class TestManagerImport(unittest.TestCase):
    """Data imported from the 2.exe_pa_analysis results."""

    def test_resource_inventory_totals(self):
        mp = manager.mapping_summary()
        self.assertEqual(mp["total"], 175)
        self.assertEqual(mp["from_file"], 126)
        self.assertEqual(mp["from_image"], 49)
        self.assertEqual(mp["unmapped"], 0)

    def test_the_two_factory_rom_containers_are_present_and_recovered(self):
        kmp = manager.resource("FAC_ROM_KMP")
        pcg = manager.resource("FAC_ROM_PCG")
        self.assertIsNotNone(kmp)
        self.assertIsNotNone(pcg)
        self.assertEqual(kmp.size, 421364)
        self.assertEqual(pcg.size, 684380)
        # both lived in the virtual tail, so both had to come from the image
        self.assertTrue(kmp.was_recovered)
        self.assertTrue(pcg.was_recovered)

    def test_packer_is_pecompact_with_evidence(self):
        pk = manager.packer_info()
        self.assertIn("PECompact", pk["value"])
        self.assertEqual(pk["evidence"]["PECompact"], "0x00B7E218")

    def test_name_table_layout_is_structured_not_regex(self):
        layout = manager.name_table_layout()
        self.assertEqual(layout["record_size"], 24)
        self.assertEqual(layout["name_field_size"], 18)
        self.assertEqual(layout["table_start"], "0x0024")

    def test_name_table_sections_are_sequential(self):
        secs = manager.program_sections()
        self.assertEqual(len(secs), 2)
        first, second = secs
        self.assertEqual((first["index_from"], first["index_to"]), (0, 1226))
        self.assertEqual((second["index_from"], second["index_to"]), (2047, 2144))
        self.assertEqual(first["records"], 1227)
        self.assertEqual(second["records"], 98)

    def test_known_factory_names_are_exact(self):
        names = {e["name"] for e in manager.factory_programs()}
        for expected in ("AcousticPiano GM", "Grand&MovingPad", "Piano Pad 1",
                         "Harpsi Korg", "Nasty Bass", "Synth Kit 3"):
            self.assertIn(expected, names)

    def test_regex_junk_is_absent(self):
        """The naive regex produced 50 junk tokens; the structured parse must not."""
        names = {e["name"] for e in manager.factory_programs()}
        for junk in ("(JSULJG3", "+cScMI", "26JS2YIVC", "4O&NcIG", "D5MDcFAa"):
            self.assertNotIn(junk, names)

    def test_percent_and_punctuation_names_survive(self):
        names = {e["name"] for e in manager.factory_programs()}
        self.assertIn("Accordion 16,8'", names)
        self.assertTrue(any(n.startswith("90's") for n in names))

    def test_embedded_modules_are_the_three_pe_kinds(self):
        mods = manager.embedded_modules()
        self.assertEqual(len(mods), 3)
        machines = sorted(m["machine"] for m in mods)
        self.assertEqual(machines, ["0x014C", "0x8664", "0x8664"])
        self.assertTrue(any(m["has_pecompact"] for m in mods))
        self.assertTrue(any(m["has_upx"] for m in mods))
        self.assertTrue(any(m["is_rex_sdk"] for m in mods))

    def test_container_templates_are_real_containers(self):
        tpl = manager.container_templates()
        self.assertGreaterEqual(len(tpl), 8)
        self.assertTrue(any(t["is_container"] for t in tpl))

    def test_find_factory_name(self):
        hits = manager.find_factory_name("korg")
        self.assertTrue(any(h["name"] == "Harpsi Korg" for h in hits))

    def test_embedded_module_layout_matches_v5_stream_family(self):
        """Same module family as PE1/PE2/PE3, but not the same bytes."""
        mods = {m["resource_file"]: m for m in manager.embedded_modules()}
        by_size = {m["size"]: m for m in mods.values()}
        self.assertIn(11264, by_size)
        self.assertIn(856576, by_size)
        self.assertEqual(by_size[11264]["sections"][:2], ["UPX0", "UPX1"])
        self.assertEqual(len(by_size[856576]["sections"]), 7)


class TestManagerDrumTable(unittest.TestCase):
    """FAC_ROM_KMP: OC32 decompression and the 40-byte name table inside it."""

    def test_oc32_payload_decodes_to_the_declared_size(self):
        dec = manager.drum_decode_detail()
        self.assertEqual(dec.get("magic"), "OC32")
        self.assertEqual(dec.get("capacity"), 1141260)
        self.assertEqual(dec.get("trailer_bytes"), 30)

    def test_oc32_xor_check_byte_matches(self):
        dec = manager.drum_decode_detail()
        self.assertEqual(dec.get("xor_computed"), "0x5b")
        self.assertEqual(dec.get("xor_in_file"), "0x5b")
        self.assertTrue(dec.get("xor_match"))

    def test_drum_table_is_verified(self):
        self.assertEqual(manager.drum_names_status(), Status.VERIFIED)

    def test_drum_table_layout(self):
        t = manager.drum_table_layout()
        self.assertEqual(t["record_size"], 40)
        self.assertEqual(t["name_field_size"], 18)
        self.assertEqual(t["table_start"], "0xAFF78")

    def test_drum_table_record_count(self):
        t = manager.drum_table_layout()
        self.assertEqual(t["records"], 1599)
        self.assertEqual(t["distinct_names"], 1337)

    def test_real_drum_articulation_names_present(self):
        """Names that come from the 40-byte table itself (not the sample region)."""
        names = {e["name"] for e in manager.drum_entries()}
        for expected in ("Inch Std1", "Inch Std6", "+Rim Gate1 f", "+Rim Plate mf",
                         "Natural 1a", "zz Hi center"):
            self.assertIn(expected, names)

    def test_articulation_suffixes_present(self):
        """p / mf / f are the velocity articulations of one drum."""
        names = {e["name"] for e in manager.drum_entries()}
        for suffix in ("p", "mf", "f"):
            self.assertIn(f"+Rim Gate1 {suffix}", names)

    def test_unnamed_slots_are_kept_not_dropped(self):
        dk = manager.drum_names()
        self.assertEqual(dk["named_records"] + dk["unnamed_slots"], dk["records"])
        self.assertGreater(dk["unnamed_slots"], 0)

    def test_sample_region_names_extracted(self):
        """The separate sample region holds names like 'BD Natural1 mf'."""
        self.assertGreater(manager.drum_names().get("sample_name_count", 0), 500)
        sample_texts = {e["text"] for e in manager.drum_sample_names()}
        self.assertIn("BD Natural1 mf", sample_texts)
        self.assertIn("BD 22 Inch Std1", sample_texts)

    def test_sample_region_filter_removes_compressed_noise(self):
        """Raw ASCII runs contain compressed-stream junk such as '1iDv'."""
        dk = manager.drum_names()
        self.assertGreater(dk["sample_names_raw_count"], dk["sample_name_count"])
        texts = {e["text"] for e in manager.drum_sample_names()}
        for junk in ("1iDv", "5Izo", "NBIy"):
            self.assertNotIn(junk, texts)

    def test_find_drum_name(self):
        hits = manager.find_drum_name("rim")
        self.assertTrue(any("Rim" in (h.get("name") or "") for h in hits))

    def test_oc_decode_method_documented(self):
        info = manager.oc_decode_info()
        self.assertEqual(info.get("status"), "VERIFIED")
        self.assertIn("CBarc", info.get("note", ""))


class TestConvert(unittest.TestCase):
    """Conversion planning and the refusals that must happen before any work."""

    def test_matrix_matches_the_documented_directions(self):
        keys = [d.key for d in convert.directions()]
        self.assertIn("mg_to_std", keys)
        self.assertIn("set_to_kst", keys)
        self.assertIn("kst_to_set", keys)

    def test_kst_to_set_does_not_exist_and_is_blocked(self):
        plan = convert.plan_conversion("a.kst", "b.set", "kst_to_set")
        self.assertTrue(plan.blocked)
        self.assertIn("does not exist", plan.block_reason)

    def test_factory_rom_is_refused_before_acting(self):
        plan = convert.plan_conversion("a.bin", "b.bin", "mg_to_std",
                                       source_has_factory_rom=True)
        self.assertTrue(plan.blocked)
        self.assertEqual(plan.block_evidence, "0x002DD868")

    def test_locked_style_is_refused(self):
        plan = convert.plan_conversion("a.sty", "b.sty", "mg_to_std",
                                       kind="STYLE", source_is_locked=True)
        self.assertTrue(plan.blocked)
        self.assertEqual(plan.block_evidence, "0x0024AD90")

    def test_pa5x_sound_import_is_refused(self):
        plan = convert.plan_conversion("a.pcg", "b.pcg", "mg_to_std", model="Pa5X")
        self.assertTrue(plan.blocked)
        self.assertEqual(plan.block_evidence, "0x002DD968")

    def test_in_place_conversion_is_refused(self):
        plan = convert.plan_conversion("same.bin", "same.bin", "mg_to_std")
        self.assertTrue(plan.blocked)
        self.assertIn("equals source", plan.block_reason)

    def test_set_to_kst_is_planned_but_marked_unproven(self):
        plan = convert.plan_conversion("a.set", "b.kst", "set_to_kst")
        self.assertFalse(plan.blocked)
        self.assertEqual(plan.status, Status.NEPOTVRDENO)
        self.assertTrue(any("NEPOTVRDENO" in w for w in plan.warnings))

    def test_mg_direction_is_verified_and_reversible(self):
        plan = convert.plan_conversion("a.bin", "b.bin", "mg_to_std")
        self.assertFalse(plan.blocked)
        self.assertEqual(plan.status, Status.VERIFIED)
        self.assertTrue(plan.direction.reversible)

    def test_copy_helper_writes_a_new_path_only(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "in.bin"
            src.write_bytes(b"payload")
            out = Path(td) / "out"
            made = convert.copy_input(src, out)
            self.assertTrue(made.is_file())
            self.assertNotEqual(made.resolve(), src.resolve())
            self.assertEqual(src.read_bytes(), b"payload")

    def test_copy_helper_refuses_to_write_over_the_source(self):
        if not SAMPLE_PCG.is_file():
            self.skipTest("sample missing")
        with self.assertRaises(ValueError):
            convert.copy_input(SAMPLE_PCG, SAMPLE_PCG.parent)


class TestOcDecode(unittest.TestCase):
    """OC31/OC32 decoding through container.decode_oc_payload."""

    KMP = (WORKSPACE / "2.exe_pa_analysis" / "2_e059fd4b" / "resources")

    def _kmp(self):
        hits = list(self.KMP.glob("*FAC_ROM_KMP*"))
        return hits[0] if hits else None

    def test_kmp_oc32_decodes_with_matching_xor(self):
        p = self._kmp()
        if p is None:
            self.skipTest("KMP resource missing")
        data = p.read_bytes()
        pos = data.find(b"OC32")
        self.assertNotEqual(pos, -1)
        result = container.decode_oc_payload(data, pos)
        self.assertTrue(result.ok)
        self.assertEqual(result.capacity, len(result.decoded))
        self.assertEqual(result.magic, "OC32")
        self.assertEqual(result.trailer_bytes, 30)
        self.assertTrue(result.xor_match)
        self.assertEqual(result.xor_computed, "0x5b")

    def test_decode_reports_missing_codec_gracefully(self):
        """A plausible header with no codec available reports it, never crashes."""
        blob = b"OC32" + (1024).to_bytes(4, "little") + b"\x00" * 64
        saved = (container._CODEC, container._CODEC_TRIED)
        container._CODEC, container._CODEC_TRIED = None, True
        try:
            result = container.decode_oc_payload(blob, 0)
        finally:
            container._CODEC, container._CODEC_TRIED = saved
        self.assertFalse(result.ok)
        self.assertIn("codec", result.error.lower())

    def test_decode_reports_out_of_range_declared_size(self):
        blob = b"OC32" + (0).to_bytes(4, "little") + b"\x00" * 16
        result = container.decode_oc_payload(blob, 0, codec=object())
        self.assertFalse(result.ok)
        self.assertIn("out of range", result.error.lower())


class TestCompressionOffsets(unittest.TestCase):
    def test_scan_reports_first_offsets(self):
        if not SAMPLE_PCG.is_file():
            self.skipTest("sample missing")
        data = SAMPLE_PCG.read_bytes()
        comp = container.scan_compression(data)
        if comp.any:
            self.assertIsNotNone(comp.first_offset)
            self.assertIsNotNone(comp.first_magic)

    def test_high_entropy_container_is_flagged_as_compressed(self):
        p = (WORKSPACE / "2.exe_pa_analysis" / "2_e059fd4b" / "resources")
        hits = list(p.glob("*FAC_ROM_KMP*"))
        if not hits:
            self.skipTest("KMP resource missing")
        data = hits[0].read_bytes()
        comp = container.scan_compression(data)
        self.assertTrue(comp.any)
        self.assertEqual(comp.first_magic, b"OC32")


class TestConvertAssets(unittest.TestCase):
    """Reuse of the existing setconvert + pa800_native code, no reimplementation."""

    def test_paths_resolve(self):
        paths = convert.resolve_paths()
        if paths.get("setconvert_parent") is None or paths.get("pa800_native_parent") is None:
            self.skipTest("setconvert / pa800_native assets not reachable")
        self.assertIsNotNone(paths.get("setconvert_parent"))
        self.assertIsNotNone(paths.get("pa800_native_parent"))

    def test_setconvert_loads_with_the_missing_encoder_shimmed(self):
        if convert.resolve_paths().get("setconvert_parent") is None:
            self.skipTest("setconvert assets not reachable")
        mod, notes = convert.load_setconvert()
        self.assertIsNotNone(mod)
        self.assertIn("shimmed", notes.get("fast_encoder", "") + notes.get("error", ""))

    def test_six_sty_families_are_exposed(self):
        fams = convert.sty_families()
        if not fams:
            self.skipTest("setconvert families not reachable")
        ids = {f["id"] for f in fams}
        self.assertEqual(ids, {"legacy", "transition", "pa1x", "pa800", "pa3x", "pa4x"})

    def test_pa4x_family_uses_the_64_byte_name_table(self):
        fams = convert.sty_families()
        if not fams:
            self.skipTest("setconvert families not reachable")
        by_id = {f["id"]: f for f in fams}
        self.assertEqual(by_id["pa4x"]["name_table"], "64B")
        self.assertEqual(by_id["pa800"]["name_table"], "24B")

    def test_fingerprint_a_real_pa4x_style(self):
        hits = [f for f in (WORKSPACE / "KORGA" if False else WORKSPACE / "KORG_KOMPLET_2026-10-06").rglob("*.sty")]
        if not hits:
            self.skipTest("no style files")
        # pick one the family table can identify
        for p in hits[:40]:
            info = convert.fingerprint_sty(p)
            if info.get("ok") and info.get("family"):
                self.assertTrue(info["styles"] > 0)
                self.assertTrue(info["kbeg_ok"])
                return
        self.skipTest("no identifiable style found")

    def test_fingerprint_missing_file_reports_error(self):
        info = convert.fingerprint_sty(WORKSPACE / "nope-not-here.sty")
        self.assertFalse(info.get("ok"))

    def test_env_override_is_probed_before_defaults(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "setconvert").mkdir()
            (root / "setconvert" / "conv.py").write_text("# stub")
            (root / "pa800_native").mkdir()
            (root / "pa800_native" / "oc31_codec.py").write_text("# stub")
            saved = os.environ.get("PA_TOOL_ASSET_ROOT")
            os.environ["PA_TOOL_ASSET_ROOT"] = str(root)
            try:
                paths = convert.resolve_paths()
            finally:
                if saved is None:
                    os.environ.pop("PA_TOOL_ASSET_ROOT", None)
                else:
                    os.environ["PA_TOOL_ASSET_ROOT"] = saved
            self.assertEqual(paths["setconvert_parent"], root)
            self.assertEqual(paths["pa800_native_parent"], root)


class TestCliSmoke(unittest.TestCase):
    """The CLI must exit 0 on the read-only commands."""

    def _run(self, *args):
        import subprocess
        env = dict(os.environ, PYTHONIOENCODING="utf-8")
        return subprocess.run(
            [sys.executable, str(ROOT / "run_pa_tool.py"), *args],
            capture_output=True, env=env,
        )

    def test_doctor(self):
        r = self._run("doctor")
        self.assertEqual(r.returncode, 0, r.stderr.decode("utf-8", "replace"))

    def test_models(self):
        r = self._run("models")
        self.assertEqual(r.returncode, 0, r.stderr.decode("utf-8", "replace"))
        self.assertIn(b"Pa5X", r.stdout)

    def test_conversions(self):
        r = self._run("conversions")
        self.assertEqual(r.returncode, 0, r.stderr.decode("utf-8", "replace"))

    def test_pcg(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "USER01.PCG"
            data = bytearray(80)
            data[:4] = b"\x01\x00\x01\x14"
            data[4:8] = (72).to_bytes(4, "big")
            data[23:27] = b"KORF"
            path.write_bytes(data)
            r = self._run("pcg", str(path))
        self.assertEqual(r.returncode, 0, r.stderr.decode("utf-8", "replace"))
        self.assertIn(b"pa_korf", r.stdout)

    def test_status(self):
        r = self._run("status")
        self.assertEqual(r.returncode, 0, r.stderr.decode("utf-8", "replace"))

    def test_manager(self):
        r = self._run("manager", "--names")
        self.assertEqual(r.returncode, 0, r.stderr.decode("utf-8", "replace"))
        self.assertIn(b"1325", r.stdout)
        self.assertIn(b"1599", r.stdout)

    def test_convert_matrix(self):
        r = self._run("convert", "--matrix")
        self.assertEqual(r.returncode, 0, r.stderr.decode("utf-8", "replace"))
        self.assertIn(b"set_to_kst", r.stdout)

    def test_convert_assets(self):
        r = self._run("convert", "--assets")
        self.assertEqual(r.returncode, 0, r.stderr.decode("utf-8", "replace"))
        self.assertIn(b"sty families", r.stdout)

    def test_convert_refusal_exit_code(self):
        r = self._run("convert", "x.bin", "--direction", "kst_to_set", "--dry-run")
        self.assertEqual(r.returncode, 3, r.stderr.decode("utf-8", "replace"))

    def test_oc_decode(self):
        p = (WORKSPACE / "2.exe_pa_analysis" / "2_e059fd4b" / "resources")
        hits = list(p.glob("*FAC_ROM_KMP*"))
        if not hits:
            self.skipTest("KMP resource missing")
        r = self._run("oc", str(hits[0]))
        self.assertEqual(r.returncode, 0, r.stderr.decode("utf-8", "replace"))
        self.assertIn(b"1141260", r.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
