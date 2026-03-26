"""Centralized constants and enums for the Minecraft launcher."""

from enum import Enum


class ModLoader(str, Enum):
    FABRIC = "fabric"
    FORGE = "forge"
    VANILLA = "vanilla"


class ServerType(str, Enum):
    VANILLA = "vanilla"
    FABRIC = "fabric"
    FORGE = "forge"


# Server file names
FABRIC_SERVER_JAR = "fabric-server-launch.jar"
FORGE_RUN_BAT = "run.bat"
FORGE_RUN_SH = "run.sh"
SERVER_JAR = "server.jar"
SERVER_TYPE_FILE = "server_type.json"

# UI tab indices
TAB_HOME = 0
TAB_MODS = 1
TAB_SHADERS = 2
TAB_SERVER = 3
TAB_SETTINGS = 4
