"""Install shader mod (Iris/Sodium for Fabric, Oculus/Rubidium for Forge) from Modrinth."""
import argparse
import json
import sys
from pathlib import Path
from urllib.request import urlopen, Request
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from mc_common import download_url_file

MODRINTH_API = "https://api.modrinth.com/v2"

SHADER_MODS = {
    "fabric": [
        {"slug": "iris", "name": "Iris"},
        {"slug": "sodium", "name": "Sodium"},
    ],
    "forge": [
        {"slug": "oculus", "name": "Oculus"},
        {"slug": "rubidium", "name": "Rubidium"},
    ],
}


def _modrinth_fetch(url):
    """GET JSON from Modrinth API with User-Agent."""
    req = Request(url, headers={"User-Agent": "mc-launcher/1.0"})
    with urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


def _download_mod(slug, mc_version, loader, mods_dir):
    """Download the latest compatible version of a mod from Modrinth."""
    url = (
        f"{MODRINTH_API}/project/{quote(slug, safe='')}/version"
        f"?game_versions=[%22{quote(mc_version, safe='')}%22]"
        f"&loaders=[%22{quote(loader, safe='')}%22]"
    )
    versions = _modrinth_fetch(url)
    if not versions:
        print(f"  No compatible version found for {slug} "
              f"(MC {mc_version}, {loader})", file=sys.stderr)
        return False

    file_info = versions[0]["files"][0]
    filename = file_info["filename"]
    file_url = file_info["url"]
    dest = Path(mods_dir) / filename

    if dest.exists():
        print(f"  {filename} already exists, skipping.")
        return True

    print(f"  Downloading {filename}...")
    download_url_file(file_url, dest)
    print(f"  Saved: {dest}")
    return True


def install(mc_version, base_dir, loader):
    """Install shader mods for the given loader."""
    mods_dir = Path(base_dir) / "mods"
    mods_dir.mkdir(parents=True, exist_ok=True)

    if loader not in SHADER_MODS:
        print(f"Unsupported loader: {loader}. Use 'fabric' or 'forge'.",
              file=sys.stderr)
        return 1

    print(f"Installing shader mods for {loader} (MC {mc_version})...")
    ok = 0
    for mod in SHADER_MODS[loader]:
        print(f"[{mod['name']}]")
        if _download_mod(mod["slug"], mc_version, loader, mods_dir):
            ok += 1

    total = len(SHADER_MODS[loader])
    print(f"\n{ok}/{total} mods installed successfully.")
    if ok == total:
        print("Shader mod is ready. Download a shader pack and launch the game.")
        return 0
    else:
        print("Some mods failed to install.", file=sys.stderr)
        return 1


def main():
    parser = argparse.ArgumentParser(
        description="Install shader mod from Modrinth."
    )
    parser.add_argument("mc_version", help="Minecraft version, e.g. 1.21.1")
    parser.add_argument("--base-dir", required=True,
                        help="Path to .minecraft")
    parser.add_argument("--loader", required=True, choices=["fabric", "forge"],
                        help="Mod loader (fabric or forge)")
    args = parser.parse_args()
    return install(args.mc_version, args.base_dir, args.loader)


if __name__ == "__main__":
    raise SystemExit(main())
