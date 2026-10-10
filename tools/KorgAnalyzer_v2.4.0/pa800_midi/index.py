"""Evidence indexes: FACTORY (styles/sounds) and GOLD (proven references).

FACTORY index
    Built from real Korg factory material with the existing binary decoder
    (``python -m korg_binary_analyzer decode``), which is the same codec that
    validated the user's own SET files.  One record per factory style:
    name, bank, slot, element count, track geometry, sha256, size.

GOLD index
    Built from material the user has proven to be correct (his own styles /
    MIDIs).  Per role it measures note count, velocity spread, gate ratio and
    density.  Fewer than three samples cannot support an automatic decision,
    so it is reported as CANDIDATE.

DEMO index
    Built from the vendor demo MIDIs.  Used only for EFX / articulation and
    as proof that an RX/DNC variant is a real playable preset -- never for
    groove, timing, pitch or key range.
"""
from __future__ import annotations

import json
import re
import statistics
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from pa800_midi.analysis import analyze
from pa800_midi.data.pa800_sounds import PA800_SOUNDS_FULL
from pa800_midi.evidence import (
    SOURCE_DEMO,
    SOURCE_FACTORY_SOUND,
    SOURCE_FACTORY_STYLE,
    SOURCE_GOLD,
    SOURCE_MANUAL,
    SOURCE_RX,
    Confidence,
    Evidence,
)

DATA_DIR = Path(__file__).resolve().parent / "data"
FACTORY_INDEX_FILE = DATA_DIR / "factory_index.json"
DEMO_INDEX_FILE = DATA_DIR / "demo_index.json"

STYLE_SUFFIXES = {".sty", ".set"}
RX_MARKERS = ("RX", "DNC")


@dataclass
class FactoryStyle:
    name: str
    bank: str
    slot: int
    element_count: int
    geometry: str
    sha256: str
    size: int

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class FactoryIndex:
    version: int = 1
    source: str = ""
    model: str = "Korg Pa800"
    styles: List[FactoryStyle] = field(default_factory=list)
    failed: List[Dict[str, str]] = field(default_factory=list)
    sound_count: int = 0
    drum_kit_count: int = 0

    # ---------------------------------------------------------------- lookup
    def style_names(self) -> List[str]:
        return [s.name for s in self.styles]

    def find_styles(self, query: str, limit: int = 8) -> List[FactoryStyle]:
        needle = query.casefold().strip()
        if not needle:
            return []
        words = [w for w in re.split(r"[^0-9a-zčćžšđ]+", needle) if w]
        scored: List[tuple] = []
        for style in self.styles:
            haystack = style.name.casefold()
            if needle in haystack:
                scored.append((0, len(style.name), style))
                continue
            hits = sum(1 for w in words if w in haystack)
            if hits:
                scored.append((1, -hits, style))
        scored.sort(key=lambda item: (item[0], item[1], item[2].name))
        return [item[2] for item in scored[:limit]]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "version": self.version,
            "source": self.source,
            "model": self.model,
            "style_count": len(self.styles),
            "sound_count": self.sound_count,
            "drum_kit_count": self.drum_kit_count,
            "styles": [s.to_dict() for s in self.styles],
            "failed": self.failed,
        }

    def save(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), indent=1, ensure_ascii=False),
                        encoding="utf-8")
        return path

    # ------------------------------------------------------- key range proof
    def proven_range(self, instrument_type: str, min_files: int = 3,
                     min_notes: int = 5) -> Optional[tuple]:
        """Lowest/highest pitch that GOLD really plays beyond the rule.

        Only counts pitches seen in at least ``min_files`` different files and
        ``min_notes`` notes: one stray note never widens a hard limit.
        """
        stat = self.types.get(instrument_type)
        if stat is None:
            return None
        low = high = None
        for name, (notes, files) in (stat.beyond_low or {}).items():
            if notes >= min_notes and files >= min_files:
                pitch = _note_to_number(name)
                if pitch is not None and (low is None or pitch < low):
                    low = pitch
        for name, (notes, files) in (stat.beyond_high or {}).items():
            if notes >= min_notes and files >= min_files:
                pitch = _note_to_number(name)
                if pitch is not None and (high is None or pitch > high):
                    high = pitch
        if low is None and high is None:
            return None
        return low, high

    def evidence_line(self, instrument_type: str) -> str:
        stat = self.types.get(instrument_type)
        if stat is None or not stat.notes:
            return ""
        return (f"GOLD: {stat.files} pjesama, {stat.notes} nota, "
                f"svirano {stat.to_dict()['played_range']}")

    @classmethod
    def load(cls, path: Optional[Path] = None) -> "FactoryIndex":
        target = Path(path) if path else FACTORY_INDEX_FILE
        if not target.is_file():
            return cls(source="(nema indeksa)")
        data = json.loads(target.read_text(encoding="utf-8"))
        index = cls(
            version=data.get("version", 1),
            source=data.get("source", ""),
            model=data.get("model", "Korg Pa800"),
            sound_count=data.get("sound_count", 0),
            drum_kit_count=data.get("drum_kit_count", 0),
            failed=data.get("failed", []),
        )
        for item in data.get("styles", []):
            index.styles.append(FactoryStyle(**item))
        return index


def _decode_style_file(path: Path, workdir: Path) -> Optional[Dict[str, Any]]:
    """Decode one factory bank with the existing binary analyser."""
    out = workdir / "decode" / path.stem
    cmd = [sys.executable, "-m", "korg_binary_analyzer", "decode", str(path),
           "--output", str(out)]
    proc = subprocess.run(cmd, capture_output=True, text=True,
                          cwd=str(Path(__file__).resolve().parent.parent))
    if proc.returncode != 0:
        return {"error": (proc.stderr or proc.stdout).strip()[-400:]}
    manifests = sorted(out.glob("decoded/*/manifest.json"))
    if not manifests:
        return {"error": "dekoder nije proizveo manifest"}
    return json.loads(manifests[-1].read_text(encoding="utf-8"))


def build_factory_index(source_dir: str | Path, workdir: str | Path,
                        model: str = "Korg Pa800") -> FactoryIndex:
    """Build the factory style index from a folder of factory banks."""
    source = Path(source_dir)
    work = Path(workdir)
    work.mkdir(parents=True, exist_ok=True)

    index = FactoryIndex(source=str(source), model=model)
    index.sound_count = len(PA800_SOUNDS_FULL)

    files = sorted(
        p for p in source.rglob("*")
        if p.is_file() and p.suffix.lower() in (".sty", ".set")
    )
    for path in files:
        data = _decode_style_file(path, work)
        if data is None or "error" in (data or {}):
            index.failed.append({
                "file": path.name,
                "error": (data or {}).get("error", "nepoznata greška"),
            })
            continue
        bank = path.stem
        # a factory bank manifest carries one entry per style in "slots";
        # a single-style file falls back to "styles"/"style_names"
        styles = list(data.get("slots") or data.get("styles") or [])
        if not styles:
            # a single-style file: the manifest carries one entry
            styles = [{
                "slot": 0,
                "name": (data.get("style_names") or [path.stem])[0],
                "element_count": (data.get("slots") or [{}])[0].get("element_count", 0),
                "element_track_geometries": (data.get("slots") or [{}])[0]
                    .get("element_track_geometries", {}),
                "sha256": data.get("source_sha256", ""),
                "size": data.get("source_size", 0),
            }]
        for entry in styles:
            geometry = entry.get("element_track_geometries") or {}
            index.styles.append(FactoryStyle(
                name=(entry.get("decoded_name") or entry.get("directory_name")
                      or entry.get("name") or f"{bank} #{entry.get('slot', 0)}"),
                bank=bank,
                slot=int(entry.get("slot", 0)),
                element_count=int(entry.get("element_count", 0)),
                geometry=", ".join(f"{k}x{v}" for k, v in sorted(geometry.items())),
                sha256=entry.get("sha256", ""),
                size=int(entry.get("size", 0)),
            ))
    return index


# --------------------------------------------------------------- gold -------------

@dataclass
class GoldSample:
    file: str
    role: str
    category: str = "other"
    program: int = -1
    note_count: int = 0
    pitch_low: int = 0
    pitch_high: int = 0
    velocity_median: int = 0
    velocity_p10: int = 0
    velocity_p90: int = 0
    gate_ratio: float = 0.0
    notes_per_beat: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class GoldProfile:
    role: str
    samples: int
    velocity_median: int
    velocity_p10: int
    velocity_p90: int
    gate_ratio: float
    notes_per_beat: float
    pitch_low: int = 0
    pitch_high: int = 0

    @property
    def confidence(self) -> Confidence:
        if self.samples >= 3:
            return Confidence.STRONG_EVIDENCE
        if self.samples >= 1:
            return Confidence.CANDIDATE
        return Confidence.UNKNOWN

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["confidence"] = self.confidence.value
        return data


@dataclass
class GoldTypeRange:
    """What the GOLD corpus really plays on one instrument type.

    ``beyond_low`` / ``beyond_high`` count notes that fall outside the shipped
    Key Range rule for that type: ``{pitch_name: [notes, files]}``.  A pitch
    that appears in several different files is concrete evidence, a single
    stray note is not.
    """

    instrument_type: str
    samples: int = 0
    files: int = 0
    notes: int = 0
    pitch_low: int = 127
    pitch_high: int = 0
    rule_low: int = 0
    rule_high: int = 127
    beyond_low: Dict[str, List[int]] = field(default_factory=dict)
    beyond_high: Dict[str, List[int]] = field(default_factory=dict)
    programs: Dict[str, int] = field(default_factory=dict)
    velocity_median: int = 0
    pitch_hist: Dict[str, int] = field(default_factory=dict)

    def percentile(self, low_pct: float = 1.0, high_pct: float = 99.0):
        """Raspon u kojem je (100-low_pct-high_pct)% stvarno odsviranih nota."""
        hist = {int(k): v for k, v in (self.pitch_hist or {}).items()}
        total = sum(hist.values())
        if total < 100:
            return None
        seen = 0
        low = high = None
        for pitch in sorted(hist):
            seen += hist[pitch]
            if low is None and seen >= total * low_pct / 100.0:
                low = pitch
            if seen >= total * high_pct / 100.0:
                high = pitch
                break
        if low is None or high is None:
            return None
        return low, high

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["pitch_low_name"] = _note_name(self.pitch_low)
        data["pitch_high_name"] = _note_name(self.pitch_high)
        data["rule_range"] = f"{_note_name(self.rule_low)}-{_note_name(self.rule_high)}"
        data["played_range"] = f"{_note_name(self.pitch_low)}-{_note_name(self.pitch_high)}"
        return data


def _note_name(pitch: int) -> str:
    names = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")
    return f"{names[pitch % 12]}{pitch // 12 - 1}"


@dataclass
class GoldIndex:
    version: int = 2
    source: str = ""
    profiles: Dict[str, GoldProfile] = field(default_factory=dict)
    types: Dict[str, GoldTypeRange] = field(default_factory=dict)
    files: List[str] = field(default_factory=list)
    unreadable: List[str] = field(default_factory=list)

    def profile(self, role: str) -> Optional[GoldProfile]:
        return self.profiles.get(role)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "version": self.version,
            "source": self.source,
            "file_count": len(self.files),
            "profiles": {k: v.to_dict() for k, v in sorted(self.profiles.items())},
            "types": {k: v.to_dict() for k, v in sorted(self.types.items())},
            "unreadable": self.unreadable,
        }

    def save(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), indent=1, ensure_ascii=False),
                        encoding="utf-8")
        return path

    # ------------------------------------------------------- key range proof
    def proven_range(self, instrument_type: str, min_files: int = 3,
                     min_notes: int = 5) -> Optional[tuple]:
        """Lowest/highest pitch that GOLD really plays beyond the rule.

        Only counts pitches seen in at least ``min_files`` different files and
        ``min_notes`` notes: one stray note never widens a hard limit.
        """
        stat = self.types.get(instrument_type)
        if stat is None:
            return None
        low = high = None
        for name, (notes, files) in (stat.beyond_low or {}).items():
            if notes >= min_notes and files >= min_files:
                pitch = _note_to_number(name)
                if pitch is not None and (low is None or pitch < low):
                    low = pitch
        for name, (notes, files) in (stat.beyond_high or {}).items():
            if notes >= min_notes and files >= min_files:
                pitch = _note_to_number(name)
                if pitch is not None and (high is None or pitch > high):
                    high = pitch
        if low is None and high is None:
            return None
        return low, high

    def evidence_line(self, instrument_type: str) -> str:
        stat = self.types.get(instrument_type)
        if stat is None or not stat.notes:
            return ""
        return (f"GOLD: {stat.files} pjesama, {stat.notes} nota, "
                f"svirano {stat.to_dict()['played_range']}")

    @classmethod
    def load(cls, path: Path) -> "GoldIndex":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        index = cls(version=data.get("version", 1), source=data.get("source", ""),
                    files=data.get("files", []), unreadable=data.get("unreadable", []))
        for role, item in (data.get("profiles") or {}).items():
            item = dict(item)
            item.pop("confidence", None)
            index.profiles[role] = GoldProfile(**item)
        return index


def _note_to_number(name: str) -> Optional[int]:
    from pa800_midi.keyrange import parse_note

    try:
        return parse_note(name)
    except ValueError:
        return None


def _percentile(values: List[int], fraction: float) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    position = min(len(ordered) - 1, max(0, int(round(fraction * (len(ordered) - 1)))))
    return ordered[position]


def build_gold_index(source_dir: str | Path, limit: int = 0,
                     progress=None) -> GoldIndex:
    """Measure reference statistics from proven MIDI material.

    Style files (.STY) are a Pa800-native container and are NOT a MIDI stream,
    so they are recorded as present but their note-level statistics are only
    available when the style has already been exported to MIDI.
    """
    source = Path(source_dir)
    index = GoldIndex(source=str(source))
    samples: List[GoldSample] = []
    midis = sorted(p for p in source.rglob("*")
                   if p.is_file() and p.suffix.lower() in (".mid", ".midi", ".kar"))
    styles = sorted(p for p in source.rglob("*")
                    if p.is_file() and p.suffix.lower() == ".sty")
    if limit:
        midis = midis[:limit]
    index.files = [str(p.name) for p in (midis + styles)]

    from pa800_midi.keyrange import KeyRangeTable

    typerules = KeyRangeTable()          # bez GOLD dopune: mjerimo protiv osnove
    type_files: Dict[str, set] = {}
    beyond_files: Dict[str, Dict[str, set]] = {}

    for position, path in enumerate(midis, 1):
        try:
            facts = analyze(path)
        except Exception as exc:                      # pragma: no cover - defensive
            index.unreadable.append(f"{path.name}: {exc}")
            if progress:
                progress(position, len(midis), path.name)
            continue
        if progress:
            progress(position, len(midis), path.name)
        for track in facts.tracks:
            if not track.note_count:
                continue
            notes = [n for n in facts.notes
                     if n.track == track.index and n.channel in track.channels]
            durations = [n.duration or 0 for n in notes]
            total_duration = sum(durations)
            span = max((n.tick for n in notes), default=0)
            gate = (total_duration / span) if span else 0.0
            beats = span / max(1, facts.ticks_per_beat)
            pitches = [n.pitch for n in notes]
            samples.append(GoldSample(
                file=path.name,
                role=track.role,
                category=track.category,
                program=track.program,
                note_count=track.note_count,
                pitch_low=min(pitches),
                pitch_high=max(pitches),
                velocity_median=track.velocity_median or 0,
                velocity_p10=_percentile([n.velocity for n in notes], 0.10),
                velocity_p90=_percentile([n.velocity for n in notes], 0.90),
                gate_ratio=round(gate, 4),
                notes_per_beat=round(track.note_count / beats, 3) if beats else 0.0,
            ))

            # --- dokaz za pravilo opsega po tipu instrumenta
            key = track.category or "other"
            rule = typerules.resolve(key, track.role)
            stat = index.types.get(key)
            if stat is None:
                stat = GoldTypeRange(instrument_type=key)
                index.types[key] = stat
            stat.samples += 1
            stat.notes += track.note_count
            stat.pitch_low = min(stat.pitch_low, min(pitches))
            stat.pitch_high = max(stat.pitch_high, max(pitches))
            stat.programs[str(track.program)] = stat.programs.get(str(track.program), 0) + 1
            hist = stat.pitch_hist
            for one_pitch in pitches:
                hist_key = str(one_pitch)
                hist[hist_key] = hist.get(hist_key, 0) + 1
            seen = type_files.setdefault(key, set())
            if path.name not in seen:
                seen.add(path.name)
            stat.files = len(seen)
            for note in notes:
                if rule is None:
                    continue
                name = _note_name(note.pitch)
                if note.pitch < rule.low:
                    bucket = stat.beyond_low.setdefault(name, [0, 0])
                    bucket[0] += 1
                    beyond_files.setdefault(key, {}).setdefault(name, set()).add(path.name)
                elif note.pitch > rule.high:
                    bucket = stat.beyond_high.setdefault(name, [0, 0])
                    bucket[0] += 1
                    beyond_files.setdefault(key, {}).setdefault(name, set()).add(path.name)

    grouped: Dict[str, List[GoldSample]] = {}
    for sample in samples:
        grouped.setdefault(sample.role, []).append(sample)

    for key, stat in index.types.items():
        for name, fileset in beyond_files.get(key, {}).items():
            for bucket in (stat.beyond_low, stat.beyond_high):
                if name in bucket:
                    bucket[name][1] = len(fileset)
        rule = typerules.resolve(key)
        if rule is not None:
            stat.rule_low, stat.rule_high = rule.low, rule.high
        medians = [s.velocity_median for s in grouped.get(key, [])]
        if medians:
            stat.velocity_median = int(statistics.median(medians))

    for role, items in grouped.items():
        index.profiles[role] = GoldProfile(
            role=role,
            samples=len(items),
            pitch_low=min(s.pitch_low for s in items),
            pitch_high=max(s.pitch_high for s in items),
            velocity_median=int(statistics.median(s.velocity_median for s in items)),
            velocity_p10=int(statistics.median(s.velocity_p10 for s in items)),
            velocity_p90=int(statistics.median(s.velocity_p90 for s in items)),
            gate_ratio=round(statistics.median(s.gate_ratio for s in items), 4),
            notes_per_beat=round(statistics.median(s.notes_per_beat for s in items), 3),
        )
    return index


# --------------------------------------------------------------- demo -------------

@dataclass
class DemoIndex:
    version: int = 1
    source: str = ""
    rx_presets: Dict[str, List[str]] = field(default_factory=dict)
    files: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "version": self.version,
            "source": self.source,
            "file_count": len(self.files),
            "rx_presets": self.rx_presets,
            "files": self.files,
        }

    def save(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), indent=1, ensure_ascii=False),
                        encoding="utf-8")
        return path

    # ------------------------------------------------------- key range proof
    def proven_range(self, instrument_type: str, min_files: int = 3,
                     min_notes: int = 5) -> Optional[tuple]:
        """Lowest/highest pitch that GOLD really plays beyond the rule.

        Only counts pitches seen in at least ``min_files`` different files and
        ``min_notes`` notes: one stray note never widens a hard limit.
        """
        stat = self.types.get(instrument_type)
        if stat is None:
            return None
        low = high = None
        for name, (notes, files) in (stat.beyond_low or {}).items():
            if notes >= min_notes and files >= min_files:
                pitch = _note_to_number(name)
                if pitch is not None and (low is None or pitch < low):
                    low = pitch
        for name, (notes, files) in (stat.beyond_high or {}).items():
            if notes >= min_notes and files >= min_files:
                pitch = _note_to_number(name)
                if pitch is not None and (high is None or pitch > high):
                    high = pitch
        if low is None and high is None:
            return None
        return low, high

    def evidence_line(self, instrument_type: str) -> str:
        stat = self.types.get(instrument_type)
        if stat is None or not stat.notes:
            return ""
        return (f"GOLD: {stat.files} pjesama, {stat.notes} nota, "
                f"svirano {stat.to_dict()['played_range']}")

    @classmethod
    def load(cls, path: Optional[Path] = None) -> "DemoIndex":
        target = Path(path) if path else DEMO_INDEX_FILE
        if not target.is_file():
            return cls(source="(nema indeksa)")
        data = json.loads(target.read_text(encoding="utf-8"))
        return cls(version=data.get("version", 1), source=data.get("source", ""),
                   rx_presets=data.get("rx_presets", {}),
                   files=data.get("files", []))


def build_demo_index(source_dir: str | Path) -> DemoIndex:
    source = Path(source_dir)
    index = DemoIndex(source=str(source))
    midis = sorted(p for p in source.rglob("*")
                   if p.is_file() and p.suffix.lower() in (".mid", ".midi", ".kar"))
    index.files = [p.name for p in midis]
    for path in midis:
        stem = path.stem.upper()
        for (cc00, cc32, program), name in PA800_SOUNDS_FULL.items():
            upper = name.upper()
            if not any(marker in upper for marker in RX_MARKERS):
                continue
            family = upper.replace("RX", "").replace("DNC", "").strip()
            if family and family in stem:
                index.rx_presets.setdefault(name, [])
                if path.name not in index.rx_presets[name]:
                    index.rx_presets[name].append(path.name)
    return index


def rx_evidence_for(track_sound: str, demo: DemoIndex,
                    factory: FactoryIndex) -> Evidence:
    """Evidence that an RX/DNC variant is a real, playable preset.

    VERIFIED         demo MIDI plays this exact RX preset AND the factory sound
                     table carries it.
    DOCUMENTED       factory table only (name ends in RX/DNC).
    CANDIDATE        name similarity only.
    """
    upper = track_sound.upper()
    is_rx = any(marker in upper for marker in RX_MARKERS)
    in_table = any(name.upper() == upper for name in PA800_SOUNDS_FULL.values())
    demo_files = demo.rx_presets.get(track_sound, [])

    if is_rx and in_table and demo_files:
        return Evidence(
            source=SOURCE_RX,
            confidence=Confidence.VERIFIED,
            evidence_type="rx_mapping",
            detail=(f"'{track_sound}' je RX/DNC preset u fabrickoj tabeli i svira "
                    f"u demo materijalu ({len(demo_files)} fajl)"),
            rx_reference=track_sound,
            factory_reference="PA800 factory sound table",
            demo_reference=", ".join(demo_files[:3]),
        )
    if is_rx and in_table:
        return Evidence(
            source=SOURCE_RX,
            confidence=Confidence.DOCUMENTED,
            evidence_type="rx_mapping",
            detail=f"'{track_sound}' je RX/DNC preset prisutan u fabrickoj tabeli",
            rx_reference=track_sound,
            factory_reference="PA800 factory sound table",
        )
    if is_rx:
        return Evidence(
            source=SOURCE_RX,
            confidence=Confidence.CANDIDATE,
            evidence_type="rx_mapping",
            detail=(f"'{track_sound}' liči na RX/DNC varijantu, ali nema dokaza "
                    f"u fabrickoj tabeli ni u demo materijalu"),
            rx_reference=track_sound,
        )
    return Evidence(
        source=SOURCE_RX,
        confidence=Confidence.UNKNOWN,
        evidence_type="rx_mapping",
        detail=f"nema RX/DNC dokaza za '{track_sound}'",
    )


def factory_identity_evidence(track_sound: str, in_table: bool,
                              profile_name: Optional[str]) -> Evidence:
    if in_table and profile_name:
        return Evidence(
            source=SOURCE_FACTORY_SOUND,
            confidence=Confidence.DOCUMENTED,
            evidence_type="sound_identity",
            detail=(f"'{track_sound}' postoji u fabrickoj tabeli i ima izmjeren "
                    f"profil '{profile_name}'"),
            factory_reference="PA800 factory sound table",
        )
    if in_table:
        return Evidence(
            source=SOURCE_FACTORY_SOUND,
            confidence=Confidence.DOCUMENTED,
            evidence_type="sound_identity",
            detail=f"'{track_sound}' postoji u fabrickoj tabeli",
            factory_reference="PA800 factory sound table",
        )
    return Evidence(
        source=SOURCE_FACTORY_SOUND,
        confidence=Confidence.UNKNOWN,
        evidence_type="sound_identity",
        detail=f"'{track_sound}' nije u fabrickoj tabeli",
    )


def factory_style_evidence(style: FactoryStyle, role: str) -> Evidence:
    return Evidence(
        source=SOURCE_FACTORY_STYLE,
        confidence=Confidence.STRONG_EVIDENCE,
        evidence_type="groove",
        detail=(f"fabricki stil '{style.name}' ({style.bank} #{style.slot + 1}), "
                f"{style.element_count} elemenata, geometrija {style.geometry or 'n/a'}"),
        factory_reference=f"{style.bank}.STY#{style.slot}",
    )


def gold_evidence(profile: Optional[GoldProfile], role: str,
                  source_label: str) -> Evidence:
    if profile is None:
        return Evidence(
            source=SOURCE_GOLD,
            confidence=Confidence.UNKNOWN,
            evidence_type="velocity",
            detail=f"nema gold uzoraka za rolu '{role}'",
        )
    return Evidence(
        source=SOURCE_GOLD,
        confidence=profile.confidence,
        evidence_type="velocity",
        detail=(f"gold '{role}': {profile.samples} uzoraka, medijana velocity "
                f"{profile.velocity_median} (10-90%: {profile.velocity_p10}-"
                f"{profile.velocity_p90}), gate {profile.gate_ratio}, "
                f"{profile.notes_per_beat} nota/takt"),
        gold_reference=source_label,
    )


def manual_evidence(topic: str, reference: Optional[str]) -> Evidence:
    if reference:
        return Evidence(
            source=SOURCE_MANUAL,
            confidence=Confidence.DOCUMENTED,
            evidence_type="parameter_meaning",
            detail=f"{topic} -- objašnjeno u priručniku",
            manual_reference=reference,
        )
    return Evidence(
        source=SOURCE_MANUAL,
        confidence=Confidence.UNKNOWN,
        evidence_type="parameter_meaning",
        detail=f"{topic} -- priručnik nije pronađen",
    )


def demo_evidence(track_sound: str, demo: DemoIndex) -> Evidence:
    files = demo.rx_presets.get(track_sound, [])
    if files:
        return Evidence(
            source=SOURCE_DEMO,
            confidence=Confidence.STRONG_EVIDENCE,
            evidence_type="articulation",
            detail=(f"demo snimak koristi '{track_sound}' ({len(files)} fajl) -- "
                    f"vrijedi samo za EFX i artikulaciju"),
            demo_reference=", ".join(files[:3]),
        )
    return Evidence(
        source=SOURCE_DEMO,
        confidence=Confidence.UNKNOWN,
        evidence_type="articulation",
        detail=f"nema demo dokaza za '{track_sound}'",
    )
