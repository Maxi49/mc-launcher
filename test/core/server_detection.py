"""Deterministic server type detection with metadata file + filesystem fallback."""

import json
from pathlib import Path

from core.constants import ServerType, SERVER_TYPE_FILE, FABRIC_SERVER_JAR, SERVER_JAR
from core.platform import forge_run_script_name


def write_server_type(server_dir, server_type, installed_by=None):
    """Write a server_type.json metadata file to the server directory."""
    server_dir = Path(server_dir)
    meta_path = server_dir / SERVER_TYPE_FILE
    data = {"type": server_type if isinstance(server_type, str) else server_type.value}
    if installed_by:
        data["installed_by"] = installed_by
    meta_path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def detect_server_type(server_dir):
    """Deterministic server type detection.

    Priority:
    1. Read server_type.json if present (written during installation)
    2. Fall back to filesystem heuristics with content inspection

    Returns a ServerType enum value.
    """
    server_dir = Path(server_dir)

    # 1. Check metadata file
    meta_path = server_dir / SERVER_TYPE_FILE
    if meta_path.exists():
        try:
            data = json.loads(meta_path.read_text(encoding="utf-8"))
            server_type = data.get("type", "").lower()
            if server_type in (e.value for e in ServerType):
                return ServerType(server_type)
        except (OSError, json.JSONDecodeError):
            pass  # Fall through to heuristics

    # 2. Filesystem heuristics with content inspection
    forge_run = server_dir / forge_run_script_name()
    fabric_jar = server_dir / FABRIC_SERVER_JAR
    server_jar = server_dir / SERVER_JAR

    has_forge = forge_run.exists()
    has_fabric = fabric_jar.exists()

    # Forge run scripts contain distinctive content — inspect to confirm
    if has_forge:
        try:
            content = forge_run.read_text(encoding="utf-8", errors="ignore")
            if "forge" in content.lower():
                return ServerType.FORGE
        except OSError:
            pass

    if has_fabric and not has_forge:
        return ServerType.FABRIC

    if has_forge and not has_fabric:
        return ServerType.FORGE

    # Ambiguous: both exist — this was the production bug scenario
    if has_forge and has_fabric:
        print(
            f"WARNING: both Forge run script and Fabric jar found in {server_dir}. "
            f"Create a {SERVER_TYPE_FILE} to disambiguate. Defaulting to Forge."
        )
        return ServerType.FORGE

    if server_jar.exists():
        return ServerType.VANILLA

    return ServerType.VANILLA
