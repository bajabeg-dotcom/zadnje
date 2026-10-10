#!/usr/bin/env python3
"""Desktop GUI for the verified KORG STY tooling."""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any

import korg_sty_tool as sty


OP_INSPECT = "Pregledaj / validiraj"
OP_REPACK = "PA800 native repack"
OP_TO_PA800 = "STYLE → PA800 inner v4 (eksperimentalno)"
OP_TO_V5 = "STYLE → inner v5 (eksperimentalno)"
OPERATIONS = (OP_INSPECT, OP_REPACK, OP_TO_PA800, OP_TO_V5)


def default_paths(input_path: Path, operation: str) -> tuple[Path | None, Path]:
    stem = input_path.stem
    if operation == OP_INSPECT:
        return None, input_path.with_name(f"{stem}.inspect.json")
    if operation == OP_REPACK:
        return (
            input_path.with_name(f"{stem}_PA800_NATIVE.STY"),
            input_path.with_name(f"{stem}_PA800_NATIVE.report.json"),
        )
    if operation == OP_TO_PA800:
        return (
            input_path.with_name(f"{stem}_PA800_V4.STY"),
            input_path.with_name(f"{stem}_PA800_V4.report.json"),
        )
    return (
        input_path.with_name(f"{stem}_V5.STY"),
        input_path.with_name(f"{stem}_V5.report.json"),
    )


def atomic_write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_bytes(data)
    os.replace(temporary, path)


def atomic_write_json(path: Path, value: Any) -> None:
    rendered = json.dumps(value, indent=2, ensure_ascii=False) + "\n"
    atomic_write_bytes(path, rendered.encode("utf-8"))


def conversion_errors(preview: dict[str, Any]) -> list[dict[str, Any]]:
    return [item for item in preview["styles"] if "conversion_error" in item]


def execute_operation(
    operation: str,
    input_path: Path,
    output_path: Path | None,
    report_path: Path,
    *,
    allow_lossy: bool = False,
    expand_element_info: bool = False,
) -> tuple[dict[str, Any], str]:
    if operation not in OPERATIONS:
        raise sty.StyError(f"Nepoznata GUI operacija: {operation}")
    if not input_path.is_file():
        raise sty.StyError("Ulazni STY fajl ne postoji")
    if output_path is not None and input_path.resolve() == output_path.resolve():
        raise sty.StyError("Izlaz ne smije prepisati originalni STY fajl")

    raw = input_path.read_bytes()
    if operation == OP_INSPECT:
        report = sty.inspect_bytes(raw, str(input_path))
        atomic_write_json(report_path, report)
        summary = (
            f"Pregled završen: {report['decoded_resource_count']}/"
            f"{report['resource_count']} resursa dekodirano; "
            f"greške: {report['decode_error_count']}."
        )
        return report, summary

    if output_path is None:
        raise sty.StyError("Izlazni STY fajl nije izabran")

    if operation == OP_REPACK:
        output, report = sty.repack_bytes(raw)
        atomic_write_bytes(output_path, output)
        atomic_write_json(report_path, report)
        return report, (
            f"PA800 native repack završen: {report['semantic_resource_count']} "
            "resursa očuvano."
        )

    target_version = 4 if operation == OP_TO_PA800 else 5
    preview = sty.conversion_preview(
        raw,
        str(input_path),
        target_version,
        allow_lossy=allow_lossy,
        expand_element_info=expand_element_info,
    )
    errors = conversion_errors(preview)
    if errors:
        examples = "; ".join(
            f"pozicija {item['resource_position']}: {item['conversion_error']}"
            for item in errors[:4]
        )
        raise sty.StyError(
            f"Konverzija je odbijena za {len(errors)} od {preview['style_count']} "
            f"STYLE resursa. {examples}"
        )
    output, report = sty.convert_style_bytes(
        raw,
        target_version,
        allow_lossy=allow_lossy,
        expand_element_info=expand_element_info,
    )
    report["gui_preview"] = preview
    report["hardware_validation_required"] = True
    report["id09_policy"] = "preserved_byte_exact_not_cross_model_converted"
    atomic_write_bytes(output_path, output)
    atomic_write_json(report_path, report)
    return report, (
        f"Konvertovano {report['converted_style_count']} STYLE resursa u inner "
        f"v{target_version}. ID-09 je očuvan, ali nije međumodelski preveden."
    )


def launch_gui() -> None:
    try:
        import tkinter as tk
        from tkinter import filedialog, messagebox, ttk
        from tkinter.scrolledtext import ScrolledText
    except ImportError as error:
        raise SystemExit(
            "Tkinter nije dostupan. Instaliraj standardni Python sa Tk podrškom."
        ) from error

    class App:
        def __init__(self, root: tk.Tk) -> None:
            self.root = root
            root.title("KORG PA800 STY Tool")
            root.geometry("920x650")
            root.minsize(780, 560)

            self.input_var = tk.StringVar()
            self.output_var = tk.StringVar()
            self.report_var = tk.StringVar()
            self.operation_var = tk.StringVar(value=OP_INSPECT)
            self.lossy_var = tk.BooleanVar(value=False)
            self.expand_var = tk.BooleanVar(value=False)
            self.status_var = tk.StringVar(value="Spremno")

            style = ttk.Style()
            if "vista" in style.theme_names():
                style.theme_use("vista")

            outer = ttk.Frame(root, padding=16)
            outer.pack(fill="both", expand=True)
            ttk.Label(
                outer,
                text="KORG PA800 STY Tool",
                font=("TkDefaultFont", 17, "bold"),
            ).pack(anchor="w")
            ttk.Label(
                outer,
                text=(
                    "Sigurni PA800 parser/repacker i fail-closed STYLE v4/v5 writer. "
                    "Uvijek sačuvaj originalni fajl."
                ),
            ).pack(anchor="w", pady=(2, 14))

            form = ttk.LabelFrame(outer, text="Fajlovi i operacija", padding=12)
            form.pack(fill="x")
            form.columnconfigure(1, weight=1)

            self._path_row(form, 0, "Ulazni STY", self.input_var, self.pick_input)
            ttk.Label(form, text="Operacija").grid(row=1, column=0, sticky="w", pady=6)
            operation = ttk.Combobox(
                form,
                textvariable=self.operation_var,
                values=OPERATIONS,
                state="readonly",
            )
            operation.grid(row=1, column=1, columnspan=2, sticky="ew", pady=6)
            operation.bind("<<ComboboxSelected>>", lambda _event: self.refresh_paths())
            self._path_row(form, 2, "Izlazni STY", self.output_var, self.pick_output)
            self._path_row(form, 3, "JSON izvještaj", self.report_var, self.pick_report)

            options = ttk.Frame(form)
            options.grid(row=4, column=1, columnspan=2, sticky="w", pady=(8, 2))
            ttk.Checkbutton(
                options,
                text="Dozvoli gubitak nepodržanih extension vrijednosti",
                variable=self.lossy_var,
            ).pack(anchor="w")
            ttk.Checkbutton(
                options,
                text="Proširi ElementInfo 28 → 44 canonical nastavkom",
                variable=self.expand_var,
            ).pack(anchor="w", pady=(4, 0))

            warning = ttk.LabelFrame(outer, text="Važno", padding=10)
            warning.pack(fill="x", pady=12)
            ttk.Label(
                warning,
                text=(
                    "PA800 native repack je potvrđena operacija za PA800 korpus. "
                    "STYLE v4/v5 opcije prevode ID-06, ali ne prevode kompletan "
                    "međumodelski ID-09/STS. Takav izlaz testiraj samo uz backup."
                ),
                foreground="#9b2c2c",
                wraplength=840,
                justify="left",
            ).pack(anchor="w")

            controls = ttk.Frame(outer)
            controls.pack(fill="x")
            self.run_button = ttk.Button(
                controls,
                text="Pokreni",
                command=self.start,
            )
            self.run_button.pack(side="left")
            ttk.Button(controls, text="Otvori izlazni folder", command=self.open_folder).pack(
                side="left", padx=8
            )
            self.progress = ttk.Progressbar(controls, mode="indeterminate")
            self.progress.pack(side="right", fill="x", expand=True, padx=(18, 0))

            ttk.Label(outer, textvariable=self.status_var).pack(anchor="w", pady=(10, 4))
            self.log = ScrolledText(outer, height=15, wrap="word", state="disabled")
            self.log.pack(fill="both", expand=True)
            self.write_log("Izaberi STY fajl. Za tvoj PA800 prvo koristi Pregledaj, zatim PA800 native repack.\n")

        def _path_row(self, parent, row, label, variable, command) -> None:
            ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=6)
            ttk.Entry(parent, textvariable=variable).grid(
                row=row, column=1, sticky="ew", padx=(12, 8), pady=6
            )
            ttk.Button(parent, text="Izaberi…", command=command).grid(
                row=row, column=2, pady=6
            )

        def pick_input(self) -> None:
            selected = filedialog.askopenfilename(
                title="Izaberi KORG STY fajl",
                filetypes=(("KORG STYLE", "*.STY *.sty"), ("Svi fajlovi", "*.*")),
            )
            if selected:
                self.input_var.set(selected)
                self.refresh_paths(force=True)

        def refresh_paths(self, force: bool = False) -> None:
            value = self.input_var.get().strip()
            if not value:
                return
            output, report = default_paths(Path(value), self.operation_var.get())
            if force or not self.output_var.get().strip():
                self.output_var.set(str(output) if output else "")
            else:
                self.output_var.set(str(output) if output else "")
            self.report_var.set(str(report))

        def pick_output(self) -> None:
            selected = filedialog.asksaveasfilename(
                title="Izaberi izlazni STY",
                defaultextension=".STY",
                filetypes=(("KORG STYLE", "*.STY"),),
            )
            if selected:
                self.output_var.set(selected)

        def pick_report(self) -> None:
            selected = filedialog.asksaveasfilename(
                title="Izaberi JSON izvještaj",
                defaultextension=".json",
                filetypes=(("JSON", "*.json"),),
            )
            if selected:
                self.report_var.set(selected)

        def confirm_experimental(self) -> bool:
            operation = self.operation_var.get()
            if operation not in {OP_TO_PA800, OP_TO_V5}:
                return True
            warning = (
                "Ova opcija prevodi STYLE ID-06 podatke, ali ID-09/STS ostaje "
                "byte-exact iz izvornog modela. Izlaz nije potvrđen na instrumentu.\n\n"
                "Nastaviti?"
            )
            if not messagebox.askyesno("Eksperimentalna konverzija", warning):
                return False
            if self.lossy_var.get():
                return messagebox.askyesno(
                    "Lossy režim",
                    "Lossy režim može odbaciti nepodržane vrijednosti. Sigurno nastaviti?",
                )
            return True

        def start(self) -> None:
            if not self.confirm_experimental():
                return
            try:
                input_path = Path(self.input_var.get().strip())
                report_path = Path(self.report_var.get().strip())
                output_value = self.output_var.get().strip()
                output_path = Path(output_value) if output_value else None
                if not self.report_var.get().strip():
                    raise sty.StyError("Izaberi JSON izvještaj")
                if self.operation_var.get() != OP_INSPECT and output_path is None:
                    raise sty.StyError("Izaberi izlazni STY fajl")
            except (OSError, sty.StyError) as error:
                messagebox.showerror("Greška", str(error))
                return

            self.set_busy(True)
            operation_name = self.operation_var.get()
            allow_lossy = self.lossy_var.get()
            expand_element_info = self.expand_var.get()
            self.write_log(f"\nPokrećem: {operation_name}\nUlaz: {input_path}\n")

            def worker() -> None:
                try:
                    _report, summary = execute_operation(
                        operation_name,
                        input_path,
                        output_path,
                        report_path,
                        allow_lossy=allow_lossy,
                        expand_element_info=expand_element_info,
                    )
                except Exception as error:
                    self.root.after(0, lambda: self.finish_error(error))
                    return
                self.root.after(0, lambda: self.finish_success(summary, output_path, report_path))

            threading.Thread(target=worker, daemon=True).start()

        def set_busy(self, busy: bool) -> None:
            self.run_button.configure(state="disabled" if busy else "normal")
            if busy:
                self.progress.start(12)
                self.status_var.set("Radim…")
            else:
                self.progress.stop()
                self.status_var.set("Spremno")

        def finish_success(self, summary: str, output: Path | None, report: Path) -> None:
            self.set_busy(False)
            self.write_log(summary + "\n")
            if output:
                self.write_log(f"Izlaz: {output}\n")
            self.write_log(f"Izvještaj: {report}\n")
            messagebox.showinfo("Završeno", summary)

        def finish_error(self, error: Exception) -> None:
            self.set_busy(False)
            self.write_log(f"GREŠKA: {error}\n")
            messagebox.showerror("Konverzija nije izvršena", str(error))

        def write_log(self, text: str) -> None:
            self.log.configure(state="normal")
            self.log.insert("end", text)
            self.log.see("end")
            self.log.configure(state="disabled")

        def open_folder(self) -> None:
            value = self.output_var.get().strip() or self.report_var.get().strip()
            folder = (Path(value).parent if value else Path.cwd()).resolve()
            try:
                if os.name == "nt":
                    os.startfile(folder)  # type: ignore[attr-defined]
                elif os.uname().sysname == "Darwin":
                    import subprocess
                    subprocess.Popen(["open", str(folder)])
                else:
                    import subprocess
                    subprocess.Popen(["xdg-open", str(folder)])
            except OSError as error:
                messagebox.showerror("Ne mogu otvoriti folder", str(error))

    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    launch_gui()