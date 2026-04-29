# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [1.0.0] - 2026-04-29

### Added
- Streaming MP4 atom parser that pulls creation timestamps from GoPro files
  without loading the video into memory.
- Four timestamp sources resolved in priority order: GPMF `GPSU`/`GPSF`,
  `mvhd`, per-track `mdhd`, and `udta/GPMF/CDAT`.
- `stamp` and `sync` CLI subcommands (text + JSON output).
- TCX activity export and RaceChrono v3 CSV support for the `sync` command,
  reporting the trim/offset needed to align an external time series with the
  video.
- Drag-and-drop GUI built on `tkinterdnd2`; double-click launches it with no
  console window on Windows.
- PyInstaller-based portable single-file builds (`build.py`) with
  hand-rolled multi-resolution `.ico` / `.icns` icons and platform-specific
  tkdnd pruning to keep the binary small.
- Application icon bundled across all platforms.

[Unreleased]: https://github.com/albertowd/gpmf-sync/compare/v1.0.0...HEAD
[1.0.0]: https://github.com/albertowd/gpmf-sync/releases/tag/v1.0.0
