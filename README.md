# gmpf-sync

A tool to sync GoPro files with other time based files, like Race Chrono V3
and TCX files.

Extract creation timestamps from GoPro MP4 files **without loading the file
into memory**, then compare them against external time-series files (TCX
workout exports, RaceChrono v3 CSV logs) to compute the trim/offset you need
to apply in another tool to align them.

## What it does

Given a GoPro MP4, `gmpf-sync` reads only the headers it needs (a few KB at
most — never the full file), extracts every available creation timestamp, and
prints them either as human-readable text or as JSON.

Four sources are inspected, in priority order:

| Source | Where it lives | Trustworthiness |
| --- | --- | --- |
| `gps`  | GPMF metadata track (`GPSU` / `GPSF`) | **Best** when GPS fix ≥ 2 — it is real UTC from satellites |
| `mvhd` | `moov/mvhd` (movie header) | Good when GPS missing; **may be local-time on some firmwares** |
| `mdhd` | `moov/trak/mdia/mdhd` (per-track) | Same caveat as `mvhd` |
| `cdat` | `moov/udta/GPMF/CDAT` | Documented as local-time epoch (rare in samples) |

The `auto` source returns the first present source in that order. The
`all` source returns every source so you can compare them — useful when
debugging suspect timestamps.

## Why streaming matters

A typical GoPro MP4 is a few GB; some are tens of GB. Loading it into RAM is
wasteful and slow. `gmpf-sync` walks the MP4 atom tree by reading only 8-byte
headers and seeking past payloads it doesn't need. Peak data buffer usage is
bounded by a single GPMF metadata sample (a few KB to ~25 KB), independent of
input file size.

## Install

### Prebuilt executable

Each release ships a single-file portable executable per platform — no Python
install required. Drop `gmpf-sync` (or `gmpf-sync.exe` on Windows) anywhere in
your `PATH`.

### From source

Requires Python 3.10+.

```bash
git clone <repo-url> gmpf-sync
cd gmpf-sync

python -m venv .venv
# Windows
.venv/Scripts/python -m pip install pytest pyinstaller
# macOS / Linux
.venv/bin/python -m pip install pytest pyinstaller
```

The runtime depends on a single small package — `tkinterdnd2` — which
provides native drag-and-drop on Tk for the GUI mode. Everything else is
stdlib. The extra packages above are only needed for testing and building
the portable executable.

## Usage

Run the executable with **no arguments** to open a drag-and-drop window:
drop GoPro MP4s, TCX activity files, and RaceChrono v3 CSVs onto it and
the trim/offset report appears inline. This is the path most non-technical
users will take.

For scripting, two CLI subcommands:

- `stamp` — extract one or more timestamps from a single GoPro MP4
- `sync` — compare timestamps across a mix of MP4/TCX/CSV files and report
  the trim/offset needed to align them

> **Windows note**: the released `.exe` is built as a GUI-subsystem binary
> so double-clicking it opens the GUI without a console flash. CLI
> subcommands still print to the console — the executable attaches to the
> parent shell's console at startup. From `cmd.exe`, the prompt may return
> before the process finishes (cmd doesn't wait on GUI binaries). Use
> `start /wait gmpf-sync.exe …` if you need cmd to block, or run from
> PowerShell which waits automatically. Output redirection
> (`gmpf-sync sync … > out.json`) works in both shells.

### `stamp`

```
gmpf-sync stamp <file.mp4> [<file2.mp4> ...] [--source SOURCE] [--json]
```

Options:

| Flag | Default | Meaning |
| --- | --- | --- |
| `--source`, `-s` | `auto` | `auto`, `gps`, `mvhd`, `mdhd`, `cdat`, or `all` |
| `--json` | off | Emit machine-readable JSON instead of text |

### Examples

Auto-pick the best timestamp:

```
$ gmpf-sync stamp GH010001.MP4
file:        GH010001.MP4
file_size:   2,847,392,810 bytes
camera:      ...  firmware: HD11.01.01.20.00
gps          2024-08-12T14:23:51.500000Z   fix=3
mvhd         2024-08-12T15:23:51Z
mdhd         2024-08-12T15:23:51Z
cdat         -- no CDAT key in udta GPMF

selected:    gps  ->  2024-08-12T14:23:51.500000Z  (epoch=1723472631.5)
```

Emit JSON for piping into a sync script:

```
$ gmpf-sync stamp GH010001.MP4 --json
{
  "file": "GH010001.MP4",
  "file_size": 2847392810,
  "sources": {
    "mvhd": {"epoch": 1723476231.0, "iso": "2024-08-12T15:23:51Z", ...},
    "gps":  {"epoch": 1723472631.5, "iso": "2024-08-12T14:23:51.500000Z",
             "detail": {"fix": 3, "sample_index": 0, "scanned": 1}},
    ...
  },
  "selected": {
    "source": "gps",
    "epoch": 1723472631.5,
    "iso": "2024-08-12T14:23:51.500000Z"
  }
}
```

Process several files at once:

```
$ gmpf-sync stamp GH01*.MP4 --json > timestamps.json
```

### Force a specific source

If you want to bypass GPS and inspect only what the camera clock says:

```
$ gmpf-sync stamp clip.MP4 --source mvhd
```

Or compare every source side-by-side:

```
$ gmpf-sync stamp clip.MP4 --source all
```

### `sync`

```
gmpf-sync sync <file1> [<file2> ...] [--json]
```

Reads the first/representative timestamp from each input file and reports
how each one is offset from a reference. The first MP4 in the list is used
as the reference (its `auto`-selected source); if there are no MP4 files,
the first input with a parseable timestamp is used instead.

| Format | Detected by | Timestamp source |
| --- | --- | --- |
| GoPro MP4/MOV | `.mp4`, `.mov` | `extract_timestamps(..., source="auto")` |
| TCX activity  | `.tcx`         | first `<Id>...</Id>` element |
| RaceChrono v3 CSV | `.csv`     | first row whose column 0 is a positive epoch like `1723472631.500` |

For each non-reference file, the report says either:

- **`offset by HH:MM:SS.mmm`** — the file started *after* the reference;
  delay (offset) the file by this amount in your editing tool.
- **`trim head by HH:MM:SS.mmm`** — the file started *before* the reference;
  trim that much off the start of the file.
- **`aligned`** — same instant, no adjustment needed.

Example:

```
$ gmpf-sync sync GH010001.MP4 ride.tcx racechrono.csv
reference:   GH010001.MP4
             2024-08-12T14:23:51.500000Z [gps]  (primary, epoch=1723472631.5)

GH010001.MP4    [mp4]  2024-08-12T14:23:51.500000Z   (reference)
ride.tcx        [tcx]  2024-08-12T14:23:30Z          trim head by 00:00:21.500
racechrono.csv  [csv]  2024-08-12T14:25:00.250000Z   offset by 00:01:08.750
```

When the GoPro's MP4 sources disagree (typically because GPS isn't available
and the firmware writes `mvhd` as local time but `cdat` as another clock
basis), the sync command surfaces every distinct candidate and shows the
alternative offset for each non-reference file:

```
$ gmpf-sync sync session.csv GX010076.MP4
reference:   GX010076.MP4
             2026-03-21T14:48:53Z [mvhd]  (primary, epoch=1774104533.0)
             2026-03-21T11:48:53Z [cdat]  (alternative)
             (MP4 sources disagree -- pick the row whose timezone matches your other files.)

session.csv    [csv]  2026-03-21T14:49:08.017000Z   offset by 00:00:15.017
                                                      alt vs [cdat] 2026-03-21T11:48:53Z: offset by 03:00:15.017
GX010076.MP4   [mp4]  2026-03-21T14:48:53Z          (reference)
```

JSON output (suitable for piping into a sync script):

```
$ gmpf-sync sync GH010001.MP4 ride.tcx --json
{
  "reference": { "file": "GH010001.MP4", "epoch": ..., "iso": "..." },
  "entries": [
    { "file": "GH010001.MP4", "kind": "mp4", "epoch": ..., "iso": "...",
      "delta_seconds": 0.0, "action": "reference", "detail": {"source": "gps"} },
    { "file": "ride.tcx",     "kind": "tcx", "epoch": ..., "iso": "...",
      "delta_seconds": -21.5, "action": "trim", "detail": {} }
  ]
}
```

## A note on timezones

The MP4/QuickTime spec says `mvhd`/`mdhd` `creation_time` is **UTC since
1904-01-01**. GoPro firmware historically writes that field using the
camera's *local* time (whatever was set in camera settings) without any
timezone marker. The GPMF `CDAT` field is also documented as local-time
epoch. The `GPSU` field, when GPS fix ≥ 2, is genuine UTC from the
satellites and is therefore the recommended source.

Symptom: if you see your `mvhd` timestamp drifting by a whole number of
hours from your phone clock, the camera was almost certainly set to a
different timezone. Prefer `--source gps` whenever you have GPS coverage.

A `--assume-tz` flag is on the roadmap for footage without GPS that needs
explicit timezone correction.

## How it works (under the hood)

MP4 is a tree of **atoms** (size + FourCC + payload). `gmpf-sync` walks the
tree by reading only headers:

1. Open file, seek through top-level atoms until `moov` is found. Skip
   over `mdat` (the big payload — never read it).
2. Inside `moov`, recurse only into containers we care about
   (`trak/mdia/minf/stbl/udta/edts`).
3. Read the small fixed-size payloads we need: `mvhd`, `mdhd`, `hdlr`,
   `stsd`, `stco`/`co64`, `stsz`, `stsc`.
4. Locate the GPMF metadata track (`hdlr` type `meta`, `stsd` format
   `gpmd`), use the sample tables to get the file offset of the first
   GPMF payload, then read just that payload (a few KB).
5. Parse the GPMF KLV stream, recurse into nested containers (`DEVC`,
   `STRM`), find `GPSU` / `GPSF` / `CDAT`.
6. Convert QuickTime epoch (1904-01-01 UTC) to Unix seconds and emit.

If the first GPMF sample has no GPS fix, scan up to 32 samples looking for
one with fix ≥ 2. After that, surface whatever was best.

## Build the portable executable

```bash
.venv/Scripts/python build.py        # Windows  → dist/gmpf-sync-<ver>.exe
.venv/bin/python build.py            # macOS/Linux → dist/gmpf-sync-<ver>
```

The output is a single self-contained ~8 MB executable (Windows;
macOS/Linux are similar). PyInstaller binaries don't cross-compile, so
the script must be run on each target OS — multi-platform releases are
driven through the GitHub Actions matrix in `.github/workflows/release.yml`.

### Cutting a release

1. Bump `__version__` in `src/gmpf_sync/__init__.py`.
2. Move the relevant `## [Unreleased]` entries in `CHANGELOG.md` under a
   new `## [x.y.z] - YYYY-MM-DD` heading and update the comparison links
   at the bottom of the file.
3. Commit, then tag and push: `git tag vx.y.z && git push origin vx.y.z`.
4. The `Release` workflow builds binaries for Windows x86_64, Linux
   x86_64, macOS arm64 and macOS x86_64, then publishes a GitHub Release
   whose body is the matching `CHANGELOG.md` section, with all four
   binaries attached.

`build.py` performs a few platform-aware steps automatically:

| Host | Icon embedded into binary | Bundled for window icon | tkdnd library shipped |
| --- | --- | --- | --- |
| Windows | `gmpf-sync.ico` (16/32/48/256, hand-rolled PNG-in-ICO) | same `.ico` | `tkinterdnd2/tkdnd/win-x64` only |
| macOS   | `gmpf-sync.icns` (16…512, hand-rolled PNG-in-ICNS) | small derived `.png` | `tkinterdnd2/tkdnd/osx-{x64,arm64}` matching host |
| Linux   | downsized `.png` | same `.png` | `tkinterdnd2/tkdnd/linux-*` matching host |

The 1024×1024 source `favicon.png` is **never** bundled — only the
platform-derived asset (≤300 KB). Cross-arch tkdnd libraries are
temporarily renamed in site-packages during the build so PyInstaller
doesn't pick them up.

## Project layout

```
src/gmpf_sync/
  cli.py             argparse front-end (stamp + sync subcommands; no-args → GUI)
  gui.py             tkinter drag-and-drop window backed by sync.py
  sync.py            cross-format orchestrator: reference + per-file deltas
  favicon.png        1024-px source icon (build-time only; never bundled)
  mp4/               GoPro MP4 parsing stack
    atoms.py         streaming atom walker (8-byte headers only)
    meta.py          parsers for mvhd, mdhd, hdlr, stsd, stco/co64/stsz/stsc
    gpmf.py          GPMF KLV parser (recursive container support)
    gpmf_track.py    resolves chunk offsets → per-sample (offset, size)
    timestamps.py    high-level MP4 orchestrator: chooses sources, builds report
  external/          non-MP4 time-series file readers
    tcx.py           TCX first-timestamp reader (streamed)
    rc_csv.py        RaceChrono v3 CSV first-timestamp reader (streamed)
tests/
  test_samples.py    parameterised tests against gpmf-parser sample MP4s
  test_sync.py       unit tests for tcx/csv readers and sync orchestrator
build.py             PyInstaller driver
entry.py           PyInstaller entry point (preserves package imports)
pyproject.toml     project metadata (no runtime dependencies)
```

## Development

```bash
# run the CLI from source
PYTHONPATH=src .venv/Scripts/python -m gmpf_sync stamp <file.mp4>

# run tests (skipped if samples are not present)
.venv/Scripts/python -m pytest tests/

# run tests against GoPro's sample files
git clone https://github.com/gopro/gpmf-parser /tmp/gpmf-parser
GMPF_SYNC_SAMPLES_DIR=/tmp/gpmf-parser/samples .venv/Scripts/python -m pytest tests/

# install dev tooling (pylint, pytest) and run the linter
.venv/Scripts/python -m pip install -e ".[dev]"
.venv/Scripts/python -m pylint src/gmpf_sync
```

The `conftest.py` at the project root puts `src/` on `sys.path` so tests
work without an editable install. Pylint is configured under
`[tool.pylint]` in `pyproject.toml`; the `Lint` GitHub Actions workflow
runs `pylint src/gmpf_sync` on every push and PR and fails the build
below the configured `fail-under` score.

## Roadmap

1. **Done — milestone 1**: timestamp extraction CLI with JSON output.
2. **Done — milestone 2**: cross-format `sync` command — compare GoPro MP4
   against TCX and RaceChrono v3 CSV and emit trim/offset.
3. **Next**: `--assume-tz` flag for GoPro files without GPS where
   `mvhd`/`CDAT` need explicit timezone correction.
4. **Later**: more CSV dialects (currently only RaceChrono v3 is detected
   automatically); generic `--csv-epoch-column` override.

## Acknowledgements

The MP4-atom and GPMF-KLV understanding implemented here was learned by
reading [GoPro's gpmf-parser](https://github.com/gopro/gpmf-parser)
reference implementation. The official GoPro sample MP4s are recommended
for running the test suite — see the Development section above.

## License

TBD.
