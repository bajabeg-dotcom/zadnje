"""Tests for the PA800 MIDI Optimizer / Restorator.

These tests exist to prove the hard rules, not to decorate the project:

* tick and pitch of existing notes never change,
* Key Range is reported, never "repaired",
* CANDIDATE operations are proposed only, UNKNOWN does nothing,
* the original file is never touched,
* validation catches a tampered note.
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

PROJECT = Path(__file__).resolve().parent.parent
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

from pa800_midi._mido import (                                   # noqa: E402
    Message,
    MetaMessage,
    MidiFile,
    MidiTrack,
)
from pa800_midi.analysis import analyze, sha256_file               # noqa: E402
from pa800_midi.evidence import (                                  # noqa: E402
    Confidence,
    Evidence,
    EvidenceBook,
)
from pa800_midi.index import (                                     # noqa: E402
    DemoIndex,
    FactoryIndex,
    GoldProfile,
    build_gold_index,
    rx_evidence_for,
)
from pa800_midi.plan import (                                      # noqa: E402
    FORBIDDEN_OPERATIONS,
    PlanBuilder,
    TransformationPlan,
)
from pa800_midi.pipeline import optimize                           # noqa: E402
from pa800_midi.remap import (                                     # noqa: E402
    GM_TO_PA800_REMAP,
    pa800_target,
    rx_variant_for,
)
from pa800_midi.validate import (                                  # noqa: E402
    VALIDATION_FAILED,
    VALIDATION_OK,
    compare,
    fingerprint,
)

DEMO_DIR = Path("/home/user/ev/demo_midis/demo_midis")


def make_midi(path: Path, *, program: int = 0, channel: int = 0,
              notes=((64, 80, 0, 240),), cc00: int = 121, cc32: int = 0,
              drum: bool = False) -> Path:
    mid = MidiFile(ticks_per_beat=480)
    track = MidiTrack()
    mid.tracks.append(track)
    track.append(MetaMessage("track_name", name="test", time=0))
    track.append(Message("control_change", channel=channel, control=0,
                         value=cc00, time=0))
    track.append(Message("control_change", channel=channel, control=32,
                         value=cc32, time=0))
    track.append(Message("program_change", channel=channel, program=program,
                         time=0))
    for pitch, velocity, offset, duration in notes:
        track.append(Message("note_on", channel=channel, note=pitch,
                             velocity=velocity, time=offset))
        track.append(Message("note_off", channel=channel, note=pitch,
                             velocity=0, time=duration))
    if drum:
        track.append(Message("note_on", channel=9, note=36, velocity=110, time=0))
        track.append(Message("note_off", channel=9, note=36, velocity=0, time=120))
    mid.save(str(path))
    return path


class EvidenceTests(unittest.TestCase):
    def test_confidence_gate(self) -> None:
        self.assertTrue(Confidence.VERIFIED.can_auto_apply)
        self.assertTrue(Confidence.DOCUMENTED.can_auto_apply)
        self.assertTrue(Confidence.STRONG_EVIDENCE.can_auto_apply)
        self.assertFalse(Confidence.CANDIDATE.can_auto_apply)
        self.assertFalse(Confidence.UNKNOWN.can_auto_apply)

    def test_priority_is_judged_per_question(self) -> None:
        book = EvidenceBook()
        book.add(Evidence(source="gold", confidence=Confidence.VERIFIED,
                          evidence_type="groove", detail="gold"))
        book.add(Evidence(source="factory_style", confidence=Confidence.STRONG_EVIDENCE,
                          evidence_type="groove", detail="factory"))
        # for groove the factory style wins even though gold claims VERIFIED
        self.assertEqual(book.best_for("groove").source, "factory_style")
        # for EFX only demo/manual count
        self.assertIsNone(book.best_for("efx"))

    def test_duplicate_evidence_collapses(self) -> None:
        book = EvidenceBook()
        item = Evidence(source="gold", confidence=Confidence.STRONG_EVIDENCE,
                        evidence_type="velocity", detail="isti")
        book.add(item)
        book.add(item)
        self.assertEqual(len(book), 1)

    def test_gaps_are_reported(self) -> None:
        book = EvidenceBook()
        gaps = book.gaps()
        self.assertIn("groove", gaps)
        self.assertIn("rx_mapping", gaps)


class AnalysisTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_roles_and_identity(self) -> None:
        path = make_midi(self.tmp / "bass.mid", program=33, channel=1, cc32=5)
        facts = analyze(path)
        self.assertEqual(len(facts.tracks), 1)
        track = facts.tracks[0]
        self.assertEqual(track.role, "bass")
        self.assertEqual(track.sound_name, "Finger Bass RX")
        self.assertTrue(track.in_factory_table)
        self.assertEqual(track.instrument_id, "121.5.33")

    def test_key_range_conflict_is_reported_not_fixed(self) -> None:
        # Finger Bass RX profile range is E1(28)-C4(60); C1(24) is below it
        path = make_midi(self.tmp / "low.mid", program=33, channel=1, cc32=5,
                         notes=((24, 90, 0, 240), (45, 90, 0, 240)))
        facts = analyze(path)
        conflicts = facts.conflicts
        self.assertEqual(len(conflicts), 1)
        self.assertEqual(conflicts[0].direction, "below")
        self.assertEqual(conflicts[0].limit_low, 28)
        # the note is still exactly where it was
        self.assertIn(24, [n.pitch for n in facts.notes])

    def test_no_profile_means_no_false_conflict(self) -> None:
        path = make_midi(self.tmp / "plain.mid", program=112, channel=3,
                         notes=((10, 90, 0, 240),))
        facts = analyze(path)
        self.assertEqual(facts.conflicts, [])

    def test_drums_detected_and_not_key_checked(self) -> None:
        path = make_midi(self.tmp / "drums.mid", program=0, drum=True)
        facts = analyze(path)
        drum_track = next(t for t in facts.tracks if t.role == "drums")
        self.assertTrue(drum_track.is_drum)
        self.assertEqual(drum_track.conflicts, [])


class ImmutabilityTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_user_velocity_rule_keeps_tick_and_pitch(self) -> None:
        path = make_midi(self.tmp / "song.mid", program=0,
                         notes=((60, 100, 0, 240), (64, 90, 0, 240),
                                (67, 80, 0, 240)))
        result = optimize(path, workspace=self.tmp / "ws", mode="apply",
                          rules=["velocity=-15"],
                          factory_index=self.tmp / "none.json",
                          gold_index=self.tmp / "none.json",
                          demo_index=self.tmp / "none.json")
        self.assertTrue(result.output_path)
        before = fingerprint(MidiFile(str(path)))
        after = fingerprint(MidiFile(str(result.output_path)))
        self.assertEqual([k[2] for k in before.keys], [k[2] for k in after.keys])
        self.assertEqual([k[3] for k in before.keys], [k[3] for k in after.keys])
        self.assertTrue(result.validation.ok)

        velocities = [m.velocity for tr in MidiFile(str(result.output_path)).tracks
                      for m in tr if m.type == "note_on" and m.velocity > 0]
        self.assertEqual(sorted(velocities), [65, 75, 85])

    def test_transpose_is_refused(self) -> None:
        path = make_midi(self.tmp / "song.mid")
        facts = analyze(path)
        builder = PlanBuilder(facts, mode="apply")
        builder.apply_user_rules(["transpose=3"])
        self.assertTrue(builder.plan.refusals)
        refusal = builder.plan.refusals[0]
        self.assertEqual(refusal.status, "REFUSED")
        self.assertIn("NOTE_IMMUTABILITY", refusal.refusal)
        self.assertEqual(builder.plan.applied, [])

    def test_every_forbidden_operation_is_refused(self) -> None:
        path = make_midi(self.tmp / "song.mid")
        facts = analyze(path)
        for name in FORBIDDEN_OPERATIONS:
            builder = PlanBuilder(facts, mode="apply")
            builder.apply_user_rules([f"{name}=1"])
            self.assertTrue(builder.plan.refusals,
                            f"{name} nije odbijen")
            self.assertEqual(builder.plan.applied, [])

    def test_waiver_marks_the_exception(self) -> None:
        path = make_midi(self.tmp / "song.mid")
        facts = analyze(path)
        builder = PlanBuilder(facts, mode="apply", waive_immutability=True)
        builder.apply_user_rules(["transpose=2"])
        self.assertFalse(builder.plan.refusals)
        applied = builder.plan.applied
        self.assertTrue(applied)
        self.assertIn("WAIVED", applied[0].note)

    def test_original_file_never_changes(self) -> None:
        path = make_midi(self.tmp / "song.mid",
                         notes=((60, 100, 0, 240),))
        digest = sha256_file(path)
        size = path.stat().st_size
        optimize(path, workspace=self.tmp / "ws", mode="apply",
                 rules=["velocity=-20", "cc7=90"],
                 factory_index=self.tmp / "none.json",
                 gold_index=self.tmp / "none.json",
                 demo_index=self.tmp / "none.json")
        self.assertEqual(sha256_file(path), digest)
        self.assertEqual(path.stat().st_size, size)


class ValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_identical_files_pass(self) -> None:
        path = make_midi(self.tmp / "a.mid")
        result = compare(fingerprint(MidiFile(str(path))),
                         fingerprint(MidiFile(str(path))))
        self.assertEqual(result.status, VALIDATION_OK)

    def test_moved_note_fails(self) -> None:
        original = make_midi(self.tmp / "a.mid", notes=((60, 90, 0, 240),))
        tampered = make_midi(self.tmp / "b.mid", notes=((60, 90, 96, 240),))
        result = compare(fingerprint(MidiFile(str(original))),
                         fingerprint(MidiFile(str(tampered))))
        self.assertEqual(result.status, VALIDATION_FAILED)
        self.assertFalse(result.ok)

    def test_transposed_note_fails(self) -> None:
        original = make_midi(self.tmp / "a.mid", notes=((60, 90, 0, 240),))
        tampered = make_midi(self.tmp / "b.mid", notes=((62, 90, 0, 240),))
        result = compare(fingerprint(MidiFile(str(original))),
                         fingerprint(MidiFile(str(tampered))))
        self.assertEqual(result.status, VALIDATION_FAILED)

    def test_velocity_change_alone_still_valid(self) -> None:
        original = make_midi(self.tmp / "a.mid", notes=((60, 90, 0, 240),))
        changed = make_midi(self.tmp / "b.mid", notes=((60, 40, 0, 240),))
        result = compare(fingerprint(MidiFile(str(original))),
                         fingerprint(MidiFile(str(changed))))
        self.assertEqual(result.status, VALIDATION_OK)

    def test_additions_only_allowed_with_permission(self) -> None:
        original = make_midi(self.tmp / "a.mid", notes=((60, 90, 0, 240),))
        added = make_midi(self.tmp / "b.mid",
                          notes=((60, 90, 0, 240), (62, 40, 0, 240)))
        strict = compare(fingerprint(MidiFile(str(original))),
                         fingerprint(MidiFile(str(added))))
        self.assertEqual(strict.status, VALIDATION_FAILED)
        allowed = compare(fingerprint(MidiFile(str(original))),
                          fingerprint(MidiFile(str(added))),
                          additions_allowed=True)
        self.assertEqual(allowed.status, VALIDATION_OK)
        self.assertEqual(len(allowed.added), 1)


class RemapTests(unittest.TestCase):
    def test_curated_gm_target_is_used(self) -> None:
        # bank 16 is junk from a GM file; GM 24 -> 'Spanish Guitar' shares the
        # family name, so the curated table may be applied automatically
        target, confidence, _ = pa800_target(121, 16, 24)
        self.assertEqual(target, GM_TO_PA800_REMAP[24])
        self.assertTrue(confidence.can_auto_apply)

    def test_sound_already_in_factory_table_is_left_alone(self) -> None:
        target, confidence, detail = pa800_target(121, 0, 24)
        self.assertEqual(target, (121, 0, 24))
        self.assertEqual(confidence, Confidence.DOCUMENTED)
        self.assertIn("već fabricki", detail)

    def test_unverifiable_target_is_only_a_candidate(self) -> None:
        # the curated table maps GM 25 (Steel Guitar) to 'Mandolin' -- the
        # family names do not agree, so this may not be applied automatically
        target, confidence, detail = pa800_target(121, 16, 25)
        self.assertEqual(confidence, Confidence.CANDIDATE)
        self.assertFalse(confidence.can_auto_apply)
        self.assertIn("ne poklapa", detail)

    def test_factory_sound_is_left_alone(self) -> None:
        _, confidence, detail = pa800_target(121, 5, 33)
        self.assertIn("već fabricki", detail)

    def test_rx_variant_is_found_for_bass(self) -> None:
        variant = rx_variant_for(121, 0, 33)
        self.assertIsNotNone(variant)
        key, name = variant
        self.assertIn("RX", name)
        self.assertNotEqual(key, (121, 0, 33))

    def test_rx_evidence_levels(self) -> None:
        demo = DemoIndex(source="test", rx_presets={"Trumpet DNC": ["a.mid"]})
        verified = rx_evidence_for("Trumpet DNC", demo, None)
        self.assertEqual(verified.confidence, Confidence.VERIFIED)
        documented = rx_evidence_for("Finger Bass RX", demo, None)
        self.assertEqual(documented.confidence, Confidence.DOCUMENTED)
        candidate = rx_evidence_for("Some Random RX", demo, None)
        self.assertEqual(candidate.confidence, Confidence.CANDIDATE)
        self.assertFalse(candidate.can_auto_apply)
        unknown = rx_evidence_for("Grand Piano", demo, None)
        self.assertEqual(unknown.confidence, Confidence.UNKNOWN)


class FactoryIndexTests(unittest.TestCase):
    def test_bundled_index_has_real_factory_styles(self) -> None:
        index = FactoryIndex.load()
        self.assertGreater(len(index.styles), 300,
                           "ugradjeni factory indeks je prazan")
        names = index.style_names()
        self.assertTrue(any("Beat" in n or "Ballad" in n for n in names))

    def test_style_search_is_deterministic(self) -> None:
        index = FactoryIndex.load()
        hits = index.find_styles("gipsy")
        self.assertTrue(hits)
        self.assertTrue(all("gipsy" in h.name.casefold() for h in hits))
        self.assertEqual([h.name for h in hits], [h.name for h in index.find_styles("gipsy")])

    def test_every_style_carries_evidence_fields(self) -> None:
        index = FactoryIndex.load()
        for style in index.styles[:50]:
            self.assertTrue(style.bank)
            self.assertTrue(style.name)
            self.assertTrue(style.sha256 or style.size)


class GoldTests(unittest.TestCase):
    def test_few_samples_cannot_auto_apply(self) -> None:
        one = GoldProfile(role="bass", samples=1, velocity_median=90,
                          velocity_p10=80, velocity_p90=100, gate_ratio=0.5,
                          notes_per_beat=1.0)
        self.assertEqual(one.confidence, Confidence.CANDIDATE)
        self.assertFalse(one.confidence.can_auto_apply)
        many = GoldProfile(role="bass", samples=5, velocity_median=90,
                           velocity_p10=80, velocity_p90=100, gate_ratio=0.5,
                           notes_per_beat=1.0)
        self.assertEqual(many.confidence, Confidence.STRONG_EVIDENCE)

    def test_gold_index_measures_real_files(self) -> None:
        with TemporaryDirectory() as tmp:
            work = Path(tmp)
            make_midi(work / "one.mid", program=33, channel=1, cc32=5,
                      notes=((40, 100, 0, 240), (43, 80, 0, 240)))
            make_midi(work / "two.mid", program=33, channel=1, cc32=5,
                      notes=((40, 96, 0, 240),))
            index = build_gold_index(work)
        self.assertIn("bass", index.profiles)
        self.assertEqual(index.profiles["bass"].samples, 2)

    def test_velocity_plan_is_clamped(self) -> None:
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            path = make_midi(tmp_path / "loud.mid", program=0,
                             notes=((60, 120, 0, 240),))
            facts = analyze(path)
            builder = PlanBuilder(facts, mode="apply")
            profile = GoldProfile(role="piano", samples=9, velocity_median=10,
                                  velocity_p10=5, velocity_p90=20,
                                  gate_ratio=0.5, notes_per_beat=1.0)
            builder.velocity_operations({"piano": profile}, "test")
            op = next(o for o in builder.plan.operations
                      if o.operation == "velocity_offset")
            # 10 - 120 would be -110; the policy caps the move at -20
            self.assertEqual(int(op.new_value), 100)
            self.assertEqual(op.confidence, Confidence.STRONG_EVIDENCE.value)


class PipelineTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_propose_mode_writes_no_midi(self) -> None:
        path = make_midi(self.tmp / "song.mid")
        result = optimize(path, workspace=self.tmp / "ws", mode="propose",
                          rules=["velocity=-10"])
        self.assertIsNone(result.output_path)
        self.assertTrue(Path(result.report_dir, "report.json").is_file())

    def test_report_carries_hashes_and_limits(self) -> None:
        path = make_midi(self.tmp / "song.mid", notes=((60, 100, 0, 240),))
        result = optimize(path, workspace=self.tmp / "ws", mode="apply",
                          rules=["velocity=-10"])
        payload = json.loads(Path(result.report_dir, "report.json")
                             .read_text(encoding="utf-8"))
        self.assertEqual(payload["source"]["sha256"], sha256_file(path))
        self.assertEqual(payload["output"]["sha256"], result.output_sha256)
        self.assertEqual(payload["validation"]["status"], VALIDATION_OK)
        self.assertTrue(payload["limits"])
        self.assertEqual(payload["plan"]["applied_count"] >= 1, True)

    def test_key_range_conflict_survives_into_the_report(self) -> None:
        path = make_midi(self.tmp / "low.mid", program=33, channel=1, cc32=5,
                         notes=((24, 90, 0, 240),))
        result = optimize(path, workspace=self.tmp / "ws", mode="apply",
                          factory_index=self.tmp / "none.json",
                          gold_index=self.tmp / "none.json",
                          demo_index=self.tmp / "none.json")
        payload = json.loads(Path(result.report_dir, "report.json")
                             .read_text(encoding="utf-8"))
        conflicts = payload["source"]["conflicts"]
        self.assertEqual(len(conflicts), 1)
        self.assertEqual(conflicts[0]["direction"], "below")
        # a conflict produces a report row, not a "fix"
        operations = payload["plan"]["operations"]
        report_rows = [op for op in operations
                       if op["operation"] == "key_range_report"]
        self.assertTrue(report_rows)
        self.assertEqual(report_rows[0]["status"], "NOT_NEEDED")

    def test_unknown_evidence_changes_nothing(self) -> None:
        path = make_midi(self.tmp / "weird.mid", program=127, channel=5,
                         notes=((60, 90, 0, 240),), cc00=99, cc32=99)
        result = optimize(path, workspace=self.tmp / "ws", mode="apply",
                          factory_index=self.tmp / "none.json",
                          gold_index=self.tmp / "none.json",
                          demo_index=self.tmp / "none.json")
        self.assertIsNone(result.output_path)
        self.assertIn("nije napisan", " ".join(result.notes))


class TargetKeyRangeTests(unittest.TestCase):
    """Key Range stays a hard limit on the sound the notes end up playing."""

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_conflict_is_reported_after_remap(self) -> None:
        # junk GM bank 121.16.33 -> 'Finger Bass RX' whose measured range is
        # E1(28)-C4(60); the C1(24) note is below it and must be reported only
        path = make_midi(self.tmp / "junk.mid", program=33, channel=1, cc32=16,
                         notes=((24, 95, 0, 240), (40, 95, 0, 240)))
        result = optimize(path, workspace=self.tmp / "ws", mode="apply",
                          factory_index=self.tmp / "none.json",
                          gold_index=self.tmp / "none.json",
                          demo_index=self.tmp / "none.json")
        payload = json.loads(Path(result.report_dir, "report.json")
                             .read_text(encoding="utf-8"))
        after_remap = [c for c in payload["plan"]["conflicts"]
                       if c.get("after_remap")]
        self.assertEqual(len(after_remap), 1)
        self.assertEqual(after_remap[0]["note_name"], "C1")
        self.assertEqual(after_remap[0]["limit_low"], 28)

        if result.output_path:
            after = fingerprint(MidiFile(str(result.output_path)))
            pitches = sorted(k[3] for k in after.keys)
            self.assertIn(24, pitches, "konfliktna nota je pomjerena!")
            self.assertTrue(result.validation.ok)


class BatchTests(unittest.TestCase):
    def test_batch_summarises_a_corpus(self) -> None:
        from pa800_midi.batch import run_batch, write_batch_reports

        with TemporaryDirectory() as tmp:
            work = Path(tmp)
            corpus = work / "corpus"
            corpus.mkdir()
            make_midi(corpus / "a.mid", notes=((60, 100, 0, 240),))
            make_midi(corpus / "b.mid", program=33, channel=1, cc32=5,
                      notes=((45, 90, 0, 240),))
            summary = run_batch(corpus, work / "ws", mode="apply",
                                factory_index=work / "none.json",
                                gold_index=work / "none.json",
                                demo_index=work / "none.json")
            self.assertEqual(len(summary.rows), 2)
            self.assertEqual(summary.failed, [])
            paths = write_batch_reports(summary, work / "out")
            payload = json.loads(Path(paths["json"]).read_text(encoding="utf-8"))
            self.assertEqual(payload["file_count"], 2)
            self.assertIn("a.mid", Path(paths["csv"]).read_text(encoding="utf-8"))


@unittest.skipUnless(DEMO_DIR.is_dir(), "demo MIDI materijal nije prisutan")
class RealFileTests(unittest.TestCase):
    """The rules must also hold on real material, not only on test fixtures."""

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_gipsy_midi_is_analysed_and_validated(self) -> None:
        path = DEMO_DIR / "GIPSY.MID"
        if not path.is_file():
            self.skipTest("GIPSY.MID nije prisutan")
        result = optimize(path, workspace=self.tmp / "ws", mode="apply",
                          factory_index=self.tmp / "none.json",
                          gold_index=self.tmp / "none.json",
                          demo_index=self.tmp / "none.json")
        self.assertGreater(result.facts.total_notes, 1000)
        if result.output_path:
            before = fingerprint(MidiFile(str(path)))
            after = fingerprint(MidiFile(str(result.output_path)))
            self.assertEqual([k[2:] for k in before.keys],
                             [k[2:] for k in after.keys])
            self.assertTrue(result.validation.ok)
        self.assertEqual(sha256_file(path),
                         result.facts.sha256)

    def test_factory_style_match_on_real_name(self) -> None:
        from pa800_midi.index import FactoryIndex
        from pa800_midi.pipeline import match_factory_styles

        index = FactoryIndex.load()
        if not index.styles:
            self.skipTest("factory indeks nije izgradjen")
        facts = analyze(DEMO_DIR / "GIPSY.MID")
        matches = match_factory_styles(facts, index, "gipsy")
        self.assertTrue(matches, "nijedan fabricki stil se ne poklapa s 'gipsy'")
        self.assertIn("gipsy", matches[0].name.casefold())


if __name__ == "__main__":
    unittest.main()




def parse(name):
    from pa800_midi.keyrange import parse_note
    return parse_note(name)

# --------------------------------------------------------------------------
# Key Range -- pravilo po tipu instrumenta (v2.4.0)
# --------------------------------------------------------------------------

def parse(name):
    from pa800_midi.keyrange import parse_note
    return parse_note(name)


def test_parse_note_and_range():
    from pa800_midi.keyrange import parse_note, parse_range

    assert parse_note("C4") == 60
    assert parse_note("A0") == 21
    assert parse_note("E1") == 28
    assert parse_note("A#2") == 46
    assert parse_note("Bb2") == 46, "b se mora citati kao snizilica"
    assert parse_note("C-1") == 0
    assert parse_note("G9") == 127
    assert parse_range("E1-G4") == (28, 67)
    try:
        parse_range("nije-opseg")
    except ValueError:
        pass
    else:
        raise AssertionError("losa oznaka mora pasti")


def test_type_rules_cover_known_types():
    from pa800_midi.keyrange import (KEY_RANGE_RULES, NO_CLAIM_TYPES,
                                     KeyRangeTable)

    table = KeyRangeTable()
    for name in ("piano", "bass", "guitar", "rhythm_guitar", "strings", "sax",
                 "brass", "trumpet", "accordion", "ethnic", "organ", "flute"):
        rule = table.resolve(name)
        assert rule is not None and rule.low < rule.high, name
        assert rule.source == "type_rule"
        assert rule.low >= KEY_RANGE_RULES[name][0]
    for name in NO_CLAIM_TYPES:
        assert table.resolve(name) is None, f"{name} ne smije tvrditi opseg"


def test_measured_profile_outranks_type_rule():
    from pa800_midi.data.instrument_profiles import get_instrument_profile
    from pa800_midi.keyrange import KeyRangeTable

    table = KeyRangeTable()
    profile = get_instrument_profile(121, 5, 33)          # Finger Bass RX
    assert profile is not None, "profil bas-gitare mora postojati"

    measured = table.resolve("bass", "bass", profile)
    assert measured.source == "measured"
    assert (measured.low, measured.high) == (profile.playable_range_low,
                                            profile.playable_range_high)

    typed = table.resolve("bass", "bass")
    assert typed.source == "type_rule"

    # bubnjevi ni sa profilom ne tvrde opseg
    assert table.resolve("drums", "drums") is None


def test_user_override_outranks_everything():
    from pa800_midi.keyrange import KeyRangeTable

    table = KeyRangeTable()
    table.add_overrides("guitar=D2-E6,bass=E1-C4")
    g = table.resolve("guitar")
    assert g.source == "user_override"
    assert (g.low, g.high) == (parse("D2"), parse("E6"))
    b = table.resolve("bass")
    assert (b.low, b.high) == (parse("E1"), parse("C4"))
    # ostali tipovi i dalje koriste pravilo po tipu
    assert table.resolve("piano").source == "type_rule"


def test_conflict_reported_but_note_not_moved(tmp_path):
    """C1 u bas traci je ispod opsega -- mora se prijaviti, nota ostaje ista."""
    import mido

    from pa800_midi.analysis import analyze
    from pa800_midi.keyrange import KeyRangeTable

    path = tmp_path / "bas.mid"
    mid = mido.MidiFile(ticks_per_beat=480)
    track = mido.MidiTrack()
    mid.tracks.append(track)
    track.append(mido.MetaMessage("track_name", name="Bass", time=0))
    track.append(mido.Message("program_change", channel=1, program=33, time=0))
    track.append(mido.Message("note_on", channel=1, note=parse("E1"),
                              velocity=80, time=0))
    track.append(mido.Message("note_off", channel=1, note=parse("E1"),
                              velocity=0, time=480))
    track.append(mido.Message("note_on", channel=1, note=parse("C1"),
                              velocity=80, time=0))
    track.append(mido.Message("note_off", channel=1, note=parse("C1"),
                              velocity=0, time=480))
    mid.save(str(path))

    facts = analyze(path, key_ranges=KeyRangeTable())
    conflicts = list(facts.conflicts)
    assert conflicts, "C1 mora pasti ispod opsega bass-gitare"
    assert {c.note for c in conflicts} == {parse("C1")}
    assert conflicts[0].direction == "below"
    assert conflicts[0].limit_low == 28        # E1, pravilo po tipu
    assert conflicts[0].source == "type_rule"
    assert "KEY_RANGE_CONFLICT" in conflicts[0].describe()

    # note-on tikovi i visine ostaju netaknuti
    assert {(n.pitch, n.tick) for n in facts.notes} == {(parse("E1"), 0),
                                                        (parse("C1"), 480)}


def test_key_range_table_json_roundtrip(tmp_path):
    from pa800_midi.keyrange import KeyRangeTable

    table = KeyRangeTable()
    table.add_overrides("guitar=D2-E6")
    path = table.save(tmp_path / "kr.json")
    again = KeyRangeTable.from_file(path)
    rule = again.resolve("guitar")
    assert rule.source == "user_override"
    assert (rule.low, rule.high) == (parse("D2"), parse("E6"))
    assert again.resolve("piano").source == "type_rule"
    assert again.resolve("drums") is None


def test_gold_widens_rule_only_with_strong_evidence():
    """GOLD smije samo PROSIRITI pravilo, i to samo sa 98% pokrivenosti."""
    from pa800_midi.index import GoldIndex, GoldTypeRange
    from pa800_midi.keyrange import KeyRangeTable

    table = KeyRangeTable()
    base = table.resolve("bass")
    assert (base.low, base.high) == (28, 67)

    # dokaz: 11% nota je na D1 (26), ostatak u E1-G4
    hist = {str(26): 500}
    for pitch in range(28, 68):
        hist[str(pitch)] = 100
    gold = GoldIndex()
    gold.types["bass"] = GoldTypeRange(
        instrument_type="bass", samples=58, files=58, notes=4500,
        pitch_low=26, pitch_high=67, pitch_hist=hist,
    )
    changed = table.apply_gold(gold)
    assert changed and "bass" in changed[0]
    rule = table.resolve("bass")
    assert rule.low == 26, "D1 je dokazan u 11% nota -> granica se siri"
    assert rule.high == 67, "visoka granica se ne dira bez dokaza"
    assert "GOLD" in rule.explanation
    assert table.gold_note.get("bass")

    # jedna zalutala nota (0.7%) NE pomjera granicu
    t2 = KeyRangeTable()
    stray = {str(23): 30}
    for pitch in range(28, 68):
        stray[str(pitch)] = 100
    gold.types["bass"].pitch_hist = stray
    assert t2.apply_gold(gold) == []
    assert t2.resolve("bass").low == 28

    # premalo pjesama -> nema dokaza
    t3 = KeyRangeTable()
    gold.types["bass"].files = 2
    gold.types["bass"].pitch_hist = hist
    assert t3.apply_gold(gold) == []

    # tvoje pravilo je jace od GOLD-a
    t4 = KeyRangeTable()
    t4.add_overrides("bass=E1-G4")
    t4.apply_gold(gold)
    assert t4.resolve("bass").source == "user_override"
    assert t4.resolve("bass").low == 28

    # tip bez dokaza ostaje netaknut
    t5 = KeyRangeTable()
    t5.apply_gold(GoldIndex())
    assert t5.resolve("bass").low == 28
    assert t5.resolve("drums") is None
