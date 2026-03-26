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

### `core/` — Shared logic package
Centralized business logic used by both the GUI and CLI scripts:
- **`constants.py`** — `ModLoader` and `ServerType` enums, file name constants, tab indices
- **`platform.py`** — Platform abstraction (`is_windows()`, `open_folder()`, `kill_process_tree()`, `java_executable_names()`)
- **`version_utils.py`** — Version ID resolution (`resolve_mc_version`), loader detection (`detect_version_loader`), `maven_to_path`
- **`server_detection.py`** — Deterministic server type detection using `server_type.json` metadata with filesystem fallback

### `ui/` — GUI components package
PySide6 UI components extracted from the main window:
- **`workers.py`** — Background QThread classes (ManifestFetcher, UsernameChecker, UpdateChecker, ModrinthShaderSearcher, ShaderPackDownloader)
- **`process_manager.py`** — `ProcessManager` class managing QProcess lifecycle with Qt signals for main and server processes
- **`settings_manager.py`** — `SettingsManager` class for JSON settings persistence
- **`style.py`** — Stylesheet constants (MAIN_STYLESHEET, PLAY_BUTTON_STYLE)

### `mc_common.py` — Mojang/network library
All scripts and the UI import from this module. Contains:
- Network helpers in two flavors: `urllib`-based (`fetch_json_url`, `download_url_file`) used by `launch_client.py` and runtime downloads, and `requests`-based (`fetch_json`, `download_file`) used by download scripts
- Mojang rule evaluation (`allowed_by_rules`, `rule_matches`) for filtering libraries/arguments by OS/arch/features
- Java runtime auto-download (`ensure_java_runtime`) — checks Microsoft Store path, then `.minecraft/runtime/`, then downloads from Mojang
- Architecture detection (`detect_arch`) returns `x64`/`x86`/`arm64` and bit width
- Mod sync (`sync_mods`) with client-only filtering and loader-aware filtering

### `launcher_ui.py` — PySide6 GUI entry point
`LauncherWindow` class with tab-based UI. Delegates process management to `ProcessManager`, background tasks to workers in `ui/workers.py`. Settings persisted to `launcher_settings.json`.

### `scripts/` — CLI entry points
Each script does `sys.path.insert(0, parent_dir)` to import `mc_common` and `core/`. They all use `argparse` and return exit codes.

- **`download_version.py`** — Downloads client jar, libraries (Windows-only rules), and assets from Mojang. Requires `requests`.
- **`launch_client.py`** — Builds the full Java command line from the version JSON, resolving JVM/game arguments with Mojang's template substitution (`${variable}`). Generates offline UUIDs and access tokens. Handles native library extraction. Does NOT require `requests`.
- **`download_server.py`** — Downloads `server.jar` for a given version. Writes `server_type.json`. Requires `requests`.
- **`launch_server.py`** — Launches a dedicated server. Uses `core/server_detection.py` for deterministic server type detection. Uses PowerShell to detect running server processes for restart support.
- **`install_fabric.py`** / **`install_forge.py`** — Install mod loaders. Write `server_type.json` for server installations.

## Key Patterns

- Downloads skip files that already exist and match expected size (and optionally SHA1 with `--verify`)
- Platform branching is centralized in `core/platform.py` — use helpers instead of inline `sys.platform` checks
- Version ID resolution (Fabric/Forge/vanilla) is centralized in `core/version_utils.py` — never use inline regex
- Server type detection uses `server_type.json` metadata (written during installation) with filesystem heuristics as fallback — see `core/server_detection.py`
- `ModLoader` and `ServerType` are `str` enums — compare with `==` against string literals or enum values
- `launcher_ui.py` uses collapsible panels with `QPropertyAnimation` for dev settings, logs, and mods
- All scripts use `raise SystemExit(main())` as their entry point pattern
