"""Command line interface for pa_tool."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import (
    __version__,
    container,
    convert,
    detect,
    manager,
    model_convert,
    models,
    pcg,
    report,
    setfolder,
    validate,
    validator,
)
from .status import Result, Status


def _print_result(res: Result, out=None) -> None:
    out = out or sys.stdout
    print(f"\n[{res.name}]  status={res.status.value}  decision={res.decision()}", file=out)
    for f in res.findings:
        line = f"  - {f.key}: {f.value}  ({f.status.value})"
        if f.evidence:
            line += f"  evidence: {f.evidence}"
        if f.note:
            line += f"  note: {f.note}"
        print(line, file=out)
    for e in res.errors:
        print(f"  ! {e}", file=out)


def _native_sound_count() -> int:
    from .native.sounds import load_sound_catalog
    return len(load_sound_catalog()[0])


def _native_fixture_count() -> int:
    from .native.workspace import FIXTURES
    return sum(path.is_file() for path in FIXTURES.iterdir())


# --------------------------------------------------------------------------- commands

def cmd_doctor(args) -> int:
    """Verify the data layer and dependencies load cleanly."""
    res = Result(name="doctor")
    checks = [
        ("models.json", lambda: len(models.canonical_models())),
        ("korg_tokens.json", lambda: len(models.resource_tokens())),
        ("rejections.json", lambda: len(models.hard_limits())),
        ("conversions.json", lambda: len(models.directions())),
        ("pcg_chunks.json", lambda: len(models.pcg_chunks()["chunk_semantics"])),
        ("manager_resources.json", lambda: len(manager.resources())),
        ("factory_names.json", lambda: len(manager.factory_programs())),
        ("PA800_FACTORY_SOUND_LIST_CC00_CC32_PC.txt", _native_sound_count),
        ("pa800_native_fixtures", _native_fixture_count),
        ("model validator profiles", lambda: len(validator.MODEL_PROFILES)),
        ("file validator rules", lambda: len(validator.FILE_RULES)),
    ]
    ok = True
    for name, fn in checks:
        try:
            n = fn()
            res.add(name, f"{n} record(s)", Status.VERIFIED, evidence=f"data/{name}")
        except Exception as exc:  # noqa: BLE001 - doctor must report, not crash
            res.add(name, f"ERROR: {exc}", Status.UNKNOWN)
            ok = False

    magic = models.container_magic()
    res.add("container magic", magic.hex(" "), Status.VERIFIED,
            evidence="korg_tokens.json container_magic.value_hex")
    res.add("python", sys.version.split()[0], Status.VERIFIED)

    _print_result(res)
    return 0 if ok else 1


def cmd_models(args) -> int:
    """Print the canonical model table with RAM and import layer."""
    res = Result(name="models")
    rows = []
    for m in models.canonical_models():
        ram = models.ram_for(m.name)
        sub = models.sus_for(m.name)
        if ram.get("size_mb") is not None:
            ram_text = f"{ram['size_mb']} MB"
        elif ram.get("ambiguous"):
            ram_text = "ambig " + "/".join(str(c) for c in ram["candidates"])
        else:
            ram_text = "UNKNOWN"
        rows.append((m.ordinal, m.name, ram_text, ram.get("status"), sub))
        ram_status_text = ram.get("status", Status.UNKNOWN.value)
        ram_status = Status(ram_status_text) if ram_status_text in Status.__members__ else Status.UNKNOWN
        if ram.get("ambiguous"):
            model_note = "two documented base RAM sizes; which applies is not proven"
        elif sub:
            model_note = ""
        elif m.name.upper() == "PA5X":
            model_note = "no OpenSound SUB; SOUND import is explicitly refused @0x002DD968"
        else:
            model_note = "no own OpenSound SUB; model may share another handler"
        res.add(
            f"{m.ordinal}:{m.name}",
            f"RAM={ram_text}, sub={sub or 'none (own SUB absent)'}",
            ram_status,
            evidence=f"canonical order table_a={m.table_a} table_b={m.table_b}",
            note=model_note,
        )
    print(f"{'#':>2}  {'model':<8} {'RAM':>16}  {'import SUB':<12} status")
    for o, name, ram, st, sub in rows:
        print(f"{o:>2}  {name:<8} {ram:>16}  {(sub or '-'):<12} {st}")
    res.add("canonical_order_status", models.models_data()["canonical_order"]["status"],
            Status.VERIFIED, evidence=models.models_data()["canonical_order"]["evidence"])
    res.add("enum_values", "HIGH, not VERIFIED", Status.HIGH,
            evidence="models.json canonical_order.note")
    for variant in models.variants():
        status_text = variant.get("status", "UNKNOWN")
        status = (Status.HIGH if status_text.startswith("HIGH") else
                  Status.VERIFIED if status_text.startswith("VERIFIED") else Status.UNKNOWN)
        res.add(f"variant:{variant['name']}", variant.get("meaning", "meaning unknown"), status,
                evidence=variant.get("evidence", variant.get("evidence_offset", "")),
                note=variant.get("note", ""))
    _print_result(res)
    return 0


def cmd_conversions(args) -> int:
    """Print the conversion matrix with status per direction."""
    res = Result(name="conversions")
    print(f"{'direction':<18} {'exists':<8} status       evidence")
    for d in models.directions():
        exists = {True: "yes", False: "no", None: "unknown"}[d.get("exists")]
        ev = d.get("offset") or d.get("module") or d.get("offsets") or ""
        print(f"{d['id']:<18} {exists:<8} {d['status']:<12} {ev}")
        res.add(d["id"], f"exists={exists}", Status(d["status"]) if d["status"] in
                ("VERIFIED", "UNKNOWN", "NEPOTVRDENO", "HIGH") else Status.NEPOTVRDENO,
                evidence=str(ev), note=d.get("note", ""))
    kf = models.key_finding()
    res.add("key_finding", kf["conclusion"], Status.VERIFIED, evidence=kf["evidence"])
    _print_result(res)
    return 0


def cmd_detect(args) -> int:
    prof = detect.detect(args.path)
    res = Result(name=f"detect {Path(args.path).name}")
    res.add("kind", prof.kind, prof.status if prof.status is not Status.UNKNOWN else Status.UNKNOWN,
            evidence="; ".join(prof.evidence) or "no positive marker found")
    res.add("model", prof.model or "UNKNOWN", prof.model_status,
            evidence="OpenSound SUB grouping (PA_MANAGER_RULES §0.1)" if prof.model else
                     "not provable from these bytes")
    for n in prof.notes:
        res.add("note", n, Status.NEPOTVRDENO)
    if prof.container:
        res.add("container_status", prof.container.status.value, prof.container.status,
                evidence=f"KORF x{len(prof.container.chain.korf_offsets)}, "
                         f"KEND x{len(prof.container.chain.kend_offsets)}")
    _print_result(res)
    if args.json:
        print(json.dumps(prof.to_dict(), indent=2, ensure_ascii=False))
    return 0


def cmd_pcg(args) -> int:
    """Classify a .PCG dialect and report evidence-backed chunk meanings."""
    rep = pcg.classify_pcg(args.path)
    res = Result(name=f"pcg {Path(args.path).name}")
    if rep.error:
        res.add("error", rep.error, Status.UNKNOWN)
        _print_result(res)
        return 1
    res.add("dialect", rep.dialect, rep.status,
            evidence="; ".join(rep.evidence), note=rep.label)
    res.add("size", rep.size, Status.VERIFIED)
    if rep.declared_length is not None:
        length_status = Status.VERIFIED if rep.declared_ok else Status.NEPOTVRDENO
        res.add("declared_length", rep.declared_length, length_status,
                evidence=f"file_size-8={rep.size - 8}",
                note="match" if rep.declared_ok else "mismatch: fragment or aggregate possible")
    for token, offsets in rep.token_offsets.items():
        res.add(f"token:{token}", f"{len(offsets)} occurrence(s)", Status.VERIFIED,
                evidence=", ".join(hex(o) for o in offsets[:16]),
                note="token occurrence only; not by itself a structural parse")
    for token in ("PRG1", "PBK1", "RLP1"):
        meaning = pcg.chunk_semantics(token)
        if meaning:
            status = Status(meaning["status"])
            res.add(f"meaning:{token}", meaning["meaning"], status,
                    evidence=meaning["source"], note=meaning["format"])
    for note in rep.notes:
        note_status = (Status.HIGH if note.startswith("PBK1/PRG1 absent")
                       else Status.NEPOTVRDENO)
        res.add("note", note, note_status)
    _print_result(res)
    if args.json:
        print(json.dumps(rep.to_dict(), indent=2, ensure_ascii=False))
    return 0


def cmd_validate(args) -> int:
    results = []
    p = Path(args.path)
    if p.is_file():
        results.append(validate.check_container(p))
    if args.model:
        results.append(validate.check_ram(args.model, args.required_ram_mb))
        results.append(validate.check_osc_limit(args.model))
        d = validate.check_pa5x_sound_import(args.model)
        r = Result(name=f"PA5X sound import ({args.model})")
        r.add("decision", d.verdict, Status.VERIFIED, evidence=d.evidence, note=d.reason)
        results.append(r)
    if args.locked:
        d = validate.check_locked(args.locked)
        r = Result(name=f"locked ({args.locked})")
        r.add("decision", d.verdict, Status.VERIFIED, evidence=d.evidence,
              note=d.reason + (f" — {d.error_text}" if d.error_text else ""))
        results.append(r)
    if args.unsupported:
        d = validate.check_unsupported(args.unsupported)
        r = Result(name=f"unsupported ({args.unsupported})")
        r.add("decision", d.verdict, Status.VERIFIED, evidence=d.evidence,
              note=d.reason + (f" — {d.error_text}" if d.error_text else ""))
        results.append(r)

    if not results:
        print("nothing to validate: supply a file path and/or --model/--locked/--unsupported",
              file=sys.stderr)
        return 2

    for r in results:
        _print_result(r)
    if args.out:
        md, js = report.save_report(args.out, f"validate_{p.stem}", "pa_tool validate", results)
        print(f"\nreport: {md}\n        {js}")
    return 0


def cmd_validators(args) -> int:
    """List complete model/file validator coverage."""
    coverage = validator.validator_coverage()
    payload = {
        "coverage": coverage,
        "models": [
            profile.to_dict()
            for profile in validator.MODEL_PROFILES.values()
        ],
        "file_rules": [
            rule.to_dict()
            for rule in validator.FILE_RULES.values()
        ],
    }
    print(
        f"validators: {coverage['models']} model profile(s), "
        f"{coverage['file_rules']} file rule(s), "
        f"{coverage['pair_cells']} model-pair/file matrix cells"
    )
    if args.models:
        print("\nMODELS")
        print(f"{'model':<18} {'canonical':<10} {'RAM':<14} {'format group':<32} status")
        for profile in validator.MODEL_PROFILES.values():
            ram = (
                str(profile.ram_mb)
                if profile.ram_mb is not None
                else "/".join(str(value) for value in profile.ram_candidates) or "UNKNOWN"
            )
            print(
                f"{profile.name:<18} {str(profile.canonical):<10} {ram:<14} "
                f"{profile.format_group:<32} {profile.status}"
            )
    if args.files:
        print("\nFILE RULES")
        print(f"{'ext':<7} {'kind':<22} {'structure':<20} {'import':<7} {'export':<7} status")
        for extension, rule in sorted(validator.FILE_RULES.items()):
            print(
                f"{extension:<7} {rule.kind:<22} {rule.structure:<20} "
                f"{str(rule.import_known):<7} {str(rule.export_known):<7} {rule.status}"
            )
    if args.json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0


def _print_validation_tree(rep, indent: int = 0) -> None:
    prefix = "  " * indent
    size = "-" if rep.size is None else str(rep.size)
    print(
        f"{prefix}{rep.decision:<7} {rep.relative_path} "
        f"[{rep.kind or 'unknown'}; {size} B]"
    )
    for finding in rep.findings:
        if finding.verdict in {validator.FAIL, validator.UNKNOWN}:
            print(
                f"{prefix}  - {finding.verdict} {finding.code}: {finding.message}"
            )
    for child in rep.children:
        _print_validation_tree(child, indent + 1)


def cmd_validate_all(args) -> int:
    """Validate one file or every file in a tree against one model profile."""
    try:
        validation = validator.validate_path(
            args.path,
            target_model=args.model,
            required_ram_mb=args.required_ram_mb,
            deep=not args.shallow,
            midi_workflow=args.midi_workflow,
        )
    except (OSError, ValueError) as exc:
        print(f"validation failed: {exc}", file=sys.stderr)
        return 2
    _print_validation_tree(validation)
    if args.out:
        try:
            written = validator.save_report(validation, args.out)
        except (OSError, ValueError) as exc:
            print(f"cannot write validation report: {exc}", file=sys.stderr)
            return 2
        print(f"report: {written}")
    if args.json:
        print(json.dumps(validation.to_dict(), indent=2, ensure_ascii=False))
    return 1 if validation.decision == validator.FAIL else 0


def cmd_model_matrix(args) -> int:
    """Print complete or filtered model compatibility matrix."""
    extension = args.extension.upper()
    if not extension.startswith("."):
        extension = "." + extension
    if args.source_model and args.target_model:
        cells = [
            validator.compatibility(args.source_model, args.target_model, extension)
        ]
    elif args.source_model or args.target_model:
        print("supply both --source-model and --target-model, or neither", file=sys.stderr)
        return 2
    else:
        cells = validator.compatibility_matrix(extension)
    print(f"compatibility matrix for {extension}: {len(cells)} cell(s)")
    print(f"{'source':<18} {'target':<18} {'level':<14} {'run':<5} reason")
    for cell in cells:
        print(
            f"{cell.source_model:<18} {cell.target_model:<18} "
            f"{cell.level:<14} {str(cell.executable):<5} {cell.reason}"
        )
    payload = {
        "extension": extension,
        "cells": [cell.to_dict() for cell in cells],
    }
    if args.out:
        target = Path(args.out)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        print(f"report: {target}")
    if args.json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0


def cmd_model_convert(args) -> int:
    """Plan or execute a protected model conversion/preserve package."""
    if args.mapping_template:
        try:
            target = model_convert.save_mapping_template(
                args.source_model,
                args.target_model,
                args.mapping_template,
            )
        except (OSError, ValueError, model_convert.ConversionRefused) as exc:
            print(f"cannot create mapping template: {exc}", file=sys.stderr)
            return 2
        print(f"mapping template: {target}")
        return 0

    catalog = model_convert.CatalogOptions()
    if args.mapping:
        try:
            map_source, map_target = model_convert.mapping_pair(args.mapping)
            source_profile = validator.resolve_model(args.source_model)
            target_profile = validator.resolve_model(args.target_model)
            if source_profile is None or target_profile is None:
                raise model_convert.ConversionRefused(
                    "source/target model is not in the registry"
                )
            map_source_profile = validator.resolve_model(map_source)
            map_target_profile = validator.resolve_model(map_target)
            if (
                map_source_profile is None
                or map_target_profile is None
                or map_source_profile.key != source_profile.key
                or map_target_profile.key != target_profile.key
            ):
                raise model_convert.ConversionRefused(
                    "mapping model pair does not match command source/target models"
                )
            catalog = model_convert.load_mapping(args.mapping)
        except (
            OSError,
            ValueError,
            json.JSONDecodeError,
            model_convert.ConversionRefused,
        ) as exc:
            print(f"invalid mapping: {exc}", file=sys.stderr)
            return 2
    options = model_convert.ConversionOptions(
        source_model=args.source_model,
        target_model=args.target_model,
        execute=args.execute,
        preserve_unsupported=not args.no_preserve,
        canonicalize_wave=args.canonicalize_wave,
        catalog=catalog,
    )
    try:
        conversion = model_convert.convert_path(args.source, args.target, options)
    except (OSError, ValueError, model_convert.ConversionRefused) as exc:
        print(f"conversion refused: {exc}", file=sys.stderr)
        return 3
    print(
        f"decision={conversion.decision} executed={conversion.executed} "
        f"files={len(conversion.files)}"
    )
    for item in conversion.files:
        print(
            f"  {item.status:<14} {item.operation:<26} {item.relative_path}"
        )
        for note in item.notes:
            print(f"    note: {note}")
    if args.report:
        try:
            target = model_convert.save_conversion_report(conversion, args.report)
        except (OSError, ValueError, model_convert.ConversionRefused) as exc:
            print(f"cannot write conversion report: {exc}", file=sys.stderr)
            return 2
        print(f"report: {target}")
    if args.json:
        print(json.dumps(conversion.to_dict(), indent=2, ensure_ascii=False))
    return 0 if conversion.decision in {"PASS_EXACT", "PRESERVE_ONLY", "PARTIAL"} else 4


def cmd_inventory(args) -> int:
    rep = setfolder.inventory_set(args.path)
    res = Result(name=f"inventory {rep.name}")
    res.add("metadir", "present" if rep.metadir else "absent",
            Status.VERIFIED if rep.metadir else Status.NEPOTVRDENO)
    res.add("categories", ", ".join(f"{c.name}({c.count})" for c in rep.categories) or "none",
            Status.VERIFIED if rep.categories else Status.UNKNOWN)
    res.add("files", rep.lo_files, Status.VERIFIED)
    res.add("total_bytes", f"{rep.total_bytes:,}", Status.VERIFIED)
    res.add("by_extension", json.dumps(rep.by_extension), Status.VERIFIED)
    for n in rep.notes:
        res.add("note", n, Status.NEPOTVRDENO)
    _print_result(res)
    if args.json:
        print(json.dumps(rep.to_dict(), indent=2, ensure_ascii=False))
    return 0


def cmd_metadir(args) -> int:
    info = setfolder.parse_metadir(args.path)
    res = Result(name=f"metadir {Path(args.path).name}")
    if not info.get("found"):
        res.add("found", "no", Status.VERIFIED, evidence=info["path"])
        _print_result(res)
        return 0
    if "error" in info:
        res.add("found", "yes but unreadable", Status.UNKNOWN, evidence=info["error"])
        _print_result(res)
        return 0
    res.add("found", "yes", Status.VERIFIED, evidence=info["path"])
    res.add("subdirs", info["subdir_count"], Status.VERIFIED,
            evidence="metadir.json schema: subdirs[] = [{dir,id?}] (verified on 50 local files)")
    res.add("files", info["file_count"], Status.VERIFIED,
            evidence="metadir.json schema: files[] = [{file,icon?}]")
    if info.get("default_file_icon"):
        res.add("defaultFileIcon", info["default_file_icon"], Status.VERIFIED)
    res.add("percent_decoding", "%2F decoded to '/'", Status.VERIFIED,
            evidence="verified: '6%2F8 Ballad.stg' -> '6/8 Ballad.stg'")
    _print_result(res)
    if args.json and info.get("files_decoded"):
        print(json.dumps(info["files_decoded"], indent=2, ensure_ascii=False))
    return 0


def cmd_manager(args) -> int:
    """Inventory of the Pa Manager binary itself and what was recovered from it."""
    res = Result(name="pa manager resources")
    sb = manager.source_binary()
    pk = manager.packer_info()
    mp = manager.mapping_summary()
    hr = manager.how_recovered()

    res.add("source binary", f"{sb.get('name')} {sb.get('size'):,} B", Status.VERIFIED,
            evidence=f"sha256 {sb.get('sha256')}")
    res.add("machine", f"{sb.get('machine')} / {sb.get('subsystem')}", Status.VERIFIED)
    res.add("packer", ", ".join(pk.get("value", [])) or "none", Status.VERIFIED,
            evidence=json.dumps(pk.get("evidence", {})), note=pk.get("note", ""))
    res.add("resources total", mp.get("total"), Status.VERIFIED)
    res.add("from file", mp.get("from_file"), Status.VERIFIED,
            evidence=hr.get("from_file", ""))
    res.add("from image", mp.get("from_image"), Status.VERIFIED,
            evidence=hr.get("from_image", ""))
    res.add("unmapped", mp.get("unmapped"), Status.VERIFIED,
            note="0 = svaki resurs je oporavljen" if mp.get("unmapped") == 0 else "")

    if args.names:
        prog = manager.program_table()
        res.add("name table records", prog.get("records"), Status.VERIFIED,
                evidence=prog.get("evidence", ""))
        for s in manager.program_sections():
            res.add(
                f"section {s['section']}",
                f"{s['records']} records, idx {s['index_from']}..{s['index_to']}",
                Status.VERIFIED,
                evidence=f"{s['first_name']!r} .. {s['last_name']!r}",
            )
        dk = manager.drum_names()
        res.add("drum/multisample table", f"{dk.get('records')} records",
                manager.drum_names_status(), evidence=dk.get("evidence", ""),
                note=dk.get("note", ""))
        dec = manager.drum_decode_detail()
        if dec:
            res.add("oc32 decode",
                    f"{dec.get('capacity'):,} B declared -> {dec.get('capacity'):,} B decoded",
                    Status.VERIFIED,
                    evidence=f"trailer {dec.get('trailer_bytes')} B, XOR computed "
                             f"{dec.get('xor_computed')} vs file {dec.get('xor_in_file')} "
                             f"-> match={dec.get('xor_match')}")
        res.add("drum table layout",
                f"{manager.drum_table_layout().get('record_size')} B stride, "
                f"{manager.drum_table_layout().get('name_field_size')} B name",
                Status.VERIFIED,
                evidence=f"{manager.drum_table_layout().get('table_start')} .. "
                         f"{manager.drum_table_layout().get('table_end')}")
        res.add("distinct drum names", dk.get("distinct_names"), Status.VERIFIED,
                note=f"{dk.get('named_records')} named, {dk.get('unnamed_slots')} unnamed slots")
        res.add("drum sample names", dk.get("sample_name_count"), Status.NEPOTVRDENO,
                note="sire region uzoraka; izvuceno kao ASCII nizovi (slabija metoda)")
        res.add("container templates", len(manager.container_templates()), Status.VERIFIED)
        res.add("embedded modules", len(manager.embedded_modules()), Status.VERIFIED,
                note="; ".join(f"{m['resource_file'].split('_')[2]}={m['machine']}"
                               for m in manager.embedded_modules()))
        ocd = manager.oc_decode_info()
        if ocd:
            res.add("oc decode method", ocd.get("note", ""), Status.VERIFIED,
                    evidence=ocd.get("evidence", ""))

    if args.list_names:
        for e in manager.factory_programs():
            print(f"  {e['section']}  idx={e['index']:>5}  flag={e['flag']}  {e['name']}")
    if args.find:
        hits = manager.find_factory_name(args.find)
        if not hits:
            hits = manager.find_drum_name(args.find)
        for h in hits:
            label = h.get("name") or h.get("text")
            idx = h.get("index", h.get("offset"))
            print(f"  {label}  ({idx})")
        print(f"  {len(hits)} match(es)")
    if args.list_drum:
        for e in manager.drum_entries():
            if e["name"]:
                print(f"  {e['offset']}  {e['name']}")
    if args.json:
        print(json.dumps({"resources": [r.__dict__ for r in manager.resources()]},
                         indent=2, ensure_ascii=False))

    _print_result(res)
    return 0


def cmd_convert(args) -> int:
    """Plan (and optionally perform) a conversion. Refuses before acting."""
    if getattr(args, "assets", False):
        res = Result(name="external assets")
        seen: set[str] = set()
        resolved = convert.resolve_paths()
        for key, val in resolved.items():
            seen.add(key)
            res.add(key, str(val) if val else "NOT FOUND",
                    Status.VERIFIED if val else Status.UNKNOWN)
        if any(v is None for v in resolved.values()):
            res.add("hint", "set PA_TOOL_ASSET_ROOT (or PA_TOOL_NATIVE_PARENT / "
                    "PA_TOOL_SETCONVERT_PARENT) to point at your copy",
                    Status.UNKNOWN,
                    note="os.pathsep-separated; probed before the built-in defaults")
        sc_mod, sc_notes = convert.load_setconvert()
        for key, val in sc_notes.items():
            if key in seen:
                continue          # resolve_paths already reported this one
            seen.add(key)
            res.add(key, str(val), Status.VERIFIED if val else Status.UNKNOWN)
        if sc_mod is not None:
            res.add("setconvert loaded", "yes", Status.VERIFIED)
        elif "error" in sc_notes:
            res.add("setconvert loaded", "no", Status.UNKNOWN,
                    evidence=sc_notes["error"])
        fams = convert.sty_families()
        res.add("sty families", len(fams),
                Status.VERIFIED if fams else Status.UNKNOWN)
        for fam in fams:
            res.add(f"family:{fam['id']}",
                    f"{fam['label']} ({len(fam['models'])} models, {fam['name_table']})",
                    Status.VERIFIED)
        _print_result(res)
        return 0

    if getattr(args, "fingerprint", None):
        info = convert.fingerprint_sty(args.fingerprint)
        res = Result(name=f"style fingerprint ({Path(args.fingerprint).name})")
        if not info.get("ok"):
            res.add("error", info.get("error", "unknown"), Status.UNKNOWN)
            _print_result(res)
            return 1
        res.add("family", info["family"] or "unknown",
                Status.VERIFIED if info["family"] else Status.UNKNOWN,
                note=info.get("family_label", ""))
        res.add("styles", info["styles"], Status.VERIFIED)
        res.add("sts", info["sts"], Status.VERIFIED)
        res.add("family_votes", json.dumps(info["family_votes"]), Status.VERIFIED)
        res.add("dialects", json.dumps(info["dialect_counts"]),
                Status.VERIFIED if info["dialect_counts"] else Status.UNKNOWN)
        res.add("kbeg_chain", "ok" if info["kbeg_ok"] else "broken",
                Status.VERIFIED if info["kbeg_ok"] else Status.NEPOTVRDENO)
        res.add("mixed", "yes" if info["mixed"] else "no", Status.VERIFIED)
        for e in info["errors"]:
            res.add("error", e, Status.UNKNOWN)
        _print_result(res)
        return 0

    if getattr(args, "set_family", None):
        info = convert.set_families_for_folder(args.set_family)
        res = Result(name=f"set family ({Path(args.set_family).name})")
        if not info.get("ok"):
            res.add("error", info.get("error", "unknown"), Status.UNKNOWN)
            _print_result(res)
            return 1
        res.add("family", info["family"] or "unknown",
                Status.VERIFIED if info["family"] else Status.UNKNOWN,
                note=info.get("family_label", ""))
        res.add("family_votes", json.dumps(info["family_votes"]), Status.VERIFIED)
        res.add("style_files", info["sty_files"], Status.VERIFIED)
        res.add("folders", ", ".join(info["folders"]) or "none",
                Status.VERIFIED if info["folders"] else Status.UNKNOWN)
        _print_result(res)
        return 0

    if getattr(args, "matrix", False):
        res = Result(name="conversion matrix")
        print(f"{'direction':<18} {'exists':<8} {'status':<12} evidence")
        for key, appl, status, ev in convert.conversion_matrix_rows():
            print(f"{key:<18} {appl:<8} {status:<12} {ev}")
            res.add(key, appl, Status(status) if status in Status.__members__
                    else Status.NEPOTVRDENO, evidence=ev)
        _print_result(res)
        return 0

    if not args.source or not args.direction:
        print("convert requires <source> and --direction (or use --matrix/--assets)",
              file=sys.stderr)
        return 2

    # the direction must exist before anything else is even considered
    if convert.direction(args.direction) is None:
        print(f"unknown conversion direction: {args.direction}", file=sys.stderr)
        print(f"known: {', '.join(convert.supported_directions())}", file=sys.stderr)
        return 2

    plan = convert.plan_conversion(
        args.source, args.target or (str(args.source) + "_conv"), args.direction,
        kind=args.kind, model=args.model,
        source_has_factory_rom=args.factory_rom, source_is_locked=args.locked,
    )
    _print_result(plan.to_result())

    if plan.blocked:
        return 3
    if args.dry_run:
        print("\ndry run: nothing written")
        return 0
    print(
        "\nlegacy 'convert' command is plan-only; use 'model-convert' for the "
        "guarded executable subset and see MODEL_VALIDATOR_CONVERTER.md"
    )
    return 0


def cmd_oc(args) -> int:
    """Decode the first OC31/OC32 payload in a KORG container."""
    from pathlib import Path as _P
    p = _P(args.path)
    res = Result(name=f"oc decode ({p.name})")
    data = p.read_bytes()
    codec = container.load_codec()
    res.add("codec", "CBarc loaded" if codec else "not available",
            Status.VERIFIED if codec else Status.UNKNOWN,
            evidence="pa_tool/native/codec.py (bounded bundled decoder)")
    offsets = []
    for magic in (b"OC20", b"OC31", b"OC32"):
        start = 0
        while True:
            pos = data.find(magic, start)
            if pos < 0:
                break
            offsets.append((pos, magic.decode()))
            start = pos + 1
    if not offsets:
        res.add("payloads", "none found", Status.UNKNOWN,
                note="no OC20/OC31/OC32 magic in this file")
        _print_result(res)
        return 0
    res.add("payloads", ", ".join(f"{m}@{hex(o)}" for o, m in offsets), Status.VERIFIED)

    pos, magic = offsets[0]
    if magic not in ("OC31", "OC32"):
        res.add("decoded", "skipped", Status.UNKNOWN,
                note=f"{magic} uses a different paging path (OC20 pages)")
        _print_result(res)
        return 0

    result = container.decode_oc_payload(data, pos, codec)
    st = Status.VERIFIED if result.ok and result.xor_match else Status.NEPOTVRDENO
    res.add("declared_size", result.capacity, Status.VERIFIED, evidence=f"u32LE @{hex(pos + 4)}")
    if result.decoded is not None:
        res.add("decoded_size", len(result.decoded), Status.VERIFIED)
        res.add("trailer_bytes", result.trailer_bytes, Status.VERIFIED,
                note="KORG trailer the CBarc stream does not account for")
        res.add("xor_check", f"{result.xor_computed} vs {result.xor_in_file} "
                f"-> {'match' if result.xor_match else 'no match'}",
                Status.VERIFIED if result.xor_match else Status.NEPOTVRDENO)
    if result.error:
        res.add("error", result.error, Status.UNKNOWN)
    res.add("decision", "PASS" if result.ok else "FAIL", st)

    if result.decoded is not None and args.out:
        out_dir = _P(args.out)
        out_dir.mkdir(parents=True, exist_ok=True)
        target = out_dir / (p.stem + "_decoded.bin")
        target.write_bytes(result.decoded)
        res.add("written", str(target), Status.VERIFIED)
    _print_result(res)
    return 0


def cmd_native_bank(args) -> int:
    """Strictly parse one native PA800 resource bank without modifying it."""
    from .native import Bank, FormatError, load_sound_catalog
    from .native.codec import MAX_FILE

    path = Path(args.path)
    res = Result(name=f"native bank ({path.name})")
    try:
        if path.stat().st_size > MAX_FILE:
            raise FormatError("Fajl prelazi limit od 64 MiB")
        _, lookup = load_sound_catalog()
        bank = Bank(path.name, path.read_bytes(), lookup)
    except (OSError, FormatError, ValueError) as exc:
        res.add("decision", "FAIL", Status.VERIFIED, note=str(exc))
        _print_result(res)
        return 1

    info = bank.base_info()
    res.add("decision", "PASS", Status.VERIFIED,
            evidence="bounded OC20/root/type-05/FE/KBEG-KEND validation")
    res.add("kind", info["kind"], Status.VERIFIED)
    res.add("envelope", info["envelope"], Status.VERIFIED)
    res.add("source_sha256", info["sha256"], Status.VERIFIED)
    res.add("bytes", info["bytes"], Status.VERIFIED)
    res.add("decoded_bytes", info["decodedBytes"], Status.VERIFIED)
    res.add("pages", info["pages"], Status.VERIFIED)
    res.add("resources", info["resources"], Status.VERIFIED)
    res.add("types", json.dumps(info["types"], sort_keys=True), Status.VERIFIED)

    listing = bank.resource_listing()
    detail = None
    if args.resource is not None:
        index = args.resource - 1
        if index < 0:
            print("--resource is 1-based and must be at least 1", file=sys.stderr)
            return 2
        try:
            detail = bank.resource_detail(index)
        except (FormatError, ValueError) as exc:
            res.add("resource", f"#{args.resource} FAIL", Status.UNKNOWN, note=str(exc))
            _print_result(res)
            return 1
        coverage = detail.get("coverage", "STRUCTURED_READ")
        status = Status.NEPOTVRDENO if coverage == "PRESERVE_ONLY" else Status.VERIFIED
        res.add("resource", f"#{args.resource} {detail['name']}", status,
                evidence=detail.get("evidence", "validated framing"),
                note=detail.get("note", ""))
    _print_result(res)

    if args.list:
        for row in listing:
            print(f"  {row['slot']:>4}  {row['family']:<15} {row['codec']:<5} "
                  f"{row['bytes']:>8} B  {row['name']}")
    if args.json:
        print(json.dumps({"bank": info, "resources": listing, "detail": detail},
                         indent=2, ensure_ascii=False))
    return 0


def cmd_native_set(args) -> int:
    """Inventory and optionally deep-audit a bounded SET ZIP."""
    from .native import FormatError
    from .native.codec import MAX_FILE
    from .native.workspace import UserError, Workspace

    path = Path(args.path)
    res = Result(name=f"native SET ({path.name})")
    try:
        if path.stat().st_size > MAX_FILE:
            raise UserError("Prevelik fajl (limit 64 MiB)")
        workspace = Workspace(load_demo=False)
        overview = workspace.import_file(path.name, path.read_bytes())
    except (OSError, UserError, FormatError, ValueError) as exc:
        res.add("decision", "FAIL", Status.VERIFIED, note=str(exc))
        _print_result(res)
        return 1

    summary = overview.get("archiveSummary")
    if summary is None:
        res.add("decision", "FAIL", Status.VERIFIED,
                note="native-set expects a ZIP archive; use native-bank for one bank")
        _print_result(res)
        return 1
    res.add("decision", "PASS", Status.VERIFIED,
            evidence="safe ZIP paths + CRC/SHA-256 streamed inventory")
    res.add("archive_sha256", summary["archiveSha256"], Status.VERIFIED)
    res.add("files", summary["fileCount"], Status.VERIFIED)
    res.add("expanded_bytes", summary["expandedBytes"], Status.VERIFIED)
    res.add("parsed_banks", summary["parsedBankCount"], Status.VERIFIED)
    res.add("extensions", json.dumps(summary["extensions"], sort_keys=True), Status.VERIFIED)
    if summary["encryptedFiles"]:
        res.add("encrypted_files", summary["encryptedFiles"], Status.NEPOTVRDENO,
                note="names/sizes only; encrypted content was not decoded")
    if summary["bkpPresent"]:
        res.add("BKP", "present, inventory only", Status.NEPOTVRDENO,
                note="not decoded and never restored")

    payload: dict[str, object] = {"overview": overview}
    audit = None
    if args.deep_audit:
        audit = workspace.audit()
        payload["audit"] = audit
        audit_status = (Status.VERIFIED if audit["status"] == "PASS"
                        else Status.NEPOTVRDENO)
        res.add("deep_audit", audit["status"], audit_status,
                evidence=f"{audit['checkedResources']} resource(s), "
                         f"{len(audit['failures'])} failure(s), "
                         f"{audit['preserveOnly']} preserve-only")
    if args.graph:
        graph = workspace.graph()
        payload["graph"] = graph
        res.add("selector_graph", f"{graph['candidateUnique']} unique candidate(s), "
                f"{graph['notInArchivePcg']} absent from archive PCG",
                Status.NEPOTVRDENO,
                note="candidate graph only; not oscillator/KMP/PCM closure or remap proof")
    if args.manifest:
        payload["manifest"] = workspace.manifest()
    payload["report"] = workspace.report()

    if args.out:
        target = Path(args.out)
        try:
            if path.resolve() == target.resolve():
                raise ValueError("refusing to overwrite the source archive")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps(payload, indent=2, ensure_ascii=False),
                              encoding="utf-8")
            res.add("report_written", str(target), Status.VERIFIED)
        except (OSError, ValueError) as exc:
            res.add("report_write", "FAIL", Status.UNKNOWN, note=str(exc))
            _print_result(res)
            return 1

    _print_result(res)
    if args.json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 1 if audit and audit["failures"] else 0


def cmd_native_sounds(args) -> int:
    """Search the exact 1,003-selector PA800 factory sound catalog."""
    from .native.sounds import load_sound_catalog

    entries, _ = load_sound_catalog()
    if not 1 <= args.limit <= 1003:
        print("--limit must be between 1 and 1003", file=sys.stderr)
        return 2
    query = (args.find or "").strip().lower()
    matches = [row for row in entries if not query or query in row["name"].lower()
               or query in f"{row['msb']}.{row['lsb']}.{row['pc']}"]
    shown = matches[:args.limit]
    for row in shown:
        print(f"{row['msb']:>3}.{row['lsb']:>3}.{row['pc']:>3}  {row['name']}")
    print(f"{len(matches)} match(es); showing {len(shown)} of 1,003 proven bindings")
    if args.json:
        print(json.dumps({"total": len(matches), "items": shown},
                         indent=2, ensure_ascii=False))
    return 0


def cmd_web(args) -> int:
    """Run the integrated local read-only browser interface."""
    from .native.web import serve
    serve(args.host, args.port)
    return 0


def cmd_status(args) -> int:
    """Print what is implemented and what is still open.

    The two groups are separate Results on purpose: the health of what exists is
    one question, the backlog is another. Mixing them made the whole command report
    UNKNOWN even for a fully working tool.
    """
    implemented = Result(name="implemented")
    for item in (
        "detect (file/folder type, container fingerprint)",
        "container (magic, declared length, KORF/KBEG/KEND, compression magics)",
        "container.oc (bundled bounded OC31/OC32 CBarc decode + XOR check)",
        "native-bank (OC20 pages/checksum, type-05, FE/KBEG/KEND, sparse Style/PAD, CV/PRF)",
        "native-set (safe whole-ZIP CRC/SHA inventory, deep audit, original preservation)",
        "native dependency graph (Style TrackInfo -> USER01/02/DK PCG candidates)",
        "native sound catalog (1,003 exact PA800 CC00.CC32.PC bindings)",
        "web UI (Style/PAD/Performance/Sound/Global/archive/validation read-only workflows)",
        "pcg (PA KORF vs workstation KORG/PCG1 dialect classification)",
        "setfolder (inventory, metadir parsing, hash manifest)",
        "validate (hard limits, RAM, locked, content class, canonical sample format)",
        "validator (18 model profiles, 32 file rules, recursive per-file reports)",
        "model matrix (10,368 source/target/file compatibility decisions)",
        "model-convert (staging/hash copy, canonical PCM WAVE, fixed KORF catalog fields)",
        "manager (Pa Manager resource inventory + structured factory name table)",
        "convert.plan (conversion planning + refusal before acting)",
        "convert.assets (setconvert + pa800_native integration, 6 STY families)",
        "convert.fingerprint (real .STY family/dialect detection)",
        "report (MD + JSON, every row with status + evidence)",
        "status (VERIFIED / NEPOTVRDENO / UNKNOWN discipline)",
    ):
        implemented.add("implemented", item, Status.VERIFIED)

    open_items = Result(name="open / not implemented")
    for key, what in (
        ("convert_payloads", "remaining native payload transforms (STY family sequence, MG<->STD, v5->v4)"),
        ("remap", "complete STS/PRF -> PCG -> oscillator -> KMP -> PCM model remap"),
        ("native_write", "instrument-safe OC20/STY/PCG/KMP/PCM/SET writer and hardware acceptance"),
        ("bkp", "BKP pack/restore contract and destructive hardware validation"),
        ("kst_edit", ".KST read/write editing"),
        ("merge", "MERGE operation (flow not proven)"),
        ("osc_limit", "OSC limit per model (proven undecidable from the binary)"),
    ):
        open_items.add(f"pending:{key}", what, Status.UNKNOWN,
                       note="planned, or proven undecidable; not implemented")

    _print_result(implemented)
    _print_result(open_items)
    return 0


# --------------------------------------------------------------------------- parser

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="pa_tool",
        description=(
            "Own PA Manager workbench for KORG PA formats "
            "(read-only inputs; guarded new outputs only)."
        ),
    )
    p.add_argument("--version", action="version", version=f"pa_tool {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    sp = sub.add_parser("doctor", help="verify the data layer loads")
    sp.set_defaults(func=cmd_doctor)

    sp = sub.add_parser("models", help="canonical model table")
    sp.set_defaults(func=cmd_models)

    sp = sub.add_parser("conversions", help="conversion matrix")
    sp.set_defaults(func=cmd_conversions)

    sp = sub.add_parser("detect", help="detect type/model of a file or folder")
    sp.add_argument("path")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_detect)

    sp = sub.add_parser("pcg", help="classify PA KORF vs KORG/PCG1 .PCG dialect")
    sp.add_argument("path")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_pcg)

    sp = sub.add_parser("validate", help="pre-import validation")
    sp.add_argument("path")
    sp.add_argument("--model")
    sp.add_argument("--required-ram-mb", type=int)
    sp.add_argument("--locked", help="class: PAD / STYLE / PERF / SAMPLE")
    sp.add_argument("--unsupported", help="kind: FACTORY_ROM / SONG / M50 / OASIS / ...")
    sp.add_argument("--out")
    sp.set_defaults(func=cmd_validate)

    sp = sub.add_parser(
        "validators",
        help="list validator coverage for every known model and file type",
    )
    sp.add_argument("--models", action="store_true", help="list model profiles")
    sp.add_argument("--files", action="store_true", help="list file rules")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_validators)

    sp = sub.add_parser(
        "validate-all",
        help="validate one file or every file in a folder against a model",
    )
    sp.add_argument("path")
    sp.add_argument("--model", help="target/instrument model profile")
    sp.add_argument("--required-ram-mb", type=int)
    sp.add_argument("--shallow", action="store_true", help="skip deep native parser")
    sp.add_argument(
        "--midi-workflow",
        choices=("generic", "manager_import", "pa_style_import"),
        default="generic",
    )
    sp.add_argument("--out", help="atomic JSON report path")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_validate_all)

    sp = sub.add_parser(
        "model-matrix",
        help="complete source-model/target-model compatibility matrix",
    )
    sp.add_argument("extension", help="file extension, for example STY or .PCG")
    sp.add_argument("--source-model")
    sp.add_argument("--target-model")
    sp.add_argument("--out")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_model_matrix)

    sp = sub.add_parser(
        "model-convert",
        help="plan or execute safe model conversion/preservation",
    )
    sp.add_argument("source")
    sp.add_argument("target")
    sp.add_argument("--source-model", required=True)
    sp.add_argument("--target-model", required=True)
    sp.add_argument(
        "--execute",
        action="store_true",
        help="write a new output; without this flag the command is a dry plan",
    )
    sp.add_argument(
        "--no-preserve",
        action="store_true",
        help="refuse UNKNOWN/PARTIAL resources instead of preserving bytes",
    )
    sp.add_argument(
        "--canonicalize-wave",
        action="store_true",
        help="convert PCM WAV to 44.1 kHz / 16-bit / mono",
    )
    sp.add_argument(
        "--mapping",
        help="explicit pa_tool.explicit-model-map.v1 JSON catalog map",
    )
    sp.add_argument(
        "--mapping-template",
        help="write a mapping template and exit",
    )
    sp.add_argument("--report", help="write an additional JSON report")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_model_convert)

    sp = sub.add_parser("inventory", help="inventory a .SET folder")
    sp.add_argument("path")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_inventory)

    sp = sub.add_parser("metadir", help="parse a metadir.json")
    sp.add_argument("path")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_metadir)

    sp = sub.add_parser("status", help="what is implemented, what is UNKNOWN")
    sp.set_defaults(func=cmd_status)

    sp = sub.add_parser("manager", help="Pa Manager resources and factory name table")
    sp.add_argument("--names", action="store_true", help="prikazi tabelu imena i sekcije")
    sp.add_argument("--list-names", action="store_true", help="ispisi sva fabricka imena programa")
    sp.add_argument("--find", help="trazi podnisku u fabrickim imenima")
    sp.add_argument("--list-drum", action="store_true", help="ispisi imena iz KMP tabele")
    sp.add_argument("--json", action="store_true", help="ukljucen JSON izlaz resursa")
    sp.set_defaults(func=cmd_manager)

    sp = sub.add_parser("convert", help="plan a conversion (refuses before acting)")
    sp.add_argument("source", nargs="?")
    sp.add_argument("--target")
    sp.add_argument("--direction")
    sp.add_argument("--kind", help="klasa sadrzaja za provjeru odbijanja")
    sp.add_argument("--model")
    sp.add_argument("--factory-rom", action="store_true",
                    help="izvor sadrzi Factory ROM sadrzaj (odbija se)")
    sp.add_argument("--locked", action="store_true", help="izvor je zakljucan (odbija se)")
    sp.add_argument("--dry-run", action="store_true", help="samo plan, nista se ne pise")
    sp.add_argument("--matrix", action="store_true", help="ispisi matricu smjerova")
    sp.add_argument("--assets", action="store_true",
                    help="prikazi dostupnost setconvert/pa800_native/pa800_remap")
    sp.add_argument("--fingerprint", help="fingerprint jednog .STY fajla")
    sp.add_argument("--set-family", help="kojoj familiji pripada .SET folder")
    sp.set_defaults(func=cmd_convert)

    sp = sub.add_parser("native-bank", help="strict read-only PA800 bank parser")
    sp.add_argument("path")
    sp.add_argument("--resource", type=int, help="show one 1-based resource in depth")
    sp.add_argument("--list", action="store_true", help="list every catalog resource")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_native_bank)

    sp = sub.add_parser("native-set", help="safe ZIP/SET inventory and native audit")
    sp.add_argument("path", help="ZIP containing the complete .SET/ALL folder")
    sp.add_argument("--deep-audit", action="store_true",
                    help="decode every supported resource and report preserve-only variants")
    sp.add_argument("--graph", action="store_true",
                    help="build candidate Style TrackInfo -> USER PCG links")
    sp.add_argument("--manifest", action="store_true",
                    help="include the full CRC/SHA member manifest in JSON/report output")
    sp.add_argument("--out", help="write a JSON report (never overwrites the source)")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_native_set)

    sp = sub.add_parser("native-sounds", help="search 1,003 exact PA800 sound selectors")
    sp.add_argument("--find", help="name fragment or CC00.CC32.PC address")
    sp.add_argument("--limit", type=int, default=80)
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_native_sounds)

    sp = sub.add_parser("web", help="run the integrated read-only browser UI")
    sp.add_argument("--host", default="127.0.0.1")
    sp.add_argument("--port", type=int, default=8765)
    sp.set_defaults(func=cmd_web)

    sp = sub.add_parser("oc", help="decode the first OC31/OC32 payload")
    sp.add_argument("path")
    sp.add_argument("--out", help="direktorij za dekodirani izlaz")
    sp.set_defaults(func=cmd_oc)

    return p


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
