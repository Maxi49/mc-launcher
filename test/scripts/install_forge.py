"""Install Forge mod loader (client or server) for a given Minecraft version."""
import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path
from urllib.request import urlopen, Request

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from mc_common import (
    ensure_java_runtime,
    detect_arch,
    find_java,
    default_minecraft_dir,
    _SSL_CTX,
)

FORGE_PROMOTIONS_URL = (
    "https://files.minecraftforge.net/net/minecraftforge/forge/promotions_slim.json"
)
FORGE_MAVEN_BASE = "https://maven.minecraftforge.net/net/minecraftforge/forge"


def _get_forge_versions(mc_version):
    """Query promotions_slim.json and return available Forge versions for a MC version.

    Returns dict like {"latest": "47.3.0", "recommended": "47.2.0"} or None.
    """
    req = Request(FORGE_PROMOTIONS_URL, headers={"User-Agent": "Mozilla/5.0"})
    with urlopen(req, timeout=30, context=_SSL_CTX) as resp:
        data = json.loads(resp.read())
    promos = data.get("promos", {})
    result = {}
    latest = promos.get(f"{mc_version}-latest")
    recommended = promos.get(f"{mc_version}-recommended")
    if latest:
        result["latest"] = latest
    if recommended:
        result["recommended"] = recommended
    return result if result else None


def _download_forge_file(url, dest):
    """Download a file from Forge servers with a proper User-Agent header."""
    if dest.exists():
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    req = Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urlopen(req, timeout=120, context=_SSL_CTX) as resp, open(dest, "wb") as out:
        while True:
            chunk = resp.read(128 * 1024)
            if not chunk:
                break
            out.write(chunk)


def _download_installer(mc_version, forge_version, dest_dir):
    """Download the Forge installer JAR from Maven. Returns path to the JAR."""
    full_version = f"{mc_version}-{forge_version}"
    jar_name = f"forge-{full_version}-installer.jar"
    url = f"{FORGE_MAVEN_BASE}/{full_version}/{jar_name}"
    dest = Path(dest_dir) / jar_name
    print(f"Downloading Forge installer: {jar_name}")
    _download_forge_file(url, dest)
    print(f"Saved: {dest}")
    return dest


def _find_java(base_dir):
    """Find a Java executable, checking PATH first, then Mojang runtime."""
    java = find_java(None)
    if java:
        return java
    base_dir = Path(base_dir)
    arch = detect_arch()[0]
    # Try java-runtime-gamma (Java 17) first, then java-runtime-beta (Java 16)
    for component in ("java-runtime-gamma", "java-runtime-beta", "java-runtime-alpha"):
        result = ensure_java_runtime(base_dir, component, arch)
        if result:
            return result
    return None


def install_client(mc_version, base_dir, forge_version):
    """Install Forge client by launching the Forge installer GUI."""
    base_dir = Path(base_dir)

    # 1. Resolve forge version
    if not forge_version:
        versions = _get_forge_versions(mc_version)
        if not versions:
            print(f"No Forge versions found for Minecraft {mc_version}", file=sys.stderr)
            return 1
        forge_version = versions.get("recommended") or versions.get("latest")
        print(f"Using Forge {forge_version} ({'recommended' if 'recommended' in versions else 'latest'})")

    # 2. Download installer to temp dir
    tmp_dir = base_dir / "forge_installer_tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    try:
        installer_path = _download_installer(mc_version, forge_version, tmp_dir)
    except OSError as exc:
        print(f"Failed to download Forge installer: {exc}", file=sys.stderr)
        shutil.rmtree(tmp_dir, ignore_errors=True)
        return 1

    # 3. Find Java
    java = _find_java(base_dir)
    if not java:
        print("Java not found. Install Java or set --java.", file=sys.stderr)
        shutil.rmtree(tmp_dir, ignore_errors=True)
        return 1

    # 4. Launch installer GUI
    print(f"Launching Forge installer GUI...")
    print("Click 'Install Client' in the Forge installer window.")
    print("Make sure the install path points to your .minecraft folder:")
    print(f"  {base_dir}")
    result = subprocess.call([str(java), "-jar", str(installer_path)], cwd=str(base_dir))

    # Clean up installer log file that Forge drops in cwd
    for log in base_dir.glob("forge-*-installer.jar.log"):
        try:
            log.unlink()
        except OSError:
            pass  # file may still be locked by the installer process

    # 5. Find the Forge version directory and copy vanilla jar into it
    #    (so list_installed_versions picks it up — it requires both .json and .jar)
    versions_dir = base_dir / "versions"
    parent_jar = versions_dir / mc_version / f"{mc_version}.jar"
    forge_dir = None
    if versions_dir.exists():
        for entry in sorted(versions_dir.iterdir(), key=lambda e: e.stat().st_mtime, reverse=True):
            if entry.is_dir() and "forge" in entry.name.lower() and mc_version in entry.name:
                if (entry / f"{entry.name}.json").exists():
                    forge_dir = entry
                    break

    if forge_dir:
        forge_jar = forge_dir / f"{forge_dir.name}.jar"
        if not forge_jar.exists() and parent_jar.exists():
            shutil.copy2(str(parent_jar), str(forge_jar))
            print(f"Copied client jar from {mc_version}")
        elif not forge_jar.exists():
            print(
                f"Warning: vanilla jar {parent_jar} not found — "
                "the Forge version may not appear in the launcher.",
                file=sys.stderr,
            )
        print(f"\nForge installed: {forge_dir.name}")
        print("Select this version in the launcher to play with mods.")
    else:
        if result == 0:
            print("\nForge installer finished but version directory was not found.")
            print("Check your .minecraft/versions/ folder.")
        else:
            print(f"\nForge installer exited with code {result}.", file=sys.stderr)

    # 6. Cleanup
    shutil.rmtree(tmp_dir, ignore_errors=True)
    return 0


def install_server(mc_version, servers_dir, forge_version, instance=None):
    """Install Forge server using --installServer (headless)."""
    server_dir = Path(servers_dir) / mc_version
    if instance:
        server_dir = server_dir / instance
    server_dir.mkdir(parents=True, exist_ok=True)

    # 1. Resolve forge version
    if not forge_version:
        versions = _get_forge_versions(mc_version)
        if not versions:
            print(f"No Forge versions found for Minecraft {mc_version}", file=sys.stderr)
            return 1
        forge_version = versions.get("recommended") or versions.get("latest")
        print(f"Using Forge {forge_version} ({'recommended' if 'recommended' in versions else 'latest'})")

    # 2. Download installer to server dir
    try:
        installer_path = _download_installer(mc_version, forge_version, server_dir)
    except OSError as exc:
        print(f"Failed to download Forge installer: {exc}", file=sys.stderr)
        return 1

    # 3. Find Java
    minecraft_dir = default_minecraft_dir()
    java = _find_java(minecraft_dir)
    if not java:
        print("Java not found. Install Java or set --java.", file=sys.stderr)
        return 1

    # 4. Run --installServer (headless)
    print("Installing Forge server (headless)...")
    result = subprocess.call(
        [str(java), "-jar", str(installer_path), "--installServer"],
        cwd=str(server_dir),
    )

    # 5. Cleanup installer JAR
    if installer_path.exists():
        installer_path.unlink()
        print("Cleaned up installer JAR.")

    # 6. Verify
    if result != 0:
        print(f"Forge installer exited with code {result}.", file=sys.stderr)
        return 1

    # Write server type metadata for deterministic detection
    from core.server_detection import write_server_type
    write_server_type(server_dir, "forge", installed_by="install_forge.py")

    from core.platform import forge_run_script_name
    run_script = server_dir / forge_run_script_name()
    if run_script.exists():
        print(f"Forge server installed successfully.")
        print(f"Run script: {run_script}")
    else:
        print("Warning: run script not found. The server may use an older Forge format.")
        # Older Forge versions create a forge-*-universal.jar or forge-*.jar instead
        forge_jars = list(server_dir.glob("forge-*.jar"))
        if forge_jars:
            print(f"Found: {forge_jars[0].name}")

    print("Place server-side mods in the server folder's 'mods/' subfolder.")
    return 0


def main():
    parser = argparse.ArgumentParser(description="Install Forge mod loader.")
    parser.add_argument("mc_version", help="Minecraft version, e.g. 1.20.1")
    parser.add_argument("--base-dir", default=None, help="Path to .minecraft (client install)")
    parser.add_argument("--servers-dir", default=None, help="Path to servers dir (server install)")
    parser.add_argument("--forge-version", default=None, help="Forge version (default: recommended or latest)")
    parser.add_argument("--server", action="store_true", help="Install Forge server (headless)")
    parser.add_argument("--instance", default=None, help="Server instance name (multi-world support)")
    args = parser.parse_args()

    if args.server:
        if not args.servers_dir:
            print("--servers-dir is required for server install", file=sys.stderr)
            return 1
        return install_server(args.mc_version, args.servers_dir, args.forge_version, args.instance)
    else:
        if not args.base_dir:
            print("--base-dir is required for client install", file=sys.stderr)
            return 1
        return install_client(args.mc_version, args.base_dir, args.forge_version)


if __name__ == "__main__":
    raise SystemExit(main())
