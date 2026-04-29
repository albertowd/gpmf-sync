"""Drag-and-drop GUI for cross-format timestamp synchronisation.

Launched automatically when the ``gmpf-sync`` executable is run with no
arguments. The window has a single drop zone that accepts MP4/TCX/CSV files;
on drop, it computes the sync report and renders the same trim/offset
output the CLI prints.

Implementation note: native OS drag-and-drop is delivered by ``tkinterdnd2``
which wraps the ``tkdnd`` Tk extension. PyInstaller bundles the tkdnd shared
library files automatically when the ``tkinterdnd2`` package is imported
during the build's analysis phase.
"""
from __future__ import annotations

import re
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, ttk

from tkinterdnd2 import DND_FILES, TkinterDnD

from .sync import (
    SyncReport,
    build_sync_report,
    describe_action,
)


_BG = "#1e1e1e"
_FG = "#e8e8e8"
_DROP_BG = "#2d2d2d"
_DROP_HOVER_BG = "#3a3a3a"
_DROP_BORDER = "#5a8dee"
_MUTED = "#9a9a9a"
_OK = "#5fcf80"
_WARN = "#ffb454"


def _parse_drop_paths(data: str) -> list[Path]:
    """Tk's drop event delivers a single string. Paths with spaces are wrapped
    in braces (``{C:/Path with spaces/file.mp4}``); the rest are space-separated.
    """
    out: list[Path] = []
    # Match either a {…} group or a non-whitespace run.
    for match in re.finditer(r"\{([^}]*)\}|(\S+)", data):
        raw = match.group(1) if match.group(1) is not None else match.group(2)
        if raw:
            out.append(Path(raw))
    return out


class SyncApp:
    def __init__(self) -> None:
        self.root = TkinterDnD.Tk()
        self.root.title("gmpf-sync")
        self.root.geometry("760x520")
        self.root.configure(bg=_BG)
        self.root.minsize(560, 380)

        self._files: list[Path] = []
        self._build_ui()

    # ---- UI construction -------------------------------------------------

    def _build_ui(self) -> None:
        outer = tk.Frame(self.root, bg=_BG, padx=16, pady=16)
        outer.pack(fill="both", expand=True)

        title = tk.Label(
            outer, text="Drop GoPro MP4 + TCX / CSV files to compare timestamps",
            fg=_FG, bg=_BG, font=("Segoe UI", 12, "bold"),
        )
        title.pack(anchor="w")

        subtitle = tk.Label(
            outer,
            text="The first MP4 is the reference. Other files report a trim/offset to apply in your editor.",
            fg=_MUTED, bg=_BG, font=("Segoe UI", 9),
        )
        subtitle.pack(anchor="w", pady=(0, 12))

        self.drop_zone = tk.Label(
            outer,
            text="Drop files here\n\nor click to browse",
            fg=_FG, bg=_DROP_BG,
            font=("Segoe UI", 11),
            relief="solid", bd=1,
            highlightthickness=2,
            highlightbackground=_DROP_BG,
            cursor="hand2",
        )
        self.drop_zone.pack(fill="x", pady=(0, 12), ipady=24)
        self.drop_zone.drop_target_register(DND_FILES)
        self.drop_zone.dnd_bind("<<DropEnter>>", self._on_drop_enter)
        self.drop_zone.dnd_bind("<<DropLeave>>", self._on_drop_leave)
        self.drop_zone.dnd_bind("<<Drop>>", self._on_drop)
        self.drop_zone.bind("<Button-1>", lambda _e: self._browse())

        button_row = tk.Frame(outer, bg=_BG)
        button_row.pack(fill="x", pady=(0, 8))
        self.clear_btn = ttk.Button(button_row, text="Clear", command=self._clear)
        self.clear_btn.pack(side="right")

        # Output area.
        self.output = tk.Text(
            outer, bg="#141414", fg=_FG,
            insertbackground=_FG, relief="flat",
            font=("Consolas", 10), wrap="none",
            state="disabled",
        )
        yscroll = ttk.Scrollbar(outer, orient="vertical", command=self.output.yview)
        xscroll = ttk.Scrollbar(outer, orient="horizontal", command=self.output.xview)
        self.output.configure(yscrollcommand=yscroll.set, xscrollcommand=xscroll.set)

        self.output.pack(side="top", fill="both", expand=True)
        yscroll.pack(side="right", fill="y")
        xscroll.pack(side="bottom", fill="x")

        self.output.tag_configure("muted", foreground=_MUTED)
        self.output.tag_configure("ok", foreground=_OK)
        self.output.tag_configure("warn", foreground=_WARN)
        self.output.tag_configure("ref", foreground=_DROP_BORDER, font=("Consolas", 10, "bold"))

        self._set_output("Drop one or more files to begin.\n", muted=True)

    # ---- event handlers --------------------------------------------------

    def _on_drop_enter(self, _event) -> None:
        self.drop_zone.configure(bg=_DROP_HOVER_BG, highlightbackground=_DROP_BORDER)

    def _on_drop_leave(self, _event) -> None:
        self.drop_zone.configure(bg=_DROP_BG, highlightbackground=_DROP_BG)

    def _on_drop(self, event) -> None:
        self.drop_zone.configure(bg=_DROP_BG, highlightbackground=_DROP_BG)
        paths = _parse_drop_paths(event.data)
        self._add_files(paths)

    def _browse(self) -> None:
        chosen = filedialog.askopenfilenames(
            title="Pick MP4 / TCX / CSV files",
            filetypes=[
                ("Supported formats", "*.mp4 *.mov *.tcx *.csv"),
                ("GoPro MP4", "*.mp4 *.mov"),
                ("TCX", "*.tcx"),
                ("CSV (RaceChrono v3)", "*.csv"),
                ("All files", "*.*"),
            ],
        )
        if chosen:
            self._add_files([Path(p) for p in chosen])

    def _clear(self) -> None:
        self._files = []
        self._set_output("Drop one or more files to begin.\n", muted=True)

    # ---- core flow -------------------------------------------------------

    def _add_files(self, new: list[Path]) -> None:
        if not new:
            return
        # Append, dedupe while preserving order.
        seen = {str(p) for p in self._files}
        for p in new:
            if str(p) not in seen:
                self._files.append(p)
                seen.add(str(p))

        self._set_output("Reading timestamps...\n", muted=True)
        # Run the (potentially slow) MP4 parse off the UI thread.
        threading.Thread(target=self._compute_and_render, daemon=True).start()

    def _compute_and_render(self) -> None:
        try:
            report = build_sync_report(self._files)
        except Exception as exc:  # pragma: no cover - defensive
            self.root.after(0, lambda: self._set_output(f"Error: {exc}\n", warn=True))
            return
        self.root.after(0, lambda: self._render(report))

    # ---- rendering -------------------------------------------------------

    def _set_output(self, text: str, muted: bool = False, warn: bool = False) -> None:
        self.output.configure(state="normal")
        self.output.delete("1.0", "end")
        tag = ()
        if muted:
            tag = ("muted",)
        elif warn:
            tag = ("warn",)
        self.output.insert("end", text, tag)
        self.output.configure(state="disabled")

    def _render(self, r: SyncReport) -> None:
        self.output.configure(state="normal")
        self.output.delete("1.0", "end")

        if r.reference_file is None:
            self.output.insert("end", "No usable timestamp in any input.\n", "warn")
            self.output.configure(state="disabled")
            return

        ref_label = f" [{r.reference_primary_source}]" if r.reference_primary_source else ""
        self.output.insert("end", "reference:   ", "muted")
        self.output.insert("end", f"{r.reference_file}\n", "ref")
        self.output.insert("end", f"             {r.reference_iso}{ref_label}", "ref")
        self.output.insert("end", f"  (primary, epoch={r.reference_epoch})\n", "muted")

        for c in r.reference_alternatives:
            self.output.insert("end", f"             {c.iso} [{c.source}]", "warn")
            self.output.insert("end", "  (alternative)\n", "muted")
        if r.reference_alternatives:
            self.output.insert(
                "end",
                "             MP4 sources disagree -- pick the row whose\n"
                "             timezone matches your other files.\n",
                "warn",
            )

        self.output.insert("end", "\n")

        width = max((len(e.file) for e in r.entries), default=0)
        for e in r.entries:
            file_col = f"{e.file:<{width}}"
            kind_col = f"[{e.kind:<3}]"
            if e.epoch is None:
                reason = e.detail.get("missing") or e.detail.get("error") or "no timestamp"
                self.output.insert("end", f"{file_col}  {kind_col}  -- {reason}\n", "warn")
                continue

            if e.action == "reference":
                self.output.insert("end", f"{file_col}  {kind_col}  {e.iso}", "ref")
                self.output.insert("end", "   (reference)\n", "muted")
                continue

            note = describe_action(e.action, e.delta_seconds)
            self.output.insert("end", f"{file_col}  {kind_col}  {e.iso}   ")
            self.output.insert("end", f"{note}\n", "ok" if e.action == "aligned" else "")

            indent = " " * (width + 2 + 5 + 2)
            for alt in e.alternatives:
                alt_note = describe_action(alt.action, alt.delta_seconds)
                self.output.insert(
                    "end",
                    f"{indent}  alt vs [{alt.reference_source}] {alt.reference_iso}: {alt_note}\n",
                    "muted",
                )

        self.output.configure(state="disabled")

    def run(self) -> None:
        self.root.mainloop()


def launch() -> int:
    app = SyncApp()
    app.run()
    return 0
