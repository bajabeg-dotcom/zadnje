"""Korg Binary Analyzer -- desktop interface.

A read-only viewer for Korg Pa resource containers.  It answers three
questions without asking the operator to trust a black box:

* **What is this file?**  identity, container kind, framing checks;
* **What is inside it?**  every resource, its id, size and verified checksum;
* **Which styles are in it?**  the slot table with the uncompressed name
  directory aligned against the names recovered from the decoded streams.

Every number shown comes from a validated decode: declared size and XOR
checksum agreement, or the row is reported as rejected with the reason.

Run with ``python -m korg_binary_analyzer.gui`` or by double-clicking the
launcher next to this project.
"""

from __future__ import annotations

import queue
import threading
import traceback
import webbrowser
from pathlib import Path
from tkinter import BOTH, END, LEFT, RIGHT, VERTICAL, X, Y, filedialog, messagebox
import tkinter as tk
from tkinter import ttk

from . import __version__
from .ai import QUICK_PROMPTS, ask as ask_model, report_summary_line
from .decoder_engine import DecoderRegistry
from .ollama_client import (
    SUGGESTED_MODELS,
    OllamaClient,
    OllamaError,
)
from .manuals import (
    brands,
    catalogue_size,
    deep_reference_docs,
    download as download_manual,
    families,
    human_size,
    manuals_for_model,
    parse_size,
    products as catalog_products,
    safe_filename,
)
from .scanner import analyze_file

SUPPORTED_SUFFIXES = {".sty", ".pcg", ".kmp", ".prf", ".sbd", ".pad", ".bkp", ".pkg", ".set", ".bin"}

APP_TITLE = f"Korg Binary Analyzer {__version__}"

OVERVIEW_TEXT = (
    "Otvori Korg fajl (.STY, .PCG, .KMP, .BKP, .pkg ...) ili cijeli folder.\n\n"
    "Program je READ-ONLY: ulazni fajl se nikada ne mijenja.\n\n"
    "Za svaki resurs se provjerava:\n"
    "  1. da li CBarc stream daje TACNO deklarisanu velicinu, i\n"
    "  2. da li XOR checksum odgovara sadrzaju.\n\n"
    "Samo resursi koji prodju obje provjere se prijavljuju kao VALIDATED.\n"
    "Sve ostalo se prikazuje kao CANDIDATE s razlogom odbijanja."
)


def _format_bytes(value: int | None) -> str:
    if value is None:
        return "-"
    if value < 1024:
        return f"{value} B"
    if value < 1024 * 1024:
        return f"{value / 1024:.1f} KiB"
    return f"{value / (1024 * 1024):.2f} MiB"


class AnalysisWorker(threading.Thread):
    """Runs the decode pipeline off the UI thread and reports progress."""

    def __init__(self, targets: list[Path], workspace: Path, outbox: "queue.Queue[tuple[str, object]]"):
        super().__init__(daemon=True)
        self.targets = targets
        self.workspace = workspace
        self.outbox = outbox
        self._stop = threading.Event()

    def cancel(self) -> None:
        self._stop.set()

    def run(self) -> None:
        registry = DecoderRegistry()
        results: list[dict] = []
        try:
            for index, target in enumerate(self.targets, start=1):
                if self._stop.is_set():
                    self.outbox.put(("cancelled", None))
                    return
                self.outbox.put(("progress", (index, len(self.targets), target.name)))
                entry: dict = {"path": str(target), "name": target.name}
                try:
                    data = target.read_bytes()
                    entry["size"] = len(data)
                    try:
                        # The full report (identity, strings, manual matching) is
                        # what the assistant is allowed to reason about.
                        entry["report"] = analyze_file(target, self.workspace)
                    except Exception as report_error:  # noqa: BLE001
                        entry["report_error"] = f"{type(report_error).__name__}: {report_error}"
                    probes = registry.probe(data)
                    entry["probes"] = probes
                    supported = [item for item in probes if item["implementation"] == "SUPPORTED"]
                    if supported:
                        runs = registry.run(target, self.workspace, data)
                        entry["run"] = runs[0] if runs else None
                        report = entry.get("report")
                        if isinstance(report, dict) and entry["run"]:
                            report["decoded"] = {
                                "status": entry["run"].get("status"),
                                "probes": probes,
                                "results": runs,
                            }
                            report["validated"] = [
                                item for item in runs if item.get("status") == "DECODED_VALIDATED"
                            ]
                    else:
                        entry["run"] = None
                        entry["note"] = (
                            "nijedan implementirani dekoder se ne poklapa"
                            if probes else "nema prepoznatog formata"
                        )
                except Exception as error:
                    entry["error"] = f"{type(error).__name__}: {error}"
                results.append(entry)
            self.outbox.put(("done", results))
        except Exception:
            self.outbox.put(("failed", traceback.format_exc()))


class AnalyzerApp(ttk.Frame):
    def __init__(self, master: tk.Tk):
        super().__init__(master, padding=8)
        self.master = master  # type: ignore[assignment]
        self.grid(row=0, column=0, sticky="nsew")
        master.rowconfigure(0, weight=1)
        master.columnconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)

        self.outbox: "queue.Queue[tuple[str, object]]" = queue.Queue()
        self.worker: AnalysisWorker | None = None
        self.results: list[dict] = []
        self.workspace = Path.cwd() / "workspace"
        self.current_files: list[Path] = []
        self.manual_catalog = catalog_products()
        self.manual_index: dict[str, dict] = {}
        self.download_root = Path.cwd() / "prirucnici"

        # AI state: the last analysed report is what the model is allowed to see.
        self.current_report: dict | None = None
        self.ai_client = OllamaClient()
        self.ai_models: list[str] = []
        self.ai_busy = False
        self.ai_cancel = threading.Event()
        self.ai_history: list[dict[str, str]] = []

        self._build_toolbar()
        self._build_status()
        self._build_notebook()
        self.after(120, self._pump)

    # ------------------------------------------------------------------ UI
    def _build_toolbar(self) -> None:
        bar = ttk.Frame(self)
        bar.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        bar.columnconfigure(6, weight=1)

        ttk.Button(bar, text="Otvori fajl...", command=self.open_file).grid(row=0, column=0, padx=(0, 4))
        ttk.Button(bar, text="Otvori folder...", command=self.open_folder).grid(row=0, column=1, padx=4)
        self.decode_button = ttk.Button(bar, text="Dekodiraj i snimi kopije...", command=self.export, state="disabled")
        self.decode_button.grid(row=0, column=2, padx=4)
        ttk.Button(bar, text="Snimi izvjestaj...", command=self.save_report, state="normal").grid(row=0, column=3, padx=4)
        ttk.Button(bar, text="Ocisti", command=self.clear).grid(row=0, column=4, padx=4)

        self.subject = ttk.Label(bar, text="Nijedan fajl nije otvoren", foreground="#444")
        self.subject.grid(row=0, column=6, sticky="e")

    def _build_status(self) -> None:
        frame = ttk.Frame(self)
        frame.grid(row=1, column=0, sticky="ew", pady=(0, 6))
        frame.columnconfigure(0, weight=1)
        self.progress = ttk.Progressbar(frame, mode="determinate", length=200)
        self.progress.grid(row=0, column=0, sticky="ew")
        self.status = ttk.Label(frame, text="Spremno.", foreground="#444")
        self.status.grid(row=0, column=1, sticky="e", padx=(8, 0))

    def _build_notebook(self) -> None:
        self.notebook = ttk.Notebook(self)
        self.notebook.grid(row=2, column=0, sticky="nsew")

        self.overview = self._text_tab("Pregled")
        self.overview.insert("1.0", OVERVIEW_TEXT)

        self.styles_tab = ttk.Frame(self.notebook)
        self.notebook.add(self.styles_tab, text="Stilovi")
        columns = ("file", "slot", "dir", "decoded", "agree", "elements", "geometry", "size")
        headings = {
            "file": "Fajl", "slot": "Slot", "dir": "Ime iz tabele", "decoded": "Ime iz streama",
            "agree": "Poklapanje", "elements": "Elemenata", "geometry": "Geometrija", "size": "Velicina",
        }
        widths = {"file": 130, "slot": 50, "dir": 190, "decoded": 190, "agree": 90, "elements": 80, "geometry": 90, "size": 90}
        self.style_tree = ttk.Treeview(self.styles_tab, columns=columns, show="headings")
        for key in columns:
            self.style_tree.heading(key, text=headings[key])
            self.style_tree.column(key, width=widths[key], anchor="w" if key in ("file", "dir", "decoded") else "center")
        self.style_tree.tag_configure("disagree", background="#ffe6e6")
        self.style_tree.tag_configure("agree", background="#eaf7ea")
        style_scroll = ttk.Scrollbar(self.styles_tab, orient=VERTICAL, command=self.style_tree.yview)
        self.style_tree.configure(yscrollcommand=style_scroll.set)
        self.style_tree.pack(side=LEFT, fill=BOTH, expand=True)
        style_scroll.pack(side=RIGHT, fill=Y)

        self.res_tab = ttk.Frame(self.notebook)
        self.notebook.add(self.res_tab, text="Resursi")
        rcolumns = ("file", "pos", "id", "wrapper", "declared", "decoded", "xor", "sha", "status")
        rheadings = {
            "file": "Fajl", "pos": "#", "id": "ID", "wrapper": "Omot", "declared": "Deklarisano",
            "decoded": "Dekodirano", "xor": "XOR", "sha": "SHA-256", "status": "Status",
        }
        self.res_tree = ttk.Treeview(self.res_tab, columns=rcolumns, show="headings")
        for key in rcolumns:
            self.res_tree.heading(key, text=rheadings[key])
            self.res_tree.column(key, width=150 if key == "file" else (190 if key == "sha" else 85), anchor="w" if key in ("file", "sha") else "center")
        self.res_tree.tag_configure("raw", background="#f2f2f7")
        self.res_tree.tag_configure("reject", background="#ffe6e6")
        res_scroll = ttk.Scrollbar(self.res_tab, orient=VERTICAL, command=self.res_tree.yview)
        self.res_tree.configure(yscrollcommand=res_scroll.set)
        self.res_tree.pack(side=LEFT, fill=BOTH, expand=True)
        res_scroll.pack(side=RIGHT, fill=Y)

        self._build_manuals_tab()
        self._build_ai_tab()

        self.report = self._text_tab("Izvjestaj")

    def _build_manuals_tab(self) -> None:
        """Official manual library: browse, open, and download."""
        tab = ttk.Frame(self.notebook)
        self.notebook.add(tab, text="Prirucnici")
        tab.columnconfigure(0, weight=1)
        tab.rowconfigure(2, weight=1)

        head = ttk.Frame(tab, padding=(0, 4))
        head.grid(row=0, column=0, sticky="ew")
        head.columnconfigure(3, weight=1)
        ttk.Label(head, text="Trazi:").grid(row=0, column=0, padx=(0, 4))
        self.manual_query = tk.StringVar()
        entry = ttk.Entry(head, textvariable=self.manual_query, width=24)
        entry.grid(row=0, column=1, padx=(0, 6))
        entry.bind("<KeyRelease>", lambda _event: self._fill_manuals())
        self.manual_brand = tk.StringVar(value="Svi")
        brand_box = ttk.Combobox(head, textvariable=self.manual_brand, width=12, state="readonly",
                                 values=["Svi", *brands()])
        brand_box.grid(row=0, column=2, padx=(0, 8))
        brand_box.bind("<<ComboboxSelected>>", lambda _event: self._fill_manuals())
        self.manual_status = ttk.Label(head, text="", foreground="#444")
        self.manual_status.grid(row=0, column=3, sticky="e")

        buttons = ttk.Frame(tab, padding=(0, 0, 0, 6))
        buttons.grid(row=1, column=0, sticky="ew")
        ttk.Button(buttons, text="Otvori u browseru (sluzbeni link)", command=self.open_manual_web).pack(side=LEFT, padx=(0, 4))
        ttk.Button(buttons, text="Preuzmi odabrano...", command=self.download_selected_manuals).pack(side=LEFT, padx=4)
        ttk.Button(buttons, text="Preuzmi SVE za odabrani model", command=self.download_model_manuals).pack(side=LEFT, padx=4)
        ttk.Button(buttons, text="Preuzmi cijelu biblioteku (2 GB)", command=self.download_everything).pack(side=LEFT, padx=4)
        ttk.Button(buttons, text="Otvori folder", command=self.open_manual_folder).pack(side=LEFT, padx=4)

        columns = ("brand", "model", "doc", "size", "file")
        titles = {"brand": "Marka", "model": "Model", "doc": "Dokument", "size": "Velicina", "file": "Fajl"}
        widths = {"brand": 70, "model": 150, "doc": 230, "size": 80, "file": 330}
        self.manual_tree = ttk.Treeview(tab, columns=columns, show="headings")
        for key in columns:
            self.manual_tree.heading(key, text=titles[key])
            self.manual_tree.column(key, width=widths[key], anchor="w" if key in ("doc", "file") else "center")
        self.manual_tree.tag_configure("relevant", background="#e6f0ff")
        self.manual_tree.tag_configure("deep", foreground="#0a5c2e")
        self.manual_tree.bind("<Double-1>", lambda _event: self.open_manual_web())
        self.manual_tree.bind("<Return>", lambda _event: self.open_manual_web())
        scroll = ttk.Scrollbar(tab, orient=VERTICAL, command=self.manual_tree.yview)
        self.manual_tree.configure(yscrollcommand=scroll.set)
        self.manual_tree.grid(row=2, column=0, sticky="nsew")
        scroll.grid(row=2, column=1, sticky="ns")

        note = (
            "Zeleno = referentna dokumentacija (User/Reference Manual, Data List, MIDI Implementation) — "
            "to su dokumenti koji opisuju format i sadrzaj. Svi linkovi vode na SLUZBENE stranice proizvodaca "
            "(korg.com, yamaha.com, roland.com); nista se ne preprodaje ni ne prehostuje. "
            "Dvoklik otvara prirucnik u browseru. "
            f"Ukupno u biblioteci: {human_size(catalogue_size())}."
        )
        ttk.Label(tab, text=note, wraplength=1100, foreground="#555", padding=(0, 6)).grid(row=3, column=0, sticky="w")

        self._fill_manuals()

    def _fill_manuals(self) -> None:
        query = self.manual_query.get().strip().casefold()
        brand = self.manual_brand.get()
        self.manual_tree.delete(*self.manual_tree.get_children())
        self.manual_index.clear()
        count = 0
        for model, item in sorted(self.manual_catalog.items()):
            if brand != "Svi" and item["brand"] != brand:
                continue
            if query and query not in model.casefold() and query not in item["brand"].casefold():
                continue
            docs = sorted(
                item.get("docs", []),
                key=lambda doc: (doc.get("depth") != "reference", str(doc.get("doc"))),
            )
            if not docs:
                continue
            first = True
            for doc in docs:
                iid = self.manual_tree.insert("", END, values=(
                    item["brand"] if first else "",
                    model if first else "",
                    doc.get("doc"),
                    doc.get("size") or "-",
                    doc.get("filename"),
                ), tags=("deep",) if doc.get("depth") == "reference" else ())
                self.manual_index[iid] = {"model": model, **doc, **{k: item.get(k) for k in ("brand", "family", "generation")}}
                count += 1
                first = False
        self.manual_status.configure(text=f"{count} dokument(a)")

    def _selected_manual_ids(self) -> list[str]:
        ids = list(self.manual_tree.selection())
        if not ids:
            focused = self.manual_tree.focus()
            ids = [focused] if focused else []
        return [iid for iid in ids if iid in self.manual_index]

    def open_manual_web(self) -> None:
        entries = [self.manual_index[iid] for iid in self._selected_manual_ids()]
        if not entries:
            messagebox.showinfo(APP_TITLE, "Izaberi prirucnik u tabeli.")
            return
        for entry in entries[:6]:
            webbrowser.open(entry["direct"])

    def open_manual_folder(self) -> None:
        self.download_root.mkdir(parents=True, exist_ok=True)
        webbrowser.open(self.download_root.resolve().as_uri())

    def _start_manual_downloads(self, plan: list[dict]) -> None:
        if not plan:
            messagebox.showinfo(APP_TITLE, "Nema sta preuzeti.")
            return
        total = sum(parse_size(item.get("size")) for item in plan)
        if not messagebox.askyesno(
            APP_TITLE,
            f"Preuzeti {len(plan)} dokument(a), oko {human_size(total)}?\n\n"
            f"Folder: {self.download_root}\n\n"
            "Preuzimanje ide direktno sa sluzbenih stranica proizvodaca.",
        ):
            return
        self._manual_downloads = plan
        self.manual_status.configure(text=f"0/{len(plan)} ...")
        worker = threading.Thread(target=self._manual_download_worker, args=(plan,), daemon=True)
        worker.start()

    def _manual_download_worker(self, plan: list[dict]) -> None:
        for index, item in enumerate(plan, start=1):
            folder = self.download_root / safe_filename(item.get("brand", "ostalo")) / safe_filename(item["model"])
            target = folder / safe_filename(item.get("filename") or "manual.pdf")
            self.outbox.put(("manual_progress", (index, len(plan), item)))
            result = download_manual(item["direct"], target)
            self.outbox.put(("manual_result", (item, result)))
        self.outbox.put(("manual_done", len(plan)))

    def download_selected_manuals(self) -> None:
        entries = [self.manual_index[iid] for iid in self._selected_manual_ids()]
        self._start_manual_downloads(entries)

    def download_model_manuals(self) -> None:
        entries = [self.manual_index[iid] for iid in self._selected_manual_ids()]
        if not entries:
            messagebox.showinfo(APP_TITLE, "Izaberi barem jedan red da znam koji model.")
            return
        models = {entry["model"] for entry in entries}
        plan = [
            {"model": name, **doc, "brand": self.manual_catalog[name]["brand"]}
            for name in sorted(models)
            for doc in self.manual_catalog[name].get("docs", [])
        ]
        self._start_manual_downloads(plan)

    def download_everything(self) -> None:
        plan = [
            {"model": name, **doc, "brand": item["brand"]}
            for name, item in sorted(self.manual_catalog.items())
            for doc in item.get("docs", [])
        ]
        self._start_manual_downloads(plan)

    def _build_ai_tab(self) -> None:
        """Local Qwen assistant: explains the loaded report, nothing else."""
        tab = ttk.Frame(self.notebook)
        self.notebook.add(tab, text="AI (Qwen)")
        tab.columnconfigure(0, weight=1)
        tab.rowconfigure(3, weight=1)

        status_row = ttk.Frame(tab, padding=(0, 4))
        status_row.grid(row=0, column=0, sticky="ew")
        status_row.columnconfigure(1, weight=1)
        self.ai_indicator = tk.Canvas(status_row, width=14, height=14, highlightthickness=0)
        self.ai_indicator.grid(row=0, column=0, padx=(0, 6))
        self.ai_dot = self.ai_indicator.create_oval(2, 2, 12, 12, fill="#bbb", outline="")
        self.ai_status_label = ttk.Label(status_row, text="Provjeravam Ollamu ...", foreground="#444")
        self.ai_status_label.grid(row=0, column=1, sticky="w")
        ttk.Button(status_row, text="Provjeri ponovo", command=self.refresh_ai_status).grid(row=0, column=2, padx=4)

        model_row = ttk.Frame(tab, padding=(0, 0, 0, 6))
        model_row.grid(row=1, column=0, sticky="ew")
        ttk.Label(model_row, text="Model:").grid(row=0, column=0, padx=(0, 4))
        self.ai_model = tk.StringVar()
        self.ai_model_box = ttk.Combobox(model_row, textvariable=self.ai_model, width=26, state="readonly", values=[])
        self.ai_model_box.grid(row=0, column=1, padx=(0, 8))
        ttk.Button(model_row, text="Preuzmi model ...", command=self.pull_ai_model).grid(row=0, column=2, padx=4)
        self.ai_context_label = ttk.Label(model_row, text="", foreground="#666")
        self.ai_context_label.grid(row=0, column=3, sticky="w", padx=8)

        prompts_row = ttk.Frame(tab, padding=(0, 0, 0, 6))
        prompts_row.grid(row=2, column=0, sticky="ew")
        for index, (label, _prompt) in enumerate(QUICK_PROMPTS):
            ttk.Button(
                prompts_row, text=label, width=len(label) + 2,
                command=lambda i=index: self.ai_quick(i),
            ).grid(row=0, column=index, padx=(0 if index == 0 else 4, 4))

        chat_frame = ttk.Frame(tab)
        chat_frame.grid(row=3, column=0, sticky="nsew")
        chat_frame.columnconfigure(0, weight=1)
        chat_frame.rowconfigure(0, weight=1)
        self.ai_chat = tk.Text(
            chat_frame, wrap="word", font=("Consolas", 9) if tk.TkVersion >= 8.6 else ("Courier", 9),
            background="#fbfbfd", state="disabled",
        )
        self.ai_chat.tag_configure("user", foreground="#0a3d7a", spacing1=6, spacing3=2)
        self.ai_chat.tag_configure("assistant", foreground="#111")
        self.ai_chat.tag_configure("meta", foreground="#777", spacing3=6)
        self.ai_chat.tag_configure("error", foreground="#a11")
        chat_scroll = ttk.Scrollbar(chat_frame, orient=VERTICAL, command=self.ai_chat.yview)
        self.ai_chat.configure(yscrollcommand=chat_scroll.set)
        self.ai_chat.grid(row=0, column=0, sticky="nsew")
        chat_scroll.grid(row=0, column=1, sticky="ns")

        input_row = ttk.Frame(tab, padding=(0, 6, 0, 0))
        input_row.grid(row=4, column=0, sticky="ew")
        input_row.columnconfigure(0, weight=1)
        self.ai_question = tk.Entry(input_row)
        self.ai_question.grid(row=0, column=0, sticky="ew", padx=(0, 6))
        self.ai_question.bind("<Return>", lambda _event: self.ask_ai())
        self.ai_send = ttk.Button(input_row, text="Pitaj", command=self.ask_ai)
        self.ai_send.grid(row=0, column=1, padx=(0, 4))
        self.ai_stop = ttk.Button(input_row, text="Zaustavi", command=self.cancel_ai, state="disabled")
        self.ai_stop.grid(row=0, column=2, padx=(0, 4))
        ttk.Button(input_row, text="Ocisti", command=self.clear_ai_chat).grid(row=0, column=3)

        self._ai_say(
            "meta",
            "Lokalni asistent preko Ollame. Radi isključivo na tvojoj mašini — ništa se ne šalje na internet.\n"
            "Model smije koristiti SAMO podatke iz izvještaja koji je program napravio za tvoj fajl.\n"
            "Ako podatak nije u izvještaju, reći će ti to umjesto da pogodi.\n\n"
            "Prvi korak: otvori fajl u tabu 'Pregled' pa se vrati ovdje i pitaj.",
        )
        self.refresh_ai_status()

    # ------------------------------------------------------------- ai helpers
    def _ai_say(self, tag: str, text: str) -> None:
        self.ai_chat.configure(state="normal")
        self.ai_chat.insert(END, text, tag)
        self.ai_chat.see(END)
        self.ai_chat.configure(state="disabled")

    def _ai_set_indicator(self, colour: str) -> None:
        self.ai_indicator.itemconfigure(self.ai_dot, fill=colour)

    def refresh_ai_status(self) -> None:
        """Check the local Ollama server without blocking the interface."""
        self._ai_set_indicator("#e6a700")
        self.ai_status_label.configure(text="Provjeravam Ollamu ...")

        def probe() -> None:
            client = self.ai_client
            available, detail = client.is_available()
            if not available:
                self.outbox.put(("ai_status", (False, detail, [], "")))
                return
            try:
                models = client.models()
            except OllamaError as error:
                self.outbox.put(("ai_status", (False, str(error), [], detail)))
                return
            names = [model.name for model in models]
            default = client.resolve_model()
            self.outbox.put(("ai_status", (True, detail, names, default or "")))

        threading.Thread(target=probe, daemon=True).start()

    def _on_ai_status(self, available: bool, detail: str, names: list[str], default: str) -> None:
        if not available:
            self._ai_set_indicator("#c0392b")
            self.ai_status_label.configure(
                text="Ollama ne radi. Pokreni je sa `ollama serve` (ili je instaliraj sa ollama.com)."
            )
            self.ai_model_box.configure(values=[])
            self.ai_send.configure(state="disabled")
            return
        self._ai_set_indicator("#1e9e4a")
        self.ai_models = names
        if names:
            self.ai_model_box.configure(values=names)
            self.ai_model.set(default or names[0])
            self.ai_status_label.configure(
                text=f"Ollama radi (verzija {detail}) · {len(names)} model(a) instalirano"
            )
            self.ai_send.configure(state="normal")
        else:
            self.ai_model_box.configure(values=[])
            self.ai_status_label.configure(
                text=f"Ollama radi (verzija {detail}) ali nema nijedan model. Klikni 'Preuzmi model ...'."
            )
            self.ai_send.configure(state="disabled")

    def pull_ai_model(self) -> None:
        available, _detail = self.ai_client.is_available()
        if not available:
            messagebox.showinfo(
                APP_TITLE,
                "Ollama ne radi.\n\n1. Instaliraj je sa https://ollama.com/download\n"
                "2. Pokreni je (na Windowsu se pokreće sama)\n3. Klikni 'Provjeri ponovo'",
            )
            return
        choice = tk.Toplevel(self)
        choice.title("Preuzmi model")
        choice.transient(self.master)
        choice.grab_set()
        ttk.Label(choice, text="Izaberi model za preuzimanje:", padding=8).pack(anchor="w")
        listed = "\n".join(f"{tag:<22} {size:>9}   {why}" for tag, size, why in SUGGESTED_MODELS)
        ttk.Label(choice, text=listed, font=("Consolas", 9), padding=(8, 0, 8, 8)).pack(anchor="w")
        row = ttk.Frame(choice, padding=(8, 0, 8, 8))
        row.pack(fill=X)
        tag_var = tk.StringVar(value="qwen2.5:7b")
        ttk.Combobox(row, textvariable=tag_var, width=24, values=[tag for tag, _s, _w in SUGGESTED_MODELS]).pack(side=LEFT, padx=(0, 6))
        custom = ttk.Entry(row, width=18)
        custom.pack(side=LEFT, padx=(0, 6))
        custom.insert(0, "ili upiši tag")

        def start() -> None:
            model = custom.get().strip() if custom.get().strip() and custom.get().strip() != "ili upiši tag" else tag_var.get().strip()
            choice.destroy()
            if not model:
                return
            self.ai_status_label.configure(text=f"Preuzimam {model} ...")
            self._ai_set_indicator("#e6a700")
            threading.Thread(target=self._pull_worker, args=(model,), daemon=True).start()

        ttk.Button(row, text="Preuzmi", command=start).pack(side=LEFT)

    def _pull_worker(self, model: str) -> None:
        def progress(event: dict) -> None:
            self.outbox.put(("ai_pull", (model, event)))

        try:
            result = self.ai_client.pull(model, on_progress=progress)
        except OllamaError as error:
            self.outbox.put(("ai_pull_failed", str(error)))
            return
        self.outbox.put(("ai_pull_done", (model, result)))

    def ai_quick(self, index: int) -> None:
        if index >= len(QUICK_PROMPTS):
            return
        self.ai_question.delete(0, END)
        self.ai_question.insert(0, QUICK_PROMPTS[index][1])
        self.ask_ai()

    def ask_ai(self) -> None:
        if self.ai_busy:
            return
        question = self.ai_question.get().strip()
        if not question:
            return
        model = self.ai_model.get().strip()
        if not model:
            messagebox.showinfo(APP_TITLE, "Nema izabranog modela. Klikni 'Provjeri ponovo' ili 'Preuzmi model ...'.")
            return
        if not self.current_report:
            self._ai_say(
                "meta",
                "\n[!] Nema učitanog izvještaja. Otvori fajl u tabu 'Pregled' pa pitaj ponovo.\n",
            )
            return

        self.ai_question.delete(0, END)
        self._ai_say("user", f"\n▸ {question}\n")
        self.ai_busy = True
        self.ai_cancel.clear()
        self.ai_send.configure(state="disabled")
        self.ai_stop.configure(state="normal")
        self.ai_status_label.configure(text=f"Model {model} odgovara ...")
        self._ai_set_indicator("#e6a700")
        self._ai_say("assistant", "")

        threading.Thread(
            target=self._ask_worker,
            args=(question, dict(self.current_report), model),
            daemon=True,
        ).start()

    def _ask_worker(self, question: str, report: dict, model: str) -> None:
        def on_token(piece: str) -> None:
            self.outbox.put(("ai_token", piece))

        try:
            answer = ask_model(
                question,
                report,
                self.ai_client,
                model=model,
                history=self.ai_history,
                on_token=on_token,
                cancel=self.ai_cancel.is_set,
            )
        except OllamaError as error:
            self.outbox.put(("ai_error", str(error)))
            return
        except Exception as error:  # noqa: BLE001
            self.outbox.put(("ai_error", f"{type(error).__name__}: {error}"))
            return
        self.outbox.put(("ai_done", (question, answer)))

    def cancel_ai(self) -> None:
        if self.ai_busy:
            self.ai_cancel.set()
            self.ai_status_label.configure(text="Zaustavljam ...")

    def clear_ai_chat(self) -> None:
        self.ai_chat.configure(state="normal")
        self.ai_chat.delete("1.0", END)
        self.ai_chat.configure(state="disabled")
        self.ai_history = []

    def _text_tab(self, title: str) -> tk.Text:
        frame = ttk.Frame(self.notebook)
        self.notebook.add(frame, text=title)
        widget = tk.Text(frame, wrap="none", font=("Consolas", 9) if tk.TkVersion >= 8.6 else ("Courier", 9))
        scroll_y = ttk.Scrollbar(frame, orient=VERTICAL, command=widget.yview)
        scroll_x = ttk.Scrollbar(frame, orient="horizontal", command=widget.xview)
        widget.configure(yscrollcommand=scroll_y.set, xscrollcommand=scroll_x.set)
        widget.grid(row=0, column=0, sticky="nsew")
        scroll_y.grid(row=0, column=1, sticky="ns")
        scroll_x.grid(row=1, column=0, sticky="ew")
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)
        return widget

    # ------------------------------------------------------------- actions
    def open_file(self) -> None:
        path = filedialog.askopenfilename(
            title="Izaberi Korg fajl",
            filetypes=[
                ("Korg fajlovi", "*.STY *.sty *.PCG *.pcg *.KMP *.PRF *.SBD *.PAD *.BKP *.pkg *.SET"),
                ("Svi fajlovi", "*.*"),
            ],
        )
        if path:
            self.start([Path(path)])

    def open_folder(self) -> None:
        folder = filedialog.askdirectory(title="Izaberi folder")
        if not folder:
            return
        root = Path(folder)
        targets = sorted(
            item for item in root.rglob("*")
            if item.is_file() and (item.suffix.lower() in SUPPORTED_SUFFIXES or item.suffix == "")
        )
        if not targets:
            messagebox.showinfo(APP_TITLE, "U tom folderu nema prepoznatih Korg fajlova.")
            return
        if len(targets) > 400:
            if not messagebox.askyesno(APP_TITLE, f"Folder sadrzi {len(targets)} fajlova. Nastaviti?"):
                return
            targets = targets[:400]
        self.start(targets)

    def start(self, targets: list[Path]) -> None:
        if self.worker and self.worker.is_alive():
            messagebox.showinfo(APP_TITLE, "Analiza je vec u toku.")
            return
        self.clear(keep_status=True)
        self.current_files = targets
        self.subject.configure(text=f"{len(targets)} fajl(ova): {targets[0].name}" + (" ..." if len(targets) > 1 else ""))
        self.progress.configure(maximum=max(1, len(targets)), value=0)
        self.status.configure(text="Analiza u toku...")
        self.worker = AnalysisWorker(targets, self.workspace, self.outbox)
        self.worker.start()

    def export(self) -> None:
        if not self.results:
            return
        folder = filedialog.askdirectory(title="Izaberi folder za dekodirane kopije")
        if not folder:
            return
        self.workspace = Path(folder) / "korg_analyzer_output"
        self.status.configure(text=f"Ponovo dekodiram u {self.workspace} ...")
        self.start(list(self.current_files))

    def save_report(self) -> None:
        text = self.report.get("1.0", END).strip()
        if not text:
            messagebox.showinfo(APP_TITLE, "Nema izvjestaja za snimanje.")
            return
        path = filedialog.asksaveasfilename(
            title="Snimi izvjestaj", defaultextension=".txt",
            initialfile="korg_izvjestaj.txt", filetypes=[("Tekst", "*.txt"), ("Svi fajlovi", "*.*")],
        )
        if path:
            Path(path).write_text(text + "\n", encoding="utf-8")
            self.status.configure(text=f"Izvjestaj snimljen: {path}")

    def clear(self, keep_status: bool = False) -> None:
        self.results = []
        self.style_tree.delete(*self.style_tree.get_children())
        self.res_tree.delete(*self.res_tree.get_children())
        self.report.delete("1.0", END)
        self.decode_button.configure(state="disabled")
        if not keep_status:
            self.status.configure(text="Spremno.")
            self.subject.configure(text="Nijedan fajl nije otvoren")
            self.progress.configure(value=0)

    # -------------------------------------------------------------- pump
    def _pump(self) -> None:
        try:
            while True:
                kind, payload = self.outbox.get_nowait()
                if kind == "progress":
                    index, total, name = payload  # type: ignore[misc]
                    self.progress.configure(value=index - 1)
                    self.status.configure(text=f"({index}/{total}) {name}")
                elif kind == "done":
                    self.results = payload  # type: ignore[assignment]
                    self.progress.configure(value=self.progress["maximum"])
                    self._render()
                elif kind == "manual_progress":
                    index, total, item = payload  # type: ignore[misc]
                    self.manual_status.configure(
                        text=f"{index}/{total} — {item.get('model')} {item.get('doc')}"
                    )
                elif kind == "manual_result":
                    item, result = payload  # type: ignore[misc]
                    if result.get("status") not in ("DOWNLOADED", "ALREADY_PRESENT"):
                        self.status.configure(text=f"Greska pri preuzimanju: {item.get('filename')}")
                elif kind == "ai_status":
                    self._on_ai_status(*payload)  # type: ignore[misc]
                elif kind == "ai_token":
                    self.ai_chat.configure(state="normal")
                    self.ai_chat.insert(END, str(payload), "assistant")
                    self.ai_chat.see(END)
                    self.ai_chat.configure(state="disabled")
                elif kind == "ai_done":
                    question, answer = payload  # type: ignore[misc]
                    self.ai_busy = False
                    self.ai_send.configure(state="normal")
                    self.ai_stop.configure(state="disabled")
                    self._ai_set_indicator("#1e9e4a")
                    tokens = answer.get("eval_count")
                    note = "zaustavljeno" if answer.get("cancelled") else (f"{tokens} tokena" if tokens else "gotovo")
                    self.ai_chat.configure(state="normal")
                    self.ai_chat.insert(END, f"\n", "assistant")
                    self.ai_chat.insert(END, f"[{note}]\n", "meta")
                    self.ai_chat.configure(state="disabled")
                    self.ai_status_label.configure(text=f"Ollama radi · model {answer.get('model')}")
                    self.ai_history.append({"role": "user", "content": question})
                    self.ai_history.append({"role": "assistant", "content": answer.get("content", "")})
                elif kind == "ai_error":
                    self.ai_busy = False
                    self.ai_send.configure(state="normal")
                    self.ai_stop.configure(state="disabled")
                    self._ai_set_indicator("#c0392b")
                    self._ai_say("error", f"\n[greška] {payload}\n")
                elif kind == "ai_pull":
                    model, event = payload  # type: ignore[misc]
                    status = event.get("status", "")
                    total = event.get("total")
                    done = event.get("completed")
                    if total and done:
                        self.ai_status_label.configure(
                            text=f"Preuzimam {model}: {status} {done / total * 100:.0f}%"
                        )
                    else:
                        self.ai_status_label.configure(text=f"Preuzimam {model}: {status}")
                elif kind == "ai_pull_done":
                    model, result = payload  # type: ignore[misc]
                    self._ai_say("meta", f"\n[model] {model}: {result.get('status')}\n")
                    self.refresh_ai_status()
                elif kind == "ai_pull_failed":
                    self._ai_set_indicator("#c0392b")
                    self._ai_say("error", f"\n[greška pri preuzimanju] {payload}\n")
                elif kind == "manual_done":
                    self.manual_status.configure(
                        text=f"Gotovo: {payload} dokument(a) u {self.download_root}"
                    )
                elif kind == "cancelled":
                    self.status.configure(text="Otkazano.")
                elif kind == "failed":
                    self.status.configure(text="Greska.")
                    messagebox.showerror(APP_TITLE, str(payload))
        except queue.Empty:
            pass
        self.after(120, self._pump)

    def _highlight_relevant_manuals(self, lines: list[str]) -> None:
        """Mark the catalogue rows that document whatever was just analysed.

        Model names are matched against the file name, the container era and the
        raw directory the file sits in, then the matching rows are selected so
        the operator sees *which* document explains this file.
        """
        if not self.manual_index:
            return
        haystack = " ".join(
            f"{entry.get('name', '')} {entry.get('path', '')}" for entry in self.results
        ).casefold()
        if not haystack.strip():
            return
        wanted: set[str] = set()
        for model in self.manual_catalog:
            key = model.casefold().replace(" ", "")
            if key and key in haystack.replace(" ", ""):
                wanted.add(model)
        extra: list[str] = []
        if wanted:
            extra.append("")
            extra.append("PRIRUCNICI ZA OVAJ MODEL (tab 'Prirucnici'):")
        self.manual_tree.selection_remove(*self.manual_tree.selection())
        first_hit: str | None = None
        for iid, entry in self.manual_index.items():
            relevant = entry["model"] in wanted
            tags = ["relevant"] if relevant else (["deep"] if entry.get("depth") == "reference" else [])
            self.manual_tree.item(iid, tags=tuple(tags))
            if relevant and first_hit is None:
                first_hit = iid
            if relevant and entry["model"] in wanted:
                wanted.discard(entry["model"])
                extra.append(f"  {entry['brand']} {entry['model']} — {entry.get('product_page', '')}")
        if first_hit:
            self.manual_tree.see(first_hit)
            self.manual_tree.selection_set(first_hit)
        if extra:
            lines.extend(extra)

    # ------------------------------------------------------------ render
    def _render(self) -> None:
        self.report.delete("1.0", END)
        validated_files = 0
        total_resources = 0
        total_styles = 0
        rejected = 0
        lines: list[str] = [
            "=" * 78,
            f"KORG BINARY ANALYZER {__version__} -- izvjestaj",
            "READ-ONLY analiza; ulazni fajlovi nisu mijenjani.",
            "=" * 78,
            "",
        ]

        for entry in self.results:
            name = entry["name"]
            path = entry["path"]
            lines.append("-" * 78)
            lines.append(f"FAJL: {name}")
            lines.append(f"  putanja : {path}")
            lines.append(f"  velicina: {_format_bytes(entry.get('size'))}")
            if entry.get("error"):
                lines.append(f"  GRESKA  : {entry['error']}")
                lines.append("")
                continue
            probes = entry.get("probes") or []
            supported = [item for item in probes if item["implementation"] == "SUPPORTED"]
            if supported:
                best = supported[0]
                lines.append(f"  format  : {best['name']} (pouzdanost {best['confidence']})")
                for evidence in best.get("evidence", []):
                    lines.append(f"            - {evidence}")
            run = entry.get("run")
            if not run:
                lines.append("  dekodiranje: nije primijenjeno")
                if entry.get("note"):
                    lines.append(f"               {entry['note']}")
                lines.append("")
                continue

            status = run.get("status")
            validation = (run.get("validation") or {}).get("status")
            if status == "DECODED_VALIDATED":
                validated_files += 1
            if run.get("resource_count"):
                total_resources += run["resource_count"]
            if run.get("style_count"):
                total_styles += run["style_count"]
            rejected += run.get("resource_decode_errors", 0)

            lines.append(f"  status  : {status}" + (f"  (validacija: {validation})" if validation else ""))
            lines.append(f"  resursa : {run.get('resource_count', 0)} (kompresovanih {run.get('compressed_resource_count', 0)}, sirovih {run.get('raw_resource_count', 0)})")
            lines.append(f"  stilova : {run.get('style_count', 0)}")
            agreement = run.get("name_agreement") or {}
            if agreement.get("compared"):
                lines.append(
                    f"  imena   : {agreement.get('agreed', 0)}/{agreement.get('compared', 0)} poklapanja "
                    f"tabele imena i dekodiranih streamova"
                )
            for evidence in (run.get("validation") or {}).get("evidence", []):
                lines.append(f"            - {evidence}")

            slots = run.get("slots") or []
            if slots:
                lines.append("")
                lines.append("  STILOVI:")
                lines.append(f"    {'#':>3}  {'ime iz tabele':<22} {'ime iz streama':<22} {'ok':<4} {'elem':>4}  geometrija")
                for slot in slots:
                    ok = "-" if slot.get("agrees") is None else ("DA" if slot["agrees"] else "NE")
                    lines.append(
                        f"    {slot['slot']:>3}  {str(slot.get('directory_name') or '-'):<22}"
                        f" {str(slot.get('decoded_name') or '-'):<22} {ok:<4} {slot.get('element_count') or 0:>4}"
                        f"  {slot.get('element_track_geometries') or ''}"
                    )
            lines.append("")
            lines.append(f"  manifest: {run.get('manifest') or '-'}")
            lines.append(f"  kopija  : {run.get('decoded_container') or run.get('output_directory') or '-'}")

            for slot in slots:
                tag = "agree" if slot.get("agrees") else ("disagree" if slot.get("agrees") is False else "")
                self.style_tree.insert("", END, values=(
                    name, slot["slot"], slot.get("directory_name") or "-", slot.get("decoded_name") or "-",
                    "DA" if slot.get("agrees") else ("NE" if slot.get("agrees") is False else "-"),
                    slot.get("element_count") or 0, slot.get("element_track_geometries") or "-",
                    _format_bytes(slot.get("size")),
                ), tags=(tag,) if tag else ())

            resources = list(run.get("outputs") or [])
            for item in resources:
                self.res_tree.insert("", END, values=(
                    name, item.get("position"), item.get("id"), run.get("decoder"), "-", _format_bytes(item.get("size")),
                    "-", item.get("sha256", "")[:16], "VALIDATED",
                ))
            for item in run.get("raw_resources") or []:
                self.res_tree.insert("", END, values=(
                    name, item.get("resource_position"), item.get("id"), "-", "-", _format_bytes(item.get("decoded_size")),
                    "-", item.get("sha256", "")[:16], "RAW (nije kompresovan)",
                ), tags=("raw",))
            for item in run.get("resource_errors") or []:
                self.res_tree.insert("", END, values=(
                    name, item.get("resource_position"), item.get("id"), "-", "-", _format_bytes(item.get("payload_size")),
                    "-", "", "ODBIJENO",
                ), tags=("reject",))

        lines.insert(4, f"Fajlova: {len(self.results)} | validiranih: {validated_files} | resursa: {total_resources} | stilova: {total_styles} | odbijenih resursa: {rejected}")
        lines.insert(5, "")
        self.report.insert("1.0", "\n".join(lines))

        self.overview.delete("1.0", END)
        self.overview.insert("1.0", "\n".join(lines[:120]))

        self._highlight_relevant_manuals(lines)

        # Hand the newest full report to the assistant and say so.
        reports = [entry.get("report") for entry in self.results if isinstance(entry.get("report"), dict)]
        if reports:
            self.current_report = reports[-1]
            self.ai_context_label.configure(text=f"Kontekst: {report_summary_line(self.current_report)}")
        else:
            self.current_report = None
            self.ai_context_label.configure(text="Nema izvještaja za asistenta.")

        self.decode_button.configure(state="normal")
        if validated_files == len([item for item in self.results if item.get("run")]):
            self.status.configure(text=f"Gotovo: {validated_files} fajl(ova) validirano, {total_resources} resursa, {total_styles} stilova.")
        else:
            self.status.configure(text=f"Gotovo: {validated_files} validirano od {len(self.results)} fajl(ova); {rejected} resursa odbijeno.")
        self.notebook.select(self.styles_tab if total_styles else self.report)


def main() -> int:
    root = tk.Tk()
    root.title(APP_TITLE)
    root.geometry("1280x760")
    root.minsize(900, 520)
    try:
        ttk.Style().theme_use("clam")
    except tk.TclError:
        pass
    AnalyzerApp(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
