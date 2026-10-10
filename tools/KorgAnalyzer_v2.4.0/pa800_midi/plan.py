"""The Transformation Plan -- what will be done, why, and on which evidence.

The plan is built before anything is touched.  Every operation is one row:

    track, role, operation, old_value, new_value, reason, evidence, confidence

Hard rules encoded here:

* ``transpose``, ``quantize``, ``humanize_timing``, ``swing``, ``snap`` and any
  other tick/pitch operation are REFUSED with reason ``NOTE_IMMUTABILITY``.
  Only an explicit user rule with ``waive_immutability`` turns them into a
  recorded exception.
* An operation whose confidence is CANDIDATE is only ever proposed.
* UNKNOWN changes nothing.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

from pa800_midi import __version__
from pa800_midi.analysis import MidiFacts, TrackFacts
from pa800_midi.data.categories import clamp, note_name
from pa800_midi.evidence import Confidence, Evidence

#: Operations that never touch tick or pitch of an existing note.
SAFE_OPERATIONS = (
    "bank_select",
    "program_change",
    "velocity_offset",
    "velocity_scale",
    "cc_set",
    "rx_remap",
    "articulation_cc",
    "add_ghost_note",
)

#: Operations that would move or retune a note.  Refused by default.
FORBIDDEN_OPERATIONS = {
    "transpose": "NOTE_IMMUTABILITY (pitch)",
    "pitch_shift": "NOTE_IMMUTABILITY (pitch)",
    "quantize": "NOTE_IMMUTABILITY (tick)",
    "humanize_timing": "NOTE_IMMUTABILITY (tick)",
    "swing": "NOTE_IMMUTABILITY (tick)",
    "snap": "NOTE_IMMUTABILITY (tick)",
    "timing_shift": "NOTE_IMMUTABILITY (tick)",
    "note_relocation": "NOTE_IMMUTABILITY (tick)",
}

#: CC numbers the plan is allowed to write.
WRITABLE_CC = {
    1: "modulation",
    7: "volume",
    10: "pan",
    11: "expression",
    64: "sustain",
    71: "resonance",
    74: "cutoff",
    91: "reverb",
    93: "chorus",
}


@dataclass
class Operation:
    track: int
    channel: int
    role: str
    operation: str
    old_value: Any
    new_value: Any
    reason: str
    evidence: List[Dict[str, Any]] = field(default_factory=list)
    confidence: str = Confidence.UNKNOWN.value
    applies_to: int = 0            # how many notes/events are touched
    status: str = "PROPOSED"       # PROPOSED / APPLIED / REFUSED / NOT_NEEDED
    refusal: Optional[str] = None
    note: Optional[str] = None

    @property
    def auto(self) -> bool:
        try:
            return Confidence(self.confidence).can_auto_apply
        except ValueError:
            return False

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["auto_eligible"] = self.auto
        return data

    def describe(self) -> str:
        flag = {"APPLIED": "PRIMIJENO", "PROPOSED": "PREDLOG",
                "REFUSED": "ODBIJENO", "NOT_NEEDED": "NIJE POTREBNO"}[self.status]
        head = (f"[{flag}] track {self.track} kanal {self.channel + 1} "
                f"({self.role}) {self.operation}")
        if self.old_value is not None or self.new_value is not None:
            head += f": {self.old_value} -> {self.new_value}"
        body = f"\n    razlog: {self.reason}"\
               f"\n    dokaz: {self.confidence}"
        for item in self.evidence:
            if hasattr(item, "to_dict"):
                item = item.to_dict()
            source = item.get("source", "?")
            detail = item.get("detail", "")
            conf = item.get("confidence", "")
            body += f"\n      - [{source}] {conf}: {detail}"
        if self.note:
            body += f"\n    napomena: {self.note}"
        if self.refusal:
            body += f"\n    ODBIJENO: {self.refusal}"
        return head + body


@dataclass
class TransformationPlan:
    source: str = ""
    source_sha256: str = ""
    output: str = ""
    mode: str = "apply"            # apply / propose
    waive_immutability: bool = False
    allow_note_additions: bool = False
    operations: List[Operation] = field(default_factory=list)
    refusals: List[Operation] = field(default_factory=list)
    conflicts: List[Dict[str, Any]] = field(default_factory=list)
    evidence_gaps: List[str] = field(default_factory=list)
    user_rules: List[str] = field(default_factory=list)

    @property
    def applied(self) -> List[Operation]:
        return [op for op in self.operations if op.status == "APPLIED"]

    @property
    def proposed(self) -> List[Operation]:
        return [op for op in self.operations if op.status == "PROPOSED"]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "tool": "pa800_midi",
            "version": __version__,
            "source": self.source,
            "source_sha256": self.source_sha256,
            "output": self.output,
            "mode": self.mode,
            "waive_immutability": self.waive_immutability,
            "allow_note_additions": self.allow_note_additions,
            "operation_count": len(self.operations),
            "applied_count": len(self.applied),
            "proposed_count": len(self.proposed),
            "refused_count": len(self.refusals),
            "user_rules": self.user_rules,
            "operations": [op.to_dict() for op in self.operations],
            "refusals": [op.to_dict() for op in self.refusals],
            "conflicts": self.conflicts,
            "evidence_gaps": self.evidence_gaps,
        }


class PlanBuilder:
    """Builds the plan for one MIDI file from facts + evidence."""

    def __init__(self, facts: MidiFacts, mode: str = "apply",
                 waive_immutability: bool = False,
                 allow_note_additions: bool = False) -> None:
        self.facts = facts
        self.mode = mode
        self.waive_immutability = waive_immutability
        self.allow_note_additions = allow_note_additions
        self.plan = TransformationPlan(
            source=facts.path,
            source_sha256=facts.sha256,
            mode=mode,
            waive_immutability=waive_immutability,
            allow_note_additions=allow_note_additions,
        )

    # ------------------------------------------------------------ helpers
    def _finish(self, op: Operation) -> Operation:
        if op.status == "PROPOSED" and self.mode == "apply" and op.auto:
            op.status = "APPLIED"
        self.plan.operations.append(op)
        return op

    def refuse(self, track: TrackFacts, operation: str, reason: str,
               detail: str = "") -> Operation:
        op = Operation(
            track=track.index,
            channel=track.channels[0] if track.channels else 0,
            role=track.role,
            operation=operation,
            old_value=None,
            new_value=None,
            reason=detail or "operacija mijenja tick ili pitch postojeće note",
            evidence=[],
            confidence=Confidence.UNKNOWN.value,
            status="REFUSED",
            refusal=reason,
            note="nota ostaje netaknuta; prijavljeno kao konflikt, ne popravljeno",
        )
        self.plan.refusals.append(op)
        return op

    # ------------------------------------------------------- rule handling
    def apply_user_rules(self, rules: List[str]) -> None:
        """Deterministic user rules: ``velocity=-15``, ``cc7=100``, ..."""
        for rule in rules:
            if "=" not in rule:
                self.plan.user_rules.append(rule)
                continue
            key, _, raw = rule.partition("=")
            key = key.strip().casefold()
            self.plan.user_rules.append(rule)

            if key in FORBIDDEN_OPERATIONS:
                if not self.waive_immutability:
                    self._refuse_rule(rule, key, FORBIDDEN_OPERATIONS[key])
                    continue
                self._apply_forbidden_rule(key, raw, rule)
                continue
            if key == "velocity":
                self._rule_velocity(raw, rule)
            elif key.startswith("cc") and key[2:].isdigit():
                self._rule_cc(int(key[2:]), raw, rule)
            elif key == "rx":
                self._rule_rx(raw, rule)
            elif key == "ghost":
                self._rule_ghost(raw, rule)
            else:
                self.plan.user_rules[-1] = f"{rule} (nepoznato pravilo -- ignorisano)"

    def _refuse_rule(self, rule: str, key: str, reason: str) -> None:
        dummy = TrackFacts(index=-1, name="(korisničko pravilo)")
        op = self.refuse(dummy, key, reason,
                         detail=f"pravilo '{rule}' traži izmjenu koja je zabranjena")
        op.role = "user_rule"
        op.note = "za izvršenje je potrebno --waive-immutability (svjesno kršenje pravila)"

    def _apply_forbidden_rule(self, key: str, raw: str, rule: str) -> None:
        evidence = Evidence(
            source="user_rule",
            confidence=Confidence.VERIFIED,
            evidence_type="transformation",
            detail=f"korisnik je eksplicitno zatražio '{rule}' i svjesno odobrio "
                   f"odstupanje od NOTE_IMMUTABILITY",
        ).to_dict()
        for track in self.facts.tracks:
            if not track.note_count:
                continue
            if key in ("transpose", "pitch_shift"):
                old = (track.pitch_min, track.pitch_max)
                try:
                    delta = int(raw)
                except ValueError:
                    delta = 0
                new = (old[0] + delta, old[1] + delta)
            else:
                old, new = "tick", f"tick {raw}"
            op = Operation(
                track=track.index,
                channel=track.channels[0],
                role=track.role,
                operation=key,
                old_value=old,
                new_value=new,
                reason=f"korisničko pravilo: {rule}",
                evidence=[evidence],
                confidence=Confidence.VERIFIED.value,
                applies_to=track.note_count,
                status="APPLIED" if self.mode == "apply" else "PROPOSED",
                note="WAIVED: pravilo je svjesno prekršeno na zahtjev korisnika",
            )
            self.plan.operations.append(op)

    def _rule_velocity(self, raw: str, rule: str) -> None:
        try:
            delta = int(raw)
        except ValueError:
            self.plan.user_rules[-1] = f"{rule} (nije broj -- ignorisano)"
            return
        delta = clamp(delta, -126, 126)
        evidence = Evidence(
            source="user_rule",
            confidence=Confidence.VERIFIED,
            evidence_type="transformation",
            detail=(f"determinističko pravilo: new_velocity = "
                    f"clamp(old_velocity - {-delta:+d} -> {delta:+d}, 1, 127)"),
        )
        for track in self.facts.tracks:
            if not track.note_count or track.is_drum:
                continue
            op = Operation(
                track=track.index,
                channel=track.channels[0],
                role=track.role,
                operation="velocity_offset",
                old_value=track.velocity_median,
                new_value=clamp((track.velocity_median or 0) + delta, 1, 127),
                reason=f"korisničko pravilo: {rule}",
                evidence=[evidence],
                confidence=Confidence.VERIFIED.value,
                applies_to=track.note_count,
                status="APPLIED" if self.mode == "apply" else "PROPOSED",
                note="tick i pitch se ne diraju",
            )
            self.plan.operations.append(op)

    def _rule_cc(self, number: int, raw: str, rule: str) -> None:
        if number not in WRITABLE_CC:
            self.plan.user_rules[-1] = f"{rule} (CC {number} nije dozvoljen -- ignorisano)"
            return
        try:
            value = clamp(int(raw), 0, 127)
        except ValueError:
            self.plan.user_rules[-1] = f"{rule} (nije broj -- ignorisano)"
            return
        evidence = Evidence(
            source="user_rule",
            confidence=Confidence.VERIFIED,
            evidence_type="transformation",
            detail=f"determinističko pravilo: CC{number} ({WRITABLE_CC[number]}) = {value}",
        )
        for track in self.facts.tracks:
            if track.is_drum:
                continue
            op = Operation(
                track=track.index,
                channel=track.channels[0],
                role=track.role,
                operation="cc_set",
                old_value=track.controller_counts.get(number, 0),
                new_value=f"CC{number}={value}",
                reason=f"korisničko pravilo: {rule}",
                evidence=[evidence],
                confidence=Confidence.VERIFIED.value,
                applies_to=1,
                status="APPLIED" if self.mode == "apply" else "PROPOSED",
                note=WRITABLE_CC[number],
            )
            self.plan.operations.append(op)

    def _rule_rx(self, raw: str, rule: str) -> None:
        self.plan.user_rules[-1] = (
            f"{rule} (RX remap se ne zadaje ručno -- traži dokaz; vidi 'rx_presets')"
        )

    def _rule_ghost(self, raw: str, rule: str) -> None:
        if not self.allow_note_additions:
            dummy = TrackFacts(index=-1, name="(korisničko pravilo)")
            op = self.refuse(dummy, "add_ghost_note", "NOTE_ADDITIONS_NOT_ALLOWED",
                             detail=f"pravilo '{rule}' dodaje note")
            op.role = "user_rule"
            op.note = "za dodavanje nota je potrebno --allow-note-additions"
            return
        self.plan.user_rules[-1] = f"{rule} (nema dokazanog obrasca -- ignorisano)"

    # -------------------------------------------------------- evidence ops
    def identity_operations(self, factory_style_note: Optional[str] = None) -> None:
        from pa800_midi.remap import pa800_target

        for track in self.facts.tracks:
            if track.is_drum:
                continue
            target, confidence, detail = pa800_target(
                track.cc00, track.cc32, track.program)
            if target == (track.cc00, track.cc32, track.program):
                continue
            if not target[2] and target[0] == 121:
                continue
            cc00, cc32, program = target
            from pa800_midi.data.pa800_sounds import get_sound_name

            evidence = [
                Evidence(
                    source="factory_sound",
                    confidence=confidence,
                    evidence_type="sound_identity",
                    detail=detail,
                    factory_reference="PA800 factory sound table + GM remap tabela",
                ).to_dict()
            ]
            op = Operation(
                track=track.index,
                channel=track.channels[0],
                role=track.role,
                operation="program_change",
                old_value=f"{track.cc00}.{track.cc32}.{track.program} {track.sound_name}",
                new_value=f"{cc00}.{cc32}.{program} {get_sound_name(cc00, cc32, program)}",
                reason=detail,
                evidence=evidence,
                confidence=confidence.value,
                applies_to=1,
                status="PROPOSED",
                note="identitet zvuka; tick i pitch se ne diraju",
            )
            self._finish(op)

    def velocity_operations(self, gold_profiles, gold_label: str) -> None:
        """``gold_profiles`` maps role -> GoldProfile (or is a single profile)."""
        from pa800_midi.data.instrument_profiles import get_instrument_profile

        if gold_profiles is None:
            gold_profiles = {}
        if hasattr(gold_profiles, "role"):
            gold_profiles = {gold_profiles.role: gold_profiles}

        for track in self.facts.tracks:
            if track.is_drum or not track.note_count:
                continue
            current = track.velocity_median or 0
            if not current:
                continue

            gold_profile = gold_profiles.get(track.role)
            evidence_list: List[Dict[str, Any]] = []
            target = None
            confidence = Confidence.UNKNOWN
            reason = ""

            if gold_profile is not None:
                confidence = gold_profile.confidence
                target = gold_profile.velocity_median
                evidence_list.append(Evidence(
                    source="gold",
                    confidence=confidence,
                    evidence_type="velocity",
                    detail=(f"gold '{track.role}': {gold_profile.samples} uzoraka, "
                            f"medijana {gold_profile.velocity_median} "
                            f"(10-90%: {gold_profile.velocity_p10}-"
                            f"{gold_profile.velocity_p90})"),
                    gold_reference=gold_label,
                ).to_dict())
                reason = (f"poravnanje na izmjereni gold uzorak role '{track.role}' "
                          f"({gold_profile.samples} referenci)")
            else:
                profile = get_instrument_profile(track.cc00, track.cc32, track.program)
                if profile:
                    target = profile.velocity_base
                    confidence = Confidence.DOCUMENTED
                    evidence_list.append(Evidence(
                        source="factory_sound",
                        confidence=confidence,
                        evidence_type="velocity",
                        detail=(f"profil '{profile.name}': ciljna velocity "
                                f"{profile.velocity_base} (opseg {profile.velocity_min}-"
                                f"{profile.velocity_max}), bez slučajnosti"),
                        factory_reference="PA800 instrument profile",
                    ).to_dict())
                    reason = (f"poravnanje na izmjereni profil instrumenta "
                              f"'{profile.name}' (bez randomizacije)")

            if target is None:
                continue
            offset = int(round(target - current))
            # conservative policy: a median alignment never moves further
            # than 20 velocity steps, no matter what the reference says
            offset = clamp(offset, -20, 20)
            if abs(offset) < 3:
                op = Operation(
                    track=track.index,
                    channel=track.channels[0],
                    role=track.role,
                    operation="velocity_offset",
                    old_value=current,
                    new_value=current,
                    reason="velocity je već unutar ±2 od dokazanog cilja",
                    evidence=evidence_list,
                    confidence=confidence.value,
                    applies_to=0,
                    status="NOT_NEEDED",
                )
                self.plan.operations.append(op)
                continue

            op = Operation(
                track=track.index,
                channel=track.channels[0],
                role=track.role,
                operation="velocity_offset",
                old_value=current,
                new_value=clamp(current + offset, 1, 127),
                reason=reason + f"; pomak {offset:+d} (medijana {current} -> "
                                 f"{clamp(current + offset, 1, 127)})",
                evidence=evidence_list,
                confidence=confidence.value,
                applies_to=track.note_count,
                status="PROPOSED",
                note="velocity, gate i CC se smiju mijenjati; tick i pitch se ne diraju",
            )
            self._finish(op)

    def rx_operations(self, demo_index) -> None:
        from pa800_midi.index import rx_evidence_for
        from pa800_midi.remap import rx_variant_for

        for track in self.facts.tracks:
            if track.is_drum or not track.note_count:
                continue
            variant = rx_variant_for(track.cc00, track.cc32, track.program)
            if variant is None:
                continue
            key, name = variant
            if key == (track.cc00, track.cc32, track.program):
                continue
            evidence = rx_evidence_for(name, demo_index, None)
            if evidence.confidence is Confidence.UNKNOWN:
                continue
            op = Operation(
                track=track.index,
                channel=track.channels[0],
                role=track.role,
                operation="rx_remap",
                old_value=f"{track.cc00}.{track.cc32}.{track.program} {track.sound_name}",
                new_value=f"{key[0]}.{key[1]}.{key[2]} {name}",
                reason=evidence.detail,
                evidence=[evidence.to_dict()],
                confidence=evidence.confidence.value,
                applies_to=1,
                status="PROPOSED",
                note="RX/DNC identitet; tick i pitch se ne diraju",
            )
            self._finish(op)

    def target_key_range_report(self, key_ranges=None) -> None:
        """Key Range check against the identity the notes will actually play.

        A GM bass line that is fine on a GM sound can fall outside the range of
        the Pa800 sound it is remapped to.  That is still a HARD LIMIT: we
        report it and leave the note alone.
        """
        from pa800_midi.data.categories import get_instrument_category
        from pa800_midi.data.instrument_profiles import get_instrument_profile
        from pa800_midi.remap import pa800_target

        if key_ranges is None:
            from pa800_midi.keyrange import default_table
            key_ranges = default_table()

        for track in self.facts.tracks:
            if track.is_drum or not track.note_count:
                continue
            target, confidence, detail = pa800_target(
                track.cc00, track.cc32, track.program)
            if target == (track.cc00, track.cc32, track.program):
                continue
            profile = get_instrument_profile(*target)
            category = get_instrument_category(target[2])
            rule = key_ranges.resolve(category, track.role, profile)
            if rule is None:
                continue
            if (rule.source == "measured" and track.key_rule_source == "measured"
                    and (rule.low, rule.high) == (track.key_low, track.key_high)):
                continue          # same range already checked on the source sound

            notes = [n for n in self.facts.notes
                     if n.track == track.index and n.channel in track.channels]
            offending = [n for n in notes
                         if n.pitch < rule.low or n.pitch > rule.high]
            if not offending:
                continue

            buckets: Dict[tuple, int] = {}
            ticks: Dict[tuple, int] = {}
            for note in offending:
                direction = ("below" if note.pitch < rule.low else "above")
                key = (note.pitch, direction)
                buckets[key] = buckets.get(key, 0) + 1
                ticks.setdefault(key, note.tick)

            for (pitch, direction), count in sorted(buckets.items()):
                conflict = {
                    "track": track.index,
                    "channel": track.channels[0] + 1,
                    "note": pitch,
                    "note_name": note_name(pitch),
                    "tick": ticks[(pitch, direction)],
                    "direction": direction,
                    "limit_low": rule.low,
                    "limit_high": rule.high,
                    "instrument": (profile.name if profile
                                   and rule.source == "measured"
                                   else f"{category} (tip instrumenta)"),
                    "ticks": count,
                    "after_remap": True,
                    "source": rule.source,
                    "rule_note": rule.explanation,
                }
                self.plan.conflicts.append(conflict)
                where = "ispod" if direction == "below" else "iznad"
                op = Operation(
                    track=track.index,
                    channel=track.channels[0],
                    role=track.role,
                    operation="key_range_report",
                    old_value=conflict,
                    new_value=conflict,
                    reason=(f"KEY_RANGE_CONFLICT nakon remapa: {note_name(pitch)} je "
                            f"{where} opsega {note_name(rule.low)}-"
                            f"{note_name(rule.high)} za "
                            f"'{conflict['instrument']}' "
                            f"({'izmjereno' if rule.source == 'measured' else 'pravilo po tipu'})"
                            f" ({count} nota)"),
                    evidence=[Evidence(
                        source="hard_limit",
                        confidence=Confidence.DOCUMENTED,
                        evidence_type="key_range",
                        detail=(f"ciljni zvuk '{conflict['instrument']}' ima opseg "
                                f"{note_name(rule.low)}-{note_name(rule.high)} "
                                f"({'izmjereni profil' if rule.source == 'measured' else 'pravilo po tipu instrumenta: ' + rule.explanation}); "
                                f"Key Range je HARD LIMIT"),
                        factory_reference=("PA800 instrument profile + remap tabela"
                                           if rule.source == "measured"
                                           else "pravilo po tipu instrumenta"),
                    ).to_dict()],
                    confidence=Confidence.DOCUMENTED.value,
                    applies_to=count,
                    status="NOT_NEEDED",
                    note=("nota se NE pomjera i NE transponuje -- prijavljeno, "
                          "odluka je tvoja"),
                )
                self.plan.operations.append(op)

    def conflict_report(self) -> None:
        self.plan.conflicts = [c.to_dict() for c in self.facts.conflicts]
        for conflict in self.facts.conflicts:
            track = self.facts.track(conflict.track)
            if track is None:
                continue
            op = Operation(
                track=track.index,
                channel=track.channels[0],
                role=track.role,
                operation="key_range_report",
                old_value=conflict.to_dict(),
                new_value=conflict.to_dict(),
                reason=conflict.describe(),
                evidence=[Evidence(
                    source="hard_limit",
                    confidence=Confidence.DOCUMENTED,
                    evidence_type="key_range",
                    detail=(f"Key Range je HARD LIMIT: "
                            f"{note_name(conflict.note)} je izvan "
                            f"{note_name(conflict.limit_low)}-"
                            f"{note_name(conflict.limit_high)} za "
                            f"'{conflict.instrument}' "
                            f"({'izmjereni profil' if conflict.source == 'measured' else 'pravilo po tipu instrumenta'})"
                            + (f" -- {conflict.rule_note}" if conflict.rule_note else "")),
                    factory_reference=("PA800 instrument profile"
                                       if conflict.source == "measured"
                                       else "pravilo po tipu instrumenta"),
                ).to_dict()],
                confidence=Confidence.DOCUMENTED.value,
                applies_to=conflict.ticks,
                status="NOT_NEEDED",
                note=("nota se NE pomjera i NE transponuje -- prijavljeno kao "
                      "KEY_RANGE_CONFLICT"),
            )
            self.plan.operations.append(op)
