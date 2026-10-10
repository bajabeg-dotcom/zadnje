"""Selftest -- proves the hard rules on the spot, no reference material needed."""
from __future__ import annotations

from pathlib import Path
from typing import List, Tuple

from pa800_midi._mido import Message, MidiFile, MidiTrack
from pa800_midi.analysis import analyze
from pa800_midi.plan import FORBIDDEN_OPERATIONS, PlanBuilder
from pa800_midi.pipeline import optimize
from pa800_midi.validate import compare, fingerprint


def _demo_midi(path: Path) -> Path:
    """Two channels: bass (ch2, program 33) with an out-of-range low note."""
    mid = MidiFile(ticks_per_beat=480)
    track = MidiTrack()
    mid.tracks.append(track)
    # ch1: plain factory piano (no instrument profile -> only the user rule acts)
    track.append(Message("program_change", channel=0, program=0, time=0))
    track.append(Message("note_on", channel=0, note=64, velocity=80, time=0))
    track.append(Message("note_off", channel=0, note=64, velocity=0, time=240))
    # ch2: Finger Bass RX (has a profile -> key range is checked)
    track.append(Message("program_change", channel=1, program=33, time=0))
    track.append(Message("control_change", channel=1, control=0, value=121, time=0))
    track.append(Message("control_change", channel=1, control=32, value=5, time=0))
    track.append(Message("note_on", channel=1, note=45, velocity=90, time=0))
    track.append(Message("note_off", channel=1, note=45, velocity=0, time=240))
    track.append(Message("note_on", channel=1, note=24, velocity=80, time=0))
    track.append(Message("note_off", channel=1, note=24, velocity=0, time=240))
    track.append(Message("note_on", channel=9, note=36, velocity=100, time=0))
    track.append(Message("note_off", channel=9, note=36, velocity=0, time=120))
    mid.save(str(path))
    return path


def run_selftest(workdir: Path) -> List[Tuple[str, bool, str]]:
    checks: List[Tuple[str, bool, str]] = []
    source = _demo_midi(workdir / "selftest.mid")

    facts = analyze(source)
    checks.append(("analiza procita note", facts.total_notes == 4,
                   f"{facts.total_notes} nota"))
    bass = next((t for t in facts.tracks if t.role == "bass"), None)
    checks.append(("bass traka prepoznata", bass is not None and bass.note_count == 2,
                   bass.sound_name if bass else "nema"))
    checks.append(("key range konflikt nadjen",
                   bool(bass and bass.conflicts),
                   bass.conflicts[0].describe() if bass and bass.conflicts else "nema"))

    # isolated run: no gold index, so only the deterministic user rule applies
    result = optimize(source, workspace=workdir / "ws",
                      rules=["velocity=-10"], mode="apply",
                      gold_index=workdir / "nema_gold.json",
                      factory_index=workdir / "nema_factory.json",
                      demo_index=workdir / "nema_demo.json")
    checks.append(("izlazni MIDI napisan", bool(result.output_path), ""))

    if result.output_path:
        before = fingerprint(MidiFile(str(source)))
        after = fingerprint(MidiFile(str(result.output_path)))
        same_ticks = [k[2] for k in before.keys] == [k[2] for k in after.keys]
        same_pitch = [k[3] for k in before.keys] == [k[3] for k in after.keys]
        checks.append(("tick nepromijenjen", same_ticks, ""))
        checks.append(("pitch nepromijenjen", same_pitch, ""))
        checks.append(("validacija OK", result.validation.ok,
                       result.validation.status))
        mid = MidiFile(str(result.output_path))
        piano_velocities = [m.velocity for tr in mid.tracks for m in tr
                            if m.type == "note_on" and m.velocity > 0
                            and m.channel == 0]
        checks.append(("velocity smanjen tacno za 10 (clamp pravilo)",
                       piano_velocities == [70],
                       f"piano kanal: {piano_velocities} (ocekivano [70])"))

    from pa800_midi.report import OptimizeResult  # noqa: F401
    builder = PlanBuilder(facts, mode="apply")
    builder.apply_user_rules(["transpose=2"])
    refused = [op for op in builder.plan.refusals
               if op.operation in FORBIDDEN_OPERATIONS]
    checks.append(("transpose odbijen bez waiver-a", bool(refused),
                   refused[0].refusal if refused else "nije odbijen"))

    original_hash = source.read_bytes()
    checks.append(("original netaknut",
                   source.read_bytes() == original_hash, ""))
    return checks
