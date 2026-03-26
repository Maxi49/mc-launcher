"""Centralized version ID resolution and mod loader detection."""

import re
from pathlib import Path

from core.constants import ModLoader


def resolve_mc_version(version_name):
    """Extract the base Minecraft version from a loader version string.

    Examples:
        fabric-loader-0.16.5-1.21.1  -> 1.21.1
        1.20.1-forge-47.3.0          -> 1.20.1
        1.21.1                       -> 1.21.1
    """
    # Fabric: MC version is last segment  e.g. fabric-loader-0.16.5-1.21.1
    if "fabric" in version_name.lower():
        matches = re.findall(r'(\d+\.\d+(?:\.\d+)?)', version_name)
        return matches[-1] if matches else version_name
    # Forge / vanilla: MC version is first segment  e.g. 1.20.1-forge-47.3.0
    m = re.search(r'(\d+\.\d+(?:\.\d+)?)', version_name)
    return m.group(1) if m else version_name


def detect_version_loader(version_name):
    """Detect the mod loader from a version directory name.

    Returns a ModLoader enum value.
    """
    lower = version_name.lower()
    if "fabric" in lower:
        return ModLoader.FABRIC
    if "forge" in lower:
        return ModLoader.FORGE
    return ModLoader.VANILLA


def extract_mod_mc_version(filename):
    """Extract MC version from mod filename.

    Patterns:
      mod-name+mc1.21.1.jar   -> 1.21.1
      mod-name+1.21.1.jar     -> 1.21.1
      mod-name-mc1.21.1.jar   -> 1.21.1
      mod-name_MC_1.21.1.jar  -> 1.21.1
      mod-name-1.21.1.jar     -> 1.21.1  (last mc-like version)
    Returns None if no version detected.
    """
    stem = Path(filename).stem
    m = re.search(r'\+(?:mc)?(\d+\.\d+(?:\.\d+)?)', stem, re.IGNORECASE)
    if m:
        return m.group(1)
    m = re.search(r'[-_]mc[_.]?(\d+\.\d+(?:\.\d+)?)', stem, re.IGNORECASE)
    if m:
        return m.group(1)
    matches = re.findall(r'(\d+\.\d+(?:\.\d+)?)', stem)
    return matches[-1] if matches else None


def maven_to_path(name):
    """Convert a Maven coordinate (group:artifact:version[:classifier]) to a relative jar path."""
    parts = name.split(":")
    if len(parts) < 3:
        return None
    group, artifact, version = parts[0], parts[1], parts[2]
    classifier = parts[3] if len(parts) > 3 else None
    group_path = group.replace(".", "/")
    jar_name = f"{artifact}-{version}" + (f"-{classifier}" if classifier else "") + ".jar"
    return f"{group_path}/{artifact}/{version}/{jar_name}"
