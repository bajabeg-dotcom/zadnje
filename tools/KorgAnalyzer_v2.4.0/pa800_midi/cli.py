"""Command line for the PA800 MIDI Optimizer / Restorator."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from pa800_midi import __version__
from pa800_midi.analysis import analyze


def _print_header(title: str) -> None:
    print("=" * 72)
    print(f"  {title}")
    print("=" * 72)


# ------------------------------------------------------------------ commands ---

def _key_range_table(args):
    from pa800_midi.keyrange import default_table

    table = default_table(getattr(args, "key_range_file", None),
                          use_gold=not getattr(args, "key_range_strict", False))
    spec = getattr(args, "key_range", None)
    if spec:
        table.add_overrides(spec)
    return table


def _run_key_range(args) -> int:
    from pa800_midi.keyrange import default_table, describe_gold, describe_table

    table = default_table(args.file, use_gold=not args.strict)
    _print_header("KEY RANGE -- pravilo po tipu instrumenta")
    for line in describe_table(table):
        print(line)
    print()
    print("  * = pravilo prosireno dokazom iz tvog GOLD korpusa")
    print("  (prazno) = osnovno pravilo po tipu instrumenta")
    print()
    _print_header("GOLD -- sta tvoje pjesme stvarno sviraju")
    for line in describe_gold():
        print(line)
    print()
    if table.gold_note:
        print("GOLD je prosirio ova pravila (samo tamo gdje je dokaz jasan):")
        for key, note in sorted(table.gold_note.items()):
            low, high, _ = table.rules[key]
            from pa800_midi.data.categories import note_name
            print(f"  {key:14s} sada {note_name(low)}-{note_name(high)}: {note}")
        print()
    print("Izmjereni profil konkretnog zvuka uvijek ima prednost nad ovim pravilom.")
    print("Promijeni ovako:")
    print('  python -m pa800_midi optimize "pjesma.mid" --key-range "guitar=D2-E6"')
    print(f"  ili uredi fajl: {table and ''}"
          f"{__import__('pa800_midi.keyrange', fromlist=['DEFAULT_RULES_FILE']).DEFAULT_RULES_FILE}")
    if args.write:
        path = table.save(args.write)
        print(f"upisano: {path}")
    return 0


def _run_analyze(args) -> int:
    facts = analyze(args.target, key_ranges=_key_range_table(args))
    _print_header("PA800 MIDI -- ANALIZA (read-only)")
    print(f"Fajl   : {facts.path}")
    print(f"SHA256 : {facts.sha256}")
    print(f"Format : MIDI tip {facts.midi_type}, {facts.track_count} traka, "
          f"TPB {facts.ticks_per_beat}, {facts.total_notes} nota, "
          f"{facts.length_seconds:.1f}s")
    print()
    for track in facts.tracks:
        channel = track.channels[0] + 1 if track.channels else "-"
        print(f"track {track.index} kanal {channel} [{track.role}] "
              f"{track.sound_name}")
        print(f"    identitet {track.instrument_id}"
              f"{' (fabricki)' if track.in_factory_table else ' (nije u tabeli)'}"
              f", {track.note_count} nota")
        if track.pitch_min is not None:
            print(f"    pitch {track.pitch_min}-{track.pitch_max}, "
                  f"velocity {track.velocity_min}/{track.velocity_median}/"
                  f"{track.velocity_max}")
        if track.key_low is not None:
            print(f"    key range {track.key_low}-{track.key_high} "
                  f"('{track.profile_name}')")
        for conflict in track.conflicts:
            print(f"    !! {conflict.describe()}")
    if not facts.conflicts:
        print("Key range: nema konflikata.")
    if args.json:
        Path(args.json).write_text(json.dumps(facts.to_dict(), indent=2,
                                              ensure_ascii=False),
                                   encoding="utf-8")
        print(f"\nJSON: {args.json}")
    return 0


def _run_optimize(args, mode: str) -> int:
    from pa800_midi.pipeline import optimize

    result = optimize(
        args.target,
        workspace=args.workspace,
        factory_dir=args.factory,
        factory_index=args.factory_index,
        gold_index=args.gold,
        demo_index=args.demo,
        rules=args.rule or [],
        style_hint=args.style,
        key_ranges=_key_range_table(args),
        mode=mode,
        waive_immutability=args.waive_immutability,
        allow_note_additions=args.allow_note_additions,
        model=args.model,
    )
    if result.report_dir:
        text = Path(result.report_dir) / "report.txt"
        if text.is_file():
            print(text.read_text(encoding="utf-8"))
    print()
    if result.output_path:
        print(f"MIDI   : {result.output_path}")
        print(f"izlaz  : {result.output_sha256}")
    if result.validation:
        print(f"provjera: {result.validation.status}"
              f"{'' if result.validation.ok else '  (IZVOZ NIJE VALIDAN)'}")
    return 0 if (result.validation is None or result.validation.ok) else 2


def _run_index(args) -> int:
    from pa800_midi.index import (build_demo_index, build_factory_index,
                                  build_gold_index)

    if args.kind == "factory":
        index = build_factory_index(args.source, args.workdir, model=args.model)
        target = Path(args.out) if args.out else (
            Path(__file__).resolve().parent / "data" / "factory_index.json")
        index.save(target)
        _print_header("FACTORY indeks")
        print(f"stilova : {len(index.styles)}")
        print(f"neuspjelo: {len(index.failed)}")
        for item in index.failed[:5]:
            print(f"  - {item['file']}: {item['error'][:80]}")
        print(f"upisano : {target}")
        return 0

    if args.kind == "gold":
        def _progress(position, total, name):
            if position % 25 == 0 or position == total:
                print(f"  [{position}/{total}] {name[:48]}")

        index = build_gold_index(args.source, limit=args.limit or 0,
                                 progress=_progress)
        target = Path(args.out) if args.out else (
            Path(__file__).resolve().parent / "data" / "gold_index.json")
        index.save(target)
        _print_header("GOLD indeks")
        print(f"fajlova : {len(index.files)}")
        for role, profile in sorted(index.profiles.items()):
            print(f"  {role:16s} {profile.samples} uzoraka, "
                  f"velocity {profile.velocity_median}, {profile.confidence.value}")
        print(f"upisano : {target}")
        return 0

    index = build_demo_index(args.source)
    target = Path(args.out) if args.out else (
        Path(__file__).resolve().parent / "data" / "demo_index.json")
    index.save(target)
    _print_header("DEMO indeks")
    print(f"fajlova : {len(index.files)}")
    print(f"RX preseta dokazano: {len(index.rx_presets)}")
    for name, files in sorted(index.rx_presets.items())[:10]:
        print(f"  {name}: {', '.join(files[:2])}")
    print(f"upisano : {target}")
    return 0


def _run_ask(args) -> int:
    from pa800_midi.qwen import ask_about
    from pa800_midi.pipeline import optimize

    if args.report:
        payload = json.loads(Path(args.report).read_text(encoding="utf-8"))
        return ask_about(payload, " ".join(args.question), host=args.host,
                         model=args.model_name)
    result = optimize(args.target, workspace=args.workspace, mode="propose",
                      factory_index=args.factory_index, gold_index=args.gold,
                      demo_index=args.demo)
    return ask_about(result.to_dict(), " ".join(args.question), host=args.host,
                     model=args.model_name)


def _run_batch(args) -> int:
    from pa800_midi.batch import run_batch, write_batch_reports

    table = _key_range_table(args)
    summary = run_batch(args.source, args.workspace, rules=args.rule or [],
                        key_ranges=table,
                        mode=args.mode, limit=args.limit,
                        factory_index=args.factory_index, gold_index=args.gold,
                        demo_index=args.demo, style_hint=args.style)
    from pa800_midi.pipeline import default_workspace

    out = (Path(args.workspace) if args.workspace else default_workspace()) / "batch"
    paths = write_batch_reports(summary, out)
    _print_header("PA800 MIDI -- BATCH")
    print(Path(paths["txt"]).read_text(encoding="utf-8"))
    print(f"JSON: {paths['json']}")
    print(f"CSV : {paths['csv']}")
    return 0


def _run_selftest(args) -> int:
    import tempfile
    from pa800_midi.selftest import run_selftest

    with tempfile.TemporaryDirectory() as workdir:
        checks = run_selftest(Path(workdir))
    _print_header("SELFTEST -- PA800 MIDI OPTIMIZER")
    failed = 0
    for name, ok, detail in checks:
        mark = "OK  " if ok else "FAIL"
        print(f"[{mark}] {name}" + (f" -- {detail}" if detail else ""))
        failed += 0 if ok else 1
    print()
    print(f"{len(checks) - failed}/{len(checks)} provjera prolazi")
    return 0 if not failed else 1


# ------------------------------------------------------------------- parser ----

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pa800_midi",
        description="PA800 MIDI Optimizer / Restorator -- deterministic, "
                    "evidence-driven, read-only prema originalu.",
    )
    parser.add_argument("--version", action="version",
                        version=f"pa800_midi {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--workspace", default=None,
                        help="gdje idu rezultati (podrazumijevano: "
                             "<program>/workspace/pa800_midi)")
    common.add_argument("--factory-index", default=None,
                        help="factory_index.json (podrazumijevano: ugradjeni)")
    common.add_argument("--gold", default=None, help="gold_index.json")
    common.add_argument("--demo", default=None, help="demo_index.json")
    common.add_argument("--model", default="Pa800")
    common.add_argument("--key-range", default=None,
                        help="pravilo po tipu, npr. 'bass=E1-C4,guitar=E2-E6'")
    common.add_argument("--key-range-file", default=None,
                        dest="key_range_file",
                        help="JSON sa tabelom opsega (vidi: pa800_midi key-range)")
    common.add_argument("--key-range-strict", action="store_true",
                        dest="key_range_strict",
                        help="samo osnovno pravilo po tipu, bez GOLD dopune")

    p_an = sub.add_parser("analyze", help="analiza MIDI fajla (ne mijenja nista)")
    p_an.add_argument("target")
    p_an.add_argument("--json", default=None)
    p_an.add_argument("--key-range", default=None)
    p_an.add_argument("--key-range-file", default=None, dest="key_range_file")
    p_an.add_argument("--key-range-strict", action="store_true",
                      dest="key_range_strict",
                      help="samo fizicko pravilo, bez GOLD dopune")
    p_an.set_defaults(model="Pa800")
    p_an.set_defaults(func=_run_analyze)

    for name, mode, helptext in (
        ("plan", "propose", "napravi plan bez primjene"),
        ("optimize", "apply", "napravi plan i primijeni dokazane operacije"),
    ):
        p = sub.add_parser(name, parents=[common], help=helptext)
        p.add_argument("target")
        p.add_argument("--factory", default=None,
                       help="folder sa fabrickim stilovima (STY) za svjez indeks")
        p.add_argument("--style", default=None,
                       help="rucno zadaj fabricki stil za poredjenje")
        p.add_argument("--rule", action="append", default=[],
                       help="deterministicko pravilo, npr. velocity=-15 ili cc7=100")
        p.add_argument("--waive-immutability", action="store_true",
                       help="dozvoli izmjenu tick/pitch SAMO na eksplicitni zahtjev "
                            "(krsi glavno pravilo; sve se biljezi)")
        p.add_argument("--allow-note-additions", action="store_true",
                       help="dozvoli dodavanje nota (ghost) uz zadrzavanje "
                            "postojecih nota")
        p.set_defaults(func=lambda a, m=mode: _run_optimize(a, m))

    p_idx = sub.add_parser("index", help="napravi factory/gold/demo indeks")
    p_idx.add_argument("kind", choices=["factory", "gold", "demo"])
    p_idx.add_argument("source", help="folder sa referentnim materijalom")
    p_idx.add_argument("--out", default=None)
    p_idx.add_argument("--workdir", default="/tmp/pa800_index")
    p_idx.add_argument("--model", default="Pa800")
    p_idx.add_argument("--limit", type=int, default=0,
                       help="0 = obradi sve fajlove")
    p_idx.set_defaults(func=_run_index)

    p_ask = sub.add_parser("ask", help="pitaj lokalni Qwen o nalazima",
                           parents=[common])
    p_ask.add_argument("target", nargs="?", default=None)
    p_ask.add_argument("question", nargs="*")
    p_ask.add_argument("--report", default=None,
                       help="pisi pitanje o vec snimljenom report.json")
    p_ask.add_argument("--host", default=None)
    p_ask.add_argument("--model-name", default=None, dest="model_name")
    p_ask.set_defaults(func=_run_ask)

    p_batch = sub.add_parser("batch", parents=[common],
                             help="obradi cijeli folder MIDI fajlova")
    p_batch.add_argument("source")
    p_batch.add_argument("--rule", action="append", default=[])
    p_batch.add_argument("--mode", choices=["apply", "propose"], default="apply")
    p_batch.add_argument("--style", default=None)
    p_batch.add_argument("--limit", type=int, default=None)
    p_batch.set_defaults(func=_run_batch)

    p_kr = sub.add_parser("key-range", help="pokazi/podesi pravilo opsega po tipu")
    p_kr.add_argument("--file", default=None, help="JSON sa tabelom")
    p_kr.add_argument("--write", default=None, help="upisi tabelu u JSON")
    p_kr.add_argument("--strict", action="store_true",
                      help="prikazi bez GOLD dopune")
    p_kr.set_defaults(func=_run_key_range)

    p_self = sub.add_parser("selftest", help="provjeri da sve radi")
    p_self.set_defaults(func=_run_selftest)

    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except KeyboardInterrupt:
        print("\nPrekinuto.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
