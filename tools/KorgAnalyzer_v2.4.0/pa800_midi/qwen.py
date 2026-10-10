"""Ask the local Qwen (through Ollama) about the optimizer's findings.

Reuses the analyser's Ollama client -- same host default, same model
preference ladder, same "everything stays on your machine" rule.  The model is
given nothing but the measured report, and the system prompt repeats the hard
rules so it cannot talk the user into moving notes.
"""
from __future__ import annotations

import json
from typing import Any, Callable, Dict, List, Optional

from korg_binary_analyzer.ollama_client import OllamaClient, OllamaError

SYSTEM_PROMPT = """Ti si tehnicar za Korg Pa800 i PA800 MIDI Optimizer.

Pravila koja NIKAD ne kršiš:

1. Koristi isključivo podatke iz priloženog izvještaja. Ne izmišljaj brojeve,
   note, tickove, programe, checksume ni imena zvukova.
2. Ako podatak nije u izvještaju, reci "to nije u ovom izvještaju" i navedi
   KOJI bi podatak to pokazao. Ne nagađaj.
3. Razlikuj nivoe dokaza i nikad ih ne miješaj:
   VERIFIED i DOCUMENTED = dokazano; STRONG_EVIDENCE = direktno izmjereno;
   CANDIDATE = samo predlog, NE primjenjuje se automatski; UNKNOWN = nema dokaza.
4. Tick i pitch postojećih nota su NEPROMJENJIVI. Nikad ne predlaži quantize,
   humanize, swing, transpose, pomicanje note. To je zabranjeno pravilom.
5. Key Range je HARD LIMIT: nota izvan opsega se samo PRIJAVLJUJE kao
   KEY_RANGE_CONFLICT, nikad ne popravlja automatski.
6. Originalni fajl se nikad ne mijenja -- izlaz ide u workspace i nosi SHA-256.
7. Ako nešto nije dokazano, reci UNKNOWN. Bolje UNKNOWN nego pogrešan Pa800 izlaz.

Odgovaraj na bosanskom, kratko i konkretno. Format: zaključak, pa numerisane
činjenice iz izvještaja, pa "Sljedeci korak" ako ima smisla."""

QUICK_PROMPTS = (
    ("Objasni sta je uradjeno",
     "Objasni mi ukratko šta je ovaj program uradio s mojim MIDI fajlom i zašto."),
    ("Ima li konflikata",
     "Ima li KEY_RANGE_CONFLICT ili nešto sumnjivo? Navedi tačno šta i na kojem kanalu."),
    ("Zasto ovaj zvuk",
     "Zašto je predložen ili primijenjen ovaj zvuk/RX remap? Koji dokaz stoji iza toga?"),
    ("Sta je samo predlog",
     "Koje operacije su samo PREDLOG (CANDIDATE) i zašto nisu primijenjene?"),
    ("Za laika",
     "Objasni mi isto ali jednostavnim jezikom, bez tehničkih termina."),
)

DEFAULT_OPTIONS = {"temperature": 0.2}


def _compact(result: Dict[str, Any], max_chars: int = 11000) -> str:
    """Report -> dense evidence digest.  Only measured facts travel."""
    lines: List[str] = []
    facts = result.get("source") or {}
    if facts:
        lines.append(f"FAJL: {facts.get('path')}")
        lines.append(f"SHA-256: {facts.get('sha256')}")
        lines.append(f"FORMAT: MIDI tip {facts.get('midi_type')}, "
                     f"{facts.get('track_count')} traka, TPB {facts.get('ticks_per_beat')}, "
                     f"{facts.get('total_notes')} nota, {facts.get('length_seconds')}s")
        lines.append("")
        lines.append("TRAKE:")
        for track in facts.get("tracks", []):
            lines.append(
                f"  track {track.get('index')} kanal {track.get('channels')} "
                f"[{track.get('role')}] '{track.get('sound_name')}' "
                f"identitet {track.get('instrument_id')} "
                f"fabricki={track.get('in_factory_table')} "
                f"nota={track.get('note_count')} "
                f"velocity={track.get('velocity')} "
                f"key_range={track.get('key_range')}"
            )
            for conflict in track.get("conflicts", []):
                lines.append(
                    f"    KEY_RANGE_CONFLICT nota {conflict.get('note_name')} "
                    f"({conflict.get('direction')} opsega "
                    f"{conflict.get('limit_low')}-{conflict.get('limit_high')}) "
                    f"x{conflict.get('ticks')}"
                )
    plan = result.get("plan") or {}
    if plan:
        lines.append("")
        lines.append(f"PLAN (rezim {plan.get('mode')}): "
                     f"primijenjeno {plan.get('applied_count')}, "
                     f"predloga {plan.get('proposed_count')}, "
                     f"odbijeno {plan.get('refused_count')}")
        for op in plan.get("operations", []):
            lines.append(
                f"  [{op.get('status')}] track {op.get('track')} "
                f"kanal {op.get('channel', 0) + 1} ({op.get('role')}) "
                f"{op.get('operation')}: {op.get('old_value')} -> {op.get('new_value')}"
            )
            lines.append(f"      dokaz={op.get('confidence')} razlog: {op.get('reason')}")
        for op in plan.get("refusals", []):
            lines.append(f"  [REFUSED] {op.get('operation')}: {op.get('refusal')}")
    validation = result.get("validation")
    if validation:
        lines.append("")
        lines.append(f"VALIDACIJA: {validation.get('status')} -- "
                     f"{validation.get('description')}")
    gaps = result.get("evidence_gaps") or []
    if gaps:
        lines.append("")
        lines.append("NEDOSTAJE DOKAZ za: " + ", ".join(gaps))
    limits = result.get("limits") or []
    if limits:
        lines.append("")
        lines.append("OGRANICENJA:")
        for limit in limits:
            lines.append("  - " + limit)

    text = "\n".join(lines)
    if len(text) > max_chars:
        text = text[:max_chars] + "\n... (skracen radi konteksta)"
    return text


def ask_about(result: Dict[str, Any], question: str,
              host: Optional[str] = None,
              model: Optional[str] = None,
              history: Optional[List[Dict[str, str]]] = None,
              on_token: Optional[Callable[[str], None]] = None,
              max_chars: int = 11000) -> int:
    """Stream an answer about the optimizer report to stdout."""
    client = OllamaClient(host)
    ok, detail = client.is_available()
    if not ok:
        print("Lokalni model nije dostupan:")
        print("  " + detail)
        print("Pokreni Ollamu (ollama serve) ili provjeri: ",
              "python -m korg_binary_analyzer ai-status")
        return 1

    try:
        resolved = model or client.resolve_model()
        if not resolved:
            raise OllamaError("Nema instaliranog modela. Prvo: ollama pull qwen2.5:7b")
        digest = _compact(result, max_chars)
        user = (f"PODACI IZ IZVJESTAJA (samo ovo smiješ koristiti):\n\n{digest}\n\n"
                f"PITANJE: {question}")
        messages = [{"role": "system", "content": SYSTEM_PROMPT}]
        messages.extend(history or [])
        messages.append({"role": "user", "content": user})

        print(f"Model: {resolved}")
        print("-" * 72)
        result_chat = client.chat(messages, resolved,
                                  options=dict(DEFAULT_OPTIONS),
                                  on_token=on_token or (lambda token: print(
                                      token, end="", flush=True)))
        print()
        print("-" * 72)
        tokens = result_chat.get("eval_count")
        if tokens:
            print(f"[{tokens} tokena]")
        return 0
    except OllamaError as exc:
        print(f"Greška: {exc}")
        return 1
    except KeyboardInterrupt:
        print("\nPrekinuto.")
        return 130
