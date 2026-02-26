# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

A Windows-only Minecraft launcher with a PySide6 GUI that can download, launch, and manage Minecraft client and server instances in offline mode. It interacts with Mojang's APIs to fetch version manifests, download game files (jars, libraries, assets), manage Java runtimes, and launch the game with proper JVM arguments.

## Running

```bash
# Launch the GUI
python launcher_ui.py

# CLI scripts can be run independently:
python scripts/download_version.py <version_id> --base-dir <path>
python scripts/launch_client.py <version_id> --base-dir <path>
python scripts/download_server.py <version_id> --servers-dir <path>
python scripts/launch_server.py <version_id> --servers-dir <path>
python scripts/fetch_manifest.py
```

## Dependencies

- **PySide6** — GUI framework (required for `launcher_ui.py`)
- **requests** — HTTP client (required for `scripts/`, optional in UI and `mc_common.py` which fall back to `urllib`)

No `requirements.txt` exists; install manually: `pip install PySide6 requests`

## Architecture

### `mc_common.py` — Shared library
All scripts and the UI import from this module. Contains:
- Network helpers in two flavors: `urllib`-based (`fetch_json_url`, `download_url_file`) used by `launch_client.py` and runtime downloads, and `requests`-based (`fetch_json`, `download_file`) used by download scripts
- Mojang rule evaluation (`allowed_by_rules`, `rule_matches`) for filtering libraries/arguments by OS/arch/features
- Java runtime auto-download (`ensure_java_runtime`) — checks Microsoft Store path, then `.minecraft/runtime/`, then downloads from Mojang
- Architecture detection (`detect_arch`) returns `x64`/`x86`/`arm64` and bit width

### `launcher_ui.py` — PySide6 GUI
Single `LauncherWindow` class with `QProcess`-based subprocess management. Runs download/launch scripts as child processes and streams their output to a log panel. Settings are persisted to `launcher_settings.json` on close. Version manifest is fetched in a background `QThread` (`ManifestFetcher`) and cached to `version_manifest_cache.json`.

### `scripts/` — CLI entry points
Each script does `sys.path.insert(0, parent_dir)` to import `mc_common`. They all use `argparse` and return exit codes.

- **`download_version.py`** — Downloads client jar, libraries (Windows-only rules), and assets from Mojang. Requires `requests`.
- **`launch_client.py`** — Builds the full Java command line from the version JSON, resolving JVM/game arguments with Mojang's template substitution (`${variable}`). Generates offline UUIDs and access tokens. Handles native library extraction. Does NOT require `requests`.
- **`download_server.py`** — Downloads `server.jar` for a given version. Requires `requests`.
- **`launch_server.py`** — Launches a dedicated server. Uses PowerShell to detect running server processes for restart support.

## Key Patterns

- Downloads skip files that already exist and match expected size (and optionally SHA1 with `--verify`)
- The launcher is Windows-only: OS name is hardcoded to `"windows"`, native library filtering uses Windows classifiers, Java runtime lookup checks Windows-specific paths including Microsoft Store
- `launcher_ui.py` uses collapsible panels with `QPropertyAnimation` for dev settings, logs, and mods
- All scripts use `raise SystemExit(main())` as their entry point pattern
