from __future__ import annotations

from typing import Any


PLAINTEXT_MAGICS = (
    (b"\x7fELF", "ELF"), (b"\x89PNG", "PNG"), (b"PK\x03\x04", "ZIP"),
    (b"\x1f\x8b\x08", "GZIP"), (b"MZ", "DOS/PE"), (b"SQLite", "SQLite"),
    (b"MThd", "MIDI"), (b"RIFF", "RIFF"), (b"BZh", "BZIP2"),
)


def transform_candidates(data: Any) -> dict[str, Any]:
    candidates = []
    sample = bytes(data[:32])
    for magic, label in PLAINTEXT_MAGICS:
        if len(sample) < len(magic):
            continue
        key = sample[0] ^ magic[0]
        transformed_prefix = bytes(byte ^ key for byte in sample[:len(magic)])
        if transformed_prefix == magic and key:
            candidates.append({
                "transform": "single_byte_xor", "key_hex": f"{key:02x}", "matched_magic": label,
                "offset": 0, "confidence": 0.55, "status": "CANDIDATE_ONLY",
                "evidence": [f"XOR key 0x{key:02X} maps the first {len(magic)} bytes to {label} magic", "payload has not been decoded or independently validated"],
            })
    reversed_sample = sample[:16][::-1]
    for magic, label in PLAINTEXT_MAGICS:
        if len(magic) >= 4 and reversed_sample.startswith(magic):
            candidates.append({
                "transform": "byte_reversal", "key_hex": None, "matched_magic": label, "offset": 0,
                "confidence": 0.25, "status": "CANDIDATE_ONLY",
                "evidence": ["prefix reversal resembles a known magic", "no transformed output or structure was validated"],
            })
    return {
        "candidates": candidates,
        "not_attempted": ["repeating-key XOR", "additive transforms", "rotations", "bit transforms"],
        "reason": "No known-plaintext evidence justified broader transform search",
    }


def crypto_candidates(strings: list[dict[str, Any]], signatures: list[dict[str, Any]], entropy: dict[str, Any], pe: dict[str, Any] | None) -> dict[str, Any]:
    evidence = []
    crypto_words = [item for item in strings if "CRYPTO" in item.get("categories", [])]
    for item in crypto_words[:100]:
        evidence.append({"kind": "string", "offset": item["offset"], "text": item["content"][:120], "status": "CANDIDATE"})
    if pe:
        for module in pe.get("imports", []):
            if any(token in module.get("dll", "").casefold() for token in ("crypt", "ssl", "crypto", "bcrypt")):
                evidence.append({"kind": "import_module", "name": module["dll"], "status": "CANDIDATE"})
            for symbol in module.get("symbols", []):
                name = symbol.get("name", "")
                if any(token in name.casefold() for token in ("crypt", "aes", "sha", "md5", "rsa", "bcrypt")):
                    evidence.append({"kind": "import_symbol", "module": module.get("dll"), "name": name, "status": "CANDIDATE"})
    for region in entropy.get("high_entropy_regions", [])[:100]:
        evidence.append({"kind": "high_entropy_region", "offset": region["start"], "size": region["size"], "value": region["maximum_entropy"], "status": "CANDIDATE", "note": "high entropy is not evidence of encryption or a specific cipher"})
    if entropy.get("whole_file", {}).get("value", 0) >= 7.2:
        evidence.append({"kind": "high_entropy", "value": entropy["whole_file"]["value"], "status": "CANDIDATE", "note": "high entropy is not evidence of a specific cipher"})
    if any(item.get("name") == "AES S-box prefix" for item in signatures):
        evidence.append({"kind": "known_table_signature", "name": "AES S-box prefix", "status": "CANDIDATE", "note": "table presence alone does not verify algorithm use"})
    return {"status": "CRYPTO_CANDIDATE" if evidence else "NO_CRYPTO_EVIDENCE", "verified_algorithm": None, "evidence": evidence[:500], "statement": "No cipher is verified by high entropy or isolated strings/tables."}


def structure_candidates(pointer_tables: dict[str, Any]) -> list[dict[str, Any]]:
    result = []
    for table in pointer_tables.get("tables", []):
        if table["count"] >= 3:
            result.append({
                "structure": "pointer_table_candidate", "start_offset": f"0x{table['source_offset']:X}",
                "record_width_bytes": table["width_bits"] // 8, "count": table["count"],
                "confidence": table["confidence"], "status": "CANDIDATE",
                "evidence": table["evidence"],
                "interpretation": "Repeated in-file values; record semantics and target type are unknown",
            })
    return result[:256]


def next_actions(report: dict[str, Any]) -> list[dict[str, Any]]:
    actions = []
    decoded = report.get("decoded", {})
    if decoded.get("status") == "NOT_ATTEMPTED":
        supported = [item for item in decoded.get("probes", []) if item.get("implementation") == "SUPPORTED"]
        if supported:
            probe = supported[0]
            actions.append({
                "priority": "HIGH" if probe.get("confidence", 0) >= 0.95 else "MEDIUM",
                "action": f"Run validated {probe['name']} decoder",
                "reason": "A supported decoder matched a recognized stream signature",
                "evidence": probe.get("evidence", []), "offset": 0,
                "confidence": probe.get("confidence", 0),
                "required_tool": "python -m korg_binary_analyzer decode",
            })
    elif decoded.get("status") == "DECODED_VALIDATED":
        outputs = [item.get("output") or item.get("output_directory") for item in decoded.get("results", []) if item.get("status") == "DECODED_VALIDATED"]
        actions.append({
            "priority": "MEDIUM", "action": "Review the validated decoded output",
            "reason": "The decoder completed and independent format integrity checks passed",
            "evidence": [f"Validated output: {output}" for output in outputs],
            "offset": 0, "confidence": 0.99, "required_tool": "format-specific parser / manual review",
        })
    elif decoded.get("status") == "DECODED_UNVERIFIED":
        actions.append({
            "priority": "MEDIUM", "action": "Independently validate the decoded output",
            "reason": "Decoding completed without a strong independent validation result",
            "evidence": [item.get("output", item.get("decoder", "unknown output")) for item in decoded.get("results", [])],
            "offset": 0, "confidence": 0.9, "required_tool": "format parser / checksum verifier",
        })
    elif decoded.get("status") == "ATTEMPTED_FAILED":
        actions.append({
            "priority": "MEDIUM", "action": "Review failed decoder evidence",
            "reason": "A decoder probe was justified but output validation failed",
            "evidence": [item.get("error", "Decoder attempt failed") for item in decoded.get("results", [])],
            "offset": 0, "confidence": 0.8, "required_tool": "manual format validation",
        })
    evidence = report.get("evidence", {})
    candidates = report.get("candidates", {})
    pe = report.get("pe") or evidence.get("pe") or {}
    korg_candidates = report.get("korg_candidates") or candidates.get("korg_relevance", [])
    embedded_objects = report.get("embedded_objects") or evidence.get("embedded_objects", [])
    entropy = report.get("entropy") or evidence.get("entropy", {})
    if pe and pe.get("overlay"):
        overlay = pe["overlay"]
        actions.append({
            "priority": "HIGH", "action": "Inspect PE overlay", "reason": f"Trailing overlay of {overlay['size']} bytes is present",
            "evidence": [f"Overlay starts at 0x{overlay['start']:X}"], "offset": overlay["start"],
            "confidence": 0.98, "required_tool": "hex viewer / signature analysis",
        })
    if korg_candidates:
        item = korg_candidates[0]
        actions.append({
            "priority": "MEDIUM", "action": "Review Korg lexical candidates", "reason": "Korg-related terms are indicators, not format proof",
            "evidence": [f"{len(korg_candidates)} lexical candidate(s)"], "offset": item["offset"],
            "confidence": 0.7, "required_tool": "manual structure analysis / reference samples",
        })
    if embedded_objects:
        item = embedded_objects[0]
        actions.append({
            "priority": "HIGH" if item.get("status") == "BOUNDED_VALIDATED" else "MEDIUM",
            "action": f"Review embedded {item['embedded_signature']} object",
            "reason": "A known signature was observed away from offset zero",
            "evidence": item["evidence"], "offset": item["embedded_start"],
            "confidence": item["confidence"], "required_tool": "extract / format validator",
        })
    if entropy.get("high_entropy_regions"):
        region = entropy["high_entropy_regions"][0]
        actions.append({
            "priority": "MEDIUM", "action": "Compare high-entropy region with format boundaries",
            "reason": "High entropy is a lead only; it does not establish encryption",
            "evidence": [f"{len(entropy['high_entropy_regions'])} region(s), first at 0x{region['start']:X}"],
            "offset": region["start"], "confidence": 0.55, "required_tool": "format-specific decoder / comparative sample",
        })
    if pe.get("entry_point", {}).get("outside_executable_section"):
        actions.append({
            "priority": "HIGH", "action": "Inspect PE entry-point mapping",
            "reason": "Entry point did not map to an executable section",
            "evidence": [f"Entry RVA 0x{pe['entry_point']['rva']:X}"],
            "offset": pe["entry_point"]["file_offset"], "confidence": 0.85,
            "required_tool": "PE loader / disassembler",
        })
    if not actions:
        actions.append({
            "priority": "INFORMATIONAL", "action": "Obtain comparison samples or format documentation",
            "reason": "No strong decoding or structural lead was identified",
            "evidence": ["Analysis did not verify an undocumented Korg format"], "offset": None,
            "confidence": 0.9, "required_tool": "manual reverse engineering",
        })
    rank = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3, "INFORMATIONAL": 4}
    return sorted(actions, key=lambda item: rank.get(item["priority"], 5))[:20]