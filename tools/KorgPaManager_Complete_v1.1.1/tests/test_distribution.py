from __future__ import annotations

import hashlib
from pathlib import Path

from mido import MidiFile, version_info

from korg_pa_manager import __version__
from korg_pa_manager.selftest import run_self_test

ROOT = Path(__file__).resolve().parents[1]
PYTHON_INSTALLER_SHA256 = (
    "67b5635e80ea51072b87941312d00ec8927c4db9ba18938f7ad2d27b328b95fb"
)


def test_bundled_mido_and_application_versions() -> None:
    assert __version__ == "1.1.1"
    assert str(version_info) == "1.3.3"


def test_builtin_selftest_exercises_real_write_path() -> None:
    report = run_self_test(require_gui=False)
    assert report["status"] == "PASS"
    assert report["source_preserved"] is True
    assert report["saved_bytes"] > 0
    assert "validated atomic Save As" in report["checks"]
    assert "strict reopen of edited output" in report["checks"]


def test_generated_demo_is_a_strict_valid_smf() -> None:
    midi = MidiFile(ROOT / "examples" / "KorgPaManager_Demo_Style.mid", clip=False)
    assert midi.type == 0
    assert midi.ticks_per_beat == 96
    assert len(midi.tracks) == 1
    assert any(
        message.is_meta
        and message.type == "marker"
        and message.text == "v1cv1"
        for message in midi.tracks[0]
    )


def test_complete_offline_windows_payload_and_hash() -> None:
    installer = ROOT / "runtime_installer" / "python-3.12.10-amd64.exe"
    assert installer.is_file()
    assert hashlib.sha256(installer.read_bytes()).hexdigest() == PYTHON_INSTALLER_SHA256
    launcher = (ROOT / "KorgPaManager.bat").read_text(encoding="utf-8")
    assert "Include_tcltk=1" in launcher
    assert "Include_pip=0" in launcher
    assert "--self-test" in launcher
    assert "PrependPath=0" in launcher


def test_license_and_third_party_notices_are_present() -> None:
    license_text = (ROOT / "LICENSE").read_text(encoding="utf-8")
    assert license_text.startswith("MIT License\n")
    assert "Copyright (c) 2026 KorgPaManager contributors" in license_text
    assert (ROOT / "third_party" / "MIDO_LICENSE.txt").is_file()
    assert (ROOT / "third_party" / "PYTHON_LICENSE.txt").is_file()
    notice = (ROOT / "third_party" / "NOTICE.md").read_text(encoding="utf-8")
    assert PYTHON_INSTALLER_SHA256 in notice
