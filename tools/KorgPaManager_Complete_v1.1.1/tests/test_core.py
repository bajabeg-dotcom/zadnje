from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest
from mido import Message, MetaMessage, MidiFile, MidiTrack

from korg_pa_manager.core import MidiDocument, MidiDocumentError
from korg_pa_manager.markers import classify_marker


def absolute_messages(track: MidiTrack):
    tick = 0
    result = []
    for message in track:
        tick += message.time
        result.append((tick, message))
    return result


def make_fixture(path: Path) -> Path:
    midi = MidiFile(type=0, ticks_per_beat=96)
    track = MidiTrack()
    midi.tracks.append(track)
    track.append(MetaMessage("track_name", name="Style fixture", time=0))
    track.append(MetaMessage("marker", text="v1cv1", time=0))
    track.append(Message("control_change", channel=8, control=0, value=121, time=0))
    track.append(Message("control_change", channel=8, control=32, value=10, time=0))
    track.append(Message("program_change", channel=8, program=34, time=0))
    track.append(Message("control_change", channel=9, control=0, value=120, time=0))
    track.append(Message("control_change", channel=9, control=32, value=0, time=0))
    track.append(Message("program_change", channel=9, program=4, time=0))
    track.append(Message("note_on", channel=9, note=36, velocity=100, time=24))
    track.append(Message("note_off", channel=9, note=36, velocity=0, time=24))
    track.append(MetaMessage("marker", text="f1cv1", time=48))
    track.append(Message("note_on", channel=9, note=38, velocity=90, time=0))
    track.append(Message("note_on", channel=9, note=38, velocity=0, time=24))
    track.append(Message("note_on", channel=10, note=60, velocity=70, time=0))
    track.append(Message("note_off", channel=10, note=60, velocity=0, time=12))
    track.append(MetaMessage("end_of_track", time=0))
    midi.save(path)
    return path


def test_marker_grammar_and_exact_ticks(tmp_path: Path):
    source = make_fixture(tmp_path / "source.mid")
    doc = MidiDocument()
    doc.load(source)
    markers = doc.markers()
    assert [(item.text, item.tick, item.family) for item in markers] == [
        ("v1cv1", 0, "Variation"),
        ("f1cv1", 96, "Fill"),
    ]
    assert classify_marker("i2cv3") == ("Intro", 2, 3, True)
    assert classify_marker("e1cv1") == ("Ending", 1, 1, True)
    assert classify_marker("Variation 4 CV2") == ("Variation", 4, 2, True)


def test_style_channels_program_and_bank(tmp_path: Path):
    source = make_fixture(tmp_path / "source.mid")
    doc = MidiDocument()
    doc.load(source)
    summaries = {item.midi_channel: item for item in doc.channel_summaries()}
    assert summaries[9].role == "Bass"
    assert summaries[10].role == "Drums"
    assert summaries[11].role == "Percussion"
    assignments = doc.program_assignments()
    bass = next(item for item in assignments if item.midi_channel == 9)
    drums = next(item for item in assignments if item.midi_channel == 10)
    assert (bass.bank_msb, bass.bank_lsb, bass.program) == (121, 10, 34)
    assert (drums.bank_msb, drums.bank_lsb, drums.program) == (120, 0, 4)


def test_program_edit_is_real_and_undoable(tmp_path: Path):
    source = make_fixture(tmp_path / "source.mid")
    doc = MidiDocument()
    doc.load(source)
    item = next(
        value for value in doc.program_assignments() if value.midi_channel == 10
    )
    doc.edit_program_assignment(item.ref, program=12, bank_msb=120, bank_lsb=7)
    changed = next(
        value for value in doc.program_assignments() if value.midi_channel == 10
    )
    assert (changed.bank_msb, changed.bank_lsb, changed.program) == (120, 7, 12)
    assert doc.dirty
    doc.undo()
    restored = next(
        value for value in doc.program_assignments() if value.midi_channel == 10
    )
    assert (restored.bank_msb, restored.bank_lsb, restored.program) == (120, 0, 4)
    doc.redo()
    redone = next(
        value for value in doc.program_assignments() if value.midi_channel == 10
    )
    assert redone.program == 12


def test_note_activity_sections_remap_and_timeslice(tmp_path: Path):
    source = make_fixture(tmp_path / "source.mid")
    doc = MidiDocument()
    doc.load(source)
    activity = {(item.midi_channel, item.note): item for item in doc.note_activity()}
    assert activity[(10, 36)].sections == {"Variation 1 CV1"}
    assert activity[(10, 38)].sections == {"Fill 1 CV1"}
    assert activity[(11, 60)].hit_count == 1

    before_ticks = [
        tick for tick, msg in absolute_messages(doc.midi.tracks[0]) if not msg.is_meta
    ]
    changed = doc.remap_note(channel=9, source_note=36, target_note=40)
    assert changed == 2
    after_events = absolute_messages(doc.midi.tracks[0])
    after_ticks = [tick for tick, msg in after_events if not msg.is_meta]
    assert before_ticks == after_ticks
    note_messages = [
        msg
        for _, msg in after_events
        if not msg.is_meta and msg.type in {"note_on", "note_off"}
    ]
    assert not any(msg.channel == 9 and msg.note == 36 for msg in note_messages)
    assert sum(msg.channel == 9 and msg.note == 40 for msg in note_messages) == 2

    report = doc.timeslice_report(9)
    assert report.hit_count == 2
    assert report.unique_notes == 2
    transformed = doc.transform_timeslice_range(
        9, 38, 40, note_offset=1, tick_offset=3, velocity_percent=50
    )
    assert transformed == 4
    hits = doc.note_hits(9)
    assert {hit.note for hit in hits} == {39, 41}
    assert {hit.velocity for hit in hits} == {45, 50}


def test_marker_add_edit_delete_preserves_track_timing(tmp_path: Path):
    source = make_fixture(tmp_path / "source.mid")
    doc = MidiDocument()
    doc.load(source)
    original_length = doc.total_length_ticks()
    doc.add_marker(50, "i1cv1")
    added = next(item for item in doc.markers() if item.text == "i1cv1")
    assert added.tick == 50
    doc.edit_marker(added.ref, 70, "e1cv1")
    edited = next(item for item in doc.markers() if item.text == "e1cv1")
    assert edited.tick == 70
    doc.delete_marker(edited.ref)
    assert all(item.text != "e1cv1" for item in doc.markers())
    assert doc.total_length_ticks() == original_length


def test_channel_swap_changes_all_channel_messages(tmp_path: Path):
    source = make_fixture(tmp_path / "source.mid")
    doc = MidiDocument()
    doc.load(source)
    before_drums = next(
        item for item in doc.channel_summaries() if item.channel == 9
    ).message_count
    before_perc = next(
        item for item in doc.channel_summaries() if item.channel == 10
    ).message_count
    count = doc.remap_channels(9, 10, swap=True)
    assert count == before_drums + before_perc
    after_drums = next(
        item for item in doc.channel_summaries() if item.channel == 9
    ).message_count
    after_perc = next(
        item for item in doc.channel_summaries() if item.channel == 10
    ).message_count
    assert after_drums == before_perc
    assert after_perc == before_drums


def test_validated_save_as_preserves_source(tmp_path: Path):
    source = make_fixture(tmp_path / "source.mid")
    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    doc = MidiDocument()
    doc.load(source)
    item = next(
        value for value in doc.program_assignments() if value.midi_channel == 10
    )
    doc.edit_program_assignment(item.ref, 99, 120, 5)
    output = tmp_path / "output.mid"
    result = doc.save_as(output)
    assert result.path == output.resolve()
    assert hashlib.sha256(source.read_bytes()).hexdigest() == source_hash
    reopened = MidiFile(output, clip=False)
    programs = [
        message.program
        for track in reopened.tracks
        for message in track
        if not message.is_meta
        and message.type == "program_change"
        and message.channel == 9
    ]
    assert programs == [99]
    assert not doc.dirty
    with pytest.raises(MidiDocumentError, match="Refusing"):
        doc.save_as(source)


def test_save_fsync_reopens_temp_with_windows_compatible_writable_handle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    source = make_fixture(tmp_path / "source.mid")
    document = MidiDocument()
    document.load(source)
    opened_temp_modes: list[str] = []
    writable_temp_descriptors: set[int] = set()
    original_open = Path.open
    original_fsync = os.fsync

    def tracked_open(path: Path, mode: str = "r", *args, **kwargs):
        handle = original_open(path, mode, *args, **kwargs)
        if path.suffix == ".tmp":
            opened_temp_modes.append(mode)
            if "+" in mode or "w" in mode or "a" in mode:
                writable_temp_descriptors.add(handle.fileno())
        return handle

    def windows_compatible_fsync(descriptor: int) -> None:
        if descriptor not in writable_temp_descriptors:
            raise OSError(9, "Bad file descriptor")
        original_fsync(descriptor)

    monkeypatch.setattr(Path, "open", tracked_open)
    monkeypatch.setattr(os, "fsync", windows_compatible_fsync)
    document.save_as(tmp_path / "windows-compatible.mid")
    assert "r+b" in opened_temp_modes
    assert "rb" not in opened_temp_modes


def test_custom_labels_sidecar(tmp_path: Path):
    source = make_fixture(tmp_path / "source.mid")
    doc = MidiDocument()
    doc.load(source)
    doc.set_custom_label(9, 36, "Verified user kick")
    result = doc.save_as(tmp_path / "labeled.mid")
    assert result.labels_path is not None
    assert result.labels_path.is_file()
    reloaded = MidiDocument()
    reloaded.load(result.path)
    assert reloaded.get_custom_label(9, 36) == "Verified user kick"


def test_json_and_csv_reports(tmp_path: Path):
    source = make_fixture(tmp_path / "source.mid")
    doc = MidiDocument()
    doc.load(source)
    report = doc.build_report()
    assert report["midi"]["type"] == 0
    assert report["channel_mapping"][1] == {
        "midi_channel": 10,
        "mido_channel": 9,
        "role": "Drums",
    }
    assert "No Pa4X-to-Pa800" in report["sound_mapping_note"]
    json_path = doc.export_report_json(tmp_path / "report.json")
    csv_path = doc.export_note_csv(tmp_path / "activity.csv")
    assert json_path.stat().st_size > 100
    assert "gm_reference_only" in csv_path.read_text(encoding="utf-8")
