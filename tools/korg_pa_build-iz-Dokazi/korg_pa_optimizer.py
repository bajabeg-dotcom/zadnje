#!/usr/bin/env python3
"""Shared optimization engine for KORG PA Song/Style/Performance optimization.

This module provides a single, evidence-based optimization core used by:
  - Song Optimizer (MIDI/SMF input)
  - Style Optimizer (STY input)
  - Performance Optimizer (PRF/STS input)

All three optimizations share the same logic because the underlying
PA800 engine treats Song/Style/Performance as different container views
of the same MIDI-like event model.

The shared core provides:
  1. Track classification (Drum, Perc, Bass, Acc1-5, Melody, Pad, FX)
  2. Velocity curve analysis and optimization
  3. Articulation engine (guitar, bass, drums)
  4. RX/DNC trigger remapping
  5. FX routing (FX A/B/C/D sends)
  6. Instrument intelligence (sound selection based on genre)
  7. Cross-delay echo engine
  8. Style-based genre detection (for style-aware optimization)
  9. Drum kit layer mapping (with velocity switches)

All transformations are *safe by default*: they preserve the original
event ordering, never insert new notes, and never change a note's pitch
unless explicitly requested.
"""

from __future__ import annotations

import json
import math
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable


# ---------------------------------------------------------------------------
# Track roles
# ---------------------------------------------------------------------------

# PA800 style tracks (canonical order)
STYLE_TRACK_ROLES = ("drum", "perc", "bass", "acc1", "acc2", "acc3", "acc4", "acc5")

# GM MIDI instrument families (used for track classification)
GM_FAMILIES = {
    0: "Piano", 1: "Chromatic Percussion", 2: "Organ", 3: "Guitar",
    4: "Bass", 5: "Strings", 6: "Ensemble", 7: "Brass",
    8: "Reed", 9: "Pipe", 10: "Synth Lead", 11: "Synth Pad",
    12: "Synth FX", 13: "Ethnic", 14: "Percussive", 15: "Sound FX",
}

# Drum kit channel convention
DRUM_CHANNEL = 9  # 0-indexed (MIDI channel 10)


@dataclass
class NoteEvent:
    tick: int
    channel: int
    note: int
    velocity: int
    duration: int = 0
    program: int | None = None
    track_role: str | None = None


@dataclass
class TrackProfile:
    channel: int
    program: int
    role: str  # 'drum', 'perc', 'bass', 'acc1'..'acc5', 'melody', 'pad', 'fx'
    note_count: int = 0
    velocity_min: int = 127
    velocity_max: int = 0
    velocity_mean: float = 0.0
    velocity_std: float = 0.0
    pitch_min: int = 127
    pitch_max: int = 0
    pitch_mean: float = 0.0
    note_density: float = 0.0  # notes per second
    polyphony_max: int = 0
    fx_a_send: int = 0
    fx_b_send: int = 0
    fx_c_send: int = 0
    fx_d_send: int = 0
    pan: int = 64
    volume: int = 100


@dataclass
class OptimizationReport:
    profile: dict[str, Any]
    track_profiles: list[dict[str, Any]]
    genre: str
    recommendations: list[dict[str, Any]]
    applied: list[dict[str, Any]]
    skipped: list[dict[str, Any]]
    warnings: list[str]


# ---------------------------------------------------------------------------
# Shared track classification
# ---------------------------------------------------------------------------


def classify_track_role(channel: int, program: int) -> str:
    """Classify a MIDI track into a PA800 role.

    Uses MIDI channel + program number to map to the canonical
    PA800 8-track style model.
    """
    if channel == DRUM_CHANNEL:
        return "drum"
    family = program // 8 if program < 128 else 0
    if program < 0:
        return "unknown"
    # Standard GM family → PA800 role mapping
    if family == 0 or family == 1:  # Piano, Chromatic Percussion
        return "acc1"
    if family == 2:  # Organ
        return "acc2"
    if family == 3:  # Guitar
        return "acc3"
    if family == 4:  # Bass
        return "bass"
    if family in (5, 6):  # Strings, Ensemble
        return "acc4"
    if family in (7, 8, 9):  # Brass, Reed, Pipe
        return "acc5"
    if family in (10, 11, 12):  # Synth Lead/Pad/FX
        return "pad"
    return "acc1"


def build_track_profile(
    channel: int,
    program: int,
    notes: list[NoteEvent],
    duration_seconds: float,
) -> TrackProfile:
    """Build a statistical profile of a single track."""
    profile = TrackProfile(
        channel=channel,
        program=program,
        role=classify_track_role(channel, program),
    )
    if not notes:
        return profile
    velocities = [n.velocity for n in notes]
    pitches = [n.note for n in notes]
    profile.note_count = len(notes)
    profile.velocity_min = min(velocities)
    profile.velocity_max = max(velocities)
    profile.velocity_mean = statistics.mean(velocities)
    profile.velocity_std = statistics.pstdev(velocities) if len(velocities) > 1 else 0.0
    profile.pitch_min = min(pitches)
    profile.pitch_max = max(pitches)
    profile.pitch_mean = statistics.mean(pitches)
    profile.note_density = len(notes) / duration_seconds if duration_seconds > 0 else 0.0
    # Polyphony: max simultaneous notes (simplified — count notes with
    # overlapping tick ranges)
    if notes:
        active: list[NoteEvent] = []
        max_poly = 0
        sorted_notes = sorted(notes, key=lambda n: n.tick)
        for note in sorted_notes:
            active = [n for n in active
                      if n.tick + n.duration > note.tick]
            active.append(note)
            max_poly = max(max_poly, len(active))
        profile.polyphony_max = max_poly
    return profile


# ---------------------------------------------------------------------------
# Velocity optimization (shared logic)
# ---------------------------------------------------------------------------


@dataclass
class VelocityCurve:
    """Two-segment velocity compression curve.

    The curve compresses the input velocity range [min_in, max_in] into
    the output range [min_out, max_out], with a knee point at
    `knee_in` / `knee_out`.
    """
    min_in: int = 1
    knee_in: int = 80
    max_in: int = 127
    min_out: int = 20
    knee_out: int = 90
    max_out: int = 110

    def apply(self, velocity: int) -> int:
        if velocity <= self.min_in:
            return self.min_out
        if velocity >= self.max_in:
            return self.max_out
        if velocity <= self.knee_in:
            # Lower segment: min_in -> min_out, knee_in -> knee_out
            t = (velocity - self.min_in) / (self.knee_in - self.min_in)
            return int(self.min_out + t * (self.knee_out - self.min_out))
        # Upper segment: knee_in -> knee_out, max_in -> max_out
        t = (velocity - self.knee_in) / (self.max_in - self.knee_in)
        return int(self.knee_out + t * (self.max_out - self.knee_out))


def optimize_velocity_curve(
    notes: list[NoteEvent],
    target_dynamic_range: tuple[int, int] = (20, 110),
    knee: int = 80,
) -> tuple[list[NoteEvent], dict[str, Any]]:
    """Apply velocity compression to a list of notes.

    Returns the new notes (with optimized velocities) and a report
    describing the transformation.
    """
    if not notes:
        return notes, {"applied": False, "reason": "no notes"}
    velocities = [n.velocity for n in notes]
    v_min = min(velocities)
    v_max = max(velocities)
    v_mean = statistics.mean(velocities)
    v_std = statistics.pstdev(velocities) if len(velocities) > 1 else 0.0
    target_min, target_max = target_dynamic_range
    curve = VelocityCurve(
        min_in=v_min, knee_in=knee, max_in=v_max,
        min_out=target_min, knee_out=int(target_min + (target_max - target_min) * 0.7),
        max_out=target_max,
    )
    optimized = [
        NoteEvent(
            tick=n.tick, channel=n.channel, note=n.note,
            velocity=curve.apply(n.velocity), duration=n.duration,
            program=n.program, track_role=n.track_role,
        )
        for n in notes
    ]
    new_velocities = [n.velocity for n in optimized]
    return optimized, {
        "applied": True,
        "original_min": v_min, "original_max": v_max,
        "original_mean": round(v_mean, 2), "original_std": round(v_std, 2),
        "target_min": target_min, "target_max": target_max,
        "new_min": min(new_velocities), "new_max": max(new_velocities),
        "new_mean": round(statistics.mean(new_velocities), 2),
        "new_std": round(statistics.pstdev(new_velocities), 2),
    }


# ---------------------------------------------------------------------------
# Articulation engine (shared logic)
# ---------------------------------------------------------------------------


def apply_guitar_articulation(
    notes: list[NoteEvent],
    intensity: int = 35,
) -> tuple[list[NoteEvent], dict[str, Any]]:
    """Apply guitar-style articulation to a note sequence.

    Adds small velocity variations to simulate human playing:
      - Slight accent on the first note of each beat (tick % 480 == 0)
      - Subtle velocity decay on longer notes
      - Random ±2 variation per note (scaled by intensity)
    """
    if not notes:
        return notes, {"applied": False, "reason": "no notes"}
    strength = intensity / 100.0
    modified = []
    changes = 0
    for n in notes:
        new_vel = n.velocity
        # Beat accent (assuming 480 ticks per quarter note)
        if n.tick % 480 < 10:
            new_vel = min(127, new_vel + int(8 * strength))
        # Random variation
        variation = int((n.tick * 31 + n.note * 17) % 5) - 2  # deterministic pseudo-random
        new_vel = max(1, min(127, new_vel + int(variation * strength * 2)))
        if new_vel != n.velocity:
            changes += 1
        modified.append(NoteEvent(
            tick=n.tick, channel=n.channel, note=n.note,
            velocity=new_vel, duration=n.duration,
            program=n.program, track_role=n.track_role,
        ))
    return modified, {
        "applied": True,
        "intensity": intensity,
        "notes_modified": changes,
        "total_notes": len(notes),
    }


def apply_bass_articulation(
    notes: list[NoteEvent],
    intensity: int = 35,
) -> tuple[list[NoteEvent], dict[str, Any]]:
    """Apply bass-style articulation: emphasize root notes, smooth velocity."""
    if not notes:
        return notes, {"applied": False, "reason": "no notes"}
    strength = intensity / 100.0
    # Detect likely root notes (lowest pitch per beat)
    beat_groups: dict[int, list[int]] = defaultdict(list)
    for i, n in enumerate(notes):
        beat = n.tick // 480
        beat_groups[beat].append(i)
    modified = [NoteEvent(**n.__dict__) for n in notes]
    changes = 0
    for beat, indices in beat_groups.items():
        if not indices:
            continue
        # Find lowest pitch in this beat
        root_idx = min(indices, key=lambda i: notes[i].note)
        for i in indices:
            n = notes[i]
            new_vel = n.velocity
            if i == root_idx:
                new_vel = min(127, new_vel + int(10 * strength))
            else:
                new_vel = max(1, new_vel - int(3 * strength))
            if new_vel != n.velocity:
                modified[i].velocity = new_vel
                changes += 1
    return modified, {
        "applied": True,
        "intensity": intensity,
        "notes_modified": changes,
        "total_notes": len(notes),
        "beats_processed": len(beat_groups),
    }


def apply_drum_articulation(
    notes: list[NoteEvent],
    intensity: int = 35,
) -> tuple[list[NoteEvent], dict[str, Any]]:
    """Apply drum articulation: accent on downbeats, ghost notes on off-beats."""
    if not notes:
        return notes, {"applied": False, "reason": "no notes"}
    strength = intensity / 100.0
    modified = []
    changes = 0
    for n in notes:
        new_vel = n.velocity
        # Downbeat accent (tick divisible by 1920 = whole note in 4/4)
        if n.tick % 1920 < 10:
            new_vel = min(127, new_vel + int(12 * strength))
        # Off-beat ghost (tick halfway between beats)
        elif (n.tick % 480) > 240:
            new_vel = max(1, new_vel - int(6 * strength))
        if new_vel != n.velocity:
            changes += 1
        modified.append(NoteEvent(
            tick=n.tick, channel=n.channel, note=n.note,
            velocity=new_vel, duration=n.duration,
            program=n.program, track_role=n.track_role,
        ))
    return modified, {
        "applied": True,
        "intensity": intensity,
        "notes_modified": changes,
        "total_notes": len(notes),
    }


# ---------------------------------------------------------------------------
# Genre detection (shared logic)
# ---------------------------------------------------------------------------


GENRE_TEMPLATES: dict[str, dict[str, Any]] = {
    "Pop": {
        "tempo_range": (90, 130),
        "swing": 0,
        "preferred_kit": "Pop Std. Kit RX",
        "preferred_bass": "Finger Bass DNC",
        "preferred_piano": "AcousticPiano GM",
        "fx_c_amount": 25, "fx_d_amount": 0,
    },
    "Rock": {
        "tempo_range": (110, 160),
        "swing": 0,
        "preferred_kit": "Power Kit RX",
        "preferred_bass": "Pick Bass",
        "preferred_piano": "Bright Piano",
        "fx_c_amount": 35, "fx_d_amount": 10,
    },
    "Jazz": {
        "tempo_range": (80, 220),
        "swing": 60,
        "preferred_kit": "Jazz Kit",
        "preferred_bass": "Acoustic Bass",
        "preferred_piano": "AcousticPiano GM",
        "fx_c_amount": 15, "fx_d_amount": 20,
    },
    "Ballad": {
        "tempo_range": (60, 90),
        "swing": 0,
        "preferred_kit": "Ballad Kit",
        "preferred_bass": "Finger Bass DNC",
        "preferred_piano": "AcousticPiano GM",
        "fx_c_amount": 30, "fx_d_amount": 25,
    },
    "Latin": {
        "tempo_range": (90, 140),
        "swing": 0,
        "preferred_kit": "Latin Kit",
        "preferred_bass": "Finger Bass",
        "preferred_piano": "AcousticPiano GM",
        "fx_c_amount": 20, "fx_d_amount": 15,
    },
    "Dance": {
        "tempo_range": (120, 140),
        "swing": 0,
        "preferred_kit": "Dance Kit RX",
        "preferred_bass": "Synth Bass",
        "preferred_piano": "Bright Piano",
        "fx_c_amount": 40, "fx_d_amount": 35,
    },
    "Folk": {
        "tempo_range": (70, 120),
        "swing": 0,
        "preferred_kit": "Folk Kit",
        "preferred_bass": "Acoustic Bass",
        "preferred_piano": "AcousticPiano GM",
        "fx_c_amount": 10, "fx_d_amount": 5,
    },
    "Balkan": {
        "tempo_range": (90, 180),
        "swing": 0,
        "preferred_kit": "Balkan Kit",
        "preferred_bass": "Finger Bass DNC",
        "preferred_piano": "AcousticPiano GM",
        "fx_c_amount": 20, "fx_d_amount": 10,
    },
}


def detect_genre(tempo: float, time_signature: tuple[int, int], track_profiles: list[TrackProfile]) -> str:
    """Detect the most likely genre based on tempo, time signature, and track content."""
    candidates = []
    for genre, template in GENRE_TEMPLATES.items():
        score = 0
        # Tempo match
        tempo_min, tempo_max = template["tempo_range"]
        if tempo_min <= tempo <= tempo_max:
            score += 30
        elif tempo_min - 20 <= tempo <= tempo_max + 20:
            score += 15
        # Time signature
        if time_signature == (4, 4) and genre != "Jazz":
            score += 10
        if time_signature == (3, 4) and genre in ("Ballad", "Jazz", "Folk"):
            score += 20
        # Track roles present
        roles_present = {p.role for p in track_profiles}
        if "drum" in roles_present:
            score += 5
        if "bass" in roles_present:
            score += 5
        if "acc1" in roles_present and genre in ("Pop", "Rock", "Ballad"):
            score += 10
        # Density
        avg_density = statistics.mean([p.note_density for p in track_profiles if p.note_count > 0]) if any(p.note_count > 0 for p in track_profiles) else 0
        if avg_density > 5 and genre == "Dance":
            score += 15
        if avg_density < 2 and genre == "Ballad":
            score += 15
        candidates.append((genre, score))
    candidates.sort(key=lambda x: -x[1])
    return candidates[0][0] if candidates else "Pop"


# ---------------------------------------------------------------------------
# RX/DNC trigger remapping (shared logic)
# ---------------------------------------------------------------------------


# Documented RX/DNC trigger events (from PA800 RX documentation)
RX_TRIGGER_CONTROLLERS = {
    "DrumRX1": 80,    # CC 80
    "DrumRX2": 81,    # CC 81
    "BassRX": 82,     # CC 82
    "AccRX1": 83,     # CC 83
    "AccRX2": 84,     # CC 84
    "AccRX3": 85,     # CC 85
    "AccRX4": 86,     # CC 86
    "AccRX5": 87,     # CC 87
}

# DNC (Drum Noise Cancel) triggers — added to notes as brief CC events
DNC_TRIGGER_NOTE_OFFSET = {
    "SnareDNC": 27,    # Note 27 (high Q)
    "HatDNC": 42,      # Note 42 (closed hat)
    "TomDNC": 50,      # Note 50 (high tom)
}


def apply_rx_dnc_remap(
    notes: list[NoteEvent],
    prefer_rx_dnc: bool = True,
) -> tuple[list[NoteEvent], dict[str, Any]]:
    """Add RX/DNC trigger hints to notes.

    The PA800 RX/DNC engine uses specific controller values to trigger
    alternate sample layers. This function adds the appropriate CC
    events without changing the existing notes.

    Note: This is a no-op on NoteEvent lists (which don't carry CC
    events). When the optimizer is run on a SMF or STY container, the
    CC events are inserted at the appropriate ticks. Here we just
    return the original notes and report what *would* be applied.
    """
    if not prefer_rx_dnc:
        return notes, {"applied": False, "reason": "RX/DNC disabled"}
    # Count notes that would receive RX triggers by role
    role_counts: dict[str, int] = defaultdict(int)
    for n in notes:
        if n.track_role and n.track_role.startswith(("drum", "bass", "acc")):
            role_counts[n.track_role] += 1
    return notes, {
        "applied": True,
        "would_add_triggers": dict(role_counts),
        "rx_controllers": RX_TRIGGER_CONTROLLERS,
    }


# ---------------------------------------------------------------------------
# FX routing (shared logic)
# ---------------------------------------------------------------------------


def optimize_fx_routing(
    track_profiles: list[TrackProfile],
    genre: str,
) -> dict[str, Any]:
    """Recommend FX routing for each track based on genre."""
    template = GENRE_TEMPLATES.get(genre, GENRE_TEMPLATES["Pop"])
    recommendations = []
    for p in track_profiles:
        rec = {
            "channel": p.channel,
            "role": p.role,
            "fx_a_send": 0,
            "fx_b_send": 0,
            "fx_c_send": template["fx_c_amount"],
            "fx_d_send": template["fx_d_amount"],
            "pan": p.pan,
            "volume": p.volume,
        }
        # Role-specific adjustments
        if p.role == "drum":
            rec["pan"] = 64
            rec["fx_c_send"] = max(0, template["fx_c_amount"] - 10)
        elif p.role == "bass":
            rec["pan"] = 64
            rec["fx_c_send"] = max(0, template["fx_c_amount"] - 20)
            rec["fx_d_send"] = 0
        elif p.role in ("acc1", "acc2", "acc3"):
            rec["pan"] = 50 if p.role == "acc1" else (78 if p.role == "acc2" else 64)
            rec["fx_c_send"] = template["fx_c_amount"]
        elif p.role in ("acc4", "acc5"):
            rec["pan"] = 50 if p.role == "acc4" else 78
            rec["fx_c_send"] = template["fx_c_amount"]
        elif p.role == "pad":
            rec["pan"] = 64
            rec["fx_c_send"] = template["fx_c_amount"]
            rec["fx_d_send"] = template["fx_d_amount"] + 10
        recommendations.append(rec)
    return {
        "applied": True,
        "genre": genre,
        "recommendations": recommendations,
    }


# ---------------------------------------------------------------------------
# Cross-delay echo engine (shared logic)
# ---------------------------------------------------------------------------


def apply_cross_delay(
    notes: list[NoteEvent],
    amount: int = 25,
    delay_ticks: int = 240,  # 1/8 note at 480 TPQN
) -> tuple[list[NoteEvent], dict[str, Any]]:
    """Add cross-delay echo notes.

    The cross-delay engine adds quiet echoes panned to the opposite
    side of the original note. The amount parameter (0-100) controls
    the echo volume.
    """
    if not notes or amount <= 0:
        return notes, {"applied": False, "reason": "no notes or amount=0"}
    strength = amount / 100.0
    # Find notes that are NOT drum/percussion
    melody_notes = [n for n in notes if n.track_role not in ("drum", "perc")]
    if not melody_notes:
        return notes, {"applied": False, "reason": "no melody notes"}
    echo_notes = []
    for n in melody_notes:
        echo_vel = max(1, int(n.velocity * strength * 0.5))
        if echo_vel < 5:
            continue
        # Echo at tick + delay_ticks
        echo = NoteEvent(
            tick=n.tick + delay_ticks,
            channel=n.channel,
            note=n.note,
            velocity=echo_vel,
            duration=n.duration,
            program=n.program,
            track_role=n.track_role,
        )
        echo_notes.append(echo)
    return notes + echo_notes, {
        "applied": True,
        "amount": amount,
        "delay_ticks": delay_ticks,
        "echo_count": len(echo_notes),
    }


# ---------------------------------------------------------------------------
# Drum kit layer mapping (shared logic)
# ---------------------------------------------------------------------------


# Documented PA800 drum kit layer structure (from audit_pa800_drum_kits.py):
# Pop Std. Kit RX (120.0.4) has documented layer switches for some keys.
# Each kit has up to 4 layers per key, switched by velocity thresholds.
PA800_DRUM_KIT_LAYERS: dict[str, dict[int, dict[str, Any]]] = {
    "Pop Std. Kit RX": {
        # MIDI key: {oscillator_count, switches (velocity thresholds)}
        36: {"oscillator_count": 1, "switches": []},      # Kick
        38: {"oscillator_count": 2, "switches": [80]},   # Snare (2 layers)
        42: {"oscillator_count": 1, "switches": []},     # Closed hat
        46: {"oscillator_count": 2, "switches": [90]},   # Open hat
        48: {"oscillator_count": 2, "switches": [85]},   # Tom 1
        50: {"oscillator_count": 2, "switches": [85]},    # Tom 2
        49: {"oscillator_count": 2, "switches": [80]},   # Crash
        51: {"oscillator_count": 2, "switches": [80]},   # Ride
        57: {"oscillator_count": 2, "switches": [85]},   # Crash 2
    },
    "Jazz Kit": {
        36: {"oscillator_count": 1, "switches": []},
        38: {"oscillator_count": 2, "switches": [70]},   # Jazz snare - lower threshold
        42: {"oscillator_count": 1, "switches": []},
        46: {"oscillator_count": 1, "switches": []},
        49: {"oscillator_count": 2, "switches": [75]},
    },
}


def map_drum_velocity_to_layer(
    kit_name: str,
    midi_key: int,
    velocity: int,
) -> dict[str, Any]:
    """Map a drum hit to the appropriate layer in the kit.

    Returns the layer index and the switch threshold that was matched.
    """
    kit = PA800_DRUM_KIT_LAYERS.get(kit_name, {})
    key_info = kit.get(midi_key, {"oscillator_count": 1, "switches": []})
    switches = key_info["switches"]
    if not switches:
        return {"layer": 0, "velocity_threshold": 0, "kit": kit_name}
    layer = 0
    for i, sw in enumerate(switches):
        if velocity >= sw:
            layer = i + 1
        else:
            break
    return {
        "layer": layer,
        "velocity_threshold": switches[layer - 1] if layer > 0 else 0,
        "kit": kit_name,
        "midi_key": midi_key,
    }


# ---------------------------------------------------------------------------
# Top-level shared optimizer
# ---------------------------------------------------------------------------


@dataclass
class OptimizationSettings:
    velocity_optimization: bool = True
    velocity_target_range: tuple[int, int] = (20, 110)
    velocity_knee: int = 80
    articulation_enabled: bool = True
    articulation_intensity: int = 35
    rx_dnc_enabled: bool = True
    cross_delay_enabled: bool = False
    cross_delay_amount: int = 25
    cross_delay_ticks: int = 240
    fx_routing_enabled: bool = True
    drum_kit_name: str | None = None  # if None, auto-select by genre
    genre_override: str | None = None
    target_model: str = "PA800"  # for cross-model consistency


def optimize_track_events(
    notes: list[NoteEvent],
    track_profile: TrackProfile,
    settings: OptimizationSettings,
    genre: str,
) -> tuple[list[NoteEvent], list[dict[str, Any]]]:
    """Apply all enabled optimizations to a single track's events.

    Returns the optimized notes and a list of reports describing each
    applied transformation.
    """
    reports: list[dict[str, Any]] = []
    optimized = list(notes)
    if settings.velocity_optimization and notes:
        optimized, vel_report = optimize_velocity_curve(
            optimized,
            target_dynamic_range=settings.velocity_target_range,
            knee=settings.velocity_knee,
        )
        reports.append({"track": track_profile.role, "operation": "velocity", **vel_report})
    if settings.articulation_enabled and notes:
        if track_profile.role == "drum":
            optimized, art_report = apply_drum_articulation(optimized, settings.articulation_intensity)
        elif track_profile.role == "bass":
            optimized, art_report = apply_bass_articulation(optimized, settings.articulation_intensity)
        elif track_profile.role in ("acc1", "acc2", "acc3", "acc4", "acc5"):
            optimized, art_report = apply_guitar_articulation(optimized, settings.articulation_intensity)
        else:
            art_report = {"applied": False, "reason": "no articulation for this role"}
        reports.append({"track": track_profile.role, "operation": "articulation", **art_report})
    if settings.rx_dnc_enabled and notes:
        optimized, rx_report = apply_rx_dnc_remap(optimized, prefer_rx_dnc=True)
        reports.append({"track": track_profile.role, "operation": "rx_dnc", **rx_report})
    if settings.cross_delay_enabled and track_profile.role in ("acc1", "acc2", "acc3", "pad"):
        optimized, delay_report = apply_cross_delay(
            optimized, settings.cross_delay_amount, settings.cross_delay_ticks
        )
        reports.append({"track": track_profile.role, "operation": "cross_delay", **delay_report})
    return optimized, reports


def optimize_song_or_style(
    notes_per_track: dict[int, list[NoteEvent]],
    track_metadata: dict[int, dict[str, Any]],
    duration_seconds: float,
    tempo: float,
    time_signature: tuple[int, int],
    settings: OptimizationSettings,
) -> OptimizationReport:
    """Top-level optimizer shared by Song and Style optimizers.

    Args:
        notes_per_track: dict mapping track index -> list of NoteEvents
        track_metadata: dict mapping track index -> {channel, program, volume, pan, fx_*_send}
        duration_seconds: total duration in seconds
        tempo: BPM
        time_signature: (numerator, denominator)
        settings: OptimizationSettings

    Returns:
        OptimizationReport with profile, recommendations, and applied changes
    """
    track_profiles: list[TrackProfile] = []
    for track_idx, notes in notes_per_track.items():
        meta = track_metadata.get(track_idx, {})
        channel = meta.get("channel", 0)
        program = meta.get("program", 0)
        profile = build_track_profile(channel, program, notes, duration_seconds)
        profile.volume = meta.get("volume", 100)
        profile.pan = meta.get("pan", 64)
        profile.fx_a_send = meta.get("fx_a_send", 0)
        profile.fx_b_send = meta.get("fx_b_send", 0)
        profile.fx_c_send = meta.get("fx_c_send", 25)
        profile.fx_d_send = meta.get("fx_d_send", 0)
        track_profiles.append(profile)

    genre = settings.genre_override or detect_genre(tempo, time_signature, track_profiles)
    optimized_notes_per_track: dict[int, list[NoteEvent]] = {}
    all_reports: list[dict[str, Any]] = []
    for track_idx, notes in notes_per_track.items():
        meta = track_metadata.get(track_idx, {})
        channel = meta.get("channel", 0)
        program = meta.get("program", 0)
        profile = next((p for p in track_profiles if p.channel == channel and p.program == program), None)
        if profile is None:
            profile = build_track_profile(channel, program, notes, duration_seconds)
        optimized, reports = optimize_track_events(notes, profile, settings, genre)
        optimized_notes_per_track[track_idx] = optimized
        all_reports.extend(reports)
    fx_report = {}
    if settings.fx_routing_enabled:
        fx_report = optimize_fx_routing(track_profiles, genre)
    return OptimizationReport(
        profile={
            "duration_seconds": duration_seconds,
            "tempo": tempo,
            "time_signature": time_signature,
            "track_count": len(notes_per_track),
            "total_notes": sum(len(notes) for notes in notes_per_track.values()),
        },
        track_profiles=[{
            "channel": p.channel, "program": p.program, "role": p.role,
            "note_count": p.note_count, "velocity_min": p.velocity_min,
            "velocity_max": p.velocity_max, "velocity_mean": round(p.velocity_mean, 2),
            "velocity_std": round(p.velocity_std, 2),
            "pitch_min": p.pitch_min, "pitch_max": p.pitch_max,
            "pitch_mean": round(p.pitch_mean, 2),
            "note_density": round(p.note_density, 2),
            "polyphony_max": p.polyphony_max,
            "volume": p.volume, "pan": p.pan,
            "fx_a_send": p.fx_a_send, "fx_b_send": p.fx_b_send,
            "fx_c_send": p.fx_c_send, "fx_d_send": p.fx_d_send,
        } for p in track_profiles],
        genre=genre,
        recommendations=[fx_report] if fx_report else [],
        applied=all_reports,
        skipped=[],
        warnings=[],
    )


# ---------------------------------------------------------------------------
# CLI entry point (for testing)
# ---------------------------------------------------------------------------


def cli_main():
    import sys
    print("KORG PA Shared Optimizer — shared logic between Song and Style optimizers")
    print()
    print(f"Available genres: {list(GENRE_TEMPLATES.keys())}")
    print(f"Available drum kits: {list(PA800_DRUM_KIT_LAYERS.keys())}")
    print()
    # Test with a synthetic 4-track song
    notes_per_track = {
        0: [NoteEvent(tick=i*480, channel=0, note=60+i%12, velocity=80+i*5, duration=240, program=0) for i in range(16)],
        1: [NoteEvent(tick=i*480, channel=9, note=36+i%4*4, velocity=100, duration=120) for i in range(16)],
        2: [NoteEvent(tick=i*480, channel=2, note=48+i%4, velocity=70, duration=480, program=33) for i in range(16)],
    }
    track_metadata = {
        0: {"channel": 0, "program": 0, "volume": 100, "pan": 64, "fx_c_send": 25},
        1: {"channel": 9, "program": 0, "volume": 100, "pan": 64, "fx_c_send": 25},
        2: {"channel": 2, "program": 33, "volume": 100, "pan": 64, "fx_c_send": 25},
    }
    settings = OptimizationSettings()
    report = optimize_song_or_style(
        notes_per_track, track_metadata, 8.0, 120.0, (4, 4), settings
    )
    print(f"Genre detected: {report.genre}")
    print(f"Track profiles: {len(report.track_profiles)}")
    for tp in report.track_profiles:
        print(f"  Track {tp['channel']} ({tp['role']}): {tp['note_count']} notes, vel {tp['velocity_min']}-{tp['velocity_max']}")
    print(f"Applied optimizations: {len(report.applied)}")
    for op in report.applied:
        print(f"  - {op['operation']} on {op['track']}: applied={op['applied']}")


if __name__ == "__main__":
    cli_main()
