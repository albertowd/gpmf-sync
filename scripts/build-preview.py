#!/usr/bin/env python3
"""One-shot generator for ``preview.png``.

Spawns the real ``SyncApp`` window on-screen, loads the four bundled
example files (``scripts/preview_assets/``), waits for the worker thread
to finish, then captures the window via ``Pillow.ImageGrab``.

Run with the project venv (Pillow lives in the ``preview`` extras)::

    .venv/bin/python -m pip install -e ".[preview]"
    .venv/bin/python scripts/build-preview.py

Requirements:

- **Screen Recording permission** for the terminal you launch this from
  (System Settings → Privacy & Security → Screen Recording). macOS'
  ``screencapture`` refuses to render the window otherwise; the failure
  surfaces as ``CalledProcessError`` from ``ImageGrab.grab``.
- The window flashes briefly on the primary display while pixels are
  captured. There's no headless path on macOS without that permission.

The output PNG is committed at the repo root; convert to ``preview.webp``
with any ``cwebp`` invocation (or the gpmf-sync-web
``scripts/build-assets.mjs`` helper) if a smaller asset is needed.
"""
from __future__ import annotations

import sys
import time
import tkinter as tk
import types
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "src"))

# tkinterdnd2 needs a native tkdnd shared library that isn't always
# available in headless / fresh-venv runs. The preview script never uses
# drag-and-drop (it calls ``_add_files`` directly), so we stub it before
# importing the GUI module to avoid the runtime dependency entirely.
_dnd_stub = types.ModuleType("tkinterdnd2")
_dnd_stub.DND_FILES = "DND_Files"  # constant value — never read here.
_dnd_stub.TkinterDnD = types.SimpleNamespace(Tk=tk.Tk)
sys.modules["tkinterdnd2"] = _dnd_stub

# TkinterDnD adds drop_target_register/dnd_bind onto Tk widgets globally.
# Plain Tk doesn't have them; the GUI calls them at construction time.
# No-op shims keep the constructor happy.
def _dnd_noop(*_args, **_kwargs):
    return None


tk.Widget.drop_target_register = _dnd_noop  # type: ignore[attr-defined]
tk.Widget.dnd_bind = _dnd_noop  # type: ignore[attr-defined]

from PIL import ImageGrab  # noqa: E402  pylint: disable=wrong-import-position

from gmpf_sync.gui import SyncApp  # noqa: E402  pylint: disable=wrong-import-position

EXAMPLES = HERE / "preview_assets"
OUT_PNG = ROOT / "preview.png"

WIDTH = 1100
HEIGHT = 720


def main() -> int:
    files = sorted(EXAMPLES.glob("*"))
    if not files:
        print(f"no example files found in {EXAMPLES}", file=sys.stderr)
        return 1

    app = SyncApp()
    # Position the window on the primary display — macOS' screencapture
    # rejects rectangles that don't intersect any display.
    app.root.geometry(f"{WIDTH}x{HEIGHT}+100+100")
    app.root.update_idletasks()

    # Inject the example files exactly the way a real drop would.
    app._add_files(list(files))  # pylint: disable=protected-access

    # Pump the event loop until the worker thread finishes and the cards
    # have been rendered. _files is populated synchronously; the cards
    # appear after the background thread schedules _render via root.after.
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        app.root.update_idletasks()
        app.root.update()
        children = app.cards_frame.winfo_children()
        if len(children) >= len(files):
            break
        time.sleep(0.05)

    # One extra paint so flexbox-style geometry resolves before grabbing.
    app.root.update_idletasks()
    app.root.update()
    app.root.lift()
    app.root.attributes("-topmost", True)
    app.root.update()
    time.sleep(0.6)

    x = app.root.winfo_rootx()
    y = app.root.winfo_rooty()
    w = app.root.winfo_width()
    h = app.root.winfo_height()

    image = ImageGrab.grab(bbox=(x, y, x + w, y + h))
    image.save(OUT_PNG, format="PNG", optimize=True)
    print(f"wrote {OUT_PNG}  ({image.size[0]}x{image.size[1]}, {OUT_PNG.stat().st_size} B)")

    app.root.destroy()
    return 0


if __name__ == "__main__":
    sys.exit(main())
