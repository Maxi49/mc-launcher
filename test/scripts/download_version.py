import argparse
import json
import platform
import sys
from pathlib import Path

try:
    import requests
except ImportError:
    print("requests is required. Install with: pip install requests", file=sys.stderr)
    raise SystemExit(1)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from mc_common import (
    MANIFEST_URL,
    fetch_json,
    download_file,
    detect_arch,
    allowed_by_rules,
    find_version,
    current_os_name,
)

ASSET_BASE_URL = "https://resources.download.minecraft.net"


def main():
    parser = argparse.ArgumentParser(
        description="Download a Minecraft version."
    )
    parser.add_argument("version_id", nargs="?", help="Version id, e.g. 1.21.11")
    parser.add_argument("--base-dir", default=".minecraft", help="Base output dir")
    parser.add_argument("--no-assets", action="store_true", help="Skip assets download")
    parser.add_argument(
        "--include-server", action="store_true", help="Also download server.jar"
    )
    parser.add_argument(
        "--include-mappings",
        action="store_true",
        help="Also download client/server mappings",
    )
    parser.add_argument(
        "--verify",
        action="store_true",
        help="Verify sha1 for downloaded files (slow for assets)",
    )
    args = parser.parse_args()

    os_name = current_os_name()
    os_arch, arch_bits = detect_arch()
    os_version = platform.version()
    features = {}

    session = requests.Session()

    print("fetching version manifest...")
    manifest = fetch_json(session, MANIFEST_URL)

    version_id = args.version_id or manifest.get("latest", {}).get("release")
    if not version_id:
        print("error: could not resolve version id", file=sys.stderr)
        return 1

    version_entry = find_version(manifest, version_id)
    if not version_entry:
        print(f"error: version not found: {version_id}", file=sys.stderr)
        return 1

    print(f"fetching version json for {version_id}...")
    version_data = fetch_json(session, version_entry["url"])

    base_dir = Path(args.base_dir)
    version_dir = base_dir / "versions" / version_id
    version_dir.mkdir(parents=True, exist_ok=True)

    version_json_path = version_dir / f"{version_id}.json"
    with open(version_json_path, "w", encoding="utf-8") as fh:
        json.dump(version_data, fh, ensure_ascii=True, indent=2)
    print(f"saved {version_json_path}")

    downloads = version_data.get("downloads", {})
    client = downloads.get("client")
    if not client:
        print("error: missing client download info", file=sys.stderr)
        return 1

    print("downloading client.jar...")
    client_path = version_dir / f"{version_id}.jar"
    download_file(
        session,
        client["url"],
        client_path,
        expected_size=client.get("size"),
        expected_sha1=client.get("sha1"),
        verify_sha1=args.verify,
    )

    if args.include_server and downloads.get("server"):
        print("downloading server.jar...")
        server = downloads["server"]
        server_path = version_dir / "server.jar"
        download_file(
            session,
            server["url"],
            server_path,
            expected_size=server.get("size"),
            expected_sha1=server.get("sha1"),
            verify_sha1=args.verify,
        )

    if args.include_mappings:
        for key in ("client_mappings", "server_mappings"):
            if key in downloads:
                info = downloads[key]
                out_path = version_dir / f"{key}.txt"
                print(f"downloading {key}...")
                download_file(
                    session,
                    info["url"],
                    out_path,
                    expected_size=info.get("size"),
                    expected_sha1=info.get("sha1"),
                    verify_sha1=args.verify,
                )

    print(f"downloading libraries ({os_name} rules)...")
    libs = version_data.get("libraries", [])
    libs_dir = base_dir / "libraries"
    downloaded = 0
    skipped = 0
    for lib in libs:
        if not allowed_by_rules(
            lib.get("rules", []), os_name, os_arch, os_version, features
        ):
            continue
        downloads_info = lib.get("downloads", {})

        artifact = downloads_info.get("artifact")
        if artifact:
            dest = libs_dir / artifact["path"]
            ok = download_file(
                session,
                artifact["url"],
                dest,
                expected_size=artifact.get("size"),
                expected_sha1=artifact.get("sha1"),
                verify_sha1=args.verify,
            )
            if ok:
                downloaded += 1
            else:
                skipped += 1

        natives = lib.get("natives", {})
        classifiers = downloads_info.get("classifiers", {})
        native_key = natives.get(os_name)
        if native_key:
            native_key = native_key.replace("${arch}", "64" if arch_bits == 64 else "32")
            native_info = classifiers.get(native_key)
            if native_info:
                dest = libs_dir / native_info["path"]
                ok = download_file(
                    session,
                    native_info["url"],
                    dest,
                    expected_size=native_info.get("size"),
                    expected_sha1=native_info.get("sha1"),
                    verify_sha1=args.verify,
                )
                if ok:
                    downloaded += 1
                else:
                    skipped += 1

    print(f"libraries done. downloaded={downloaded} skipped={skipped}")

    asset_index_info = version_data.get("assetIndex", {})
    if asset_index_info and not args.no_assets:
        print("downloading asset index...")
        asset_index = fetch_json(session, asset_index_info["url"])
        indexes_dir = base_dir / "assets" / "indexes"
        indexes_dir.mkdir(parents=True, exist_ok=True)
        asset_index_path = indexes_dir / f"{asset_index_info['id']}.json"
        with open(asset_index_path, "w", encoding="utf-8") as fh:
            json.dump(asset_index, fh, ensure_ascii=True, indent=2)
        print(f"saved {asset_index_path}")

        objects = asset_index.get("objects", {})
        total_assets = len(objects)
        print(f"downloading assets ({total_assets} files)...")
        assets_dir = base_dir / "assets" / "objects"
        for i, (_, info) in enumerate(objects.items(), 1):
            hash_val = info["hash"]
            size = info.get("size")
            url = f"{ASSET_BASE_URL}/{hash_val[:2]}/{hash_val}"
            dest = assets_dir / hash_val[:2] / hash_val
            download_file(
                session,
                url,
                dest,
                expected_size=size,
                expected_sha1=hash_val if args.verify else None,
                verify_sha1=args.verify,
            )
            if i % 200 == 0 or i == total_assets:
                print(f"  assets progress: {i}/{total_assets}")
    else:
        print("assets skipped")

    print("done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
