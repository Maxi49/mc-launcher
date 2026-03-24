"""Install Fabric mod loader (client or server) for a given Minecraft version."""
import argparse
import json
import shutil
import sys
from pathlib import Path
from urllib.request import urlopen, Request
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from mc_common import fetch_json_url, download_url_file

FABRIC_META = "https://meta.fabricmc.net/v2"


def _maven_path(name):
    """Convert a Maven coordinate to a relative jar path."""
    parts = name.split(":")
    group, artifact, version = parts[0], parts[1], parts[2]
    classifier = parts[3] if len(parts) > 3 else None
    group_path = group.replace(".", "/")
    jar_name = f"{artifact}-{version}" + (f"-{classifier}" if classifier else "") + ".jar"
    return f"{group_path}/{artifact}/{version}/{jar_name}"


def _latest_loader(mc_version):
    data = fetch_json_url(f"{FABRIC_META}/versions/loader/{mc_version}")
    if not data:
        raise RuntimeError(f"No Fabric loader found for Minecraft {mc_version}")
    return data[0]["loader"]["version"]


def _latest_installer():
    data = fetch_json_url(f"{FABRIC_META}/versions/installer")
    if not data:
        raise RuntimeError("Could not fetch Fabric installer versions")
    return data[0]["version"]


def _install_fabric_api(mc_version, base_dir):
    """Download Fabric API from Modrinth for the given MC version."""
    mods_dir = Path(base_dir) / "mods"
    mods_dir.mkdir(parents=True, exist_ok=True)

    # Skip if any fabric-api jar already exists for this version
    for f in mods_dir.iterdir():
        if f.name.startswith("fabric-api") and mc_version in f.name and f.suffix == ".jar":
            print(f"Fabric API already present: {f.name}")
            return

    print(f"Downloading Fabric API for {mc_version} from Modrinth...")
    try:
        url = (
            f"https://api.modrinth.com/v2/project/fabric-api/version"
            f"?game_versions=[%22{quote(mc_version, safe='')}%22]"
            f"&loaders=[%22fabric%22]"
        )
        req = Request(url, headers={"User-Agent": "mc-launcher/1.0"})
        with urlopen(req, timeout=30) as resp:
            versions = json.loads(resp.read())
        if not versions:
            print(f"  No Fabric API version found for MC {mc_version}")
            return
        file_info = versions[0]["files"][0]
        filename = file_info["filename"]
        file_url = file_info["url"]
        dest = mods_dir / filename
        if dest.exists():
            print(f"  {filename} already exists.")
            return
        download_url_file(file_url, dest)
        print(f"  Saved: {filename}")
    except Exception as e:
        print(f"  Warning: could not download Fabric API: {e}")


def install_client(mc_version, base_dir, loader_version):
    base_dir = Path(base_dir)
    versions_dir = base_dir / "versions"
    libs_dir = base_dir / "libraries"

    url = f"{FABRIC_META}/versions/loader/{mc_version}/{loader_version}/profile/json"
    print("Fetching Fabric profile JSON...")
    profile = fetch_json_url(url)
    if not profile:
        print("Failed to fetch Fabric profile JSON.", file=sys.stderr)
        return 1

    fabric_id = profile["id"]
    print(f"Fabric version id: {fabric_id}")

    # Enrich libraries that use Maven URL format (no downloads.artifact).
    # This lets launch_client.py treat them like vanilla libraries.
    for lib in profile.get("libraries", []):
        if "downloads" not in lib and "url" in lib:
            rel = _maven_path(lib["name"])
            lib_url = lib["url"].rstrip("/") + "/" + rel
            lib["downloads"] = {"artifact": {"path": rel, "url": lib_url, "sha1": "", "size": 0}}

    # Save enriched version JSON
    version_dir = versions_dir / fabric_id
    version_dir.mkdir(parents=True, exist_ok=True)
    json_path = version_dir / f"{fabric_id}.json"
    json_path.write_text(json.dumps(profile, indent=2, ensure_ascii=True), encoding="utf-8")
    print(f"Saved {json_path.name}")

    # Copy client jar from parent vanilla version
    parent_id = profile.get("inheritsFrom", mc_version)
    parent_jar = versions_dir / parent_id / f"{parent_id}.jar"
    fabric_jar = version_dir / f"{fabric_id}.jar"
    if not fabric_jar.exists():
        if parent_jar.exists():
            shutil.copy2(str(parent_jar), str(fabric_jar))
            print(f"Copied client jar from {parent_id}")
        else:
            print(
                f"ERROR: Vanilla version {parent_id} is not installed. "
                "Download it first, then install Fabric.",
                file=sys.stderr,
            )
            return 1

    # Download Fabric libraries
    libs = profile.get("libraries", [])
    print(f"Downloading {len(libs)} Fabric libraries...")
    ok = 0
    for lib in libs:
        artifact = lib.get("downloads", {}).get("artifact", {})
        path = artifact.get("path")
        lib_url = artifact.get("url")
        if not path or not lib_url:
            continue
        dest = libs_dir / path
        if dest.exists():
            ok += 1
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            download_url_file(lib_url, dest)
            ok += 1
        except Exception as e:
            print(f"  Warning: {Path(path).name}: {e}")

    print(f"Libraries: {ok}/{len(libs)} ready")

    # Auto-download Fabric API
    _install_fabric_api(mc_version, base_dir)

    print(f"\nFabric installed: {fabric_id}")
    print("Select this version in the launcher to play with mods.")
    return 0


def install_server(mc_version, servers_dir, loader_version, installer_version, instance=None):
    server_dir = Path(servers_dir) / mc_version
    if instance:
        server_dir = server_dir / instance
    server_dir.mkdir(parents=True, exist_ok=True)

    url = (
        f"{FABRIC_META}/versions/loader/{mc_version}/{loader_version}"
        f"/{installer_version}/server/jar"
    )
    dest = server_dir / "fabric-server-launch.jar"
    print(f"Downloading Fabric server launcher ({mc_version}, loader {loader_version})...")
    download_url_file(url, dest)
    print(f"Saved: {dest}")
    print(
        "On first launch Fabric will download the vanilla server automatically.\n"
        "Place server-side mods in the server folder's 'mods/' subfolder."
    )
    return 0


def main():
    parser = argparse.ArgumentParser(description="Install Fabric mod loader.")
    parser.add_argument("mc_version", help="Minecraft version, e.g. 1.21.1")
    parser.add_argument("--base-dir", default=None, help="Path to .minecraft (client install)")
    parser.add_argument("--servers-dir", default=None, help="Path to servers dir (server install)")
    parser.add_argument("--loader-version", default=None, help="Fabric loader version (default: latest)")
    parser.add_argument("--installer-version", default=None, help="Fabric installer version (default: latest)")
    parser.add_argument("--server", action="store_true", help="Install Fabric server launcher")
    parser.add_argument("--instance", default=None, help="Server instance name (multi-world support)")
    args = parser.parse_args()

    print(f"Fetching latest Fabric loader for {args.mc_version}...")
    loader = args.loader_version or _latest_loader(args.mc_version)
    print(f"Loader: {loader}")

    if args.server:
        if not args.servers_dir:
            print("--servers-dir is required for server install", file=sys.stderr)
            return 1
        installer = args.installer_version or _latest_installer()
        print(f"Installer: {installer}")
        return install_server(args.mc_version, args.servers_dir, loader, installer, args.instance)
    else:
        if not args.base_dir:
            print("--base-dir is required for client install", file=sys.stderr)
            return 1
        return install_client(args.mc_version, args.base_dir, loader)


if __name__ == "__main__":
    raise SystemExit(main())
