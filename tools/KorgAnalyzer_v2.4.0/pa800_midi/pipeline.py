"""The pipeline (master logic §18):

    INPUT MIDI
      -> ANALYZE MIDI
      -> IDENTIFY TRACK / ROLE
      -> IDENTIFY INSTRUMENT
      -> CHECK KEY RANGE                (HARD LIMIT)
      -> FACTORY STYLE MATCH
      -> FACTORY SOUND / OSC MATCH
      -> CHECK RX EVIDENCE
      -> CHECK GOLD EVIDENCE
      -> CHECK MANUAL
      -> DEMO (EFX / ARTICULATION only)
      -> SOLO / DRUM pikanterije
      -> APPLY USER RULE
      -> CREATE TRANSFORMATION PLAN
      -> EXECUTE
      -> VALIDATE TICK/PITCH IMMUTABILITY
      -> EXPORT
      -> REPORT
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from pa800_midi._mido import MidiFile
from pa800_midi.analysis import MidiFacts, analyze, sha256_file
from pa800_midi.evidence import (
    SOURCE_DEMO,
    SOURCE_FACTORY_STYLE,
    SOURCE_GOLD,
    SOURCE_MANUAL,
    SOURCE_RX,
    Confidence,
    Evidence,
    EvidenceBook,
)
from pa800_midi.execute import apply_plan
from pa800_midi.index import (
    DemoIndex,
    FactoryIndex,
    GoldIndex,
    factory_identity_evidence,
    factory_style_evidence,
    gold_evidence,
    manual_evidence,
    rx_evidence_for,
)
from pa800_midi.plan import PlanBuilder
from pa800_midi.remap import pa800_target, rx_variant_for
from pa800_midi.report import OptimizeResult, write_reports
from pa800_midi.validate import compare, fingerprint

MANUALS_FILE = (Path(__file__).resolve().parent.parent /
                "korg_binary_analyzer" / "data" / "manuals.json")

DEFAULT_GOLD_INDEX = Path(__file__).resolve().parent / "data" / "gold_index.json"


# --------------------------------------------------------------- helpers -------

def _keywords(text: str) -> List[str]:
    words = [w for w in re.split(r"[^0-9a-zA-Zčćžšđ]+", text.casefold()) if len(w) > 2]
    stop = {"the", "and", "for", "mid", "midi", "song", "final", "mix", "demo"}
    return [w for w in words if w not in stop]


def match_factory_styles(facts: MidiFacts, factory: FactoryIndex,
                         style_hint: Optional[str] = None,
                         limit: int = 3):
    """Which real factory styles describe this material?

    Deterministic word-overlap between the file name (or an explicit
    ``--style`` hint) and the factory style names, in both directions.
    """
    query = style_hint or Path(facts.path).stem
    words = _keywords(query)
    if not words:
        return []
    scored = []
    for style in factory.styles:
        haystack = style.name.casefold()
        hits = sum(1 for word in words if word in haystack)
        if style_hint:
            reverse = sum(1 for w in _keywords(style.name) if w in query.casefold())
            hits = max(hits, reverse)
        if hits:
            scored.append((hits, style))
    scored.sort(key=lambda item: (-item[0], item[1].name))
    return [style for _, style in scored[:limit]]


def _manual_reference(model: str = "Pa800") -> Optional[str]:
    if not MANUALS_FILE.is_file():
        return None
    try:
        data = json.loads(MANUALS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    product = (data.get("products") or {}).get(model)
    if not product:
        return None
    for doc in product.get("docs", []):
        if doc.get("doc") == "Owner's Manual":
            return f"{model} Owner's Manual ({doc.get('filename', '')})"
    docs = product.get("docs") or [{}]
    return f"{model} {docs[0].get('doc', 'dokumentacija')}"


# --------------------------------------------------------------- pipeline ------

def default_workspace() -> Path:
    """Where results go: next to the program, or wherever the user points."""
    override = os.environ.get("PA800_WORKSPACE")
    if override:
        return Path(override)
    return Path(__file__).resolve().parent.parent / "workspace" / "pa800_midi"


def optimize(path: str | Path,
             workspace: Optional[str | Path] = None,
             factory_dir: Optional[str | Path] = None,
             factory_index: Optional[str | Path] = None,
             gold_index: Optional[str | Path] = None,
             demo_index: Optional[str | Path] = None,
             rules: Optional[List[str]] = None,
             style_hint: Optional[str] = None,
             mode: str = "apply",
             waive_immutability: bool = False,
             allow_note_additions: bool = False,
             key_ranges=None,
             model: str = "Pa800") -> OptimizeResult:
    """Run the full pipeline for one MIDI file."""
    rules = list(rules or [])
    workspace = Path(workspace) if workspace else default_workspace()
    if key_ranges is None:
        from pa800_midi.keyrange import default_table
        key_ranges = default_table()
    source = Path(path).resolve()
    facts = analyze(source, key_ranges=key_ranges)

    factory = FactoryIndex.load(Path(factory_index) if factory_index else None)
    if factory_dir:
        from pa800_midi.index import build_factory_index
        built = build_factory_index(factory_dir, Path(workspace) / "_factory_cache",
                                    model=model)
        if built.styles:
            factory = built
    demo = DemoIndex.load(Path(demo_index) if demo_index else None)
    gold = None
    gold_path = Path(gold_index) if gold_index else DEFAULT_GOLD_INDEX
    if gold_path.is_file():
        gold = GoldIndex.load(gold_path)

    result = OptimizeResult(
        facts=facts,
        factory_label=(f"{factory.source} ({len(factory.styles)} stilova)"
                       if factory.styles else "(nema factory indeksa)"),
        gold_label=(f"{gold.source} ({len(gold.profiles)} rola)"
                    if gold else "(nema gold indeksa)"),
        demo_label=(f"{demo.source} ({len(demo.files)} fajlova)"
                    if demo.files else "(nema demo indeksa)"),
    )
    book = EvidenceBook()
    plan_builder = PlanBuilder(facts, mode=mode,
                               waive_immutability=waive_immutability,
                               allow_note_additions=allow_note_additions)

    # --- FACTORY STYLE MATCH ------------------------------------------------
    matches = match_factory_styles(facts, factory, style_hint)
    for style in matches:
        book.add(factory_style_evidence(style, "song"))
    if not matches:
        book.add(Evidence(
            source=SOURCE_FACTORY_STYLE,
            confidence=Confidence.UNKNOWN,
            evidence_type="groove",
            detail="nijedan fabricki stil se ne poklapa s imenom materijala",
        ))

    # --- per-track evidence -------------------------------------------------
    for track in facts.tracks:
        book.add(factory_identity_evidence(track.sound_name,
                                           track.in_factory_table,
                                           track.profile_name))
        variant = rx_variant_for(track.cc00, track.cc32, track.program)
        if variant:
            book.add(rx_evidence_for(variant[1], demo, factory))
        profile = gold.profile(track.role) if gold else None
        if gold is not None:
            book.add(gold_evidence(profile, track.role, gold.source))
        if track.is_drum and not track.conflicts:
            continue

    book.add(manual_evidence("Pa800 parametri i key range", _manual_reference(model)))
    for track in facts.tracks:
        demo_files = demo.rx_presets.get(track.sound_name, [])
        book.add(Evidence(
            source=SOURCE_DEMO,
            confidence=(Confidence.STRONG_EVIDENCE if demo_files
                        else Confidence.UNKNOWN),
            evidence_type="efx",
            detail=(f"demo dokaz postoji za '{track.sound_name}' -- koristi se "
                    f"samo za EFX/artikulaciju"
                    if demo_files else
                    f"nema demo dokaza za '{track.sound_name}'"),
            demo_reference=", ".join(demo_files[:2]),
        ))

    # --- plan ---------------------------------------------------------------
    plan_builder.conflict_report()
    plan_builder.identity_operations()
    plan_builder.target_key_range_report(key_ranges)
    plan_builder.rx_operations(demo)
    plan_builder.velocity_operations(gold.profiles if gold else None,
                                     gold.source if gold else "")
    plan_builder.apply_user_rules(rules)

    # --- execute ------------------------------------------------------------
    out_root = Path(workspace)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    report_dir = out_root / "reports" / f"{stamp}_{source.stem}"
    output_dir = out_root / "output"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{source.stem}_pa800.mid"

    before = None
    if plan_builder.plan.mode == "apply" and plan_builder.plan.applied:
        mid = MidiFile(str(source))
        before = fingerprint(mid)
        result.counters = apply_plan(mid, plan_builder.plan)
        mid.save(str(output_path))
        after = fingerprint(MidiFile(str(output_path)))
        result.validation = compare(before, after,
                                    additions_allowed=allow_note_additions)
        result.output_path = str(output_path)
        result.output_sha256 = sha256_file(output_path)
    else:
        result.validation = None
        result.notes.append(
            "Nije bilo nijedne operacije koja smije biti primijenjena "
            "automatski -- izlazni MIDI nije napisan."
            if plan_builder.plan.mode == "apply" else
            "Rezim 'propose': plan je napravljen, ali nista nije primijenjeno."
        )

    result.plan = plan_builder.plan
    result.evidence = book
    plan_builder.plan.output = result.output_path or ""
    plan_builder.plan.source_sha256 = facts.sha256
    plan_builder.plan.evidence_gaps = book.gaps()

    write_reports(result, report_dir)
    return result
