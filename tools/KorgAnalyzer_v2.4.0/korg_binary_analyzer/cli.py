from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

from . import __version__
from .binary import MappedInput
from .compare import compare_files
from .decoder_engine import DecoderRegistry
from .embedded import extract_validated_embedded
from .reports import render_text, write_bundle, write_scan_bundle
from .scanner import _hex_window, analyze_file, scan_directory
from .signals import next_actions


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def _show_candidates(report: dict[str, Any]) -> None:
    print("Detected format(s):")
    for item in report.get("facts", {}).get("formats", []):
        print(f"  {item.get('fact')} — {item.get('status')} ({item.get('confidence')})")
    print("Decoder candidates:")
    probes = report.get("decoded", {}).get("probes", [])
    if not probes:
        print("  none")
    for item in probes:
        print(f"  {item['name']} — confidence {item['confidence']}; {item['implementation']}; {item['status']}")
        for evidence in item.get("evidence", []):
            print(f"    Why: {evidence}")


def _analyze_mode(source: Path, workspace: Path, mode: str) -> int:
    report = analyze_file(source, workspace)
    _show_candidates(report)
    registry = DecoderRegistry()
    if mode == "decode":
        selected = next((item for item in report["decoded"]["probes"] if item.get("implementation") == "SUPPORTED"), None)
        if selected:
            print(f"Selected decoder: {selected['name']} (confidence {selected['confidence']})")
            for evidence in selected.get("evidence", []):
                print(f"  Why selected: {evidence}")
        else:
            print("No implemented decoder is justified; probes remain candidates only.")
        with MappedInput(source) as mapped:
            results = registry.run(source, workspace, mapped.data)
        report["decoded"] = {"status": _overall_decode_status(results), "probes": report["decoded"]["probes"], "results": results}
    elif mode == "extract":
        with MappedInput(source) as mapped:
            data = mapped.data
            results = registry.run(source, workspace, data, only={"zip"})
            digest = report["facts"]["identity"]["hashes"]["sha256"]
            extracted = extract_validated_embedded(data, report["evidence"]["embedded_objects"], workspace / "extracted", digest)
        report["evidence"]["extracted_objects"] = extracted
        results = [item for item in results if item.get("decoder") == "zip"]
        if not results:
            results = [{"decoder": "embedded-object-extractor", "status": "NOT_ATTEMPTED", "reason": "No supported ZIP archive at offset zero"}]
        report["decoded"] = {"status": _overall_decode_status(results), "probes": report["decoded"]["probes"], "results": results + [{"decoder": "embedded-object-extractor", **item} for item in extracted]}
    if mode in ("decode", "extract"):
        current_hash = _sha256(source)
        original_hash = report["facts"]["identity"]["hashes"]["sha256"]
        report["source_integrity_check"] = {
            "status": "UNCHANGED" if current_hash == original_hash else "CHANGED_DURING_ANALYSIS",
            "sha256_before": original_hash, "sha256_after": current_hash,
            "evidence": ["source is opened read-only; pre/post SHA-256 values are compared"],
        }
        report["validated"] = [item for item in report["decoded"]["results"] if item.get("status") == "DECODED_VALIDATED"]
        report["next_actions"] = next_actions(report)
    output_dir = write_bundle(report, workspace, mode)
    print(f"Report: {output_dir.as_posix()}/report.txt")
    if mode in ("decode", "extract"):
        for result in report["decoded"]["results"]:
            print(f"{result.get('decoder')}: {result.get('status')} — {result.get('output') or result.get('output_directory') or result.get('path') or result.get('reason') or result.get('error', '')}")
    return 0



def _overall_decode_status(results: list[dict[str, Any]]) -> str:
    statuses = {item.get("status") for item in results}
    if "DECODED_VALIDATED" in statuses:
        return "DECODED_VALIDATED"
    if "DECODED_PARTIAL" in statuses:
        return "DECODED_PARTIAL"
    if "DECODED_UNVERIFIED" in statuses:
        return "DECODED_UNVERIFIED"
    if "ATTEMPTED_FAILED" in statuses:
        return "ATTEMPTED_FAILED"
    if "CANDIDATE_ONLY" in statuses:
        return "CANDIDATE_ONLY"
    if statuses == {"NOT_SUPPORTED"} or not statuses:
        return "NOT_SUPPORTED"
    return "NOT_ATTEMPTED"


def _run_scan(source: Path, workspace: Path) -> int:
    report = scan_directory(source, workspace)
    output_dir = write_scan_bundle(report, workspace)
    print(f"Scanned {report['files_scanned']} file(s); {report['errors_count']} error(s).")
    print(f"Report: {output_dir.as_posix()}/report.txt")
    print(f"Duplicates: {len(report['duplicate_groups'])}; interesting: {len(report['interesting_files'])}")
    return 0 if not report["errors_count"] else 1


def _run_report(source: Path, workspace: Path) -> int:
    if source.is_dir():
        reports = sorted(source.glob("**/report.txt"), key=lambda item: item.stat().st_mtime, reverse=True)
        if not reports:
            print("No report.txt found in the specified directory.", file=sys.stderr)
            return 2
        print(reports[0].read_text(encoding="utf-8"))
        return 0
    if source.is_file() and source.suffix.lower() == ".json":
        payload = json.loads(source.read_text(encoding="utf-8"))
        if isinstance(payload, dict) and "facts" in payload:
            print(render_text(payload))
        else:
            print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    if source.is_file() and source.name == "report.txt":
        print(source.read_text(encoding="utf-8"))
        return 0
    return _analyze_mode(source, workspace, "report")


def _run_hex(source: Path, offset: int, before: int, after: int, workspace: Path) -> int:
    with MappedInput(source) as mapped:
        if offset < 0 or offset >= max(1, len(mapped.data)):
            raise ValueError(f"Offset 0x{offset:X} is outside file size {len(mapped.data)}")
        window = _hex_window(mapped.data, offset, before, after)
    workspace.mkdir(parents=True, exist_ok=True)
    reports = workspace / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    target = reports / f"hex-{source.stem}-{offset:X}.txt"
    suffix = 1
    while target.exists():
        target = reports / f"hex-{source.stem}-{offset:X}-{suffix}.txt"
        suffix += 1
    target.write_text(f"FILE: {source}\nOFFSET: 0x{offset:X}\nWINDOW: 0x{window['start']:X}-0x{window['end']:X}\n\n{window['hex']}\n", encoding="utf-8")
    print(target.read_text(encoding="utf-8"))
    return 0


def _run_ai_status(args: Any) -> int:
    from .ollama_client import SUGGESTED_MODELS, OllamaClient, OllamaError

    client = OllamaClient(args.host)
    print(f"Ollama host: {client.host}")
    available, detail = client.is_available()
    if not available:
        print("Status: NOT RUNNING")
        print(f"  {detail}")
        print()
        print("Start it with:  ollama serve")
        print("Install Ollama: https://ollama.com/download")
        return 1
    print(f"Status: RUNNING - version {detail}")
    try:
        models = client.models()
    except OllamaError as error:
        print(f"Could not list models: {error}", file=sys.stderr)
        return 1
    if not models:
        print("Models installed: none")
        print()
        print("Pull one with:")
        for tag, size, why in SUGGESTED_MODELS:
            print(f"  ollama pull {tag:<20} {size:>9}  {why}")
        return 1
    print(f"Models installed: {len(models)}")
    running = set(client.running())
    for model in models:
        mark = "  <- running" if model.name in running else ""
        print(f"  {model.name:<26} {model.human_size:>9}  {model.parameters:<7} {model.quantization:<8}{mark}")
    default = client.resolve_model()
    print()
    print(f"Default model for the analyser: {default}")
    if not any("qwen" in model.name.casefold() for model in models):
        print("Note: no Qwen model installed. Recommended:")
        print("  ollama pull qwen2.5:7b")
    return 0


def _run_ai_pull(args: Any) -> int:
    from .ollama_client import SUGGESTED_MODELS, OllamaClient, OllamaError

    if args.list_suggested:
        print("Recommended local models (Qwen 2.5 family):")
        for tag, size, why in SUGGESTED_MODELS:
            print(f"  {tag:<20} {size:>9}  {why}")
        return 0

    client = OllamaClient(args.host)
    available, detail = client.is_available()
    if not available:
        print(f"Ollama is not reachable at {client.host}: {detail}", file=sys.stderr)
        return 1
    print(f"Pulling {args.model} (Ollama {detail}). This can take several minutes.")
    state = {"line": ""}

    def progress(event: dict) -> None:
        status = event.get("status", "")
        total = event.get("total")
        completed = event.get("completed")
        if total and completed:
            percent = completed / total * 100
            line = f"\r  {status:<24} {percent:5.1f}%  {completed / 1024**3:.2f}/{total / 1024**3:.2f} GB"
        else:
            line = f"\r  {status:<56}"
        if line != state["line"]:
            print(line, end="", flush=True)
            state["line"] = line

    try:
        result = client.pull(args.model, on_progress=progress)
    except OllamaError as error:
        print(f"\nPull failed: {error}", file=sys.stderr)
        return 1
    print()
    if result.get("status") == "FAILED":
        print(f"Pull failed: {result.get('error')}", file=sys.stderr)
        return 1
    print(f"Done: {args.model}")
    print("Try it:")
    print('  python -m korg_binary_analyzer ask YOUR_FILE.STY "objasni mi ovaj fajl"')
    return 0


def _run_ask(args: Any) -> int:
    from .ai import ask as ask_model, report_summary_line
    from .ollama_client import OllamaClient, OllamaError

    question = " ".join(args.question).strip() or (
        "Objasni mi ukratko sta je u ovom fajlu: koliko stilova, da li su svi resursi "
        "validni, i ima li nesto sumnjivo."
    )

    target: Path = args.target
    report: dict[str, Any] | None = None
    if target.is_file() and target.suffix.lower() == ".json":
        try:
            payload = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            print(f"Cannot read report {target}: {error}", file=sys.stderr)
            return 2
        if isinstance(payload, dict) and "facts" in payload:
            report = payload
            print(f"Using saved report: {target}")
        else:
            print(f"{target} does not look like an analyser report.", file=sys.stderr)
            return 2
    else:
        print(f"Analysing {target} ...")
        report = analyze_file(target, args.output)
        registry = DecoderRegistry()
        with MappedInput(target) as mapped:
            results = registry.run(target, args.output, mapped.data)
        report["decoded"] = {
            "status": _overall_decode_status(results),
            "probes": report["decoded"]["probes"],
            "results": results,
        }
        report["validated"] = [item for item in results if item.get("status") == "DECODED_VALIDATED"]
        print(f"  {report_summary_line(report)}")

    client = OllamaClient(args.host)
    available, detail = client.is_available()
    if not available:
        print(f"Ollama is not reachable at {client.host}.", file=sys.stderr)
        print(f"  {detail}", file=sys.stderr)
        print("Start it with `ollama serve`, or install from https://ollama.com/download", file=sys.stderr)
        return 1

    model = args.model or client.resolve_model()
    if not model:
        print("No model installed. Pull one first, for example:", file=sys.stderr)
        print("  python -m korg_binary_analyzer ai-pull qwen2.5:7b", file=sys.stderr)
        return 1
    print(f"Model: {model} (Ollama {detail})")
    print("-" * 70)

    options: dict[str, Any] = {}
    if args.temperature is not None:
        options["temperature"] = args.temperature
    try:
        answer = ask_model(
            question,
            report,
            client,
            model=model,
            on_token=lambda piece: print(piece, end="", flush=True),
            options=options,
            max_report_chars=args.context_chars,
        )
    except OllamaError as error:
        print(f"\n{error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\n[cancelled]")
        return 130
    print()
    print("-" * 70)
    if answer.get("eval_count"):
        print(f"[{answer['eval_count']} tokens, {answer.get('done_reason') or 'done'}]")
    if args.save:
        args.save.write_text(
            f"QUESTION: {question}\n\nCONTEXT: {report_summary_line(report)}\n\n"
            f"MODEL: {model}\n\n{answer['content']}\n",
            encoding="utf-8",
        )
        print(f"Saved to {args.save}")
    return 0


def _run_manuals(args: Any) -> int:
    from .manuals import (
        catalogue_size,
        deep_reference_docs,
        human_size,
        plan_download,
        products,
        safe_filename,
        search,
    )

    if args.search:
        hits = search(args.search)
    elif args.brand or args.model:
        hits = [
            (name, item) for name, item in search("")
            if (not args.brand or item["brand"].casefold() == args.brand.casefold())
            and (not args.model or args.model.casefold() in name.casefold())
        ]
    else:
        hits = search("")

    if args.json and not args.download:
        payload = {
            "models": [
                {
                    "model": name, "brand": item["brand"], "family": item.get("family"),
                    "generation": item.get("generation"), "container_era": item.get("container_era"),
                    "product_page": item.get("product_page"),
                    "documents": (deep_reference_docs(item) if args.deep_only else item.get("docs", [])),
                }
                for name, item in hits
            ]
        }
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0

    if args.download or args.dry_run:
        plan = plan_download(brand=args.brand, model=args.model, deep_only=args.deep_only)
        if args.search:
            plan = [item for item in plan if args.search.casefold() in item["model"].casefold()]
        if not plan:
            print("Nothing matched; nothing to download.", file=sys.stderr)
            return 2
        total = sum(item["size_bytes"] for item in plan)
        print(f"{len(plan)} document(s), about {human_size(total)}.")
        if args.dry_run:
            for item in plan:
                print(f"  {item['brand']:<7} {item['model']:<24} {str(item['doc']):<26} {str(item['size']):>9}  {item['filename']}")
            print(f"\nCommand to fetch them all:  --download --output {args.output}")
            return 0

        from .manuals import download

        done = failed = skipped = 0
        for index, item in enumerate(plan, start=1):
            folder = args.output / item["brand"] / safe_filename(item["model"])
            target = folder / safe_filename(item["filename"])
            print(f"[{index}/{len(plan)}] {item['brand']} {item['model']} — {item['doc']} ({item['size'] or '?'})", flush=True)
            result = download(item["direct"], target)
            status = result["status"]
            if status == "DOWNLOADED":
                done += 1
                print(f"      saved to {result['path']} ({human_size(result['size'])})", flush=True)
            elif status == "ALREADY_PRESENT":
                skipped += 1
                print("      already present, skipped", flush=True)
            else:
                failed += 1
                print(f"      FAILED: {result.get('error') or status}", flush=True)
        print(f"\nDownloaded {done}, skipped {skipped}, failed {failed}. Folder: {args.output}")
        return 0 if failed == 0 else 1

    if not hits:
        print("No model matched.", file=sys.stderr)
        return 2

    total_docs = sum(len(item.get("docs", [])) for _, item in hits)
    print(f"Official manual library — {len(hits)} model(s), {total_docs} document(s)")
    print(f"Full library size: {human_size(catalogue_size())}")
    print()
    current_brand = None
    for name, item in hits:
        if item["brand"] != current_brand:
            current_brand = item["brand"]
            print(f"=== {current_brand}")
        era = item.get("container_era") or ""
        generation = f" ({item['generation']})" if item.get("generation") else ""
        print(f"  {name}{generation} — {len(item.get('docs', []))} doc(s)")
        if era:
            print(f"      format note: {era}")
        if args.details:
            for doc in (deep_reference_docs(item) if args.deep_only else item.get("docs", [])):
                print(f"      - {str(doc.get('doc')):<28} {str(doc.get('size') or '?'):>9}  {doc.get('filename')}")
    print()
    print("Add --details to list every document, --download to fetch them,")
    print("--dry-run to preview, --deep-only for reference/data/MIDI docs.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="korg_binary_analyzer", description="Read-only, evidence-qualified binary forensic CLI")
    parser.add_argument("--version", action="version", version=f"Korg Binary Forensic Analyzer {__version__}")
    commands = parser.add_subparsers(dest="command", required=True)
    for command in ("analyze", "decode", "extract", "report"):
        item = commands.add_parser(command)
        item.add_argument("file", type=Path)
        item.add_argument("--output", type=Path, default=Path("workspace"), help="Generated-artifact workspace (default: ./workspace)")
    scan = commands.add_parser("scan", help="Recursively analyze a directory")
    scan.add_argument("directory", type=Path)
    scan.add_argument("--output", type=Path, default=Path("workspace"))
    compare = commands.add_parser("compare", help="Compare two files and group changed regions")
    compare.add_argument("file_a", type=Path)
    compare.add_argument("file_b", type=Path)
    compare.add_argument("--output", type=Path, default=Path("workspace"))
    hex_parser = commands.add_parser("hex", help="Export a bounded hex window")
    hex_parser.add_argument("file", type=Path)
    hex_parser.add_argument("offset", type=lambda value: int(value, 0))
    hex_parser.add_argument("--before", type=int, default=128)
    hex_parser.add_argument("--after", type=int, default=512)
    hex_parser.add_argument("--output", type=Path, default=Path("workspace"))
    commands.add_parser("gui", help="Open the desktop interface")
    selftest = commands.add_parser("selftest", help="Verify the codec end to end with a synthetic container")
    selftest.add_argument("--keep", type=Path, default=None, help="Keep generated self-test files in this directory")

    manuals = commands.add_parser("manuals", help="Official Korg / Yamaha / Roland manual library")
    manuals.add_argument("--search", default=None, help="Filter models by any text (pa5x, bk-5, genos ...)")
    manuals.add_argument("--brand", default=None, help="Only this brand (Korg, Yamaha, Roland)")
    manuals.add_argument("--model", default=None, help="Only models whose name contains this text")
    manuals.add_argument("--details", action="store_true", help="List every document, not just the models")
    manuals.add_argument("--deep-only", action="store_true", help="Only reference/data/midi documentation")
    manuals.add_argument("--json", action="store_true", help="Emit JSON instead of text")
    manuals.add_argument("--download", action="store_true", help="Fetch the selected manuals")
    manuals.add_argument("--output", type=Path, default=Path("manuals"), help="Download directory (default: ./manuals)")
    manuals.add_argument("--dry-run", action="store_true", help="Show the download plan without fetching")

    ask = commands.add_parser("ask", help="Ask a local Qwen model about a file or a saved report")
    ask.add_argument("target", type=Path, help="File to analyse, or a saved report.json")
    ask.add_argument("question", nargs="*", help="Question; omit to get a plain summary")
    ask.add_argument("--model", default=None, help="Ollama model (default: the best Qwen installed)")
    ask.add_argument("--host", default=None, help="Ollama host (default: 127.0.0.1:11434)")
    ask.add_argument("--output", type=Path, default=Path("workspace"), help="Workspace for the generated report")
    ask.add_argument("--temperature", type=float, default=None)
    ask.add_argument("--context-chars", type=int, default=12000, help="How much of the report to send")
    ask.add_argument("--save", type=Path, default=None, help="Write the answer to this file")

    ai_status = commands.add_parser("ai-status", help="Check the local Ollama server and list models")
    ai_status.add_argument("--host", default=None)

    ai_pull = commands.add_parser("ai-pull", help="Download a Qwen model through Ollama")
    ai_pull.add_argument("model", nargs="?", default="qwen2.5:7b", help="Model tag (default: qwen2.5:7b)")
    ai_pull.add_argument("--host", default=None)
    ai_pull.add_argument("--list-suggested", action="store_true", help="Show recommended models and exit")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "analyze":
            return _analyze_mode(args.file, args.output, "analyze")
        if args.command in ("decode", "extract"):
            return _analyze_mode(args.file, args.output, args.command)
        if args.command == "scan":
            return _run_scan(args.directory, args.output)
        if args.command == "compare":
            report = compare_files(args.file_a, args.file_b, args.output)
            output_dir = write_bundle(report, args.output, "compare")
            count = report["comparison"]["changed_region_count"]
            print(f"Changed regions: {count}; identical: {report['comparison']['same_sha256']}")
            print(f"Report: {output_dir.as_posix()}/report.txt")
            return 0
        if args.command == "report":
            return _run_report(args.file, args.output)
        if args.command == "hex":
            if args.before < 0 or args.after < 0:
                raise ValueError("--before and --after must be non-negative")
            return _run_hex(args.file, args.offset, args.before, args.after, args.output)
        if args.command == "gui":
            from .gui import main as gui_main

            return gui_main()
        if args.command == "selftest":
            from .selftest import run_selftest

            return run_selftest(keep=args.keep)
        if args.command == "manuals":
            return _run_manuals(args)
        if args.command == "ai-status":
            return _run_ai_status(args)
        if args.command == "ai-pull":
            return _run_ai_pull(args)
        if args.command == "ask":
            return _run_ask(args)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"ERROR: {type(error).__name__}: {error}", file=sys.stderr)
        return 2
    return 2