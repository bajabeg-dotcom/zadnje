"""Coverage tests for validator.py and protected model conversion."""
from __future__ import annotations

import hashlib
import json
import struct
import tempfile
import unittest
import wave
from pathlib import Path

from pa_tool import detect
from pa_tool import model_convert, validator


def build_midi(format_type: int = 0, ppqn: int = 384) -> bytes:
    track = b"\x00\xff\x2f\x00"
    return (
        b"MThd"
        + (6).to_bytes(4, "big")
        + format_type.to_bytes(2, "big")
        + (1).to_bytes(2, "big")
        + ppqn.to_bytes(2, "big")
        + b"MTrk"
        + len(track).to_bytes(4, "big")
        + track
    )


def build_wave(path: Path, rate: int = 22_050, channels: int = 2) -> None:
    samples = []
    for index in range(100):
        value = int(12_000 * ((index % 20) - 10) / 10)
        for _ in range(channels):
            samples.append(value)
    raw = b"".join(value.to_bytes(2, "little", signed=True) for value in samples)
    with wave.open(str(path), "wb") as target:
        target.setnchannels(channels)
        target.setsampwidth(2)
        target.setframerate(rate)
        target.writeframes(raw)


def build_outer_sty(path: Path, name: str = "Style One", bank: int = 17) -> None:
    size = 0x24 + 32 * 48 + 16
    data = bytearray(size)
    data[:4] = b"\x01\x00\x01\x14"
    data[4:8] = (size - 8).to_bytes(4, "big")
    data[0x17:0x1B] = b"KORF"
    for slot in range(2):
        offset = 0x24 + slot * 48
        slot_name = name if slot == 0 else "Second"
        encoded = slot_name.encode("latin-1").ljust(16, b"\x00")
        metadata = struct.pack("<HBBHH", 0, 2, bank, slot, 0)
        data[offset : offset + 16] = encoded
        data[offset + 16 : offset + 24] = metadata
        data[offset + 24 : offset + 40] = encoded
        data[offset + 40 : offset + 48] = metadata
    data[-16:-12] = b"KBEG"
    data[-4:] = b"KEND"
    path.write_bytes(data)


class TestValidatorCoverage(unittest.TestCase):
    def test_every_evidence_model_has_a_profile(self):
        coverage = validator.validator_coverage()
        self.assertEqual(coverage["models"], 18)
        for name in (
            "PA80",
            "PA1X",
            "PA2X",
            "PA800",
            "Pa3X",
            "PA588",
            "Pa4X",
            "PA4X OS Next",
            "PA1000",
            "Pa5X",
        ):
            with self.subTest(model=name):
                self.assertIsNotNone(validator.resolve_model(name))

    def test_every_verified_extension_has_a_rule(self):
        for extension in detect.CONTENT_EXTS:
            with self.subTest(extension=extension):
                self.assertIn(extension, validator.FILE_RULES)
        self.assertIn(".SET", validator.FILE_RULES)
        self.assertIn(".KST", validator.FILE_RULES)
        self.assertIn(".ZIP", validator.FILE_RULES)

    def test_complete_matrix_has_every_model_pair(self):
        cells = validator.compatibility_matrix(".STY")
        self.assertEqual(len(cells), 18 * 18)

    def test_same_model_is_exact(self):
        cell = validator.compatibility("PA800", "PA800", ".STY")
        self.assertEqual(cell.level, validator.COMPAT_EXACT)
        self.assertTrue(cell.executable)

    def test_shared_handler_is_partial_not_falsely_exact(self):
        cell = validator.compatibility("Pa600", "PA300", ".PCG")
        self.assertEqual(cell.level, validator.COMPAT_PARTIAL)
        self.assertFalse(cell.executable)

    def test_pa5x_sound_is_refused(self):
        cell = validator.compatibility("Pa4X", "Pa5X", ".PCG")
        self.assertEqual(cell.level, validator.COMPAT_REFUSED)
        self.assertIn("0x002DD968", cell.evidence)

    def test_kst_to_older_model_is_refused(self):
        cell = validator.compatibility("Pa5X", "PA800", ".KST")
        self.assertEqual(cell.level, validator.COMPAT_REFUSED)


class TestFileValidator(unittest.TestCase):
    def test_valid_midi_generic(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "style.mid"
            path.write_bytes(build_midi(0, 384))
            report = validator.validate_path(path, target_model="PA800")
            self.assertEqual(report.decision, validator.PASS)
            self.assertTrue(any(item.code == "MIDI_TRACK" for item in report.findings))

    def test_midi_workflow_contracts_are_distinct(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "style.mid"
            path.write_bytes(build_midi(0, 384))
            style = validator.validate_path(
                path, target_model="PA800", midi_workflow="pa_style_import"
            )
            manager = validator.validate_path(
                path, target_model="PA800", midi_workflow="manager_import"
            )
            self.assertEqual(style.decision, validator.PASS)
            self.assertEqual(manager.decision, validator.FAIL)

    def test_truncated_midi_fails(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "bad.mid"
            path.write_bytes(build_midi()[:-1])
            report = validator.validate_path(path, target_model="PA800")
            self.assertEqual(report.decision, validator.FAIL)

    def test_wave_metadata_and_canonical_target(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "sample.wav"
            build_wave(path)
            report = validator.validate_path(path, target_model="PA800")
            self.assertEqual(report.decision, validator.UNKNOWN)
            finding = next(item for item in report.findings if item.code == "WAVE_STRUCTURE")
            self.assertEqual(finding.value["sample_rate_hz"], 22_050)

    def test_unknown_file_is_unknown_not_pass(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "unknown.xyz"
            path.write_bytes(b"payload")
            report = validator.validate_path(path, target_model="PA800")
            self.assertEqual(report.decision, validator.UNKNOWN)

    def test_recursive_folder_validates_every_file(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / "Demo.SET"
            root.mkdir()
            (root / "STYLE").mkdir()
            (root / "STYLE" / "one.mid").write_bytes(build_midi())
            (root / "opaque.bkp").write_bytes(b"backup")
            report = validator.validate_path(root, target_model="PA800")
            self.assertEqual(report.file_count, 2)
            self.assertEqual(len(report.children), 2)

    def test_pa5x_pcg_validator_fails_before_import(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "sound.pcg"
            data = bytearray(64)
            data[:4] = b"\x01\x00\x01\x14"
            data[4:8] = (56).to_bytes(4, "big")
            data[0x17:0x1B] = b"KORF"
            path.write_bytes(data)
            report = validator.validate_path(path, target_model="Pa5X", deep=False)
            self.assertEqual(report.decision, validator.FAIL)
            self.assertTrue(any(item.code == "PA5X_SOUND_REFUSAL" for item in report.findings))

    def test_report_refuses_source_overwrite(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "one.mid"
            path.write_bytes(build_midi())
            report = validator.validate_path(path, target_model="PA800")
            with self.assertRaises(ValueError):
                validator.save_report(report, path)


class TestValidatorCli(unittest.TestCase):
    def _run(self, *arguments: str):
        import os
        import subprocess
        import sys

        root = Path(__file__).resolve().parents[1]
        return subprocess.run(
            [sys.executable, str(root / "run_pa_tool.py"), *arguments],
            cwd=root,
            capture_output=True,
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        )

    def test_validators_command(self):
        result = self._run("validators", "--models", "--files")
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertIn(b"18 model profile", result.stdout)
        self.assertIn(b".STY", result.stdout)

    def test_matrix_command(self):
        result = self._run(
            "model-matrix",
            "PCG",
            "--source-model",
            "Pa4X",
            "--target-model",
            "Pa5X",
        )
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertIn(b"REFUSED", result.stdout)

    def test_validate_all_command(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "one.mid"
            path.write_bytes(build_midi())
            result = self._run("validate-all", str(path), "--model", "PA800")
            self.assertEqual(result.returncode, 0, result.stderr.decode())
            self.assertIn(b"PASS", result.stdout)

    def test_model_convert_dry_run_command(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "one.mid"
            target = Path(folder) / "two.mid"
            source.write_bytes(build_midi())
            result = self._run(
                "model-convert",
                str(source),
                str(target),
                "--source-model",
                "PA800",
                "--target-model",
                "Pa4X",
            )
            self.assertEqual(result.returncode, 0, result.stderr.decode())
            self.assertFalse(target.exists())
            self.assertIn(b"executed=False", result.stdout)


class TestModelConversion(unittest.TestCase):
    def test_dry_plan_writes_nothing(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "input.mid"
            target = Path(folder) / "output.mid"
            source.write_bytes(build_midi())
            report = model_convert.convert_path(
                source,
                target,
                model_convert.ConversionOptions("PA800", "Pa4X", execute=False),
            )
            self.assertFalse(target.exists())
            self.assertFalse(report.executed)
            self.assertEqual(report.files[0].operation, "plan_only")

    def test_same_model_copy_is_hash_exact(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "input.mid"
            target = Path(folder) / "output.mid"
            source.write_bytes(build_midi())
            before = hashlib.sha256(source.read_bytes()).hexdigest()
            report = model_convert.convert_path(
                source,
                target,
                model_convert.ConversionOptions("PA800", "PA800", execute=True),
            )
            self.assertEqual(report.decision, "PASS_EXACT")
            self.assertEqual(before, hashlib.sha256(source.read_bytes()).hexdigest())
            self.assertEqual(source.read_bytes(), target.read_bytes())

    def test_cross_model_midi_is_preserve_only(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "input.mid"
            target = Path(folder) / "output.mid"
            source.write_bytes(build_midi())
            report = model_convert.convert_path(
                source,
                target,
                model_convert.ConversionOptions("PA800", "Pa4X", execute=True),
            )
            self.assertEqual(report.decision, "PRESERVE_ONLY")
            self.assertEqual(source.read_bytes(), target.read_bytes())

    def test_unknown_resource_without_preserve_is_refused(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "input.sty"
            target = Path(folder) / "output.sty"
            build_outer_sty(source)
            with self.assertRaises(model_convert.ConversionRefused):
                model_convert.convert_path(
                    source,
                    target,
                    model_convert.ConversionOptions(
                        "PA800", "Pa4X", execute=True, preserve_unsupported=False
                    ),
                )
            self.assertFalse(target.exists())

    def test_fixed_width_catalog_edit_is_real_and_guarded(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "input.sty"
            target = Path(folder) / "output.sty"
            build_outer_sty(source)
            original = source.read_bytes()
            options = model_convert.ConversionOptions(
                "PA800",
                "Pa4X",
                execute=True,
                catalog=model_convert.CatalogOptions(
                    rename_map={"Style One": "Converted"}, style_bank=5
                ),
            )
            report = model_convert.convert_path(source, target, options)
            self.assertEqual(report.decision, "PARTIAL")
            self.assertEqual(source.read_bytes(), original)
            output = target.read_bytes()
            self.assertEqual(len(output), len(original))
            self.assertIn(b"Converted", output)
            self.assertEqual(output[0x24 + 19], 5)
            self.assertEqual(output[0x24 + 24 + 19], 5)
            self.assertTrue(report.files[0].changes)

    def test_wave_conversion_writes_canonical_pcm(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "input.wav"
            target = Path(folder) / "output.wav"
            build_wave(source, rate=22_050, channels=2)
            report = model_convert.convert_path(
                source,
                target,
                model_convert.ConversionOptions(
                    "PA800", "Pa4X", execute=True, canonicalize_wave=True
                ),
            )
            with wave.open(str(target), "rb") as converted:
                self.assertEqual(converted.getframerate(), 44_100)
                self.assertEqual(converted.getsampwidth(), 2)
                self.assertEqual(converted.getnchannels(), 1)
            self.assertEqual(report.files[0].operation, "canonical_wave")

    def test_mapping_schema_and_pair(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "map.json"
            payload = model_convert.mapping_template("PA800", "Pa4X")
            payload["catalog"]["object_key_map"] = {}
            path.write_text(json.dumps(payload), encoding="utf-8")
            source, target = model_convert.mapping_pair(path)
            self.assertEqual((source, target), ("PA800", "Pa4X"))
            loaded = model_convert.load_mapping(path)
            self.assertFalse(loaded.object_key_map)


if __name__ == "__main__":
    unittest.main()
