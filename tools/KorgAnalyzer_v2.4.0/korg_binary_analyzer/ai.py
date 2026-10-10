"""Grounded assistant layer.

Turns an analysis report into a compact, factual prompt and asks a local Qwen
model (through Ollama) to explain it.  Three rules shape everything here:

1. **Only measured facts go into the prompt.**  Counts, offsets, checksums,
   names and framing results come straight out of the decoder.  Nothing is
   paraphrased into a claim the decoder did not make.
2. **Candidates stay candidates.**  Rejected resources, high-entropy regions and
   lexical hits are labelled as such in the prompt, and the model is instructed
   not to promote them.
3. **The model may say "not in the data".**  The system prompt requires it to
   reply with the missing measurement rather than a plausible guess.

Nothing leaves the machine: the request goes to the local Ollama server only.
"""

from __future__ import annotations

from typing import Any, Callable

from .ollama_client import ModelInfo, OllamaClient, OllamaError

#: Generation defaults tuned for analysis work: low temperature, enough context
#: for a full report plus a manual excerpt.
DEFAULT_OPTIONS: dict[str, Any] = {
    "temperature": 0.2,
    "top_p": 0.9,
    "num_ctx": 8192,
    "repeat_penalty": 1.05,
}

SYSTEM_PROMPT = """Ti si tehnicar za analizu Korg aranzera. Pomazes korisniku da razumije izvjestaj
o Korg Pa fajlu (stilovi, resursi, kontejneri) koji je napravio read-only dekoder.

PRAVILA KOJIH SE MORAS DRZATI:
1. Koristi ISKLJUCIVO podatke iz prilozenog izvjestaja. Ne izmisljaj brojeve,
   offsete, checksume, imena stilova, verzije OS-a ni nazive parametara.
2. Ako podatak nije u izvjestaju, reci: "to nije u ovom izvjestaju" i navedi
   KOJI bi podatak ili provjera to pokazali. Ne nagadjaj.
3. Razlikuj TRI nivoa i nikad ih ne mijesaj:
   - VALIDATED / VERIFIED = dekoder je dokazao (velicina se poklapa I XOR checksum prolazi)
   - CANDIDATE / OBSERVED = znak postoji, ali nije dokazano
   - UNKNOWN = nema podataka
4. Ako se ime iz tabele imena razlikuje od imena u streamu, objasni da je to
   dokaz da je stil kopiran i preimenovan samo u tabeli, a unutrasnje ime je
   ostalo staro. To je nalaz, ne greska programa.
5. Ne predlazi pisanje ni mijenjanje Korg fajlova. Program je read-only i to se
   ne mijenja.
6. Odgovaraj na bosanskom/srpskom, kratko i konkretno. Bez uvoda i bez
   ponavljanja pitanja. Koristi brojeve iz izvjestaja.
7. Ako te pitaju nesto sto nema veze s ovim fajlom, reci da si tu samo za
   analizu ovog izvjestaja.

FORMAT ODGOVORA:
- Kratki zakljucak (1-2 recenice)
- Pa nabrojane cinjenice s brojevima iz izvjestaja
- Na kraju, ako je korisno: "Sljedeci korak" - jedna konkretna provjera ili
  dokument (User Manual / Data List / Advanced Edit) koji to objasnjava."""

QUICK_PROMPTS: tuple[tuple[str, str], ...] = (
    ("Objasni ukratko", "Objasni mi ukratko sta je u ovom fajlu: koliko stilova, da li su svi resursi validni i ima li nesto sumnjivo."),
    ("Objasni razlike u imenima", "Objasni zasto se kod nekih slotova ime iz tabele imena razlikuje od imena u streamu. Navedi tacno koje slotove i sta to znaci u praksi."),
    ("Provjeri integritet", "Da li je svaki resurs prosao provjeru velicine i XOR checksuma? Ako neki nije, navedi koji i zasto, i reci sta to znaci."),
    ("Sta mogu dalje", "Koji je sljedeci korak? Predlozi konkretne provjere i koji prirucnik (User Manual, Advanced Edit, Data List) to objasnjava."),
    ("Za laika", "Objasni ovo jednostavnim jezikom, kao da ne znam nista o Korg formatima. Bez tehnickih termina gdje moze bez njih."),
)


def _compact_report(report: dict[str, Any], max_chars: int) -> str:
    """Render the measured facts of a report as compact text for the prompt."""
    lines: list[str] = []
    identity = report.get("facts", {}).get("identity", {})
    lines.append(f"FAJL: {identity.get('filename', '?')}  ({identity.get('size', '?')} bajtova)")
    hashes = identity.get("hashes", {})
    if hashes.get("sha256"):
        lines.append(f"SHA-256: {hashes['sha256']}")
    for finding in report.get("facts", {}).get("formats", [])[:6]:
        lines.append(f"FORMAT: {finding.get('fact')} [{finding.get('status')}, pouzdanost {finding.get('confidence')}]")

    for result in report.get("decoded", {}).get("results", []):
        decoder = result.get("decoder")
        if not decoder:
            continue
        lines.append("")
        lines.append(f"DEKODER: {decoder} -> {result.get('status')}")
        if result.get("container_kind"):
            lines.append(f"  kontejner: {result['container_kind']}")
        if result.get("resource_count") is not None:
            lines.append(
                f"  resursa: {result.get('resource_count')}"
                f" (kompresovanih {result.get('compressed_resource_count', '?')},"
                f" sirovih {result.get('raw_resource_count', '?')},"
                f" odbijenih {result.get('resource_decode_errors', '?')})"
            )
        validation = result.get("validation") or {}
        if validation.get("status"):
            lines.append(f"  validacija: {validation['status']}")
            for evidence in validation.get("evidence", [])[:6]:
                lines.append(f"    dokaz: {evidence}")
        agreement = result.get("name_agreement") or {}
        if agreement.get("compared"):
            lines.append(
                f"  imena: poklapa se {agreement.get('agreed')}/{agreement.get('compared')}"
                f" (razlika {agreement.get('disagreed')})"
            )
        slots = result.get("slots") or []
        if slots:
            differing = [slot for slot in slots if slot.get("agrees") is False]
            lines.append(f"  slotova ukupno: {len(slots)}; s razlikom u imenu: {len(differing)}")
            for slot in differing[:20]:
                lines.append(
                    f"    slot {slot.get('slot')}: tabela={slot.get('directory_name')!r}"
                    f" stream={slot.get('decoded_name')!r} elemenata={slot.get('element_count')}"
                )
            for slot in slots[:8]:
                if slot.get("agrees") is not False:
                    lines.append(
                        f"    slot {slot.get('slot')}: {slot.get('decoded_name')!r}"
                        f" elemenata={slot.get('element_count')}"
                        f" geometrija={slot.get('element_track_geometries')}"
                    )
        errors = result.get("resource_errors") or []
        for error in errors[:10]:
            lines.append(f"  ODBIJEN resurs #{error.get('resource_position')} id={error.get('id')}: {error.get('reason')}")

    manuals = report.get("manuals") or {}
    if manuals.get("matches"):
        match = manuals["matches"][0]
        lines.append("")
        lines.append(f"MODEL: {match.get('brand')} {match.get('model')} — {match.get('container_era')}")
        reference = [doc for doc in match.get("documents", []) if doc.get("depth") == "reference"]
        for doc in reference[:5]:
            lines.append(f"  dokument: {doc.get('doc')} ({doc.get('size')})")

    evidence = report.get("evidence", {})
    entropy = evidence.get("entropy", {}) if isinstance(evidence.get("entropy"), dict) else {}
    whole = entropy.get("whole_file")
    if whole is not None:
        lines.append("")
        if isinstance(whole, dict):
            value = whole.get("value")
            classification = whole.get("classification")
            rendered = f"{value}" + (f" ({classification})" if classification else "")
        else:
            rendered = str(whole)
        lines.append(f"ENTROPIJA (cijeli fajl): {rendered}")
    high = entropy.get("high_entropy_regions") or []
    if high:
        lines.append(
            f"  regija visoke entropije: {len(high)}"
            " (indikator, NIJE dokaz enkripcije)"
        )

    text = "\n".join(lines)
    if len(text) > max_chars:
        text = text[:max_chars] + "\n...[izvjestaj skracen zbog duzine]"
    return text


def build_messages(
    question: str,
    report: dict[str, Any] | None,
    history: list[dict[str, str]] | None = None,
    max_report_chars: int = 12000,
    extra_context: str | None = None,
) -> list[dict[str, str]]:
    """Assemble the chat messages: system rules, evidence, history, question."""
    messages: list[dict[str, str]] = [{"role": "system", "content": SYSTEM_PROMPT}]
    if report:
        block = _compact_report(report, max_report_chars)
        messages.append({
            "role": "system",
            "content": "IZVJESTAJ DEKODERA (samo ovi podaci se smiju koristiti):\n" + block,
        })
    if extra_context:
        messages.append({
            "role": "system",
            "content": "DODATNI KONTEKST IZ PRIRUCNIKA:\n" + extra_context[:6000],
        })
    for turn in (history or [])[-8:]:
        if turn.get("role") in ("user", "assistant") and turn.get("content"):
            messages.append({"role": turn["role"], "content": turn["content"]})
    messages.append({"role": "user", "content": question})
    return messages


def ask(
    question: str,
    report: dict[str, Any] | None,
    client: OllamaClient,
    model: str | None = None,
    history: list[dict[str, str]] | None = None,
    on_token: Callable[[str], None] | None = None,
    cancel: Callable[[], bool] | None = None,
    options: dict[str, Any] | None = None,
    max_report_chars: int = 12000,
) -> dict[str, Any]:
    """Ask the local model about a report.  Returns ``{content, model, ...}``."""
    resolved = model or client.resolve_model()
    if not resolved:
        raise OllamaError(
            "No model is installed in Ollama. Pull one first, for example:\n"
            "    ollama pull qwen2.5:7b"
        )
    messages = build_messages(question, report, history, max_report_chars=max_report_chars)
    result = client.chat(
        messages,
        resolved,
        options={**DEFAULT_OPTIONS, **(options or {})},
        on_token=on_token,
        cancel=cancel,
    )
    result["question"] = question
    return result


def report_summary_line(report: dict[str, Any] | None) -> str:
    """One-line description of what the assistant is looking at."""
    if not report:
        return "Nema ucitanog izvjestaja — model odgovara bez podataka o fajlu."
    identity = report.get("facts", {}).get("identity", {})
    results = [item for item in report.get("decoded", {}).get("results", []) if item.get("decoder")]
    if not results:
        return f"{identity.get('filename', '?')}: nema dekodiranog sadrzaja."
    result = results[0]
    return (
        f"{identity.get('filename', '?')}: {result.get('status')}, "
        f"{result.get('resource_count', 0)} resursa, {result.get('style_count', 0)} stilova."
    )


def list_local_models(client: OllamaClient) -> list[ModelInfo]:
    return client.models()
