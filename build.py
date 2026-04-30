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

import contextlib
import importlib.util
import os
import platform
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent
SRC = ROOT / "src"
ENTRY = ROOT / "entry.py"
GENERATED = ROOT / "build" / "_assets"


_ICO_SIZES = (16, 32, 48, 256)
# macOS .icns: type-code → pixel size. Limited to non-Retina entries up to
# 512×512 — Pillow's ICNS writer always emits @2x variants on top of every
# requested size, which doubles the pixel count and pushed the file past
# 1 MB. We hand-roll the container with these PNG-encoded entries instead.
_ICNS_TYPES = (
    (b"icp4", 16),
    (b"icp5", 32),
    (b"icp6", 64),
    (b"ic07", 128),
    (b"ic08", 256),
    (b"ic09", 512),
)
_LINUX_PNG_SIZE = 256


def _load_pillow_image(png_path: Path):
    try:
        from PIL import Image
    except ImportError as e:
        raise RuntimeError(
            "Pillow is required to convert favicon.png at build time. "
            "Install with: pip install pillow"
        ) from e
    im = Image.open(png_path)
    if im.mode != "RGBA":
        im = im.convert("RGBA")
    return Image, im


def _write_multi_png_ico(im, ico_path: Path) -> None:
    """Hand-roll a Windows .ico with **PNG-encoded entries** (supported by
    Windows since Vista). PNG carries full RGBA so resampled transparency
    round-trips cleanly — Pillow's BMP-in-ICO writer composites alpha into
    a 1-bit AND mask, which surfaced as a white halo on our icon.
    """
    import io
    import struct
    from PIL import Image

    encoded: list[tuple[int, bytes]] = []
    for size in _ICO_SIZES:
        if size > im.size[0] or size > im.size[1]:
            continue
        frame = im.resize((size, size), Image.LANCZOS)
        buf = io.BytesIO()
        frame.save(buf, format="PNG", optimize=True)
        encoded.append((size, buf.getvalue()))
    if not encoded:
        raise RuntimeError("favicon.png is too small for any target ICO size")

    out = io.BytesIO()
    out.write(struct.pack("<HHH", 0, 1, len(encoded)))
    payload_offset = 6 + 16 * len(encoded)
    for size, data in encoded:
        out.write(struct.pack(
            "<BBBBHHII",
            size if size < 256 else 0,
            size if size < 256 else 0,
            0, 0, 1, 32,
            len(data),
            payload_offset,
        ))
        payload_offset += len(data)
    for _, data in encoded:
        out.write(data)

    ico_path.parent.mkdir(parents=True, exist_ok=True)
    ico_path.write_bytes(out.getvalue())


def _write_icns(im, icns_path: Path) -> None:
    """Hand-roll a macOS .icns with **PNG-encoded entries**.

    File layout:
        magic 'icns' (4) + total length (4 BE)
        repeating: type-code (4) + chunk length incl. header (4 BE) + data

    PNG-encoded type codes have been supported by macOS since 10.7. By
    writing them ourselves we get exactly the sizes we ask for (no
    surprise Retina @2x copies) — keeps the .icns near 200 KB instead of
    1.1 MB.
    """
    import io
    import struct
    from PIL import Image

    max_size = max(s for _, s in _ICNS_TYPES)
    chunks: list[bytes] = []
    for type_code, size in _ICNS_TYPES:
        if size > min(im.size):
            continue
        frame = im.resize((size, size), Image.LANCZOS)
        buf = io.BytesIO()
        frame.save(buf, format="PNG", optimize=True)
        png_data = buf.getvalue()
        chunks.append(type_code + struct.pack(">I", 8 + len(png_data)) + png_data)
    if not chunks:
        raise RuntimeError("favicon.png is too small for any target ICNS size")

    body = b"".join(chunks)
    header = b"icns" + struct.pack(">I", 8 + len(body))
    icns_path.parent.mkdir(parents=True, exist_ok=True)
    icns_path.write_bytes(header + body)


def _write_runtime_png(im, png_path: Path, size: int = _LINUX_PNG_SIZE) -> None:
    """A single small PNG used by Tk's ``iconphoto`` at runtime."""
    from PIL import Image
    png_path.parent.mkdir(parents=True, exist_ok=True)
    im.resize((size, size), Image.LANCZOS).save(png_path, format="PNG", optimize=True)


def _build_icon_assets(png_path: Path, out_dir: Path) -> tuple[Path, list[Path]]:
    """Generate the icon files this host's PyInstaller build needs.

    Returns ``(icon_arg, runtime_assets)``:
    - ``icon_arg``: the file passed to PyInstaller's ``--icon`` flag.
    - ``runtime_assets``: list of files to bundle so the GUI can load
      them via ``importlib.resources`` — the .ico on Windows, a small
      .png on macOS/Linux (Tk's PhotoImage cannot read .icns directly).

    The 1024×1024 source PNG is never bundled — only these derived,
    much smaller artifacts are.
    """
    _, im = _load_pillow_image(png_path)
    sys_name = platform.system()

    if sys_name == "Windows":
        ico = out_dir / "gmpf-sync.ico"
        _write_multi_png_ico(im, ico)
        return ico, [ico]

    if sys_name == "Darwin":
        icns = out_dir / "gmpf-sync.icns"
        _write_icns(im, icns)
        # Tk's title bar uses iconphoto with a PNG; .icns is for Finder/Dock.
        png = out_dir / "gmpf-sync.png"
        _write_runtime_png(im, png)
        return icns, [icns, png]

    # Linux (and any other Unix-y host).
    png = out_dir / "gmpf-sync.png"
    _write_runtime_png(im, png)
    return png, [png]


def _detect_tkdnd_arch_dir() -> str:
    """Return the ``tkinterdnd2/tkdnd/<dir>`` we actually need on this host."""
    sys_name = platform.system()
    machine = platform.machine().lower()
    if sys_name == "Windows":
        if machine in ("amd64", "x86_64"):
            return "win-x64"
        if machine in ("arm64", "aarch64"):
            return "win-arm64"
        return "win-x86"
    if sys_name == "Linux":
        if machine in ("aarch64", "arm64"):
            return "linux-arm64"
        return "linux-x64"
    if sys_name == "Darwin":
        if machine == "arm64":
            return "osx-arm64"
        return "osx-x64"
    return "win-x64"  # fallback — unlikely path


@contextlib.contextmanager
def _hide_foreign_tkdnd_arches():
    """Temporarily rename the cross-platform/arch tkdnd subdirectories so
    PyInstaller's tkinterdnd2 hook doesn't bundle them. Saves ~280 KB raw
    (~150 KB compressed) on Windows x64 by skipping arm64/x86 DLLs.

    We restore the directories on exit even if the build fails.
    """
    spec = importlib.util.find_spec("tkinterdnd2")
    if spec is None or spec.origin is None:
        yield
        return

    tkdnd_root = Path(spec.origin).parent / "tkdnd"
    keep = _detect_tkdnd_arch_dir()
    renamed: list[tuple[Path, Path]] = []
    try:
        for d in tkdnd_root.iterdir():
            if d.is_dir() and d.name != keep:
                hidden = d.with_name(d.name + ".__hidden__")
                d.rename(hidden)
                renamed.append((d, hidden))
        yield
    finally:
        for original, hidden in renamed:
            try:
                hidden.rename(original)
            except OSError:
                pass


def _read_version() -> str:
    """Pull ``__version__`` out of the package without importing it.

    Avoids a build-time dependency on the package being on ``sys.path``;
    the regex tracks PEP 440-ish strings inside a string literal.
    """
    text = (SRC / "gmpf_sync" / "__init__.py").read_text(encoding="utf-8")
    m = re.search(r'__version__\s*=\s*["\']([^"\']+)["\']', text)
    if not m:
        raise RuntimeError("Could not parse __version__ from gmpf_sync/__init__.py")
    return m.group(1)


# Stdlib modules that PyInstaller would otherwise pull in transitively but
# this app never touches. Stripping them removes their compiled extensions
# and their native dependencies from the bundle (e.g. excluding ``ssl``
# drops ``libcrypto-3.dll`` + ``libssl-3.dll`` — about 6 MB on its own).
# Keep this list conservative: only modules that are truly unused at
# runtime by our code AND by tkinter / tkinterdnd2.
_EXCLUDED_MODULES = [
    # Networking / crypto stack — unused, drags in OpenSSL.
    "ssl", "_ssl",
    "hashlib", "_hashlib",
    "socket", "_socket",
    "select", "_select",
    "http", "urllib", "email", "xmlrpc",
    # XML — not used by our parsers (TCX is line-streamed with regex).
    "xml",
    # Compression — only zlib is used (by tkinterdnd2's PyZ archive).
    "bz2", "_bz2",
    "lzma", "_lzma",
    "_zstd",
    # Other stdlib bulk we don't touch.
    "asyncio", "concurrent", "multiprocessing",
    "sqlite3", "_sqlite3",
    "doctest", "unittest", "pydoc", "pdb",
    "_wmi",
    # Build-time tools that sometimes leak in from site-packages metadata.
    "distutils", "setuptools", "pip", "wheel",
    "_pytest", "pytest",
    "PIL", "numpy",
]




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

    version = _read_version()
    output_name = f"gmpf-sync-{version}"

    favicon_png = SRC / "gmpf_sync" / "favicon.png"
    if not favicon_png.is_file():
        print(f"favicon.png not found at {favicon_png}", file=sys.stderr)
        return 1

    icon_arg, runtime_assets = _build_icon_assets(favicon_png, GENERATED)
    # icon_arg may also appear in runtime_assets (Windows/Linux); dedupe for the log line.
    artifacts: dict[str, Path] = {}
    for p in [icon_arg, *runtime_assets]:
        artifacts.setdefault(p.name, p)
    summary = ", ".join(f"{p.name}={p.stat().st_size/1024:.0f}KB" for p in artifacts.values())
    print(f"[build] icon: {favicon_png.stat().st_size/1024:.0f} KB PNG -> {summary}")

    # Bundle the generated icon assets into the package so the runtime
    # importlib.resources lookup finds them. We deliberately do NOT use
    # ``--collect-data gmpf_sync`` because that would also pull in the
    # 1+ MB source PNG, defeating the whole conversion step.
    add_data_args: list[str] = []
    for asset in runtime_assets:
        add_data_args += ["--add-data", f"{asset}{os.pathsep}gmpf_sync"]

    # Card kind-icons (mp4/tcx/csv/unknown) are small static PNGs used by
    # the GUI's per-file badges. Bundle them under gmpf_sync/icons so the
    # importlib.resources lookup in gui.py finds them at runtime.
    kind_icons_dir = SRC / "gmpf_sync" / "icons"
    if kind_icons_dir.is_dir():
        for icon_file in sorted(kind_icons_dir.glob("*.png")):
            add_data_args += ["--add-data", f"{icon_file}{os.pathsep}gmpf_sync/icons"]

    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--onefile",
        "--name", output_name,
        "--icon", str(icon_arg),
        *add_data_args,
        "--paths", str(SRC),
        "--hidden-import", "gmpf_sync",
        "--hidden-import", "gmpf_sync.cli",
        "--hidden-import", "gmpf_sync.gui",
        "--hidden-import", "gmpf_sync.sync",
        "--hidden-import", "gmpf_sync.mp4",
        "--hidden-import", "gmpf_sync.mp4.atoms",
        "--hidden-import", "gmpf_sync.mp4.meta",
        "--hidden-import", "gmpf_sync.mp4.gpmf",
        "--hidden-import", "gmpf_sync.mp4.gpmf_track",
        "--hidden-import", "gmpf_sync.mp4.timestamps",
        "--hidden-import", "gmpf_sync.external",
        "--hidden-import", "gmpf_sync.external.tcx",
        "--hidden-import", "gmpf_sync.external.rc_csv",
        # tkinterdnd2 ships a tkdnd shared library that PyInstaller bundles
        # automatically when the package is collected. ``--collect-all``
        # makes sure both the Python module and its data directory tag
        # along into the one-file binary.
        "--collect-all", "tkinterdnd2",
        # Build as a Windows-subsystem binary: no console window is allocated
        # at process start, so double-clicking the GUI mode shows no flash.
        # When CLI args are passed, cli.main() dynamically attaches to the
        # parent shell's console (or allocates a fresh one) before printing.
        "--windowed",
        # Strip docstrings + asserts from the bundled bytecode (~equivalent
        # to running with ``python -OO``).
        "--optimize", "2",
        "--clean",
        "--noconfirm",
    ]
    for mod in _EXCLUDED_MODULES:
        cmd += ["--exclude-module", mod]
    cmd.append(str(ENTRY))
    print(f"[build] platform: {platform.system()} {platform.machine()}")
    print(f"[build] tkdnd arch: keeping {_detect_tkdnd_arch_dir()} only")
    print(f"[build] cmd: {' '.join(cmd)}")
    with _hide_foreign_tkdnd_arches():
        rc = subprocess.call(cmd, cwd=ROOT)
    if rc != 0:
        return rc

    suffix = ".exe" if platform.system() == "Windows" else ""
    out = ROOT / "dist" / f"{output_name}{suffix}"
    if out.is_file():
        size_mb = out.stat().st_size / (1024 * 1024)
        print(f"[build] ok: {out}  ({size_mb:.1f} MB)")
    else:
        print("[build] WARNING: expected executable not found", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
