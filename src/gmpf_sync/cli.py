"""gmpf-sync command-line interface."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__
from .mp4.timestamps import TimestampReport, extract_timestamps
from .sync import SyncReport, build_sync_report, describe_action


SOURCE_CHOICES = ["auto", "gps", "mvhd", "mdhd", "cdat", "all"]


def _human_report(r: TimestampReport) -> str:
    lines: list[str] = []
    lines.append(f"file:        {r.file}")
    lines.append(f"file_size:   {r.file_size:,} bytes")
    cam = r.sources.get("_camera")
    if cam and cam.detail:
        info = cam.detail
        if "CAME" in info or "FIRM" in info:
            lines.append(f"camera:      {info.get('CAME', '?')}  firmware: {info.get('FIRM', '?')}")

    for name in ("gps", "mvhd", "mdhd", "cdat"):
        s = r.sources.get(name)
        if s is None:
            continue
        if s.is_present():
            extras = []
            if name == "gps":
                fix = s.detail.get("fix")
                extras.append(f"fix={fix}")
                if fix == 0:
                    extras.append("(NO FIX)")
            line = f"{name:<5}        {s.iso}"
            if extras:
                line += "   " + " ".join(extras)
            lines.append(line)
        else:
            reason = s.detail.get("missing", "absent")
            lines.append(f"{name:<5}        -- {reason}")

    lines.append("")
    if r.selected_source:
        lines.append(f"selected:    {r.selected_source}  ->  {r.selected_iso}  (epoch={r.selected_epoch})")
    else:
        lines.append("selected:    -- no usable source")
    return "\n".join(lines)


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="gmpf-sync",
        description="Extract creation timestamps from GoPro MP4 files.",
    )
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    stamp = sub.add_parser("stamp", help="Print timestamp(s) for one or more MP4 files.")
    stamp.add_argument("files", nargs="+", type=Path, help="MP4 file path(s).")
    stamp.add_argument(
        "--source", "-s",
        choices=SOURCE_CHOICES, default="auto",
        help="Which timestamp source to extract (default: auto).",
    )
    stamp.add_argument("--json", action="store_true", help="Emit JSON instead of human-readable text.")

    sync = sub.add_parser(
        "sync",
        help="Compare first-timestamps across MP4/TCX/CSV files and report offsets.",
        description=(
            "Read the first/representative timestamp from each input file and "
            "report how each one is offset from a reference (the first MP4, "
            "by default). Supported formats: GoPro MP4/MOV, TCX activity files, "
            "RaceChrono v3 CSV logs."
        ),
    )
    sync.add_argument("files", nargs="+", type=Path, help="Mixed list of MP4/TCX/CSV files.")
    sync.add_argument("--json", action="store_true", help="Emit JSON instead of human-readable text.")
    return p


def _run_stamp(args: argparse.Namespace) -> int:
    results: list[dict] = []
    failures = 0
    for path in args.files:
        try:
            report = extract_timestamps(path, source=args.source)
        except (FileNotFoundError, ValueError) as e:
            failures += 1
            if args.json:
                results.append({"file": str(path), "error": str(e)})
            else:
                print(f"{path}: ERROR — {e}", file=sys.stderr)
            continue

        if args.json:
            results.append(report.to_dict())
        else:
            if len(args.files) > 1:
                print(f"=== {path} ===")
            print(_human_report(report))
            if len(args.files) > 1:
                print()

    if args.json:
        json.dump(results if len(args.files) > 1 else results[0],
                  sys.stdout, indent=2, default=str)
        sys.stdout.write("\n")

    return 0 if failures == 0 else 2


def _human_sync_report(r: SyncReport) -> str:
    lines: list[str] = []
    if r.reference_file is None:
        lines.append("reference:   -- no usable timestamp in any input")
        lines.append("")
    else:
        primary_label = f" [{r.reference_primary_source}]" if r.reference_primary_source else ""
        lines.append(f"reference:   {r.reference_file}")
        lines.append(f"             {r.reference_iso}{primary_label}  (primary, epoch={r.reference_epoch})")
        for c in r.reference_alternatives:
            lines.append(f"             {c.iso} [{c.source}]  (alternative)")
        if r.reference_alternatives:
            lines.append(
                "             (MP4 sources disagree -- pick the row whose "
                "timezone matches your other files.)"
            )
        lines.append("")

    width = max((len(e.file) for e in r.entries), default=0)
    for e in r.entries:
        if e.epoch is None:
            reason = e.detail.get("missing") or e.detail.get("error") or "no timestamp"
            lines.append(f"{e.file:<{width}}  [{e.kind:<3}]  -- {reason}")
            continue

        if e.action == "reference":
            lines.append(f"{e.file:<{width}}  [{e.kind:<3}]  {e.iso}   (reference)")
            continue

        note = describe_action(e.action, e.delta_seconds)
        lines.append(f"{e.file:<{width}}  [{e.kind:<3}]  {e.iso}   {note}")

        for alt in e.alternatives:
            alt_note = describe_action(alt.action, alt.delta_seconds)
            indent = " " * (width + 2 + 5 + 2)  # align under primary note
            lines.append(f"{indent}  alt vs [{alt.reference_source}] {alt.reference_iso}: {alt_note}")

    return "\n".join(lines)


def _run_sync(args: argparse.Namespace) -> int:
    report = build_sync_report(args.files)
    if args.json:
        json.dump(report.to_dict(), sys.stdout, indent=2, default=str)
        sys.stdout.write("\n")
    else:
        print(_human_sync_report(report))
    return 0 if report.reference_file is not None else 2


def _attach_console_on_windows() -> None:
    """Make stdio usable in CLI mode under a ``--windowed`` PyInstaller build.

    The frozen executable is built with the Windows GUI subsystem so no
    console flashes on double-click. That also means CLI subcommands have
    no stdio attached. Here we attach to the parent shell's console (or
    allocate a fresh one) and rebind ``sys.stdout/stderr/stdin`` only for
    the streams that don't already have a real file descriptor — so a
    user-supplied redirect like ``gmpf-sync sync ... > out.json`` still
    wins.

    The CONOUT$/CONIN$ handles below are deliberately leaked: they replace
    sys.std* for the lifetime of the process, so we cannot close them with
    a context manager.
    """
    # pylint: disable=import-outside-toplevel,broad-exception-caught,consider-using-with
    if sys.platform != "win32":
        return
    try:
        import ctypes
    except Exception:  # pragma: no cover - defensive
        return

    kernel32 = ctypes.windll.kernel32

    # Already have a console (e.g. running under python.exe in dev)? Nothing
    # to do — sys.std* are already wired up correctly.
    if kernel32.GetConsoleWindow():
        return

    attach_parent_process = -1  # Win32 ATTACH_PARENT_PROCESS sentinel
    if not kernel32.AttachConsole(attach_parent_process):
        # No parent console (e.g. launched via `start` without a terminal).
        # Allocate a fresh one so output is visible somewhere.
        kernel32.AllocConsole()

    def _has_real_fd(stream) -> bool:
        try:
            return stream is not None and stream.fileno() >= 0
        except (AttributeError, OSError, ValueError):
            return False

    if not _has_real_fd(sys.stdout):
        try:
            sys.stdout = open("CONOUT$", "w", buffering=1, encoding="utf-8", errors="replace")
        except OSError:
            pass
    if not _has_real_fd(sys.stderr):
        try:
            sys.stderr = open("CONOUT$", "w", buffering=1, encoding="utf-8", errors="replace")
        except OSError:
            pass
    if not _has_real_fd(sys.stdin):
        try:
            sys.stdin = open("CONIN$", "r", encoding="utf-8", errors="replace")
        except OSError:
            pass


def main(argv: list[str] | None = None) -> int:
    # No arguments at all → launch the drag-and-drop GUI. argparse with
    # required=True would otherwise exit 2 with a usage error, which is
    # unfriendly when the user just double-clicked the executable.
    if argv is None:
        argv = sys.argv[1:]
    if not argv:
        try:
            # Lazy import: keeps tkinter out of the CLI-only path, and lets
            # the GUI fall back to a CLI error if tkinterdnd2 is missing.
            from .gui import launch  # pylint: disable=import-outside-toplevel
        except ImportError as e:
            _attach_console_on_windows()
            print(
                f"GUI unavailable: {e}\n"
                "Install with `pip install gmpf-sync` or use the CLI: gmpf-sync --help",
                file=sys.stderr,
            )
            return 1
        return launch()

    # CLI mode under a windowed bundle — wire up a console for stdout/stderr.
    _attach_console_on_windows()

    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.command == "stamp":
        return _run_stamp(args)
    if args.command == "sync":
        return _run_sync(args)
    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
