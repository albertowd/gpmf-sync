"""Drag-and-drop GUI for cross-format timestamp synchronisation.

Layout (top → bottom):

    ┌──────────────────────────────────────────┐
    │ header                                   │  fixed
    │ drop zone                                │  fixed
    │ status bar [Clear]                       │  fixed
    │ ┌──────────────────────────────────────┐ │
    │ │ scrollable file cards                │ │  expandable
    │ │   - one card per dropped file        │ │
    │ │   - reference card highlighted       │ │
    │ │   - alternatives shown inline        │ │
    │ └──────────────────────────────────────┘ │
    │ ▶ Show raw output                        │  collapsible — hidden by default
    │ (raw text widget, when expanded)         │
    └──────────────────────────────────────────┘

Native OS drag-and-drop is delivered by ``tkinterdnd2`` (wraps ``tkdnd``).
PyInstaller bundles the tkdnd shared library when ``--collect-all
tkinterdnd2`` is passed during the build.
"""
from __future__ import annotations

import re
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, ttk

from tkinterdnd2 import DND_FILES, TkinterDnD

from . import __version__
from .sync import SyncReport, build_sync_report, describe_action


# ---- palette --------------------------------------------------------------

_BG = "#1e1e1e"
_FG = "#e8e8e8"
_FG_DIM = "#9a9a9a"

_CARD_BG = "#2a2a2a"
_CARD_BORDER = "#3a3a3a"
_CARD_BG_REF = "#1f3340"
_CARD_BORDER_REF = "#5a8dee"

_DROP_BG = "#2d2d2d"
_DROP_HOVER_BG = "#3a3a3a"
_DROP_BORDER = "#5a8dee"

_CONSOLE_BG = "#141414"

_OK = "#5fcf80"
_WARN = "#ffb454"
_INFO = "#7eb6ff"

_BADGE_BG = {
    "mp4": "#3a5a3a",
    "tcx": "#5a3a5a",
    "csv": "#3a4d6a",
    "unknown": "#444444",
}

_WIN_W = 860
_WIN_H = 640


# ---- utilities ------------------------------------------------------------

def _parse_drop_paths(data: str) -> list[Path]:
    """Tk's drop event delivers a single string. Paths with spaces are
    wrapped in braces (``{C:/Path with spaces/file.mp4}``); the rest are
    space-separated.
    """
    out: list[Path] = []
    for match in re.finditer(r"\{([^}]*)\}|(\S+)", data):
        raw = match.group(1) if match.group(1) is not None else match.group(2)
        if raw:
            out.append(Path(raw))
    return out


def _truncate_path(s: str, max_chars: int = 60) -> str:
    """Middle-ellipsis truncation. Keeps the filename intact at the tail
    and as much of the directory prefix as fits at the head."""
    if len(s) <= max_chars:
        return s
    keep = max_chars - 3
    head = max(keep // 3, 6)
    tail = keep - head
    return f"{s[:head]}...{s[-tail:]}"


# ---- main app -------------------------------------------------------------

class SyncApp:
    def __init__(self) -> None:
        self.root = TkinterDnD.Tk()
        self.root.title(f"GPMF Sync {__version__}")
        self.root.configure(bg=_BG)
        self.root.minsize(680, 480)

        self._files: list[Path] = []
        self._console_expanded = False

        self._build_ui()
        self._center_on_screen(_WIN_W, _WIN_H)

    # ---- positioning -----------------------------------------------------

    def _center_on_screen(self, width: int, height: int) -> None:
        self.root.update_idletasks()
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        x = max((sw - width) // 2, 0)
        y = max((sh - height) // 2, 0)
        self.root.geometry(f"{width}x{height}+{x}+{y}")

    # ---- UI construction -------------------------------------------------

    def _build_ui(self) -> None:
        outer = tk.Frame(self.root, bg=_BG)
        outer.pack(fill="both", expand=True)

        # 1. Header — subtitle only; the app name lives in the title bar.
        header = tk.Frame(outer, bg=_BG, padx=18, pady=8)
        header.pack(fill="x", side="top")
        tk.Label(
            header,
            text="Compare first-timestamps across GoPro MP4, TCX, and RaceChrono v3 CSV files.",
            bg=_BG, fg=_FG_DIM, font=("Segoe UI", 9),
        ).pack(anchor="w")

        # 2. Status bar with Browse / Clear
        bar = tk.Frame(outer, bg=_BG, padx=18, pady=8)
        bar.pack(fill="x", side="top")
        self.status_label = tk.Label(
            bar, text="Drop files to begin.",
            bg=_BG, fg=_FG_DIM, font=("Segoe UI", 9),
        )
        self.status_label.pack(side="left")
        self.clear_btn = ttk.Button(bar, text="Clear", command=self._clear)
        self.clear_btn.pack(side="right")
        self.browse_btn = ttk.Button(bar, text="Browse…", command=self._browse)
        self.browse_btn.pack(side="right", padx=(0, 6))

        # 5. Console (collapsible) — packed before the cards area so that
        #    the cards area can take all the remaining vertical space.
        self.console_section = tk.Frame(outer, bg=_BG)
        self.console_section.pack(side="bottom", fill="x")
        self._build_console_section(self.console_section)

        sep = tk.Frame(outer, bg="#2a2a2a", height=1)
        sep.pack(side="bottom", fill="x")

        # 3. Cards / drop area (scrollable, also the drop target)
        self.cards_outer = tk.Frame(outer, bg=_BG, padx=18, pady=6)
        self.cards_outer.pack(side="top", fill="both", expand=True)
        self._build_cards_area(self.cards_outer)

        self._show_placeholder("Drop GoPro MP4 / TCX / CSV files here  —  or click to browse.")

    def _build_cards_area(self, parent: tk.Frame) -> None:
        # The canvas itself is the drop target — a subtle border hints at
        # the drop affordance, brightens on drag-hover.
        self.cards_canvas = tk.Canvas(
            parent, bg=_BG,
            highlightthickness=2,
            highlightbackground=_DROP_BG,
        )
        scroll = ttk.Scrollbar(parent, orient="vertical", command=self.cards_canvas.yview)
        self.cards_canvas.configure(yscrollcommand=scroll.set)

        self.cards_frame = tk.Frame(self.cards_canvas, bg=_BG)
        self._cards_window_id = self.cards_canvas.create_window(
            (0, 0), window=self.cards_frame, anchor="nw",
        )
        self.cards_frame.bind(
            "<Configure>",
            lambda _e: self.cards_canvas.configure(scrollregion=self.cards_canvas.bbox("all")),
        )
        self.cards_canvas.bind("<Configure>", self._on_canvas_configure)

        scroll.pack(side="right", fill="y")
        self.cards_canvas.pack(side="left", fill="both", expand=True)

        # Drop target — register on both the canvas and the inner frame so a
        # drop hits regardless of whether the cursor is over a card or empty
        # space.
        for w in (self.cards_canvas, self.cards_frame):
            w.drop_target_register(DND_FILES)
            w.dnd_bind("<<DropEnter>>", self._on_drop_enter)
            w.dnd_bind("<<DropLeave>>", self._on_drop_leave)
            w.dnd_bind("<<Drop>>", self._on_drop)

        # Only scroll the cards canvas while the mouse is over it; otherwise
        # the wheel would steal events from the console text widget.
        self.cards_canvas.bind("<Enter>", self._bind_cards_wheel)
        self.cards_canvas.bind("<Leave>", self._unbind_cards_wheel)

    def _build_console_section(self, parent: tk.Frame) -> None:
        self.console_toggle = tk.Label(
            parent, text="▶  Show raw output",
            bg=_BG, fg=_FG_DIM, font=("Segoe UI", 9),
            cursor="hand2", padx=18, pady=8, anchor="w",
        )
        self.console_toggle.pack(fill="x")
        self.console_toggle.bind("<Button-1>", lambda _e: self._toggle_console())

        # Container that has a fixed height so the cards area doesn't
        # collapse to nothing when expanded.
        self.console_frame = tk.Frame(parent, bg=_BG, height=210)
        self.console_frame.pack_propagate(False)
        # Not packed initially.

        wrap = tk.Frame(self.console_frame, bg=_BG, padx=18, pady=4)
        wrap.pack(fill="both", expand=True)

        self.output = tk.Text(
            wrap, bg=_CONSOLE_BG, fg=_FG,
            insertbackground=_FG, relief="flat",
            font=("Consolas", 9), wrap="none",
            state="disabled", padx=10, pady=8,
        )
        yscroll = ttk.Scrollbar(wrap, orient="vertical", command=self.output.yview)
        xscroll = ttk.Scrollbar(wrap, orient="horizontal", command=self.output.xview)
        self.output.configure(yscrollcommand=yscroll.set, xscrollcommand=xscroll.set)

        yscroll.pack(side="right", fill="y")
        xscroll.pack(side="bottom", fill="x")
        self.output.pack(side="left", fill="both", expand=True)

        self.output.tag_configure("muted", foreground=_FG_DIM)
        self.output.tag_configure("warn", foreground=_WARN)
        self.output.tag_configure("ok", foreground=_OK)
        self.output.tag_configure("ref", foreground=_INFO, font=("Consolas", 9, "bold"))

    # ---- canvas / mousewheel --------------------------------------------

    def _on_canvas_configure(self, event) -> None:
        # Keep the inner frame's width matched to the canvas so cards
        # stretch the full width.
        self.cards_canvas.itemconfigure(self._cards_window_id, width=event.width)

    def _bind_cards_wheel(self, _event) -> None:
        self.cards_canvas.bind_all("<MouseWheel>", self._on_cards_wheel)

    def _unbind_cards_wheel(self, _event) -> None:
        self.cards_canvas.unbind_all("<MouseWheel>")

    def _on_cards_wheel(self, event) -> None:
        try:
            self.cards_canvas.yview_scroll(int(-event.delta / 120), "units")
        except tk.TclError:
            pass

    # ---- toggles ---------------------------------------------------------

    def _toggle_console(self) -> None:
        if self._console_expanded:
            self.console_frame.pack_forget()
            self.console_toggle.configure(text="▶  Show raw output")
        else:
            self.console_frame.pack(fill="x")
            self.console_toggle.configure(text="▼  Hide raw output")
        self._console_expanded = not self._console_expanded

    # ---- drop / browse / clear ------------------------------------------

    def _on_drop_enter(self, _event) -> None:
        self.cards_canvas.configure(highlightbackground=_DROP_BORDER, bg=_DROP_HOVER_BG)
        self.cards_frame.configure(bg=_DROP_HOVER_BG)

    def _on_drop_leave(self, _event) -> None:
        self.cards_canvas.configure(highlightbackground=_DROP_BG, bg=_BG)
        self.cards_frame.configure(bg=_BG)

    def _on_drop(self, event) -> None:
        self._on_drop_leave(None)
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
        self.status_label.configure(text="Drop files to begin.")
        self._show_placeholder("Drop GoPro MP4 / TCX / CSV files here  —  or click to browse.")
        self._set_console_text("(no output yet)\n", muted=True)

    # ---- pipeline --------------------------------------------------------

    def _add_files(self, new: list[Path]) -> None:
        if not new:
            return
        seen = {str(p) for p in self._files}
        for p in new:
            if str(p) not in seen:
                self._files.append(p)
                seen.add(str(p))

        self.status_label.configure(text=f"Reading {len(self._files)} file(s)…")
        self._show_placeholder("Reading timestamps…")
        threading.Thread(target=self._compute_and_render, daemon=True).start()

    def _compute_and_render(self) -> None:
        try:
            report = build_sync_report(self._files)
        except Exception as exc:  # pragma: no cover - defensive
            self.root.after(0, lambda: self._show_placeholder(f"Error: {exc}"))
            return
        self.root.after(0, lambda: self._render(report))

    # ---- rendering -------------------------------------------------------

    def _show_placeholder(self, text: str) -> None:
        self._clear_cards()
        placeholder = tk.Label(
            self.cards_frame, text=text,
            bg=_BG, fg=_FG_DIM, font=("Segoe UI", 11, "italic"),
            cursor="hand2",
        )
        placeholder.pack(pady=60, padx=20)
        placeholder.bind("<Button-1>", lambda _e: self._browse())

    def _clear_cards(self) -> None:
        for child in self.cards_frame.winfo_children():
            child.destroy()

    def _render(self, report: SyncReport) -> None:
        self._render_console_text(report)
        self._clear_cards()

        if report.reference_file is None:
            self.status_label.configure(text=f"{len(self._files)} file(s) — no usable reference.")
            self._show_placeholder("No usable timestamp in any input.")
            return

        ref_name = Path(report.reference_file).name
        self.status_label.configure(
            text=f"{len(self._files)} file(s) — reference: {_truncate_path(ref_name, 50)}",
        )

        for entry in report.entries:
            is_ref = entry.action == "reference"
            ref_alts = report.reference_alternatives if is_ref else []
            card = self._build_card(self.cards_frame, entry, is_ref, ref_alts)
            card.pack(fill="x", pady=5)

    def _build_card(self, parent, entry, is_reference: bool, ref_alternatives) -> tk.Frame:
        bg = _CARD_BG_REF if is_reference else _CARD_BG
        border = _CARD_BORDER_REF if is_reference else _CARD_BORDER

        card = tk.Frame(
            parent, bg=bg,
            padx=14, pady=12,
            highlightthickness=1,
            highlightbackground=border,
            highlightcolor=border,
        )

        # Header: badge + filename + REFERENCE tag.
        header = tk.Frame(card, bg=bg)
        header.pack(fill="x")

        badge_color = _BADGE_BG.get(entry.kind, _BADGE_BG["unknown"])
        tk.Label(
            header, text=f"  {entry.kind.upper()}  ",
            bg=badge_color, fg=_FG,
            font=("Segoe UI", 8, "bold"), padx=2, pady=2,
        ).pack(side="left")

        if is_reference:
            tk.Label(
                header, text="REFERENCE",
                bg=bg, fg=_INFO, font=("Segoe UI", 8, "bold"),
            ).pack(side="right")

        tk.Label(
            header, text=_truncate_path(entry.file, 70),
            bg=bg, fg=_FG, font=("Segoe UI", 10, "bold"),
            anchor="w",
        ).pack(side="left", padx=(10, 0))

        # Body.
        body = tk.Frame(card, bg=bg)
        body.pack(fill="x", pady=(8, 0))

        if entry.epoch is None:
            reason = entry.detail.get("missing") or entry.detail.get("error") or "no timestamp"
            tk.Label(
                body, text=f"Could not read: {reason}",
                bg=bg, fg=_WARN, font=("Segoe UI", 9), anchor="w",
            ).pack(anchor="w")
            return card

        # Primary timestamp line.
        primary_text = entry.iso or ""
        if is_reference and entry.primary_source and entry.primary_source not in ("tcx", "csv"):
            primary_text = f"{primary_text}   [{entry.primary_source}]"
            if ref_alternatives:
                primary_text = f"{primary_text}   primary"
        tk.Label(
            body, text=primary_text,
            bg=bg, fg=_FG, font=("Consolas", 9), anchor="w",
        ).pack(anchor="w")

        if is_reference:
            for alt_cand in ref_alternatives:
                tk.Label(
                    body,
                    text=f"{alt_cand.iso}   [{alt_cand.source}]   alternative",
                    bg=bg, fg=_WARN, font=("Consolas", 9), anchor="w",
                ).pack(anchor="w", pady=(2, 0))
            if ref_alternatives:
                tk.Label(
                    body,
                    text="MP4 sources disagree — pick the row whose timezone matches your other files.",
                    bg=bg, fg=_WARN, font=("Segoe UI", 8, "italic"),
                    wraplength=720, justify="left", anchor="w",
                ).pack(anchor="w", pady=(6, 0))
            return card

        # Non-reference: action + alternatives.
        note = describe_action(entry.action, entry.delta_seconds)
        action_color = _OK if entry.action == "aligned" else _INFO
        tk.Label(
            body, text=f"→  {note}",
            bg=bg, fg=action_color, font=("Segoe UI", 10, "bold"), anchor="w",
        ).pack(anchor="w", pady=(6, 0))

        for alt in entry.alternatives:
            alt_note = describe_action(alt.action, alt.delta_seconds)
            tk.Label(
                body,
                text=f"     alt vs [{alt.reference_source}]  {alt.reference_iso}:  {alt_note}",
                bg=bg, fg=_FG_DIM, font=("Consolas", 9), anchor="w",
            ).pack(anchor="w")

        return card

    # ---- raw text console -----------------------------------------------

    def _set_console_text(self, text: str, muted: bool = False, warn: bool = False) -> None:
        self.output.configure(state="normal")
        self.output.delete("1.0", "end")
        tag = ()
        if muted:
            tag = ("muted",)
        elif warn:
            tag = ("warn",)
        self.output.insert("end", text, tag)
        self.output.configure(state="disabled")

    def _render_console_text(self, r: SyncReport) -> None:
        self.output.configure(state="normal")
        self.output.delete("1.0", "end")

        if r.reference_file is None:
            self.output.insert("end", "No usable timestamp in any input.\n", "warn")
            self.output.configure(state="disabled")
            return

        ref_path_short = _truncate_path(r.reference_file, 70)
        ref_label = f" [{r.reference_primary_source}]" if r.reference_primary_source else ""
        self.output.insert("end", "reference:   ", "muted")
        self.output.insert("end", f"{ref_path_short}\n", "ref")
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

        truncated = [_truncate_path(e.file, 56) for e in r.entries]
        width = max((len(t) for t in truncated), default=0)
        for entry, fname in zip(r.entries, truncated):
            file_col = f"{fname:<{width}}"
            kind_col = f"[{entry.kind:<3}]"
            if entry.epoch is None:
                reason = entry.detail.get("missing") or entry.detail.get("error") or "no timestamp"
                self.output.insert("end", f"{file_col}  {kind_col}  -- {reason}\n", "warn")
                continue
            if entry.action == "reference":
                self.output.insert("end", f"{file_col}  {kind_col}  {entry.iso}", "ref")
                self.output.insert("end", "   (reference)\n", "muted")
                continue
            note = describe_action(entry.action, entry.delta_seconds)
            self.output.insert("end", f"{file_col}  {kind_col}  {entry.iso}   ")
            self.output.insert("end", f"{note}\n", "ok" if entry.action == "aligned" else "")
            indent = " " * (width + 2 + 5 + 2)
            for alt in entry.alternatives:
                alt_note = describe_action(alt.action, alt.delta_seconds)
                self.output.insert(
                    "end",
                    f"{indent}  alt vs [{alt.reference_source}] {alt.reference_iso}: {alt_note}\n",
                    "muted",
                )

        self.output.configure(state="disabled")

    # ---- mainloop --------------------------------------------------------

    def run(self) -> None:
        self.root.mainloop()


def launch() -> int:
    app = SyncApp()
    app.run()
    return 0
