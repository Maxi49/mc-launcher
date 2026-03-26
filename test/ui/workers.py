"""Background QThread workers for network tasks."""

import json
import sys
from pathlib import Path

from PySide6.QtCore import QThread, Signal

from mc_common import check_username_taken, _SSL_CTX

try:
    import requests
except ImportError:
    requests = None

# These are set by the main module at import time
MANIFEST_URL = "https://launchermeta.mojang.com/mc/game/version_manifest.json"
MANIFEST_CACHE = Path(__file__).resolve().parent.parent / "version_manifest_cache.json"
GITHUB_REPO = "Maxi49/mc-launcher"

try:
    from version import __version__
except ImportError:
    __version__ = "0.0.0"


class ManifestFetcher(QThread):
    """Fetch version manifest from Mojang in a background thread."""

    finished = Signal(list)

    def run(self):
        versions = []
        try:
            if requests is not None:
                resp = requests.get(MANIFEST_URL, timeout=(10, 30))
                resp.raise_for_status()
                manifest = resp.json()
            else:
                from urllib.request import urlopen
                with urlopen(MANIFEST_URL, timeout=30, context=_SSL_CTX) as fh:
                    manifest = json.loads(fh.read())
            # Cache locally
            try:
                MANIFEST_CACHE.write_text(
                    json.dumps(manifest, ensure_ascii=True, indent=2),
                    encoding="utf-8",
                )
            except OSError:
                pass
            versions = manifest.get("versions", [])
        except Exception:
            # Try cached version
            try:
                if MANIFEST_CACHE.exists():
                    manifest = json.loads(
                        MANIFEST_CACHE.read_text(encoding="utf-8")
                    )
                    versions = manifest.get("versions", [])
            except (OSError, json.JSONDecodeError):
                pass
        self.finished.emit(versions)


class UsernameChecker(QThread):
    """Check if a username is taken by a premium Minecraft account."""

    finished = Signal(dict)

    def __init__(self, username, parent=None):
        super().__init__(parent)
        self.username = username

    def run(self):
        result = check_username_taken(self.username)
        self.finished.emit(result)


class UpdateChecker(QThread):
    """Check GitHub releases for a newer version in the background."""

    update_available = Signal(str, str)  # tag, download_url

    def run(self):
        try:
            from urllib.request import urlopen
            import json as _json
            url = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
            with urlopen(url, timeout=10, context=_SSL_CTX) as resp:
                data = _json.loads(resp.read())
            tag = data.get("tag_name", "")
            latest = tag.lstrip("v")
            if self._newer(latest, __version__):
                asset_name = (
                    "launcher-windows.exe" if sys.platform == "win32"
                    else "launcher-macos"
                )
                for asset in data.get("assets", []):
                    if asset["name"] == asset_name:
                        self.update_available.emit(tag, asset["browser_download_url"])
                        return
        except Exception:
            pass

    @staticmethod
    def _newer(latest, current):
        def parse(v):
            try:
                return tuple(int(x) for x in v.strip().split("."))
            except Exception:
                return (0,)
        return parse(latest) > parse(current)


class ModrinthShaderSearcher(QThread):
    """Search Modrinth for shader packs in background."""
    finished = Signal(list)
    error = Signal(str)

    def __init__(self, query="", mc_version="", parent=None):
        super().__init__(parent)
        self.query = query
        self.mc_version = mc_version

    def run(self):
        try:
            from urllib.request import urlopen, Request
            from urllib.parse import quote
            import json as _json

            facets = '[["project_type:shader"]]'
            if self.mc_version:
                facets = (
                    f'[["project_type:shader"],'
                    f'["versions:{self.mc_version}"]]'
                )
            url = (
                f"https://api.modrinth.com/v2/search"
                f"?facets={quote(facets, safe='')}"
                f"&query={quote(self.query, safe='')}"
                f"&limit=20"
            )
            req = Request(url, headers={"User-Agent": "mc-launcher/1.0"})
            with urlopen(req, timeout=30, context=_SSL_CTX) as resp:
                data = _json.loads(resp.read())

            hits = []
            for h in data.get("hits", []):
                hits.append({
                    "title": h.get("title", ""),
                    "slug": h.get("slug", ""),
                    "author": h.get("author", ""),
                    "downloads": h.get("downloads", 0),
                    "icon_url": h.get("icon_url", ""),
                })
            self.finished.emit(hits)
        except Exception as exc:
            self.error.emit(str(exc))
            self.finished.emit([])


class ShaderPackDownloader(QThread):
    """Download a shader pack .zip from Modrinth in background."""
    finished = Signal(bool, str)  # success, message

    def __init__(self, slug, mc_version, dest_dir, parent=None):
        super().__init__(parent)
        self.slug = slug
        self.mc_version = mc_version
        self.dest_dir = dest_dir

    def run(self):
        try:
            from urllib.request import urlopen, Request
            from urllib.parse import quote
            import json as _json

            url = (
                f"https://api.modrinth.com/v2/project"
                f"/{quote(self.slug, safe='')}/version"
            )
            if self.mc_version:
                url += f"?game_versions=[%22{quote(self.mc_version, safe='')}%22]"
            req = Request(url, headers={"User-Agent": "mc-launcher/1.0"})
            with urlopen(req, timeout=30, context=_SSL_CTX) as resp:
                versions = _json.loads(resp.read())

            if not versions:
                self.finished.emit(False, f"No versions found for {self.slug}")
                return

            file_info = versions[0]["files"][0]
            filename = file_info["filename"]
            file_url = file_info["url"]
            dest = Path(self.dest_dir) / filename

            if dest.exists():
                self.finished.emit(True, f"{filename} already exists.")
                return

            dest.parent.mkdir(parents=True, exist_ok=True)
            from mc_common import download_url_file
            download_url_file(file_url, dest)
            self.finished.emit(True, f"Downloaded {filename}")
        except Exception as exc:
            self.finished.emit(False, str(exc))
