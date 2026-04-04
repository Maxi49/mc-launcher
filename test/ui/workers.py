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
            url = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
            with urlopen(url, timeout=10, context=_SSL_CTX) as resp:
                data = json.loads(resp.read())
            tag = data.get("tag_name", "")
            latest = tag.lstrip("v")
            if self._newer(latest, __version__):
                if sys.platform == "win32":
                    asset_name = "launcher-windows.exe"
                elif sys.platform == "darwin":
                    asset_name = "launcher-macos"
                else:
                    asset_name = "launcher-linux"
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


CURSEFORGE_API = "https://api.curseforge.com"
CURSEFORGE_GAME_ID = 432       # Minecraft
CURSEFORGE_SHADER_CLASS = 6552  # Shaders


def _mc_version_variants(mc_version):
    """Return version strings to try: exact, then major.minor, then no filter.

    For '1.21.11' returns ['1.21.11', '1.21'].
    For '1.21' returns ['1.21'].
    """
    if not mc_version:
        return []
    parts = mc_version.split(".")
    variants = [mc_version]
    if len(parts) >= 3:
        variants.append(f"{parts[0]}.{parts[1]}")
    return variants


def _modrinth_search(query, mc_version_filter, limit=20):
    """Search Modrinth for shaders, optionally filtered by MC version."""
    from urllib.request import urlopen, Request
    from urllib.parse import quote

    if mc_version_filter:
        facets = (
            f'[["project_type:shader"],'
            f'["versions:{mc_version_filter}"]]'
        )
    else:
        facets = '[["project_type:shader"]]'

    url = (
        f"https://api.modrinth.com/v2/search"
        f"?facets={quote(facets, safe='')}"
        f"&query={quote(query, safe='')}"
        f"&limit={limit}"
    )
    req = Request(url, headers={"User-Agent": "mc-launcher/1.0"})
    with urlopen(req, timeout=30, context=_SSL_CTX) as resp:
        data = json.loads(resp.read())
    return data.get("hits", [])


def _curseforge_search(query, mc_version_filter, api_key, limit=20):
    """Search CurseForge for shaders, optionally filtered by MC version."""
    from urllib.request import urlopen, Request
    from urllib.parse import quote

    url = (
        f"{CURSEFORGE_API}/v1/mods/search"
        f"?gameId={CURSEFORGE_GAME_ID}"
        f"&classId={CURSEFORGE_SHADER_CLASS}"
        f"&sortField=2&sortOrder=desc"
        f"&pageSize={limit}"
    )
    if query:
        url += f"&searchFilter={quote(query, safe='')}"
    if mc_version_filter:
        url += f"&gameVersion={quote(mc_version_filter, safe='')}"

    req = Request(url, headers={
        "User-Agent": "mc-launcher/1.0",
        "x-api-key": api_key,
    })
    with urlopen(req, timeout=30, context=_SSL_CTX) as resp:
        data = json.loads(resp.read())
    return data.get("data", [])


class ModrinthShaderSearcher(QThread):
    """Search Modrinth and CurseForge for shader packs in background."""
    finished = Signal(list)
    error = Signal(str)

    def __init__(self, query="", mc_version="", curseforge_key="", parent=None):
        super().__init__(parent)
        self.query = query
        self.mc_version = mc_version
        self.curseforge_key = curseforge_key

    def run(self):
        hits = []
        matched_version = ""

        # ── Modrinth search ──
        try:
            variants = _mc_version_variants(self.mc_version)
            raw_hits = []
            for v in variants:
                raw_hits = _modrinth_search(self.query, v)
                if raw_hits:
                    matched_version = v
                    break
            if not raw_hits:
                raw_hits = _modrinth_search(self.query, "")

            for h in raw_hits:
                versions_list = h.get("display_categories", [])
                hits.append({
                    "title": h.get("title", ""),
                    "slug": h.get("slug", ""),
                    "author": h.get("author", ""),
                    "downloads": h.get("downloads", 0),
                    "icon_url": h.get("icon_url", ""),
                    "categories": versions_list,
                    "matched_version": matched_version,
                    "source": "Modrinth",
                })
        except Exception as exc:
            self.error.emit(f"Modrinth: {exc}")

        # ── CurseForge search ──
        if self.curseforge_key:
            try:
                cf_hits = []
                cf_matched = ""
                variants = _mc_version_variants(self.mc_version)
                for v in variants:
                    cf_hits = _curseforge_search(self.query, v, self.curseforge_key)
                    if cf_hits:
                        cf_matched = v
                        break
                if not cf_hits:
                    cf_hits = _curseforge_search(self.query, "", self.curseforge_key)

                # Deduplicate by title (prefer Modrinth if both have it)
                existing_titles = {h["title"].lower() for h in hits}
                for m in cf_hits:
                    title = m.get("name", "")
                    if title.lower() in existing_titles:
                        continue
                    authors = m.get("authors", [])
                    author = authors[0]["name"] if authors else ""
                    categories = [c["name"] for c in m.get("categories", [])]
                    hits.append({
                        "title": title,
                        "slug": str(m.get("id", "")),
                        "author": author,
                        "downloads": int(m.get("downloadCount", 0)),
                        "icon_url": m.get("logo", {}).get("url", "") if m.get("logo") else "",
                        "categories": categories,
                        "matched_version": cf_matched,
                        "source": "CurseForge",
                        "cf_mod_id": m.get("id"),
                    })
            except Exception as exc:
                self.error.emit(f"CurseForge: {exc}")

        # Sort all results by downloads descending
        hits.sort(key=lambda h: h["downloads"], reverse=True)
        self.finished.emit(hits)


class ShaderPackDownloader(QThread):
    """Download a shader pack .zip from Modrinth or CurseForge in background."""
    finished = Signal(bool, str)  # success, message

    def __init__(self, slug, mc_version, dest_dir, source="Modrinth",
                 curseforge_key="", cf_mod_id=None, parent=None):
        super().__init__(parent)
        self.slug = slug
        self.mc_version = mc_version
        self.dest_dir = dest_dir
        self.source = source
        self.curseforge_key = curseforge_key
        self.cf_mod_id = cf_mod_id

    def _fetch_modrinth_versions(self, mc_ver_filter):
        from urllib.request import urlopen, Request
        from urllib.parse import quote

        url = (
            f"https://api.modrinth.com/v2/project"
            f"/{quote(self.slug, safe='')}/version"
        )
        if mc_ver_filter:
            url += f"?game_versions=[%22{quote(mc_ver_filter, safe='')}%22]"
        req = Request(url, headers={"User-Agent": "mc-launcher/1.0"})
        with urlopen(req, timeout=30, context=_SSL_CTX) as resp:
            return json.loads(resp.read())

    def _fetch_curseforge_files(self, mc_ver_filter):
        from urllib.request import urlopen, Request

        url = f"{CURSEFORGE_API}/v1/mods/{self.cf_mod_id}/files?pageSize=10"
        if mc_ver_filter:
            url += f"&gameVersion={mc_ver_filter}"
        req = Request(url, headers={
            "User-Agent": "mc-launcher/1.0",
            "x-api-key": self.curseforge_key,
        })
        with urlopen(req, timeout=30, context=_SSL_CTX) as resp:
            data = json.loads(resp.read())
        return data.get("data", [])

    def _download_curseforge(self):
        variants = _mc_version_variants(self.mc_version)
        files = []
        used_version = ""
        for v in variants:
            files = self._fetch_curseforge_files(v)
            if files:
                used_version = v
                break
        if not files:
            files = self._fetch_curseforge_files("")
            used_version = "any"

        if not files:
            self.finished.emit(False, f"No files found for CurseForge mod {self.cf_mod_id}")
            return

        cf_file = files[0]
        filename = cf_file["fileName"]
        download_url = cf_file.get("downloadUrl")
        game_versions = cf_file.get("gameVersions", [])
        dest = Path(self.dest_dir) / filename

        if dest.exists():
            self.finished.emit(True, f"{filename} already exists.")
            return

        if not download_url:
            # Some mods don't allow direct download via API
            self.finished.emit(False,
                f"{filename}: author disabled API downloads. "
                f"Download manually from CurseForge.")
            return

        dest.parent.mkdir(parents=True, exist_ok=True)
        from mc_common import download_url_file
        download_url_file(download_url, dest)

        ver_note = ""
        if used_version and used_version != self.mc_version and used_version != "any":
            ver_note = f" (matched {used_version})"
        elif used_version == "any":
            ver_note = f" (latest: {', '.join(game_versions[:5])})"
        self.finished.emit(True, f"Downloaded {filename}{ver_note}")

    def _download_modrinth(self):
        variants = _mc_version_variants(self.mc_version)
        versions = []
        used_version = ""
        for v in variants:
            versions = self._fetch_modrinth_versions(v)
            if versions:
                used_version = v
                break
        if not versions:
            versions = self._fetch_modrinth_versions("")
            used_version = "any"

        if not versions:
            self.finished.emit(False, f"No versions found for {self.slug}")
            return

        version_entry = versions[0]
        game_versions = version_entry.get("game_versions", [])
        file_info = version_entry["files"][0]
        filename = file_info["filename"]
        file_url = file_info["url"]
        dest = Path(self.dest_dir) / filename

        if dest.exists():
            self.finished.emit(True, f"{filename} already exists.")
            return

        dest.parent.mkdir(parents=True, exist_ok=True)
        from mc_common import download_url_file
        download_url_file(file_url, dest)

        ver_note = ""
        if used_version and used_version != self.mc_version and used_version != "any":
            ver_note = f" (matched {used_version}, supports: {', '.join(game_versions[:5])})"
        elif used_version == "any":
            ver_note = f" (no exact match, got latest: {', '.join(game_versions[:5])})"
        self.finished.emit(True, f"Downloaded {filename}{ver_note}")

    def run(self):
        try:
            if self.source == "CurseForge":
                self._download_curseforge()
            else:
                self._download_modrinth()
        except Exception as exc:
            self.finished.emit(False, str(exc))
