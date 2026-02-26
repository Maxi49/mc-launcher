import argparse
import base64
import hashlib
import os
import platform
import re
import shutil
import subprocess
import sys
import uuid
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from mc_common import (
    CHUNK_SIZE,
    RUNTIME_DOWNLOAD_TIMEOUT,
    read_json,
    fetch_json_url,
    detect_arch,
    rule_matches,
    allowed_by_rules,
    download_url_file,
    runtime_platform_key,
    runtime_os_folder,
    find_runtime_index_url,
    select_runtime_manifest,
    ensure_java_runtime,
    find_java,
    current_os_name,
    default_minecraft_dir,
)

DEFAULT_XMX = "2G"
OFFICIAL_JVM_FLAGS = [
    "-XX:+UnlockExperimentalVMOptions",
    "-XX:+UseG1GC",
    "-XX:G1NewSizePercent=20",
    "-XX:G1ReservePercent=20",
    "-XX:MaxGCPauseMillis=50",
    "-XX:G1HeapRegionSize=32M",
]


def parse_maven_name(name):
    parts = name.split(":")
    classifier = parts[3] if len(parts) > 3 else None
    return classifier


def native_classifier_matches(classifier, os_name, os_arch):
    if os_name == "windows":
        if classifier == "natives-windows":       return os_arch == "x64"
        if classifier == "natives-windows-x86":   return os_arch == "x86"
        if classifier == "natives-windows-arm64":  return os_arch == "arm64"
    elif os_name == "osx":
        if classifier in ("natives-osx", "natives-macos"): return True
        if classifier == "natives-macos-arm64":   return os_arch == "arm64"
    elif os_name == "linux":
        if classifier == "natives-linux":         return True
    return False


def offline_uuid(username):
    name_bytes = ("OfflinePlayer:" + username).encode("utf-8")
    digest = hashlib.md5(name_bytes).digest()
    data = bytearray(digest)
    data[6] = (data[6] & 0x0F) | 0x30
    data[8] = (data[8] & 0x3F) | 0x80
    return str(uuid.UUID(bytes=bytes(data)))


def offline_access_token(username):
    """Generate a deterministic fake access token for offline play."""
    token_bytes = hashlib.sha256(
        ("OfflineToken:" + username).encode("utf-8")
    ).hexdigest()
    return token_bytes


def base64_uuid(uuid_str):
    return base64.b64encode(uuid_str.encode("utf-8")).decode("ascii")


def substitute(value, replacements):
    def repl(match):
        key = match.group(1)
        return str(replacements.get(key, match.group(0)))

    return re.sub(r"\$\{([^}]+)\}", repl, value)


def resolve_arguments(items, replacements, os_name, os_arch, os_version, features):
    resolved = []
    for item in items:
        if isinstance(item, str):
            resolved.append(substitute(item, replacements))
        elif isinstance(item, dict):
            rules = item.get("rules", [])
            if allowed_by_rules(rules, os_name, os_arch, os_version, features):
                value = item.get("value")
                if isinstance(value, list):
                    resolved.extend(substitute(v, replacements) for v in value)
                elif isinstance(value, str):
                    resolved.append(substitute(value, replacements))
    return resolved


def jvm_args_contain(items, needle):
    for item in items:
        if isinstance(item, str):
            if needle in item:
                return True
        elif isinstance(item, dict):
            value = item.get("value")
            if isinstance(value, str):
                if needle in value:
                    return True
            elif isinstance(value, list):
                for entry in value:
                    if isinstance(entry, str) and needle in entry:
                        return True
    return False


def load_merged_version(versions_dir, version_id):
    """Load a version JSON, recursively merging with parent if inheritsFrom is set.

    Fabric version JSONs declare inheritsFrom pointing at the vanilla version.
    The merge rules follow the official launcher spec:
    - libraries: parent list + child list
    - arguments.game/jvm: parent list + child list
    - all other keys: child value takes precedence; parent value used as fallback
    """
    json_path = Path(versions_dir) / version_id / f"{version_id}.json"
    data = read_json(json_path)
    parent_id = data.get("inheritsFrom")
    if not parent_id:
        return data
    parent = load_merged_version(versions_dir, parent_id)
    merged = dict(parent)
    for key, val in data.items():
        if key == "inheritsFrom":
            continue
        if key == "libraries":
            merged["libraries"] = parent.get("libraries", []) + val
        elif key == "arguments":
            merged_args = {}
            for akey in ("game", "jvm"):
                merged_args[akey] = (
                    parent.get("arguments", {}).get(akey, []) + val.get(akey, [])
                )
            merged["arguments"] = merged_args
        else:
            merged[key] = val
    return merged


def extract_native_jar(jar_path, dest_dir, excludes):
    with zipfile.ZipFile(jar_path, "r") as zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            name = info.filename
            if any(name.startswith(prefix) for prefix in excludes):
                continue
            if ".." in Path(name).parts:
                continue
            lower = name.lower()
            if not lower.endswith((".dll", ".so", ".dylib")):
                continue
            out_path = dest_dir / Path(name).name
            out_path.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, open(out_path, "wb") as dst:
                shutil.copyfileobj(src, dst, CHUNK_SIZE)


def main():
    default_base = default_minecraft_dir()
    parser = argparse.ArgumentParser(
        description="Offline Minecraft launcher using an existing .minecraft folder."
    )
    parser.add_argument("version_id", help="Version id, e.g. 1.21.11")
    parser.add_argument("--base-dir", default=str(default_base), help="Path to .minecraft")
    parser.add_argument("--game-dir", default=None, help="Game directory (defaults to base-dir)")
    parser.add_argument("--java", default=None, help="Path to java.exe or javaw.exe")
    parser.add_argument("--username", default="Player", help="Offline username")
    parser.add_argument("--xmx", default=None, help="Max memory, e.g. 4G")
    parser.add_argument("--xms", default=None, help="Min memory, e.g. 1G")
    parser.add_argument("--xss", default=None, help="Thread stack size, e.g. 1M")
    parser.add_argument("--width", type=int, default=None, help="Window width")
    parser.add_argument("--height", type=int, default=None, help="Window height")
    parser.add_argument("--demo", action="store_true", help="Enable demo mode")
    parser.add_argument("--dry-run", action="store_true", help="Print command and exit")
    parser.add_argument("--clean-natives", action="store_true", help="Recreate natives folder")
    parser.add_argument(
        "--extract-natives",
        action="store_true",
        help="Extract native libraries instead of letting LWJGL do it",
    )
    parser.add_argument(
        "--bin-hash",
        default=None,
        help="Override bin hash folder name (uses .minecraft/bin/<hash>)",
    )
    parser.add_argument(
        "--natives-dir",
        default=None,
        help="Explicit natives directory path (overrides bin hash)",
    )
    parser.add_argument(
        "--launcher-name", default="minecraft-launcher", help="Launcher name"
    )
    parser.add_argument("--launcher-version", default="3.26.31", help="Launcher version")
    parser.add_argument("--uuid", default=None, help="UUID (no dashes recommended)")
    parser.add_argument("--access-token", default=None, help="Access token value (auto-generated if not set)")
    parser.add_argument("--client-id", default=None, help="Client id value")
    parser.add_argument("--xuid", default="0", help="Xbox user id value")
    parser.add_argument("--user-type", default="msa", help="User type value")
    parser.add_argument(
        "--no-official-jvm-flags",
        action="store_false",
        dest="official_jvm_flags",
        help="Disable extra JVM flags used by the official launcher",
    )
    parser.set_defaults(official_jvm_flags=True)
    args = parser.parse_args()

    base_dir = Path(args.base_dir)
    game_dir = Path(args.game_dir) if args.game_dir else base_dir
    version_id = args.version_id

    version_dir = base_dir / "versions" / version_id
    version_json_path = version_dir / f"{version_id}.json"
    if not version_json_path.exists():
        print(f"missing version json: {version_json_path}", file=sys.stderr)
        return 1
    version_data = load_merged_version(base_dir / "versions", version_id)

    os_name = current_os_name()
    os_arch, arch_bits = detect_arch()
    os_version = platform.version()
    features = {
        "is_demo_user": args.demo,
        "has_custom_resolution": args.width is not None and args.height is not None,
        "has_quick_plays_support": False,
        "is_quick_play_singleplayer": False,
        "is_quick_play_multiplayer": False,
        "is_quick_play_realms": False,
    }
    args_data = version_data.get("arguments", {})

    libs_dir = base_dir / "libraries"
    classpath_entries = []
    native_jars = []
    native_hash_entries = []
    missing = []

    for lib in version_data.get("libraries", []):
        if not allowed_by_rules(
            lib.get("rules", []), os_name, os_arch, os_version, features
        ):
            continue

        downloads = lib.get("downloads", {})
        artifact = downloads.get("artifact")
        classifier = parse_maven_name(lib.get("name", ""))

        if artifact:
            lib_path = libs_dir / artifact["path"]
            if not lib_path.exists():
                missing.append(lib_path)
            else:
                classpath_entries.append(lib_path)

        if classifier and classifier.startswith("natives-") and artifact:
            if native_classifier_matches(classifier, os_name, os_arch):
                native_path = libs_dir / artifact["path"]
                if not native_path.exists():
                    missing.append(native_path)
                else:
                    native_jars.append(
                        (native_path, lib.get("extract", {}).get("exclude", []))
                    )
                    native_hash_entries.append(artifact["path"])

        natives = lib.get("natives")
        classifiers = downloads.get("classifiers")
        if natives and classifiers:
            native_key = natives.get(os_name)
            if native_key:
                native_key = native_key.replace("${arch}", "64" if arch_bits == 64 else "32")
                native_info = classifiers.get(native_key)
                if native_info:
                    native_path = libs_dir / native_info["path"]
                    if not native_path.exists():
                        missing.append(native_path)
                    else:
                        native_jars.append(
                            (native_path, lib.get("extract", {}).get("exclude", []))
                        )
                        native_hash_entries.append(native_info["path"])

    version_jar = version_dir / f"{version_id}.jar"
    if not version_jar.exists():
        missing.append(version_jar)

    if missing:
        print("missing files:", file=sys.stderr)
        for path in missing[:20]:
            print(f"  {path}", file=sys.stderr)
        if len(missing) > 20:
            print(f"  ... and {len(missing) - 20} more", file=sys.stderr)
        print("run download_version_windows.py to fetch missing files", file=sys.stderr)
        return 1

    jvm_source = args_data.get("jvm", [])
    auto_extract_natives = False
    if native_jars and not args.extract_natives:
        has_extract_paths = (
            jvm_args_contain(jvm_source, "SharedLibraryExtractPath")
            or jvm_args_contain(jvm_source, "jna.tmpdir")
            or jvm_args_contain(jvm_source, "io.netty.native.workdir")
        )
        if not has_extract_paths:
            auto_extract_natives = True

    bin_dir = base_dir / "bin"
    if native_hash_entries:
        hash_source = "\n".join(sorted(native_hash_entries)).encode("utf-8")
        natives_hash = hashlib.sha1(hash_source).hexdigest()
    else:
        natives_hash = version_id
    if args.natives_dir:
        natives_dir = Path(args.natives_dir)
    elif args.bin_hash:
        natives_dir = bin_dir / args.bin_hash
    else:
        natives_dir = bin_dir / natives_hash
    if args.clean_natives and natives_dir.exists():
        shutil.rmtree(natives_dir)
    natives_dir.mkdir(parents=True, exist_ok=True)

    if (args.extract_natives or auto_extract_natives) and not args.dry_run:
        if auto_extract_natives and not args.extract_natives:
            print("note: extracting natives for this version")
        for jar_path, excludes in native_jars:
            extract_native_jar(jar_path, natives_dir, excludes)

    assets_root = base_dir / "assets"
    asset_index = version_data.get("assetIndex", {}).get("id") or version_data.get("assets")
    if asset_index:
        asset_index_path = assets_root / "indexes" / f"{asset_index}.json"
        if not asset_index_path.exists():
            print(f"warning: missing asset index {asset_index_path}")

    log_arg = None
    logging_info = version_data.get("logging", {}).get("client")
    if logging_info:
        log_file = logging_info.get("file", {})
        log_id = log_file.get("id")
        if log_id:
            log_path = assets_root / "log_configs" / log_id
            if not log_path.exists():
                print(f"warning: missing log config {log_path}")
            arg_template = logging_info.get("argument")
            if arg_template:
                log_arg = arg_template.replace("${path}", str(log_path))

    classpath = os.pathsep.join(str(p) for p in classpath_entries + [version_jar])

    username = args.username
    auth_uuid = args.uuid or offline_uuid(args.username).replace("-", "")
    access_token = args.access_token or offline_access_token(args.username)
    client_id = args.client_id or base64_uuid(offline_uuid(args.username))
    xuid = args.xuid
    user_type = args.user_type

    replacements = {
        "auth_player_name": username,
        "version_name": version_id,
        "game_directory": str(game_dir),
        "assets_root": str(assets_root),
        "assets_index_name": str(asset_index or ""),
        "auth_uuid": auth_uuid,
        "auth_access_token": access_token,
        "clientid": client_id,
        "auth_xuid": xuid,
        "user_type": user_type,
        "version_type": str(version_data.get("type", "release")),
        "resolution_width": str(args.width or ""),
        "resolution_height": str(args.height or ""),
        "launcher_name": args.launcher_name,
        "launcher_version": args.launcher_version,
        "classpath": classpath,
        "natives_directory": str(natives_dir),
        "user_properties": "{}",
    }

    jvm_args = resolve_arguments(
        args_data.get("jvm", []), replacements, os_name, os_arch, os_version, features
    )
    game_args = resolve_arguments(
        args_data.get("game", []), replacements, os_name, os_arch, os_version, features
    )

    if args.official_jvm_flags:
        if not any(arg.startswith("-Xss") for arg in jvm_args):
            jvm_args.insert(0, f"-Xss{args.xss or '1M'}")

        if args.xms:
            jvm_args.append(f"-Xms{args.xms}")
        if args.xmx:
            jvm_args.append(f"-Xmx{args.xmx}")
        else:
            jvm_args.append(f"-Xmx{DEFAULT_XMX}")

        for flag in OFFICIAL_JVM_FLAGS:
            if flag not in jvm_args:
                jvm_args.append(flag)
    else:
        if args.xms:
            jvm_args.append(f"-Xms{args.xms}")
        if args.xmx:
            jvm_args.append(f"-Xmx{args.xmx}")
    if log_arg:
        jvm_args.append(log_arg)

    main_class = version_data.get("mainClass")
    if not main_class:
        print("missing mainClass in version json", file=sys.stderr)
        return 1

    java_exe = args.java
    component = version_data.get("javaVersion", {}).get("component")
    if not java_exe and component:
        java_exe = ensure_java_runtime(base_dir, component, os_arch)
    if not java_exe:
        java_exe = find_java(None)
    if not java_exe:
        print("java not found in PATH; use --java to set it", file=sys.stderr)
        return 1

    cmd = [str(java_exe)] + [str(c) for c in jvm_args] + [main_class] + [
        str(c) for c in game_args
    ]

    print(f"version: {version_id}")
    print(f"base_dir: {base_dir}")
    print(f"game_dir: {game_dir}")
    print(f"libraries: {len(classpath_entries)} native_jars: {len(native_jars)}")
    print(f"natives_dir: {natives_dir}")
    if args.dry_run:
        print("dry run: command follows")
        print(" ".join(f"\"{c}\"" if " " in c else c for c in cmd))
        return 0

    return subprocess.call(cmd, cwd=game_dir)


if __name__ == "__main__":
    raise SystemExit(main())
