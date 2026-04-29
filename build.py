"""Build a portable single-file executable with PyInstaller.

Usage (run from project root, with the venv active):

    # one-time setup
    python -m venv .venv
    .venv/Scripts/python -m pip install pytest pyinstaller        # Windows
    .venv/bin/python    -m pip install pytest pyinstaller         # macOS/Linux

    # build
    .venv/Scripts/python build.py                                  # Windows
    .venv/bin/python    build.py                                   # macOS/Linux

Output goes to dist/ — a single self-contained executable named gmpf-sync(.exe).
The binary is platform-specific; run this script on each target OS.
"""
from __future__ import annotations

import platform
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent
SRC = ROOT / "src"
ENTRY = ROOT / "entry.py"


def main() -> int:
    if not ENTRY.is_file():
        print(f"entry not found: {ENTRY}", file=sys.stderr)
        return 1

    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        print("PyInstaller is not installed. Run:  pip install -e .[build]", file=sys.stderr)
        return 1

    for stale in ("build", "dist"):
        d = ROOT / stale
        if d.is_dir():
            shutil.rmtree(d)
    for spec in ROOT.glob("*.spec"):
        spec.unlink()

    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--onefile",
        "--name", "gmpf-sync",
        "--paths", str(SRC),
        "--hidden-import", "gmpf_sync",
        "--hidden-import", "gmpf_sync.cli",
        "--hidden-import", "gmpf_sync.timestamps",
        "--hidden-import", "gmpf_sync.gpmf",
        "--hidden-import", "gmpf_sync.gpmf_track",
        "--hidden-import", "gmpf_sync.mp4_atoms",
        "--hidden-import", "gmpf_sync.mp4_meta",
        "--console",
        "--clean",
        "--noconfirm",
        str(ENTRY),
    ]
    print(f"[build] platform: {platform.system()} {platform.machine()}")
    print(f"[build] cmd: {' '.join(cmd)}")
    rc = subprocess.call(cmd, cwd=ROOT)
    if rc != 0:
        return rc

    suffix = ".exe" if platform.system() == "Windows" else ""
    out = ROOT / "dist" / f"gmpf-sync{suffix}"
    if out.is_file():
        size_mb = out.stat().st_size / (1024 * 1024)
        print(f"[build] ok: {out}  ({size_mb:.1f} MB)")
    else:
        print("[build] WARNING: expected executable not found", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
