import argparse
import json
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
    find_version,
)


def main():
    parser = argparse.ArgumentParser(
        description="Download Minecraft dedicated server files for Windows users."
    )
    parser.add_argument("version_id", nargs="?", help="Version id, e.g. 1.21.11")
    parser.add_argument(
        "--servers-dir",
        default=".minecraft/servers",
        help="Root folder for server instances",
    )
    parser.add_argument(
        "--verify",
        action="store_true",
        help="Verify SHA1 for downloaded files",
    )
    parser.add_argument(
        "--include-mappings",
        action="store_true",
        help="Download server mappings if available",
    )
    parser.add_argument(
        "--instance",
        default=None,
        help="Server instance name (subfolder within version dir for multi-world support)",
    )
    args = parser.parse_args()

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
    server_info = version_data.get("downloads", {}).get("server")
    if not server_info:
        print(f"error: version {version_id} does not provide server.jar", file=sys.stderr)
        return 1

    server_dir = Path(args.servers_dir) / version_id
    if args.instance:
        server_dir = server_dir / args.instance
    server_dir.mkdir(parents=True, exist_ok=True)

    version_json_path = server_dir / f"{version_id}.json"
    version_json_path.write_text(
        json.dumps(version_data, ensure_ascii=True, indent=2),
        encoding="utf-8",
    )
    print(f"saved {version_json_path}")

    server_jar_path = server_dir / "server.jar"
    print("downloading server.jar...")
    download_file(
        session,
        server_info["url"],
        server_jar_path,
        expected_size=server_info.get("size"),
        expected_sha1=server_info.get("sha1"),
        verify_sha1=args.verify,
    )

    if args.include_mappings:
        mappings_info = version_data.get("downloads", {}).get("server_mappings")
        if mappings_info:
            mappings_path = server_dir / "server_mappings.txt"
            print("downloading server mappings...")
            download_file(
                session,
                mappings_info["url"],
                mappings_path,
                expected_size=mappings_info.get("size"),
                expected_sha1=mappings_info.get("sha1"),
                verify_sha1=args.verify,
            )

    print("done.")
    print(f"server_dir: {server_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
