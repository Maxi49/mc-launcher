import hashlib
import io
import json
import os
import platform
import re
import shutil
import ssl
import stat
import sys
import time
import tomllib
import zipfile
from pathlib import Path
from urllib.request import urlopen

# ── Constants ─────────────────────────────────────────────────

CHUNK_SIZE = 1024 * 128
RUNTIME_DOWNLOAD_TIMEOUT = 60
DEFAULT_RUNTIME_INDEX_URL = (
    "https://launchermeta.mojang.com/v1/products/java-runtime/"
    "2ec0cc96c44e5a76b9c8b7c39df7210883d12871/all.json"
)
RUNTIME_INDEX_URL_RE = re.compile(
    r"https://(?:launchermeta|piston-meta)\.mojang\.com/v1/products/"
    r"java-runtime/[0-9a-f]{40}/all\.json"
)
MANIFEST_URL = "https://launchermeta.mojang.com/mc/game/version_manifest.json"
MOJANG_PROFILE_URL = "https://api.mojang.com/users/profiles/minecraft/"
REQUEST_TIMEOUT = (10, 60)
DOWNLOAD_MAX_RETRIES = 3
DOWNLOAD_RETRY_DELAY = 2

# ── SSL context (macOS + pyenv compatibility) ─────────────────

def _make_ssl_context():
    """Create an SSL context that works on macOS with pyenv-installed Python."""
    # 1. Try certifi (pip install certifi) — most reliable cross-platform
    try:
        import certifi
        ctx = ssl.create_default_context(cafile=certifi.where())
        return ctx
    except ImportError:
        pass

    # 2. Default context — works on Windows and Linux with system certs
    ctx = ssl.create_default_context()

    # 3. On macOS, if default context fails, try common cert file locations
    if sys.platform == "darwin":
        cert_paths = [
            "/etc/ssl/cert.pem",
            "/opt/homebrew/etc/openssl@3/cert.pem",
            "/opt/homebrew/etc/openssl/cert.pem",
            "/usr/local/etc/openssl@3/cert.pem",
            "/usr/local/etc/openssl/cert.pem",
        ]
        for path in cert_paths:
            if os.path.isfile(path):
                try:
                    ctx.load_verify_locations(path)
                    return ctx
                except ssl.SSLError:
                    continue

    return ctx

_SSL_CTX = _make_ssl_context()

# ── I/O and network (urllib, no requests) ─────────────────────


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def fetch_json_url(url):
    with urlopen(url, timeout=RUNTIME_DOWNLOAD_TIMEOUT, context=_SSL_CTX) as fh:
        return json.loads(fh.read())


def download_url_file(url, dest, expected_size=None, expected_sha1=None):
    if dest.exists() and expected_size is not None:
        if dest.stat().st_size == expected_size:
            return False
    elif dest.exists() and expected_size is None:
        return False

    dest.parent.mkdir(parents=True, exist_ok=True)
    last_exc = None
    for attempt in range(1, DOWNLOAD_MAX_RETRIES + 1):
        try:
            hasher = hashlib.sha1() if expected_sha1 else None
            with urlopen(url, timeout=RUNTIME_DOWNLOAD_TIMEOUT, context=_SSL_CTX) as fh, open(dest, "wb") as out:
                while True:
                    chunk = fh.read(CHUNK_SIZE)
                    if not chunk:
                        break
                    out.write(chunk)
                    if hasher:
                        hasher.update(chunk)

            if expected_size is not None and dest.stat().st_size != expected_size:
                raise IOError(f"size mismatch for {dest} (expected {expected_size}, got {dest.stat().st_size})")
            if hasher and expected_sha1:
                actual = hasher.hexdigest()
                if actual.lower() != expected_sha1.lower():
                    raise IOError(f"sha1 mismatch for {dest} (expected {expected_sha1}, got {actual})")
            return True
        except (OSError, IOError) as exc:
            last_exc = exc
            if dest.exists():
                dest.unlink()
            if attempt < DOWNLOAD_MAX_RETRIES:
                print(f"retry {attempt}/{DOWNLOAD_MAX_RETRIES} for {dest}: {exc}")
                time.sleep(DOWNLOAD_RETRY_DELAY * attempt)
    raise OSError(f"failed to download {url} after {DOWNLOAD_MAX_RETRIES} attempts: {last_exc}")


def sha1_file(path):
    hasher = hashlib.sha1()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(CHUNK_SIZE), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


# ── I/O and network (requests session) ───────────────────────


def fetch_json(session, url):
    resp = session.get(url, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    return resp.json()


def download_file(session, url, dest, expected_size=None, expected_sha1=None, verify_sha1=False):
    if dest.exists():
        if expected_size is not None and dest.stat().st_size == expected_size:
            if not verify_sha1:
                return False
            if expected_sha1 and sha1_file(dest).lower() == expected_sha1.lower():
                return False
        if expected_size is None and not verify_sha1:
            return False

    dest.parent.mkdir(parents=True, exist_ok=True)
    last_exc = None
    for attempt in range(1, DOWNLOAD_MAX_RETRIES + 1):
        try:
            with session.get(url, stream=True, timeout=REQUEST_TIMEOUT) as resp:
                resp.raise_for_status()
                with open(dest, "wb") as fh:
                    for chunk in resp.iter_content(chunk_size=CHUNK_SIZE):
                        if chunk:
                            fh.write(chunk)

            if expected_size is not None and dest.stat().st_size != expected_size:
                raise IOError(f"size mismatch for {dest} (expected {expected_size}, got {dest.stat().st_size})")
            if verify_sha1 and expected_sha1:
                actual = sha1_file(dest)
                if actual.lower() != expected_sha1.lower():
                    raise IOError(f"sha1 mismatch for {dest} (expected {expected_sha1}, got {actual})")
            return True
        except (OSError, IOError) as exc:
            last_exc = exc
            if dest.exists():
                dest.unlink()
            if attempt < DOWNLOAD_MAX_RETRIES:
                print(f"retry {attempt}/{DOWNLOAD_MAX_RETRIES} for {dest}: {exc}")
                time.sleep(DOWNLOAD_RETRY_DELAY * attempt)
    raise OSError(f"failed to download {url} after {DOWNLOAD_MAX_RETRIES} attempts: {last_exc}")


# ── Mojang rules ──────────────────────────────────────────────


def rule_matches(rule, os_name, os_arch, os_version, features):
    os_rule = rule.get("os", {})
    if os_rule:
        if "name" in os_rule and os_rule["name"] != os_name:
            return False
        if "arch" in os_rule and os_rule["arch"] != os_arch:
            return False
        if "version" in os_rule:
            try:
                if not re.search(os_rule["version"], os_version):
                    return False
            except re.error:
                return False
    feature_rule = rule.get("features", {})
    for key, value in feature_rule.items():
        if features.get(key) != value:
            return False
    return True


def allowed_by_rules(rules, os_name, os_arch, os_version, features):
    if not rules:
        return True
    allowed = False
    for rule in rules:
        if rule_matches(rule, os_name, os_arch, os_version, features):
            allowed = rule.get("action") == "allow"
    return allowed


# ── OS helpers ────────────────────────────────────────────────


def current_os_name():
    """Returns Mojang OS name: 'windows', 'osx', or 'linux'."""
    if sys.platform == "win32":
        return "windows"
    if sys.platform == "darwin":
        return "osx"
    return "linux"


def default_minecraft_dir():
    if sys.platform == "win32":
        return Path(os.environ.get("APPDATA", ".")) / ".minecraft"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "minecraft"
    return Path.home() / ".minecraft"


# ── Architecture ──────────────────────────────────────────────


def detect_arch():
    machine = platform.machine().lower()
    bits = 64 if sys.maxsize > 2**32 else 32
    if machine in ("arm64", "aarch64"):
        return "arm64", bits
    if bits == 32:
        return "x86", bits
    return "x64", bits


# ── Java runtime ─────────────────────────────────────────────


def runtime_platform_key(os_arch):
    if sys.platform == "darwin":
        return "mac-os-arm64" if os_arch == "arm64" else "mac-os"
    if sys.platform == "win32":
        if os_arch == "x86":   
            return "windows-x86"
        if os_arch == "arm64": 
            return "windows-arm64"
        return "windows-x64"
    return "linux-i386" if os_arch == "x86" else "linux"


def runtime_os_folder(base_dir, component, os_key):
    runtime_dir = base_dir / "runtime" / component
    if runtime_dir.exists():
        if sys.platform == "darwin":
            fallbacks = (os_key, "mac-os", "mac-os-arm64")
        elif sys.platform == "win32":
            fallbacks = (os_key, "windows-x64", "windows-arm64", "windows-x86", "windows")
        else:
            fallbacks = (os_key, "linux", "linux-i386")
        for name in fallbacks:
            if (runtime_dir / name).exists():
                return name
    return os_key


def find_runtime_index_url(base_dir):
    log_paths = sorted(
        base_dir.glob("launcher_log*.txt"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    for path in log_paths:
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as fh:
                for line in fh:
                    match = RUNTIME_INDEX_URL_RE.search(line)
                    if match:
                        return match.group(0)
        except OSError:
            continue
    return DEFAULT_RUNTIME_INDEX_URL


def select_runtime_manifest(index_data, os_key, component):
    platform_data = index_data.get(os_key, {})
    entries = platform_data.get(component, [])
    if not entries:
        return None, None
    entries = sorted(
        entries,
        key=lambda e: e.get("version", {}).get("released", ""),
        reverse=True,
    )
    entry = entries[0]
    manifest = entry.get("manifest", {})
    return manifest.get("url"), entry.get("version", {}).get("name")


def ensure_java_runtime(base_dir, component, os_arch):
    os_key = runtime_platform_key(os_arch)

    if sys.platform == "win32":
        store_runtime = (
            Path(os.environ.get("LOCALAPPDATA", "."))
            / "Packages"
            / "Microsoft.4297127D64EC6_8wekyb3d8bbwe"
            / "LocalCache"
            / "Local"
            / "runtime"
            / component
            / os_key
            / component
        )
        store_javaw = store_runtime / "bin" / "javaw.exe"
        store_java = store_runtime / "bin" / "java.exe"
        if store_javaw.exists() or store_java.exists():
            return store_javaw if store_javaw.exists() else store_java

    os_folder = runtime_os_folder(base_dir, component, os_key)
    runtime_root = base_dir / "runtime" / component / os_folder / component
    if sys.platform == "win32":
        javaw = runtime_root / "bin" / "javaw.exe"
        java = runtime_root / "bin" / "java.exe"
    else:
        javaw = None
        java = runtime_root / "bin" / "java"
    if (javaw is not None and javaw.exists()) or java.exists():
        return javaw if (javaw is not None and javaw.exists()) else java

    index_url = find_runtime_index_url(base_dir)
    print(f"downloading runtime manifest from {index_url}...")
    try:
        index_data = fetch_json_url(index_url)
    except OSError as exc:
        print(f"failed to download runtime index: {exc}", file=sys.stderr)
        return None
    manifest_url, version_name = select_runtime_manifest(index_data, os_key, component)
    if not manifest_url:
        print(
            f"runtime component not found: {component} for {os_key}",
            file=sys.stderr,
        )
        return None

    print(f"downloading java runtime {component} ({version_name})...")
    try:
        manifest = fetch_json_url(manifest_url)
    except OSError as exc:
        print(f"failed to download runtime manifest: {exc}", file=sys.stderr)
        return None
    files = manifest.get("files", {})
    file_items = [(p, info) for p, info in files.items()]
    total = len(file_items)

    runtime_root.mkdir(parents=True, exist_ok=True)
    for idx, (rel_path, info) in enumerate(file_items, 1):
        entry_type = info.get("type")
        dest = runtime_root / rel_path
        if entry_type == "directory":
            dest.mkdir(parents=True, exist_ok=True)
            continue
        if entry_type != "file":
            continue
        downloads = info.get("downloads", {})
        raw = downloads.get("raw")
        if not raw:
            continue
        download_url_file(
            raw["url"],
            dest,
            expected_size=raw.get("size"),
            expected_sha1=raw.get("sha1"),
        )
        if sys.platform != "win32" and info.get("executable"):
            dest.chmod(dest.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
        if idx % 100 == 0 or idx == total:
            print(f"  runtime progress: {idx}/{total}")

    version_file = runtime_root.parent / ".version"
    if version_name:
        version_file.write_text(version_name, encoding="utf-8")

    if javaw is not None and javaw.exists():
        return javaw
    return java if java.exists() else None


def find_java(java_arg):
    if java_arg:
        return java_arg
    from core.platform import java_executable_names
    names = java_executable_names()
    for name in names:
        path = shutil.which(name)
        if path:
            return path
    return None


# ── Utilities ─────────────────────────────────────────────────


def find_version(manifest, version_id):
    for entry in manifest.get("versions", []):
        if entry.get("id") == version_id:
            return entry
    return None


def format_cmd(args):
    return " ".join(f"\"{a}\"" if " " in a else a for a in args)


# ── Mod sync ─────────────────────────────────────────────────

# Mods that are always client-only but don't declare it in their metadata.
# Keyed by modId (Forge mods.toml) or mod id (Fabric fabric.mod.json).
_KNOWN_CLIENT_ONLY_MODS = frozenset({
    "optifine",
    "oculus",        # Forge shader mod (Iris equivalent)
    "rubidium",      # Forge rendering mod (Sodium equivalent)
    "sodium",        # Fabric rendering optimization
    "iris",          # Fabric shader mod
})


def is_client_only_mod(jar_path):
    """Check if a mod JAR is client-only by inspecting its metadata.

    Checks Fabric (fabric.mod.json), Forge (META-INF/mods.toml),
    and NeoForge (META-INF/neoforge.mods.toml).
    Also checks against a known list of client-only mod IDs for mods
    that don't declare clientSideOnly (e.g. OptiFine).

    Returns (True, reason) if client-only, (False, None) otherwise.
    Unreadable or malformed JARs return (False, None) as a safe default.
    """
    try:
        zf = zipfile.ZipFile(jar_path, "r")
    except (zipfile.BadZipFile, OSError):
        return False, None

    with zf:
        # Fabric: fabric.mod.json → "environment": "client"
        try:
            raw = zf.read("fabric.mod.json")
            data = json.loads(raw)
            if data.get("environment") == "client":
                return True, "Fabric environment=client"
            mod_id = data.get("id", "")
            if mod_id.lower() in _KNOWN_CLIENT_ONLY_MODS:
                return True, f"known client-only mod: {mod_id}"
        except (KeyError, json.JSONDecodeError, OSError):
            pass

        # Forge / NeoForge: META-INF/mods.toml
        for toml_path, label in (
            ("META-INF/mods.toml", "Forge"),
            ("META-INF/neoforge.mods.toml", "NeoForge"),
        ):
            try:
                raw = zf.read(toml_path)
                data = tomllib.load(io.BytesIO(raw))
                if data.get("clientSideOnly") is True:
                    return True, f"{label} clientSideOnly=true"
                # Check mod IDs against known client-only list
                for mod in data.get("mods", []):
                    mod_id = mod.get("modId", "")
                    if mod_id.lower() in _KNOWN_CLIENT_ONLY_MODS:
                        return True, f"known client-only mod: {mod_id}"
            except (KeyError, tomllib.TOMLDecodeError, OSError):
                pass

    return False, None


def detect_mod_loader(jar_path):
    """Detect which mod loader a JAR targets by inspecting its metadata.

    Returns 'fabric', 'forge', or None if undetectable.
    """
    try:
        zf = zipfile.ZipFile(jar_path, "r")
    except (zipfile.BadZipFile, OSError):
        return None

    with zf:
        names = zf.namelist()
        has_fabric = "fabric.mod.json" in names
        has_forge = "META-INF/mods.toml" in names or "META-INF/neoforge.mods.toml" in names
        if has_fabric and not has_forge:
            return "fabric"
        if has_forge and not has_fabric:
            return "forge"
        if has_fabric and has_forge:
            return "fabric"  # dual-loader mods, favor fabric
    return None


def sync_mods(client_mods_dir, server_mods_dir, server_loader=None):
    """Sync mod jars from the client mods folder to the server mods folder.

    - Copies new/updated mods from client to server.
    - Removes server mods that are no longer active on the client
      (deleted or disabled via .jar.disabled).
    - Skips client-only mods and removes them from the server.
    - If server_loader is set ('fabric' or 'forge'), skips mods
      targeting a different loader.
    Returns (copied, skipped) counts.
    """
    client_mods_dir = Path(client_mods_dir)
    server_mods_dir = Path(server_mods_dir)
    if not client_mods_dir.is_dir():
        return 0, 0
    server_mods_dir.mkdir(parents=True, exist_ok=True)

    # Build set of active (non-client-only) client mod filenames
    active_client_mods = set()
    copied = 0
    skipped = 0
    removed = 0
    for jar in sorted(client_mods_dir.glob("*.jar")):
        client_only, reason = is_client_only_mod(jar)
        if client_only:
            print(f"  skipped client-only mod: {jar.name} ({reason})")
            skipped += 1
            continue
        # Skip mods targeting a different loader
        if server_loader:
            mod_loader = detect_mod_loader(jar)
            if mod_loader and mod_loader != server_loader:
                print(f"  skipped {mod_loader} mod on {server_loader} server: {jar.name}")
                skipped += 1
                continue
        active_client_mods.add(jar.name)
        dest = server_mods_dir / jar.name
        if dest.exists() and dest.stat().st_size == jar.stat().st_size:
            continue
        shutil.copy2(str(jar), str(dest))
        print(f"  synced mod: {jar.name}")
        copied += 1

    # Remove server mods that are no longer active on the client
    for server_jar in sorted(server_mods_dir.glob("*.jar")):
        if server_jar.name not in active_client_mods:
            server_jar.unlink()
            print(f"  removed from server: {server_jar.name}")
            removed += 1

    return copied, skipped, removed


# ── Username validation ──────────────────────────────────────


def check_username_taken(username):
    """Check if a Minecraft username is taken by a premium account.

    Returns a dict with:
      - "taken": True/False/None (None = could not determine)
      - "uuid": the account UUID if taken, else None
      - "correct_name": the exact casing of the name if taken
      - "error": error message string if the check failed
    """
    _not_taken = {"taken": False, "uuid": None, "correct_name": None, "error": None}
    url = MOJANG_PROFILE_URL + username
    try:
        with urlopen(url, timeout=10, context=_SSL_CTX) as resp:
            data = json.loads(resp.read())
            return {
                "taken": True,
                "uuid": data.get("id"),
                "correct_name": data.get("name"),
                "error": None,
            }
    except Exception as exc:
        code = getattr(exc, "code", None)
        if code in (204, 404):
            return _not_taken
        return {
            "taken": None,
            "uuid": None,
            "correct_name": None,
            "error": str(exc),
        }
