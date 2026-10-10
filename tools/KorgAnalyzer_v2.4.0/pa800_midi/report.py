"""Report writing -- JSON for the machine, TXT for the user (Bosnian).

Every export carries: source/output SHA-256, the transformation plan, the
validation result and the evidence report.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from pa800_midi import __version__
from pa800_midi.analysis import MidiFacts
from pa800_midi.evidence import EvidenceBook
from pa800_midi.plan import TransformationPlan
from pa800_midi.validate import ValidationResult

LINE = "-" * 72

LIMITS = [
    "Program je read-only prema originalu: original se nikada ne mijenja.",
    "Tick i pitch postojećih nota su nepromjenjivi; nema quantize/humanize/"
    "swing/transpose.",
    "Key Range je HARD LIMIT -- konflikt se prijavljuje, nota se ne pomjera.",
    "Notni sadržaj unutar .STY stilova nije dekodiran (kompresovani stream), "
    "pa se groove iz stila ne tvrdi bez izvoza u MIDI.",
    "Golden/FACTORY dokaz vrijedi samo za ono što je stvarno izmjereno.",
]


@dataclass
class OptimizeResult:
    facts: Optional[MidiFacts] = None
    plan: Optional[TransformationPlan] = None
    validation: Optional[ValidationResult] = None
    evidence: Optional[EvidenceBook] = None
    counters: Dict[str, int] = field(default_factory=dict)
    output_path: Optional[str] = None
    output_sha256: Optional[str] = None
    report_dir: Optional[str] = None
    started: str = field(default_factory=lambda:
                         datetime.now(timezone.utc).isoformat(timespec="seconds"))
    notes: List[str] = field(default_factory=list)
    factory_label: str = ""
    gold_label: str = ""
    demo_label: str = ""

    @property
    def valid(self) -> bool:
        return bool(self.validation and self.validation.ok)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "tool": "pa800_midi",
            "version": __version__,
            "started": self.started,
            "source": self.facts.to_dict() if self.facts else None,
            "output": {
                "path": self.output_path,
                "sha256": self.output_sha256,
            },
            "evidence_sources": {
                "factory": self.factory_label,
                "gold": self.gold_label,
                "demo": self.demo_label,
            },
            "plan": self.plan.to_dict() if self.plan else None,
            "validation": self.validation.to_dict() if self.validation else None,
            "counters": self.counters,
            "evidence": self.evidence.to_list() if self.evidence else [],
            "evidence_gaps": self.evidence.gaps() if self.evidence else [],
            "limits": LIMITS,
            "notes": self.notes,
        }


def write_reports(result: OptimizeResult, directory: Path) -> Dict[str, str]:
    directory.mkdir(parents=True, exist_ok=True)
    json_path = directory / "report.json"
    text_path = directory / "report.txt"
    json_path.write_text(json.dumps(result.to_dict(), indent=2,
                                    ensure_ascii=False), encoding="utf-8")
    text_path.write_text(render_text(result), encoding="utf-8")
    result.report_dir = str(directory)
    return {"json": str(json_path), "txt": str(text_path)}


def render_text(result: OptimizeResult) -> str:
    facts, plan, validation = result.facts, result.plan, result.validation
    out: List[str] = []
    add = out.append

    add(LINE)
    add("PA800 MIDI OPTIMIZER / RESTORATOR")
    add(f"verzija {__version__}   {result.started}")
    add(LINE)
    if facts:
        add("")
        add("IZVOR")
        add(f"  fajl      : {facts.path}")
        add(f"  sha256    : {facts.sha256}")
        add(f"  velicina  : {facts.size} bajtova")
        add(f"  format    : MIDI tip {facts.midi_type}, {facts.track_count} traka, "
            f"TPB {facts.ticks_per_beat}, {facts.total_notes} nota")
    if result.output_path:
        add(f"  izlaz     : {result.output_path}")
        add(f"  izlaz sha : {result.output_sha256}")

    if facts:
        add("")
        add("ANALIZA PO TRAKAMA")
        for track in facts.tracks:
            vel = (f"{track.velocity_min}-{track.velocity_median}"
                   f"-{track.velocity_max}"
                   if track.velocity_median is not None else "nema nota")
            pitch = (f"{track.pitch_min}-{track.pitch_max}"
                     if track.pitch_min is not None else "-")
            add(f"  track {track.index} kanal "
                f"{track.channels[0] + 1 if track.channels else '-'} "
                f"[{track.role}] {track.sound_name}")
            add(f"      identitet : {track.instrument_id}"
                f"{' (fabricki)' if track.in_factory_table else ' (nije u tabeli)'}")
            add(f"      note      : {track.note_count}, pitch {pitch}, "
                f"velocity {vel}")
            if track.key_low is not None:
                source = {
                    "measured": f"izmjereno za '{track.profile_name}'",
                    "type_rule": f"pravilo po tipu instrumenta ({track.key_rule_note})",
                    "user_override": "tvoje pravilo",
                }.get(track.key_rule_source or "", track.key_rule_source or "")
                add(f"      key range : {track.key_low}-{track.key_high} ({source})")
            if track.conflicts:
                add(f"      KONFLIKT  : {len(track.conflicts)} vrsta nota izvan "
                    f"opsega")

    if facts:
        measured = [t for t in facts.tracks if t.key_rule_source == "measured"]
        rule_based = [t for t in facts.tracks if t.key_rule_source == "type_rule"]
        override = [t for t in facts.tracks if t.key_rule_source == "user_override"]
        add("")
        add(f"KEY RANGE: provjereno {len(measured) + len(rule_based) + len(override)}"
            f"/{len(facts.tracks)} traka "
            f"(izmjereni profil: {len(measured)}, pravilo po tipu: {len(rule_based)}, "
            f"tvoje pravilo: {len(override)})")

    after_remap = [c for c in (plan.conflicts if plan else [])
                   if isinstance(c, dict) and c.get("after_remap")]
    if (facts and facts.conflicts) or after_remap:
        add("")
        add("KEY_RANGE_CONFLICT (HARD LIMIT -- nista se ne pomjera)")
        for conflict in (facts.conflicts if facts else []):
            add("  " + conflict.describe())
        for conflict in after_remap:
            where = "ispod" if conflict["direction"] == "below" else "iznad"
            add(f"  KEY_RANGE_CONFLICT (nakon remapa) track {conflict['track']} "
                f"kanal {conflict['channel']}: {conflict['note_name']} je {where} "
                f"opsega {conflict['limit_low']}-{conflict['limit_high']} za "
                f"'{conflict['instrument']}' ({conflict['ticks']} nota)")

    if result.evidence:
        add("")
        add("DOKAZI (EVIDENCE)")
        for item in result.evidence:
            add("  " + item.line())
        gaps = result.evidence.gaps()
        if gaps:
            add(f"  NEDOSTAJE DOKAZ za: {', '.join(gaps)} -> UNKNOWN")

    if plan:
        add("")
        add(f"PLAN TRANSFORMACIJE (rezim: {plan.mode})")
        if not plan.operations and not plan.refusals:
            add("  nema operacija -- stanje je vec u skladu s dokazima")
        for op in plan.operations:
            add("  " + op.describe())
        if plan.refusals:
            add("")
            add("ODBIJENE OPERACIJE (zabranjene pravilom)")
            for op in plan.refusals:
                add("  " + op.describe())
        if result.counters:
            add("")
            add("PRIMIJENJENO")
            for key, value in sorted(result.counters.items()):
                add(f"  {key}: {value}")

    if validation:
        add("")
        add("VALIDACIJA")
        add("  " + validation.describe())
        if not validation.ok:
            add("  IZVOZ NIJE VALIDAN -- koristi se samo kao dijagnostika")
            for before, after in validation.moved[:10]:
                add(f"    prije {before} -> poslije {after}")

    add("")
    add("OGRANICENJA (posteno receno)")
    for limit in LIMITS:
        add("  - " + limit)

    if result.notes:
        add("")
        add("NAPOMENE")
        for note in result.notes:
            add("  - " + note)

    add("")
    add(LINE)
    return "\n".join(out) + "\n"
