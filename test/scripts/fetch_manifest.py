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
from mc_common import MANIFEST_URL, REQUEST_TIMEOUT


def main():
    parser = argparse.ArgumentParser(
        description="Fetch Minecraft version_manifest.json from Mojang."
    )
    parser.add_argument(
        "--out",
        default="version_manifest.json",
        help="Output path (default: version_manifest.json)",
    )
    args = parser.parse_args()

    print(f"fetching {MANIFEST_URL} ...")
    resp = requests.get(MANIFEST_URL, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    manifest = resp.json()

    out_path = Path(args.out)
    out_path.write_text(json.dumps(manifest, ensure_ascii=True, indent=2), encoding="utf-8")
    print(f"saved: {out_path}")
    latest = manifest.get("latest", {})
    print(f"latest release: {latest.get('release')}")
    print(f"latest snapshot: {latest.get('snapshot')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
