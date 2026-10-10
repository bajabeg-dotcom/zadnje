"""Tkinter desktop interface for KorgPaManager."""

from __future__ import annotations

import argparse
import sys
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk
from typing import Optional

from . import __version__
from .core import DRUM_CHANNELS, STYLE_CHANNELS, MidiDocument, MidiDocumentError
from .gm_drums import gm_reference_label, midi_note_name
from .models import EventRef, NoteActivity, ProgramAssignment

APP_NAME = "KorgPaManager"
APP_VERSION = __version__


class MarkerDialog(simpledialog.Dialog):
    def __init__(
        self,
        parent: tk.Misc,
        track_count: int,
        title: str,
        tick: int = 0,
        text: str = "v1cv1",
        track: int = 0,
        meta_type: str = "marker",
        allow_track_type: bool = True,
    ) -> None:
        self.track_count = track_count
        self.initial_tick = tick
        self.initial_text = text
        self.initial_track = track
        self.initial_type = meta_type
        self.allow_track_type = allow_track_type
        self.result: Optional[tuple[int, str, int, str]] = None
        super().__init__(parent, title)

    def body(self, master: tk.Misc) -> tk.Widget:
        ttk.Label(master, text="Absolute tick:").grid(
            row=0, column=0, sticky="w", padx=6, pady=5
        )
        self.tick_var = tk.StringVar(value=str(self.initial_tick))
        tick_entry = ttk.Entry(master, textvariable=self.tick_var, width=22)
        tick_entry.grid(row=0, column=1, sticky="ew", padx=6, pady=5)

        ttk.Label(master, text="Marker text:").grid(
            row=1, column=0, sticky="w", padx=6, pady=5
        )
        self.text_var = tk.StringVar(value=self.initial_text)
        ttk.Entry(master, textvariable=self.text_var, width=32).grid(
            row=1, column=1, sticky="ew", padx=6, pady=5
        )

        ttk.Label(master, text="Track (zero-based):").grid(
            row=2, column=0, sticky="w", padx=6, pady=5
        )
        self.track_var = tk.StringVar(value=str(self.initial_track))
        track_box = ttk.Combobox(
            master,
            textvariable=self.track_var,
            values=[str(value) for value in range(self.track_count)],
            state="readonly" if self.allow_track_type else "disabled",
            width=20,
        )
        track_box.grid(row=2, column=1, sticky="ew", padx=6, pady=5)

        ttk.Label(master, text="Meta type:").grid(
            row=3, column=0, sticky="w", padx=6, pady=5
        )
        self.type_var = tk.StringVar(value=self.initial_type)
        type_box = ttk.Combobox(
            master,
            textvariable=self.type_var,
            values=("marker", "cue_marker", "text"),
            state="readonly" if self.allow_track_type else "disabled",
            width=20,
        )
        type_box.grid(row=3, column=1, sticky="ew", padx=6, pady=5)
        master.columnconfigure(1, weight=1)
        return tick_entry

    def validate(self) -> bool:
        try:
            tick = int(self.tick_var.get())
            track = int(self.track_var.get())
            if tick < 0:
                raise ValueError("Tick must be non-negative")
            if not 0 <= track < self.track_count:
                raise ValueError("Track is outside the available range")
            if not self.text_var.get():
                raise ValueError("Marker text cannot be empty")
        except ValueError as exc:
            messagebox.showerror("Invalid marker", str(exc), parent=self)
            return False
        return True

    def apply(self) -> None:
        self.result = (
            int(self.tick_var.get()),
            self.text_var.get(),
            int(self.track_var.get()),
            self.type_var.get(),
        )


class AssignmentDialog(simpledialog.Dialog):
    def __init__(
        self,
        parent: tk.Misc,
        track_count: int,
        title: str,
        assignment: Optional[ProgramAssignment] = None,
    ) -> None:
        self.track_count = track_count
        self.assignment = assignment
        self.result: Optional[
            tuple[int, int, int, int, Optional[int], Optional[int]]
        ] = None
        super().__init__(parent, title)

    def body(self, master: tk.Misc) -> tk.Widget:
        item = self.assignment
        channel_values = [
            f"{channel + 1} — {role}" for channel, role in STYLE_CHANNELS.items()
        ]
        defaults = {
            "track": str(item.ref.track_index if item else 0),
            "tick": str(item.tick if item else 0),
            "channel": f"{item.channel + 1} — {item.role}"
            if item
            else channel_values[0],
            "msb": "" if item is None or item.bank_msb is None else str(item.bank_msb),
            "lsb": "" if item is None or item.bank_lsb is None else str(item.bank_lsb),
            "program": str(item.program if item else 0),
        }
        labels = (
            ("Track (zero-based):", "track"),
            ("Absolute tick:", "tick"),
            ("Style channel:", "channel"),
            ("Bank MSB / CC00 (0–127, blank=unchanged):", "msb"),
            ("Bank LSB / CC32 (0–127, blank=unchanged):", "lsb"),
            ("Program (0–127):", "program"),
        )
        self.vars: dict[str, tk.StringVar] = {}
        first: Optional[tk.Widget] = None
        for row, (label, key) in enumerate(labels):
            ttk.Label(master, text=label).grid(
                row=row, column=0, sticky="w", padx=6, pady=4
            )
            variable = tk.StringVar(value=defaults[key])
            self.vars[key] = variable
            if key == "track":
                widget: tk.Widget = ttk.Combobox(
                    master,
                    textvariable=variable,
                    values=[str(value) for value in range(self.track_count)],
                    state="disabled" if item else "readonly",
                )
            elif key == "channel":
                widget = ttk.Combobox(
                    master,
                    textvariable=variable,
                    values=channel_values,
                    state="disabled" if item else "readonly",
                )
            else:
                widget = ttk.Entry(master, textvariable=variable)
            widget.grid(row=row, column=1, sticky="ew", padx=6, pady=4)
            if first is None and key in {"tick", "program"}:
                first = widget
        ttk.Label(
            master,
            text=(
                "Values are raw MIDI bytes. Korg sound identity is CC00.CC32.Program; "
                "no cross-model mapping is inferred."
            ),
            wraplength=470,
            foreground="#555555",
        ).grid(row=len(labels), column=0, columnspan=2, sticky="w", padx=6, pady=(8, 4))
        master.columnconfigure(1, weight=1)
        return first or master

    def validate(self) -> bool:
        try:
            track = int(self.vars["track"].get())
            tick = int(self.vars["tick"].get())
            channel = int(self.vars["channel"].get().split(" ", 1)[0]) - 1
            if channel not in STYLE_CHANNELS:
                raise ValueError("Channel must be one of the Korg style channels 9..16")
            program = int(self.vars["program"].get())
            msb_text = self.vars["msb"].get().strip()
            lsb_text = self.vars["lsb"].get().strip()
            msb = None if not msb_text else int(msb_text)
            lsb = None if not lsb_text else int(lsb_text)
            if not 0 <= track < self.track_count:
                raise ValueError("Track is outside the available range")
            if tick < 0:
                raise ValueError("Tick must be non-negative")
            for name, value in (
                ("Program", program),
                ("Bank MSB", msb),
                ("Bank LSB", lsb),
            ):
                if value is not None and not 0 <= value <= 127:
                    raise ValueError(f"{name} must be in the range 0..127")
        except ValueError as exc:
            messagebox.showerror("Invalid assignment", str(exc), parent=self)
            return False
        return True

    def apply(self) -> None:
        msb_text = self.vars["msb"].get().strip()
        lsb_text = self.vars["lsb"].get().strip()
        self.result = (
            int(self.vars["track"].get()),
            int(self.vars["tick"].get()),
            int(self.vars["channel"].get().split(" ", 1)[0]) - 1,
            int(self.vars["program"].get()),
            None if not msb_text else int(msb_text),
            None if not lsb_text else int(lsb_text),
        )


class ChannelDialog(simpledialog.Dialog):
    def __init__(self, parent: tk.Misc) -> None:
        self.result: Optional[tuple[int, int, bool]] = None
        super().__init__(parent, "Move or swap style channels")

    def body(self, master: tk.Misc) -> tk.Widget:
        values = [f"{channel + 1} — {role}" for channel, role in STYLE_CHANNELS.items()]
        ttk.Label(master, text="Source:").grid(
            row=0, column=0, sticky="w", padx=6, pady=5
        )
        self.source_var = tk.StringVar(value=values[0])
        source = ttk.Combobox(
            master, textvariable=self.source_var, values=values, state="readonly"
        )
        source.grid(row=0, column=1, sticky="ew", padx=6, pady=5)
        ttk.Label(master, text="Target:").grid(
            row=1, column=0, sticky="w", padx=6, pady=5
        )
        self.target_var = tk.StringVar(value=values[1])
        ttk.Combobox(
            master, textvariable=self.target_var, values=values, state="readonly"
        ).grid(row=1, column=1, sticky="ew", padx=6, pady=5)
        self.swap_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            master,
            text="Swap both channels instead of moving source into target",
            variable=self.swap_var,
        ).grid(row=2, column=0, columnspan=2, sticky="w", padx=6, pady=8)
        ttk.Label(
            master,
            text="This changes every channel message on all tracks and preserves all event ticks.",
            wraplength=440,
            foreground="#555555",
        ).grid(row=3, column=0, columnspan=2, sticky="w", padx=6, pady=4)
        master.columnconfigure(1, weight=1)
        return source

    def validate(self) -> bool:
        source = int(self.source_var.get().split(" ", 1)[0]) - 1
        target = int(self.target_var.get().split(" ", 1)[0]) - 1
        if source == target:
            messagebox.showerror(
                "Invalid channels", "Source and target are the same.", parent=self
            )
            return False
        return True

    def apply(self) -> None:
        self.result = (
            int(self.source_var.get().split(" ", 1)[0]) - 1,
            int(self.target_var.get().split(" ", 1)[0]) - 1,
            self.swap_var.get(),
        )


class RemapDialog(simpledialog.Dialog):
    def __init__(self, parent: tk.Misc, channel: int, source_note: int) -> None:
        self.channel = channel
        self.source_note = source_note
        self.result: Optional[int] = None
        super().__init__(parent, "Remap Drum/Percussion note")

    def body(self, master: tk.Misc) -> tk.Widget:
        ttk.Label(
            master,
            text=f"MIDI channel: {self.channel + 1} ({STYLE_CHANNELS[self.channel]})",
        ).grid(row=0, column=0, columnspan=2, sticky="w", padx=6, pady=4)
        ttk.Label(
            master,
            text=f"Source: {self.source_note} ({midi_note_name(self.source_note)})",
        ).grid(row=1, column=0, columnspan=2, sticky="w", padx=6, pady=4)
        ttk.Label(master, text="Target note (0–127):").grid(
            row=2, column=0, sticky="w", padx=6, pady=5
        )
        self.target_var = tk.StringVar(value=str(self.source_note))
        entry = ttk.Entry(master, textvariable=self.target_var)
        entry.grid(row=2, column=1, sticky="ew", padx=6, pady=5)
        ttk.Label(
            master,
            text="All matching note-on, note-off, and poly-pressure keys are changed; ticks remain exact.",
            wraplength=430,
            foreground="#555555",
        ).grid(row=3, column=0, columnspan=2, sticky="w", padx=6, pady=6)
        master.columnconfigure(1, weight=1)
        return entry

    def validate(self) -> bool:
        try:
            value = int(self.target_var.get())
            if not 0 <= value <= 127:
                raise ValueError("Target note must be in the range 0..127")
            if value == self.source_note:
                raise ValueError("Target note is the same as the source note")
        except ValueError as exc:
            messagebox.showerror("Invalid remap", str(exc), parent=self)
            return False
        return True

    def apply(self) -> None:
        self.result = int(self.target_var.get())


class TimeSliceDialog(simpledialog.Dialog):
    def __init__(
        self, parent: tk.Misc, channel: int, note_min: int, note_max: int
    ) -> None:
        self.channel = channel
        self.note_min = note_min
        self.note_max = note_max
        self.result: Optional[tuple[int, int, int, int, int]] = None
        super().__init__(parent, "TimeSlice / trigger range transform")

    def body(self, master: tk.Misc) -> tk.Widget:
        defaults = (str(self.note_min), str(self.note_max), "0", "0", "100")
        labels = (
            "First note (0–127):",
            "Last note (0–127):",
            "Note/slice offset (-127..127):",
            "Absolute tick offset:",
            "Velocity scale percent (1..400):",
        )
        self.vars: list[tk.StringVar] = []
        first: Optional[tk.Widget] = None
        ttk.Label(
            master,
            text=f"Scope: MIDI channel {self.channel + 1} ({STYLE_CHANNELS[self.channel]})",
        ).grid(row=0, column=0, columnspan=2, sticky="w", padx=6, pady=(4, 8))
        for index, (label, default) in enumerate(zip(labels, defaults), start=1):
            ttk.Label(master, text=label).grid(
                row=index, column=0, sticky="w", padx=6, pady=4
            )
            variable = tk.StringVar(value=default)
            self.vars.append(variable)
            entry = ttk.Entry(master, textvariable=variable)
            entry.grid(row=index, column=1, sticky="ew", padx=6, pady=4)
            if first is None:
                first = entry
        ttk.Label(
            master,
            text=(
                "KorgPaManager treats these as generic MIDI triggers. A MIDI file alone does not "
                "prove Time Slice origin. Note-on/off keys move together; non-note events are untouched."
            ),
            wraplength=500,
            foreground="#555555",
        ).grid(row=6, column=0, columnspan=2, sticky="w", padx=6, pady=8)
        master.columnconfigure(1, weight=1)
        return first or master

    def validate(self) -> bool:
        try:
            low, high, note_offset, tick_offset, velocity = [
                int(variable.get()) for variable in self.vars
            ]
            if not 0 <= low <= high <= 127:
                raise ValueError(
                    "The note range must satisfy 0 <= first <= last <= 127"
                )
            if not -127 <= note_offset <= 127:
                raise ValueError("Note offset must be in the range -127..127")
            if low + note_offset < 0 or high + note_offset > 127:
                raise ValueError("The note offset would produce notes outside 0..127")
            if not 1 <= velocity <= 400:
                raise ValueError("Velocity percent must be in the range 1..400")
        except ValueError as exc:
            messagebox.showerror("Invalid transform", str(exc), parent=self)
            return False
        return True

    def apply(self) -> None:
        self.result = tuple(int(variable.get()) for variable in self.vars)  # type: ignore[assignment]


class KorgPaManagerApp(tk.Tk):
    def __init__(self, initial_path: Optional[str] = None) -> None:
        super().__init__()
        self.document = MidiDocument()
        self.title(APP_NAME)
        self.geometry("1180x760")
        self.minsize(940, 620)
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.marker_refs: dict[str, EventRef] = {}
        self.assignment_items: dict[str, ProgramAssignment] = {}
        self.note_items: dict[str, NoteActivity] = {}
        self._build_style()
        self._build_menu()
        self._build_toolbar()
        self._build_content()
        self._build_statusbar()
        self._bind_shortcuts()
        self.refresh_all()
        if initial_path:
            self.after(50, lambda: self.open_path(initial_path))

    def _build_style(self) -> None:
        style = ttk.Style(self)
        available = style.theme_names()
        if "clam" in available:
            style.theme_use("clam")
        style.configure("Treeview", rowheight=24)
        style.configure("Title.TLabel", font=("TkDefaultFont", 15, "bold"))
        style.configure("Caution.TLabel", foreground="#8a4b08")

    def _build_menu(self) -> None:
        menu = tk.Menu(self)
        file_menu = tk.Menu(menu, tearoff=False)
        file_menu.add_command(
            label="Open MIDI…", accelerator="Ctrl+O", command=self.open_file
        )
        file_menu.add_command(
            label="Save edited copy as…",
            accelerator="Ctrl+Shift+S",
            command=self.save_as,
        )
        file_menu.add_separator()
        file_menu.add_command(label="Export JSON report…", command=self.export_json)
        file_menu.add_command(
            label="Export Drum/Percussion CSV…", command=self.export_csv
        )
        file_menu.add_separator()
        file_menu.add_command(label="Exit", command=self.on_close)
        menu.add_cascade(label="File", menu=file_menu)

        edit_menu = tk.Menu(menu, tearoff=False)
        edit_menu.add_command(label="Undo", accelerator="Ctrl+Z", command=self.undo)
        edit_menu.add_command(label="Redo", accelerator="Ctrl+Y", command=self.redo)
        menu.add_cascade(label="Edit", menu=edit_menu)

        help_menu = tk.Menu(menu, tearoff=False)
        help_menu.add_command(
            label="Evidence and limitations", command=self.show_evidence
        )
        help_menu.add_command(label="About", command=self.show_about)
        menu.add_cascade(label="Help", menu=help_menu)
        self.config(menu=menu)

    def _build_toolbar(self) -> None:
        toolbar = ttk.Frame(self, padding=(8, 6))
        toolbar.pack(fill="x")
        ttk.Button(toolbar, text="Open MIDI", command=self.open_file).pack(
            side="left", padx=(0, 5)
        )
        ttk.Button(toolbar, text="Save edited copy as…", command=self.save_as).pack(
            side="left", padx=5
        )
        ttk.Separator(toolbar, orient="vertical").pack(side="left", fill="y", padx=8)
        self.undo_button = ttk.Button(toolbar, text="Undo", command=self.undo)
        self.undo_button.pack(side="left", padx=3)
        self.redo_button = ttk.Button(toolbar, text="Redo", command=self.redo)
        self.redo_button.pack(side="left", padx=3)
        ttk.Label(
            toolbar,
            text="Source is never overwritten",
            style="Caution.TLabel",
        ).pack(side="right", padx=8)

    def _build_content(self) -> None:
        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill="both", expand=True, padx=8, pady=(0, 6))
        self.overview_tab = ttk.Frame(self.notebook, padding=12)
        self.markers_tab = ttk.Frame(self.notebook, padding=8)
        self.channels_tab = ttk.Frame(self.notebook, padding=8)
        self.assignments_tab = ttk.Frame(self.notebook, padding=8)
        self.notes_tab = ttk.Frame(self.notebook, padding=8)
        self.notebook.add(self.overview_tab, text="Overview")
        self.notebook.add(self.markers_tab, text="Markers / Sections")
        self.notebook.add(self.channels_tab, text="Style Channels")
        self.notebook.add(self.assignments_tab, text="Bank / Program")
        self.notebook.add(self.notes_tab, text="Drum, Percussion & TimeSlice")
        self._build_overview()
        self._build_markers()
        self._build_channels()
        self._build_assignments()
        self._build_notes()

    def _build_overview(self) -> None:
        ttk.Label(
            self.overview_tab, text="Korg Pa Style MIDI Manager", style="Title.TLabel"
        ).pack(anchor="w")
        ttk.Label(
            self.overview_tab,
            text=(
                "A write-capable Standard MIDI editor for Korg Pa style channels, section markers, "
                "bank/program events, and Drum/Percussion trigger notes."
            ),
            wraplength=930,
        ).pack(anchor="w", pady=(4, 12))
        self.overview_text = tk.Text(
            self.overview_tab,
            height=17,
            wrap="word",
            state="disabled",
            relief="solid",
            borderwidth=1,
            padx=10,
            pady=10,
        )
        self.overview_text.pack(fill="both", expand=True)
        ttk.Label(
            self.overview_tab,
            text=(
                "Important: General MIDI percussion names are references only. The audible PA800 "
                "element depends on the selected Drum Kit, key, velocity layer, and user samples. "
                "Pa4X sounds are not automatically converted to PA800 sounds."
            ),
            wraplength=950,
            style="Caution.TLabel",
        ).pack(anchor="w", pady=(10, 0))

    @staticmethod
    def _tree(
        parent: tk.Misc, columns: tuple[str, ...], headings: tuple[str, ...]
    ) -> ttk.Treeview:
        frame = ttk.Frame(parent)
        frame.pack(fill="both", expand=True)
        tree = ttk.Treeview(
            frame, columns=columns, show="headings", selectmode="browse"
        )
        vertical = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
        horizontal = ttk.Scrollbar(frame, orient="horizontal", command=tree.xview)
        tree.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
        tree.grid(row=0, column=0, sticky="nsew")
        vertical.grid(row=0, column=1, sticky="ns")
        horizontal.grid(row=1, column=0, sticky="ew")
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)
        for column, heading in zip(columns, headings):
            tree.heading(column, text=heading)
            tree.column(column, width=105, minwidth=55, stretch=True)
        return tree

    def _build_markers(self) -> None:
        info = ttk.Frame(self.markers_tab)
        info.pack(fill="x", pady=(0, 6))
        ttk.Label(
            info,
            text=(
                "Recognizes Korg lower-case EnCVn forms such as v1cv1, i1cv1, f1cv1, and e1cv1. "
                "Unknown marker/cue text is retained and shown."
            ),
            wraplength=900,
        ).pack(side="left", fill="x", expand=True)
        buttons = ttk.Frame(self.markers_tab)
        buttons.pack(fill="x", pady=(0, 6))
        ttk.Button(buttons, text="Add marker…", command=self.add_marker).pack(
            side="left", padx=3
        )
        ttk.Button(buttons, text="Edit selected…", command=self.edit_marker).pack(
            side="left", padx=3
        )
        ttk.Button(buttons, text="Delete selected", command=self.delete_marker).pack(
            side="left", padx=3
        )
        self.marker_tree = self._tree(
            self.markers_tab,
            ("tick", "bbt", "track", "event", "section", "cv", "text", "recognized"),
            (
                "Tick",
                "Bar:Beat:Tick",
                "Track",
                "Meta type",
                "Section",
                "CV",
                "Exact text",
                "Known",
            ),
        )
        self.marker_tree.column("text", width=230)

    def _build_channels(self) -> None:
        ttk.Label(
            self.channels_tab,
            text=(
                "Official style mapping: MIDI 9–16 correspond to Mido channels 8–15. "
                "Move changes source events to target; Swap exchanges both channels."
            ),
            wraplength=950,
        ).pack(anchor="w", pady=(0, 6))
        bar = ttk.Frame(self.channels_tab)
        bar.pack(fill="x", pady=(0, 6))
        ttk.Button(bar, text="Move / swap channels…", command=self.remap_channels).pack(
            side="left"
        )
        self.channel_tree = self._tree(
            self.channels_tab,
            ("midi", "mido", "role", "messages", "notes", "programs", "first", "last"),
            (
                "MIDI channel",
                "Mido channel",
                "Korg role",
                "Messages",
                "Note-ons",
                "Programs",
                "First tick",
                "Last tick",
            ),
        )

    def _build_assignments(self) -> None:
        ttk.Label(
            self.assignments_tab,
            text=(
                "Exact raw values are shown in Korg's CC00.CC32.Program form (0–127). "
                "An absent bank byte is shown as —; no Pa4X-to-PA800 mapping is invented."
            ),
            wraplength=950,
        ).pack(anchor="w", pady=(0, 6))
        bar = ttk.Frame(self.assignments_tab)
        bar.pack(fill="x", pady=(0, 6))
        ttk.Button(bar, text="Add assignment…", command=self.add_assignment).pack(
            side="left", padx=3
        )
        ttk.Button(bar, text="Edit selected…", command=self.edit_assignment).pack(
            side="left", padx=3
        )
        ttk.Button(bar, text="Delete selected PC", command=self.delete_assignment).pack(
            side="left", padx=3
        )
        self.assignment_tree = self._tree(
            self.assignments_tab,
            (
                "tick",
                "bbt",
                "track",
                "midi",
                "mido",
                "role",
                "msb",
                "lsb",
                "program",
                "identity",
            ),
            (
                "Tick",
                "Bar:Beat:Tick",
                "Track",
                "MIDI ch",
                "Mido ch",
                "Role",
                "CC00",
                "CC32",
                "Program",
                "CC00.CC32.PC",
            ),
        )
        self.assignment_tree.column("identity", width=145)

    def _build_notes(self) -> None:
        controls = ttk.Frame(self.notes_tab)
        controls.pack(fill="x", pady=(0, 6))
        ttk.Label(controls, text="View:").pack(side="left")
        self.note_scope_var = tk.StringVar(value="Both Drum and Percussion")
        scope = ttk.Combobox(
            controls,
            textvariable=self.note_scope_var,
            values=(
                "Both Drum and Percussion",
                "MIDI 10 — Drums",
                "MIDI 11 — Percussion",
            ),
            state="readonly",
            width=28,
        )
        scope.pack(side="left", padx=5)
        scope.bind("<<ComboboxSelected>>", lambda _event: self.refresh_notes())
        ttk.Button(controls, text="Remap selected note…", command=self.remap_note).pack(
            side="left", padx=3
        )
        ttk.Button(
            controls, text="Edit custom label…", command=self.edit_note_label
        ).pack(side="left", padx=3)
        ttk.Button(
            controls,
            text="TimeSlice range transform…",
            command=self.timeslice_transform,
        ).pack(side="left", padx=3)
        ttk.Button(controls, text="Export CSV…", command=self.export_csv).pack(
            side="right", padx=3
        )

        pane = ttk.Panedwindow(self.notes_tab, orient="vertical")
        pane.pack(fill="both", expand=True)
        upper = ttk.Frame(pane)
        lower = ttk.Frame(pane)
        pane.add(upper, weight=3)
        pane.add(lower, weight=2)
        self.note_tree = self._tree(
            upper,
            (
                "midi",
                "role",
                "note",
                "name",
                "gm",
                "custom",
                "hits",
                "vel",
                "first",
                "last",
                "kit",
                "sections",
            ),
            (
                "MIDI ch",
                "Role",
                "Note",
                "Name",
                "GM reference only",
                "Custom label",
                "Hits",
                "Velocity",
                "First tick",
                "Last tick",
                "Effective CC00.CC32.PC",
                "Sections",
            ),
        )
        self.note_tree.column("gm", width=165)
        self.note_tree.column("custom", width=160)
        self.note_tree.column("kit", width=180)
        self.note_tree.column("sections", width=220)
        self.note_tree.bind(
            "<<TreeviewSelect>>", lambda _event: self.refresh_note_hits()
        )

        lower_header = ttk.Frame(lower)
        lower_header.pack(fill="x", pady=(6, 3))
        ttk.Label(
            lower_header, text="Exact note-on activity for selected element"
        ).pack(side="left")
        self.timeslice_summary = ttk.Label(lower_header, text="", foreground="#555555")
        self.timeslice_summary.pack(side="right")
        self.hit_tree = self._tree(
            lower,
            (
                "tick",
                "bbt",
                "track",
                "event",
                "velocity",
                "msb",
                "lsb",
                "program",
                "section",
            ),
            (
                "Tick",
                "Bar:Beat:Tick",
                "Track",
                "Message",
                "Velocity",
                "CC00",
                "CC32",
                "Program",
                "Active section",
            ),
        )
        self.hit_tree.column("section", width=230)

    def _build_statusbar(self) -> None:
        self.status_var = tk.StringVar(value="Open a Standard MIDI File to begin.")
        ttk.Label(
            self,
            textvariable=self.status_var,
            relief="sunken",
            anchor="w",
            padding=(6, 3),
        ).pack(fill="x", side="bottom")

    def _bind_shortcuts(self) -> None:
        self.bind_all("<Control-o>", lambda _event: self.open_file())
        self.bind_all("<Control-Shift-S>", lambda _event: self.save_as())
        self.bind_all("<Control-z>", lambda _event: self.undo())
        self.bind_all("<Control-y>", lambda _event: self.redo())

    def _set_text(self, widget: tk.Text, text: str) -> None:
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.insert("1.0", text)
        widget.configure(state="disabled")

    def _require_loaded(self) -> bool:
        if not self.document.is_loaded:
            messagebox.showinfo(APP_NAME, "Open a MIDI file first.", parent=self)
            return False
        return True

    def _selected_iid(self, tree: ttk.Treeview) -> Optional[str]:
        selection = tree.selection()
        if not selection:
            messagebox.showinfo(APP_NAME, "Select a row first.", parent=self)
            return None
        return selection[0]

    def _ask_discard(self) -> bool:
        if not self.document.dirty:
            return True
        return messagebox.askyesno(
            "Unsaved edits",
            "The current in-memory document has unsaved edits. Discard them?",
            icon="warning",
            parent=self,
        )

    def open_file(self) -> None:
        if not self._ask_discard():
            return
        path = filedialog.askopenfilename(
            parent=self,
            title="Open Standard MIDI File",
            filetypes=(("MIDI files", "*.mid *.midi"), ("All files", "*.*")),
        )
        if path:
            self.open_path(path)

    def open_path(self, path: str) -> None:
        try:
            self.document.load(path)
        except MidiDocumentError as exc:
            messagebox.showerror("Open failed", str(exc), parent=self)
            self.status_var.set("Open failed; the previous document was not changed.")
            return
        self.refresh_all()
        self.status_var.set(f"Loaded source: {self.document.source_path}")

    def save_as(self) -> None:
        if not self._require_loaded():
            return
        source = self.document.source_path
        initial_name = f"{source.stem}_edited.mid" if source else "edited.mid"
        path = filedialog.asksaveasfilename(
            parent=self,
            title="Save edited MIDI copy",
            defaultextension=".mid",
            initialdir=str(source.parent) if source else None,
            initialfile=initial_name,
            filetypes=(("Standard MIDI File", "*.mid"), ("MIDI file", "*.midi")),
        )
        if not path:
            return
        destination = Path(path)
        if destination.exists() and not messagebox.askyesno(
            "Replace output?",
            f"The output file already exists:\n{destination}\n\nReplace it?",
            icon="warning",
            parent=self,
        ):
            return
        try:
            result = self.document.save_as(destination)
        except MidiDocumentError as exc:
            messagebox.showerror("Save failed", str(exc), parent=self)
            self.status_var.set("Save failed; no validated output was installed.")
            return
        self.refresh_all()
        labels = f"\nLabels: {result.labels_path}" if result.labels_path else ""
        messagebox.showinfo(
            "Validated copy saved",
            f"Saved: {result.path}\nSize: {result.file_size:,} bytes\n"
            f"Tracks: {result.track_count}\nMessages: {result.message_count}{labels}",
            parent=self,
        )
        self.status_var.set(f"Validated edited copy saved: {result.path}")

    def undo(self) -> None:
        if not self.document.can_undo:
            self.bell()
            return
        try:
            description = self.document.undo()
        except MidiDocumentError as exc:
            messagebox.showerror("Undo failed", str(exc), parent=self)
            return
        self.refresh_all()
        self.status_var.set(f"Undid: {description}")

    def redo(self) -> None:
        if not self.document.can_redo:
            self.bell()
            return
        try:
            description = self.document.redo()
        except MidiDocumentError as exc:
            messagebox.showerror("Redo failed", str(exc), parent=self)
            return
        self.refresh_all()
        self.status_var.set(f"Redid: {description}")

    def refresh_all(self) -> None:
        loaded = self.document.is_loaded
        self.undo_button.configure(
            state="normal" if self.document.can_undo else "disabled"
        )
        self.redo_button.configure(
            state="normal" if self.document.can_redo else "disabled"
        )
        self.refresh_overview()
        self.refresh_markers()
        self.refresh_channels()
        self.refresh_assignments()
        self.refresh_notes()
        source_name = (
            self.document.source_path.name
            if loaded and self.document.source_path
            else "No file"
        )
        dirty_mark = " *" if self.document.dirty else ""
        self.title(f"{APP_NAME} — {source_name}{dirty_mark}")

    def refresh_overview(self) -> None:
        if not self.document.is_loaded:
            text = (
                "No MIDI file is loaded.\n\n"
                "Use File > Open MIDI to inspect and edit a Standard MIDI File. Edits remain in "
                "memory until you choose Save edited copy as. The loaded source is protected."
            )
        else:
            midi = self.document.midi
            assert midi is not None
            markers = self.document.markers()
            assignments = self.document.program_assignments()
            note_items = self.document.note_activity()
            text = (
                f"Source: {self.document.source_path}\n"
                f"Source SHA-256: {self.document.source_sha256}\n"
                f"Last saved output: {self.document.last_output_path or 'None'}\n\n"
                f"SMF type: {midi.type}\n"
                f"Tracks: {len(midi.tracks)}\n"
                f"Ticks per quarter note: {midi.ticks_per_beat}\n"
                f"Length in ticks: {self.document.total_length_ticks()}\n"
                f"Marker-like events: {len(markers)}\n"
                f"Style-channel program changes: {len(assignments)}\n"
                f"Active Drum/Percussion note keys: {len(note_items)}\n"
                f"State: {'Modified in memory' if self.document.dirty else 'No unsaved edits'}\n\n"
                "Save safety\n"
                "• Save As refuses the loaded source path and same-file aliases.\n"
                "• A sibling temporary file is written, reopened with Mido, structurally checked, "
                "and compared against the in-memory event stream.\n"
                "• Only then is the selected output atomically replaced."
            )
        self._set_text(self.overview_text, text)

    @staticmethod
    def _clear_tree(tree: ttk.Treeview) -> None:
        children = tree.get_children()
        if children:
            tree.delete(*children)

    def refresh_markers(self) -> None:
        self._clear_tree(self.marker_tree)
        self.marker_refs.clear()
        if not self.document.is_loaded:
            return
        for index, marker in enumerate(self.document.markers()):
            iid = f"marker-{index}"
            self.marker_refs[iid] = marker.ref
            self.marker_tree.insert(
                "",
                "end",
                iid=iid,
                values=(
                    marker.tick,
                    self.document.bar_beat_tick(marker.tick),
                    marker.ref.track_index,
                    marker.meta_type,
                    marker.family if marker.recognized else "Other",
                    marker.chord_variation
                    if marker.chord_variation is not None
                    else "—",
                    marker.text,
                    "Yes" if marker.recognized else "No",
                ),
            )

    def refresh_channels(self) -> None:
        self._clear_tree(self.channel_tree)
        if not self.document.is_loaded:
            return
        for item in self.document.channel_summaries():
            self.channel_tree.insert(
                "",
                "end",
                values=(
                    item.midi_channel,
                    item.channel,
                    item.role,
                    item.message_count,
                    item.note_on_count,
                    item.program_count,
                    item.first_tick if item.first_tick is not None else "—",
                    item.last_tick if item.last_tick is not None else "—",
                ),
            )

    def refresh_assignments(self) -> None:
        self._clear_tree(self.assignment_tree)
        self.assignment_items.clear()
        if not self.document.is_loaded:
            return
        for index, item in enumerate(self.document.program_assignments()):
            iid = f"assignment-{index}"
            self.assignment_items[iid] = item
            msb = "—" if item.bank_msb is None else str(item.bank_msb)
            lsb = "—" if item.bank_lsb is None else str(item.bank_lsb)
            identity = f"{msb}.{lsb}.{item.program}"
            self.assignment_tree.insert(
                "",
                "end",
                iid=iid,
                values=(
                    item.tick,
                    self.document.bar_beat_tick(item.tick),
                    item.ref.track_index,
                    item.midi_channel,
                    item.channel,
                    item.role,
                    msb,
                    lsb,
                    item.program,
                    identity,
                ),
            )

    def _note_channels(self) -> set[int]:
        value = self.note_scope_var.get()
        if value.startswith("MIDI 10"):
            return {9}
        if value.startswith("MIDI 11"):
            return {10}
        return set(DRUM_CHANNELS)

    def refresh_notes(self) -> None:
        self._clear_tree(self.note_tree)
        self._clear_tree(self.hit_tree)
        self.note_items.clear()
        self.timeslice_summary.configure(text="")
        if not self.document.is_loaded:
            return
        channels = self._note_channels()
        for index, item in enumerate(self.document.note_activity(channels)):
            iid = f"note-{index}"
            self.note_items[iid] = item
            self.note_tree.insert(
                "",
                "end",
                iid=iid,
                values=(
                    item.midi_channel,
                    STYLE_CHANNELS[item.channel],
                    item.note,
                    midi_note_name(item.note),
                    gm_reference_label(item.note) or "—",
                    self.document.get_custom_label(item.channel, item.note) or "—",
                    item.hit_count,
                    f"{item.velocity_min}–{item.velocity_max}",
                    item.first_tick,
                    item.last_tick,
                    " | ".join(sorted(item.assignments)),
                    " | ".join(sorted(item.sections)),
                ),
            )
        reports = [
            self.document.timeslice_report(channel) for channel in sorted(channels)
        ]
        summary = "  |  ".join(
            f"MIDI {report.channel + 1}: {report.hit_count} hits, "
            f"{report.unique_notes} keys, interval GCD {report.interval_gcd if report.interval_gcd is not None else '—'} ticks"
            for report in reports
        )
        self.timeslice_summary.configure(text=summary)

    def refresh_note_hits(self) -> None:
        self._clear_tree(self.hit_tree)
        iid = self._selected_iid_silent(self.note_tree)
        if iid is None or iid not in self.note_items:
            return
        item = self.note_items[iid]
        for hit in self.document.note_hits(item.channel, item.note):
            self.hit_tree.insert(
                "",
                "end",
                values=(
                    hit.tick,
                    self.document.bar_beat_tick(hit.tick),
                    hit.ref.track_index,
                    hit.ref.message_index,
                    hit.velocity,
                    hit.bank_msb if hit.bank_msb is not None else "—",
                    hit.bank_lsb if hit.bank_lsb is not None else "—",
                    hit.program if hit.program is not None else "—",
                    hit.section,
                ),
            )

    @staticmethod
    def _selected_iid_silent(tree: ttk.Treeview) -> Optional[str]:
        selection = tree.selection()
        return selection[0] if selection else None

    def add_marker(self) -> None:
        if not self._require_loaded():
            return
        assert self.document.midi is not None
        dialog = MarkerDialog(self, len(self.document.midi.tracks), "Add marker")
        if dialog.result is None:
            return
        tick, text, track, meta_type = dialog.result
        try:
            self.document.add_marker(tick, text, track, meta_type)
        except (MidiDocumentError, ValueError) as exc:
            messagebox.showerror("Marker edit failed", str(exc), parent=self)
            return
        self.refresh_all()
        self.status_var.set(f"Added marker {text!r} at tick {tick}.")

    def edit_marker(self) -> None:
        if not self._require_loaded():
            return
        iid = self._selected_iid(self.marker_tree)
        if iid is None:
            return
        ref = self.marker_refs[iid]
        marker = next(
            (item for item in self.document.markers() if item.ref == ref), None
        )
        if marker is None:
            return
        assert self.document.midi is not None
        dialog = MarkerDialog(
            self,
            len(self.document.midi.tracks),
            "Edit marker",
            marker.tick,
            marker.text,
            marker.ref.track_index,
            marker.meta_type,
            allow_track_type=False,
        )
        if dialog.result is None:
            return
        tick, text, _track, _meta_type = dialog.result
        try:
            self.document.edit_marker(ref, tick, text)
        except (MidiDocumentError, ValueError) as exc:
            messagebox.showerror("Marker edit failed", str(exc), parent=self)
            return
        self.refresh_all()
        self.status_var.set(f"Edited marker at tick {tick}.")

    def delete_marker(self) -> None:
        if not self._require_loaded():
            return
        iid = self._selected_iid(self.marker_tree)
        if iid is None:
            return
        values = self.marker_tree.item(iid, "values")
        if not messagebox.askyesno(
            "Delete marker?",
            f"Delete textual marker event {values[6]!r}?\n\nOnly this selected event will be removed.",
            icon="warning",
            parent=self,
        ):
            return
        try:
            self.document.delete_marker(self.marker_refs[iid])
        except MidiDocumentError as exc:
            messagebox.showerror("Delete failed", str(exc), parent=self)
            return
        self.refresh_all()
        self.status_var.set("Deleted selected marker event.")

    def add_assignment(self) -> None:
        if not self._require_loaded():
            return
        assert self.document.midi is not None
        dialog = AssignmentDialog(
            self, len(self.document.midi.tracks), "Add bank/program assignment"
        )
        if dialog.result is None:
            return
        try:
            self.document.add_program_assignment(*dialog.result)
        except (MidiDocumentError, ValueError) as exc:
            messagebox.showerror("Assignment failed", str(exc), parent=self)
            return
        self.refresh_all()
        self.status_var.set("Added bank/program assignment.")

    def edit_assignment(self) -> None:
        if not self._require_loaded():
            return
        iid = self._selected_iid(self.assignment_tree)
        if iid is None:
            return
        item = self.assignment_items[iid]
        assert self.document.midi is not None
        dialog = AssignmentDialog(
            self,
            len(self.document.midi.tracks),
            "Edit bank/program assignment",
            assignment=item,
        )
        if dialog.result is None:
            return
        _track, _tick, _channel, program, msb, lsb = dialog.result
        try:
            self.document.edit_program_assignment(item.ref, program, msb, lsb)
        except (MidiDocumentError, ValueError) as exc:
            messagebox.showerror("Assignment failed", str(exc), parent=self)
            return
        self.refresh_all()
        self.status_var.set("Edited selected bank/program assignment.")

    def delete_assignment(self) -> None:
        if not self._require_loaded():
            return
        iid = self._selected_iid(self.assignment_tree)
        if iid is None:
            return
        item = self.assignment_items[iid]
        if not messagebox.askyesno(
            "Delete program change?",
            f"Delete the Program Change at tick {item.tick} on MIDI channel {item.midi_channel}?\n\n"
            "Existing bank controller events will be retained.",
            icon="warning",
            parent=self,
        ):
            return
        try:
            self.document.delete_program_assignment(item.ref)
        except MidiDocumentError as exc:
            messagebox.showerror("Delete failed", str(exc), parent=self)
            return
        self.refresh_all()
        self.status_var.set("Deleted selected Program Change.")

    def remap_channels(self) -> None:
        if not self._require_loaded():
            return
        dialog = ChannelDialog(self)
        if dialog.result is None:
            return
        source, target, swap = dialog.result
        action = "swap" if swap else "move"
        if not messagebox.askyesno(
            "Confirm channel edit",
            f"{action.title()} MIDI channel {source + 1} ({STYLE_CHANNELS[source]}) "
            f"{'with' if swap else 'into'} MIDI channel {target + 1} ({STYLE_CHANNELS[target]})?",
            icon="warning",
            parent=self,
        ):
            return
        try:
            count = self.document.remap_channels(source, target, swap)
        except MidiDocumentError as exc:
            messagebox.showerror("Channel edit failed", str(exc), parent=self)
            return
        self.refresh_all()
        self.status_var.set(
            f"Changed {count} channel messages; event ticks were preserved."
        )

    def remap_note(self) -> None:
        if not self._require_loaded():
            return
        iid = self._selected_iid(self.note_tree)
        if iid is None:
            return
        item = self.note_items[iid]
        dialog = RemapDialog(self, item.channel, item.note)
        if dialog.result is None:
            return
        target = dialog.result
        collision = next(
            (
                activity
                for activity in self.document.note_activity({item.channel})
                if activity.note == target
            ),
            None,
        )
        if collision and not messagebox.askyesno(
            "Target already active",
            f"Note {target} ({midi_note_name(target)}) already has {collision.hit_count} hits. "
            "Remapping will merge both trigger streams. Continue?",
            icon="warning",
            parent=self,
        ):
            return
        try:
            count = self.document.remap_note(item.channel, item.note, target)
        except MidiDocumentError as exc:
            messagebox.showerror("Remap failed", str(exc), parent=self)
            return
        self.refresh_all()
        self.status_var.set(
            f"Remapped {count} note-on/off/pressure events from {item.note} to {target}."
        )

    def edit_note_label(self) -> None:
        if not self._require_loaded():
            return
        iid = self._selected_iid(self.note_tree)
        if iid is None:
            return
        item = self.note_items[iid]
        current = self.document.get_custom_label(item.channel, item.note)
        value = simpledialog.askstring(
            "Custom element label",
            f"MIDI channel {item.midi_channel}, note {item.note} ({midi_note_name(item.note)})\n"
            "Enter your verified kit/slice label. Leave blank to remove it:",
            initialvalue=current,
            parent=self,
        )
        if value is None:
            return
        try:
            self.document.set_custom_label(item.channel, item.note, value)
        except MidiDocumentError as exc:
            messagebox.showerror("Label edit failed", str(exc), parent=self)
            return
        self.refresh_all()
        self.status_var.set(
            "Updated custom note label; it will be saved in a companion JSON file."
        )

    def _timeslice_channel(self) -> Optional[int]:
        iid = self._selected_iid_silent(self.note_tree)
        if iid is not None and iid in self.note_items:
            return self.note_items[iid].channel
        channels = self._note_channels()
        if len(channels) == 1:
            return next(iter(channels))
        messagebox.showinfo(
            "Choose a channel",
            "Select a note row, or change View to one Drum/Percussion channel first.",
            parent=self,
        )
        return None

    def timeslice_transform(self) -> None:
        if not self._require_loaded():
            return
        channel = self._timeslice_channel()
        if channel is None:
            return
        activity = self.document.note_activity({channel})
        if not activity:
            messagebox.showinfo(
                "No activity",
                "That channel has no sounding note-on events.",
                parent=self,
            )
            return
        low = min(item.note for item in activity)
        high = max(item.note for item in activity)
        dialog = TimeSliceDialog(self, channel, low, high)
        if dialog.result is None:
            return
        note_min, note_max, note_offset, tick_offset, velocity = dialog.result
        if not messagebox.askyesno(
            "Apply trigger transform?",
            f"Transform note events {note_min}..{note_max} on MIDI channel {channel + 1}?\n\n"
            f"Note offset: {note_offset:+d}\nTick offset: {tick_offset:+d}\n"
            f"Velocity: {velocity}%\n\nThis operation is undoable.",
            icon="warning",
            parent=self,
        ):
            return
        try:
            count = self.document.transform_timeslice_range(
                channel, note_min, note_max, note_offset, tick_offset, velocity
            )
        except MidiDocumentError as exc:
            messagebox.showerror("Transform failed", str(exc), parent=self)
            return
        self.refresh_all()
        self.status_var.set(
            f"Transformed {count} possible TimeSlice/trigger note events."
        )

    def export_json(self) -> None:
        if not self._require_loaded():
            return
        source = self.document.source_path
        path = filedialog.asksaveasfilename(
            parent=self,
            title="Export analysis report",
            defaultextension=".json",
            initialdir=str(source.parent) if source else None,
            initialfile=f"{source.stem}_kpm_report.json"
            if source
            else "kpm_report.json",
            filetypes=(("JSON report", "*.json"),),
        )
        if not path:
            return
        try:
            result = self.document.export_report_json(path)
        except MidiDocumentError as exc:
            messagebox.showerror("Export failed", str(exc), parent=self)
            return
        self.status_var.set(f"Exported report: {result}")

    def export_csv(self) -> None:
        if not self._require_loaded():
            return
        source = self.document.source_path
        path = filedialog.asksaveasfilename(
            parent=self,
            title="Export Drum/Percussion activity",
            defaultextension=".csv",
            initialdir=str(source.parent) if source else None,
            initialfile=f"{source.stem}_drum_activity.csv"
            if source
            else "drum_activity.csv",
            filetypes=(("CSV table", "*.csv"),),
        )
        if not path:
            return
        try:
            result = self.document.export_note_csv(path)
        except MidiDocumentError as exc:
            messagebox.showerror("Export failed", str(exc), parent=self)
            return
        self.status_var.set(f"Exported Drum/Percussion CSV: {result}")

    def show_evidence(self) -> None:
        messagebox.showinfo(
            "Evidence and limitations",
            "Korg Pa800 style MIDI contract\n\n"
            "• MIDI 9 / Mido 8: Bass\n"
            "• MIDI 10 / Mido 9: Drums\n"
            "• MIDI 11 / Mido 10: Percussion\n"
            "• MIDI 12–16 / Mido 11–15: Acc 1–5\n"
            "• Korg section markers use lower-case EnCVn forms such as v1cv1.\n"
            "• Bank identity is displayed as CC00.CC32.Program using raw 0–127 values.\n\n"
            "Limits\n\n"
            "• A MIDI note does not prove the sample assigned in a selected Korg Drum Kit.\n"
            "• GM percussion names are reference labels only.\n"
            "• MIDI events alone do not prove that a groove came from Korg Time Slice.\n"
            "• Pa4X sound identities are not treated as convertible PA800 sound identities.\n"
            "• This application writes Standard MIDI Files, not native STY/PCG/SET binaries.",
            parent=self,
        )

    def show_about(self) -> None:
        messagebox.showinfo(
            "About KorgPaManager",
            f"{APP_NAME} {APP_VERSION}\n\n"
            "Write-capable Standard MIDI editor built with Python, Tkinter, and Mido.\n"
            "Designed to preserve the loaded source and save validated edited copies.",
            parent=self,
        )

    def on_close(self) -> None:
        if self._ask_discard():
            self.destroy()


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Korg Pa Standard MIDI manager")
    parser.add_argument("midi", nargs="?", help="optional MIDI file to open")
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="test Tk, MIDI editing, validated Save As, and source preservation",
    )
    parser.add_argument("--version", action="version", version=f"{APP_NAME} {APP_VERSION}")
    arguments = parser.parse_args(argv)
    if arguments.self_test:
        from .selftest import main as self_test_main

        return self_test_main()
    try:
        app = KorgPaManagerApp(arguments.midi)
        app.mainloop()
    except tk.TclError as exc:
        print(f"{APP_NAME} could not start its GUI: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
