# ── Frozen script dispatcher ───────────────────────────────────
# When the app runs as a PyInstaller binary, it also acts as the
# Python interpreter for the CLI scripts (called with --script <name>).
import sys as _sys
if getattr(_sys, 'frozen', False) and len(_sys.argv) > 1 and _sys.argv[1] == '--script':
    def _dispatch():
        import runpy
        from pathlib import Path as _Path
        script_name = _sys.argv[2]
        _sys.argv = [_sys.argv[0]] + _sys.argv[3:]
        base = _Path(getattr(_sys, '_MEIPASS', _Path(__file__).resolve().parent))
        _sys.path.insert(0, str(base))
        runpy.run_path(str(base / 'scripts' / f'{script_name}.py'), run_name='__main__')
    _dispatch()
    _sys.exit(0)
# ──────────────────────────────────────────────────────────────

import json
import os
import subprocess
import sys
from pathlib import Path

from PySide6.QtCore import Qt, QProcess, QProcessEnvironment, QThread, Signal, QPropertyAnimation, QEasingCurve, QTimer
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGraphicsOpacityEffect,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QTabWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

try:
    import requests
except ImportError:
    requests = None

from mc_common import format_cmd, check_username_taken, default_minecraft_dir

try:
    from version import __version__
except ImportError:
    __version__ = "0.0.0"

GITHUB_REPO = "Maxi49/mc-launcher"
SETTINGS_FILE = Path(__file__).with_name("launcher_settings.json")
MANIFEST_CACHE = Path(__file__).with_name("version_manifest_cache.json")
MANIFEST_URL = "https://launchermeta.mojang.com/mc/game/version_manifest.json"


def default_base_dir():
    return default_minecraft_dir()


def load_settings():
    if SETTINGS_FILE.exists():
        try:
            return json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
    return {}


def save_settings(data):
    SETTINGS_FILE.write_text(
        json.dumps(data, ensure_ascii=True, indent=2), encoding="utf-8"
    )


def list_installed_versions(base_dir):
    versions_dir = Path(base_dir) / "versions"
    if not versions_dir.exists():
        return []
    versions = []
    for entry in versions_dir.iterdir():
        if entry.is_dir() and (entry / f"{entry.name}.json").exists() and (entry / f"{entry.name}.jar").exists():
            versions.append(entry.name)
    return sorted(versions)


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
                with urlopen(MANIFEST_URL, timeout=30) as fh:
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
            with urlopen(url, timeout=10) as resp:
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


def _resolve_mc_version(version_name):
    """Extract the base Minecraft version from a loader version string.

    Examples:
        fabric-loader-0.16.5-1.21.1  -> 1.21.1
        1.20.1-forge-47.3.0          -> 1.20.1
        1.21.1                       -> 1.21.1
    """
    import re
    # Fabric: MC version is last segment  e.g. fabric-loader-0.16.5-1.21.1
    if "fabric" in version_name.lower():
        matches = re.findall(r'(\d+\.\d+(?:\.\d+)?)', version_name)
        return matches[-1] if matches else version_name
    # Forge / vanilla: MC version is first segment  e.g. 1.20.1-forge-47.3.0
    m = re.search(r'(\d+\.\d+(?:\.\d+)?)', version_name)
    return m.group(1) if m else version_name


def _detect_version_loader(version_name):
    """Detect the mod loader from a version directory name.

    Returns 'fabric', 'forge', or 'vanilla'.
    """
    lower = version_name.lower()
    if "fabric" in lower:
        return "fabric"
    if "forge" in lower:
        return "forge"
    return "vanilla"


def _extract_mod_mc_version(filename):
    """Extract MC version from mod filename.

    Patterns:
      mod-name+mc1.21.1.jar   -> 1.21.1
      mod-name+1.21.1.jar     -> 1.21.1
      mod-name-mc1.21.1.jar   -> 1.21.1
      mod-name_MC_1.21.1.jar  -> 1.21.1
      mod-name-1.21.1.jar     -> 1.21.1  (last mc-like version)
    Returns None if no version detected.
    """
    import re
    stem = Path(filename).stem
    m = re.search(r'\+(?:mc)?(\d+\.\d+(?:\.\d+)?)', stem, re.IGNORECASE)
    if m:
        return m.group(1)
    m = re.search(r'[-_]mc[_.]?(\d+\.\d+(?:\.\d+)?)', stem, re.IGNORECASE)
    if m:
        return m.group(1)
    matches = re.findall(r'(\d+\.\d+(?:\.\d+)?)', stem)
    return matches[-1] if matches else None


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
            with urlopen(req, timeout=30) as resp:
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
            with urlopen(req, timeout=30) as resp:
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


class LauncherWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Minecraft Launcher")
        self.resize(680, 750)

        self.script_dir = Path(__file__).resolve().parent
        self.downloader_path = self.script_dir / "scripts" / "download_version.py"
        self.launcher_path = self.script_dir / "scripts" / "launch_client.py"
        self.server_downloader_path = self.script_dir / "scripts" / "download_server.py"
        self.server_launcher_path = self.script_dir / "scripts" / "launch_server.py"
        self.fabric_installer_path = self.script_dir / "scripts" / "install_fabric.py"
        self.forge_installer_path = self.script_dir / "scripts" / "install_forge.py"
        self.shader_mod_installer_path = self.script_dir / "scripts" / "install_shader_mod.py"

        self.all_manifest_versions = []
        self.manifest_fetcher = None
        self._update_checker = None
        self._shader_searcher = None
        self._shader_downloader = None
        self._update_url = None
        self._username_checker = None
        self._username_check_timer = QTimer(self)
        self._username_check_timer.setSingleShot(True)
        self._username_check_timer.setInterval(600)
        self._username_check_timer.timeout.connect(self._do_username_check)
        self._last_checked_username = ""
        self._username_is_taken = None  # None=unknown, True=taken, False=available

        self.main_process = QProcess(self)
        self.main_process.setProcessChannelMode(QProcess.MergedChannels)
        self.main_process.readyReadStandardOutput.connect(self.on_main_process_output)
        self.main_process.readyReadStandardError.connect(self.on_main_process_output)
        self.main_process.finished.connect(self.on_main_process_finished)

        self.server_process = QProcess(self)
        self.server_process.setProcessChannelMode(QProcess.MergedChannels)
        self.server_process.readyReadStandardOutput.connect(self.on_server_process_output)
        self.server_process.readyReadStandardError.connect(self.on_server_process_output)
        self.server_process.finished.connect(self.on_server_process_finished)

        self._build_ui()
        self._load_settings()
        self.refresh_versions()
        self._fetch_manifest()
        self._start_update_checker()

    # ── UI ──────────────────────────────────────────────────────

    def _build_ui(self):
        central = QWidget()
        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(20, 20, 20, 20)
        main_layout.setSpacing(14)

        # ── Fixed zone (always visible) ────────────────────────

        # Header
        header = QLabel("Minecraft Launcher")
        header.setStyleSheet("font-size: 22px; font-weight: 700; color: #58a6ff;")
        header.setAlignment(Qt.AlignCenter)
        main_layout.addWidget(header)

        # Username + RAM in one row
        top_row = QHBoxLayout()
        top_row.addWidget(QLabel("Username:"))
        self.username_edit = QLineEdit()
        self.username_edit.setPlaceholderText("Player")
        self.username_edit.setMinimumWidth(100)
        self.username_edit.textChanged.connect(self._on_username_changed)
        top_row.addWidget(self.username_edit, 1)
        self.username_status_label = QLabel("")
        self.username_status_label.setFixedWidth(24)
        self.username_status_label.setAlignment(Qt.AlignCenter)
        top_row.addWidget(self.username_status_label)
        top_row.addSpacing(12)
        top_row.addWidget(QLabel("RAM:"))
        self.ram_combo = QComboBox()
        self.ram_combo.addItems(["2G", "4G", "6G", "8G", "12G", "16G"])
        self.ram_combo.setCurrentIndex(1)
        self.ram_combo.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.ram_combo.setMinimumWidth(70)
        top_row.addWidget(self.ram_combo)
        main_layout.addLayout(top_row)

        # Installed versions (play row)
        play_row = QHBoxLayout()
        play_row.addWidget(QLabel("Version:"))
        self.installed_combo = QComboBox()
        self.installed_combo.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        play_row.addWidget(self.installed_combo, 1)
        main_layout.addLayout(play_row)

        # Play button
        self.launch_button = QPushButton("PLAY")
        self.launch_button.setMinimumHeight(52)
        self.launch_button.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.launch_button.setStyleSheet(
            """
            QPushButton {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 #2ea043, stop:1 #238636);
                border: 1px solid #2ea043;
                font-size: 18px;
                font-weight: 700;
                border-radius: 10px;
                padding: 10px 20px;
                color: #ffffff;
            }
            QPushButton:hover {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 #3fb950, stop:1 #2ea043);
                border: 1px solid #3fb950;
            }
            QPushButton:disabled {
                background: #1a3a1a;
                color: #5a7a5a;
                border: 1px solid #253525;
            }
            """
        )
        self.launch_button.clicked.connect(self.on_launch_clicked)
        self._play_opacity = QGraphicsOpacityEffect(self.launch_button)
        self._play_opacity.setOpacity(1.0)
        self.launch_button.setGraphicsEffect(self._play_opacity)
        main_layout.addWidget(self.launch_button)

        # ── Tab widget ─────────────────────────────────────────

        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_home_tab(), "Home")
        self.tabs.addTab(self._build_mods_tab(), "Mods")
        self.tabs.addTab(self._build_shaders_tab(), "Shaders")
        self.tabs.addTab(self._build_server_tab(), "Server")
        self.tabs.addTab(self._build_settings_tab(), "Settings")
        main_layout.addWidget(self.tabs, 1)

        self.setCentralWidget(central)

        self.status_label = QLabel("Ready.")
        self.statusBar().addWidget(self.status_label, 1)

        self.setStyleSheet(
            """
            QWidget {
                background-color: #1a1d23;
                color: #e6edf3;
            }
            QScrollArea {
                background-color: #1a1d23;
            }
            QTabWidget::pane {
                border: 1px solid #2d3548;
                border-radius: 6px;
                background: #1a1d23;
                top: -1px;
            }
            QTabBar::tab {
                background: #1e2330;
                border: 1px solid #2d3548;
                padding: 8px 16px;
                margin-right: 2px;
                border-top-left-radius: 6px;
                border-top-right-radius: 6px;
            }
            QTabBar::tab:selected {
                background: #1a1d23;
                border-bottom: none;
                color: #58a6ff;
            }
            QTabBar::tab:hover {
                background: #252d3b;
            }
            QLineEdit, QPlainTextEdit, QListWidget, QSpinBox, QComboBox {
                background-color: #1e2330;
                border: 1px solid #2d3548;
                padding: 6px;
                border-radius: 6px;
            }
            QLineEdit:focus, QPlainTextEdit:focus, QSpinBox:focus, QComboBox:focus {
                border: 1px solid #58a6ff;
            }
            QComboBox::drop-down {
                border: none;
                padding-right: 8px;
            }
            QComboBox QAbstractItemView {
                background-color: #1e2330;
                border: 1px solid #2d3548;
                selection-background-color: #2a3a4a;
            }
            QPushButton, QToolButton {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 #2c3444, stop:1 #252d3b);
                border: 1px solid #3a4250;
                padding: 6px 10px;
                border-radius: 6px;
            }
            QPushButton:hover, QToolButton:hover {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 #374050, stop:1 #2c3444);
                border: 1px solid #58a6ff;
            }
            QPushButton:disabled {
                color: #7f8792;
                background-color: #242a33;
                border: 1px solid #2d3548;
            }
            QPushButton#serverButton {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 #253350, stop:1 #1e2a42);
                border: 1px solid #2d4a6f;
            }
            QPushButton#serverButton:hover {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 #2e3d5e, stop:1 #253350);
                border: 1px solid #58a6ff;
            }
            QGroupBox {
                border: 1px solid #2d3548;
                border-radius: 8px;
                margin-top: 6px;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 8px;
                padding: 0 4px;
                color: #58a6ff;
            }
            QCheckBox { spacing: 6px; }
            QScrollBar:vertical {
                background: #1a1d23;
                width: 10px;
                border-radius: 5px;
            }
            QScrollBar::handle:vertical {
                background: #2d3548;
                border-radius: 5px;
                min-height: 30px;
            }
            QScrollBar::handle:vertical:hover {
                background: #3a4560;
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
            """
        )

    # ── Tab builders ───────────────────────────────────────────

    def _build_home_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        # Download row
        dl_row = QHBoxLayout()
        dl_row.addWidget(QLabel("Download:"))
        self.version_combo = QComboBox()
        self.version_combo.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.version_combo.addItem("Loading...")
        self.version_combo.setEnabled(False)
        dl_row.addWidget(self.version_combo, 1)
        self.download_button = QPushButton("Download")
        self.download_button.clicked.connect(self.on_download_clicked)
        dl_row.addWidget(self.download_button)
        self.install_fabric_button = QPushButton("Install Fabric")
        self.install_fabric_button.setToolTip(
            "Install the Fabric mod loader for the selected version.\n"
            "Download the vanilla version first, then click this."
        )
        self.install_fabric_button.clicked.connect(self.on_install_fabric_clicked)
        dl_row.addWidget(self.install_fabric_button)
        self.install_forge_button = QPushButton("Install Forge")
        self.install_forge_button.setToolTip(
            "Install the Forge mod loader for the selected version.\n"
            "Download the vanilla version first, then click this.\n"
            "The Forge installer GUI will open — click 'Install Client'."
        )
        self.install_forge_button.clicked.connect(self.on_install_forge_clicked)
        dl_row.addWidget(self.install_forge_button)
        layout.addLayout(dl_row)

        # Log output (always visible)
        log_label = QLabel("Logs")
        log_label.setStyleSheet("color: #58a6ff; font-weight: 600;")
        layout.addWidget(log_label)

        self.log_output = QPlainTextEdit()
        self.log_output.setReadOnly(True)
        self.log_output.setMaximumBlockCount(2000)
        self.log_output.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.log_output.setMinimumHeight(80)
        layout.addWidget(self.log_output, 1)

        return tab

    def _build_mods_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        # Filter row
        filter_row = QHBoxLayout()
        self.mods_show_all_check = QCheckBox("Show all versions")
        self.mods_show_all_check.toggled.connect(self._refresh_mods)
        filter_row.addWidget(self.mods_show_all_check)
        filter_row.addStretch()
        layout.addLayout(filter_row)

        # Mods list
        self.mods_list = QListWidget()
        self.mods_list.setMinimumHeight(100)
        self.mods_list.itemChanged.connect(self._on_mod_toggled)
        layout.addWidget(self.mods_list, 1)

        # Buttons
        mods_buttons = QHBoxLayout()
        refresh_mods_btn = QPushButton("Refresh")
        refresh_mods_btn.clicked.connect(self._refresh_mods)
        open_mods_btn = QPushButton("Open mods folder")
        open_mods_btn.clicked.connect(self._open_mods_folder)
        mods_buttons.addWidget(refresh_mods_btn)
        mods_buttons.addWidget(open_mods_btn)
        layout.addLayout(mods_buttons)

        # Refresh mods when selected version changes
        self.installed_combo.currentIndexChanged.connect(self._refresh_mods)

        return tab

    def _build_shaders_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        # Install buttons row
        shader_install_row = QHBoxLayout()
        self.install_shader_mod_btn = QPushButton("Install Shader Mod")
        self.install_shader_mod_btn.setToolTip(
            "Auto-install the shader mod for the selected version's loader.\n"
            "Fabric: Iris + Sodium | Forge: Oculus + Rubidium"
        )
        self.install_shader_mod_btn.clicked.connect(self._on_install_shader_mod)
        self.optifine_guide_btn = QPushButton("OptiFine Guide")
        self.optifine_guide_btn.clicked.connect(self._show_optifine_guide)
        shader_install_row.addWidget(self.install_shader_mod_btn)
        shader_install_row.addWidget(self.optifine_guide_btn)
        layout.addLayout(shader_install_row)

        # Installed shader packs
        sp_label = QLabel("Installed Shader Packs")
        sp_label.setStyleSheet("color: #58a6ff; font-weight: 600;")
        layout.addWidget(sp_label)

        self.shaderpacks_list = QListWidget()
        self.shaderpacks_list.setMinimumHeight(100)
        self.shaderpacks_list.itemChanged.connect(self._on_shaderpack_toggled)
        layout.addWidget(self.shaderpacks_list)

        sp_buttons = QHBoxLayout()
        refresh_sp_btn = QPushButton("Refresh")
        refresh_sp_btn.clicked.connect(self._refresh_shaderpacks)
        open_sp_btn = QPushButton("Open shaderpacks folder")
        open_sp_btn.clicked.connect(self._open_shaderpacks_folder)
        sp_buttons.addWidget(refresh_sp_btn)
        sp_buttons.addWidget(open_sp_btn)
        layout.addLayout(sp_buttons)

        # Browse Modrinth shader packs
        browse_label = QLabel("Browse Shader Packs (Modrinth)")
        browse_label.setStyleSheet("color: #58a6ff; font-weight: 600;")
        layout.addWidget(browse_label)

        search_row = QHBoxLayout()
        search_row.addWidget(QLabel("Search:"))
        self.shader_search_edit = QLineEdit()
        self.shader_search_edit.setPlaceholderText("e.g. Complementary, BSL...")
        self.shader_search_edit.returnPressed.connect(self._search_shaderpacks)
        search_row.addWidget(self.shader_search_edit, 1)
        self.shader_search_btn = QPushButton("Search")
        self.shader_search_btn.clicked.connect(self._search_shaderpacks)
        search_row.addWidget(self.shader_search_btn)
        layout.addLayout(search_row)

        self.shader_results_list = QListWidget()
        self.shader_results_list.setMinimumHeight(150)
        layout.addWidget(self.shader_results_list, 1)

        return tab

    def _build_server_tab(self):
        tab = QWidget()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)

        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        # Server buttons
        server_row = QHBoxLayout()
        self.download_server_button = QPushButton("Download Server")
        self.download_server_button.setObjectName("serverButton")
        self.download_server_button.clicked.connect(self.on_download_server_clicked)
        self.launch_server_button = QPushButton("Launch Server")
        self.launch_server_button.setObjectName("serverButton")
        self.launch_server_button.clicked.connect(self.on_launch_server_clicked)
        server_row.addWidget(self.download_server_button)
        server_row.addWidget(self.launch_server_button)
        layout.addLayout(server_row)

        # Server options
        server_group = QGroupBox("Server options")
        srv_form = QFormLayout(server_group)
        srv_form.setLabelAlignment(Qt.AlignLeft)
        self.server_accept_eula_check = QCheckBox("Auto accept EULA")
        self.server_gui_check = QCheckBox("Enable server GUI")
        self.server_restart_check = QCheckBox("Restart if running")
        self.server_offline_check = QCheckBox("Offline mode (online-mode=false)")
        self.server_fabric_check = QCheckBox("Fabric server (downloads Fabric launcher)")
        self.server_forge_check = QCheckBox("Forge server (runs Forge installer --installServer)")
        self.server_fabric_check.toggled.connect(
            lambda checked: self.server_forge_check.setChecked(False) if checked else None
        )
        self.server_forge_check.toggled.connect(
            lambda checked: self.server_fabric_check.setChecked(False) if checked else None
        )
        srv_form.addRow(self.server_accept_eula_check)
        srv_form.addRow(self.server_gui_check)
        srv_form.addRow(self.server_restart_check)
        srv_form.addRow(self.server_offline_check)
        srv_form.addRow(self.server_fabric_check)
        srv_form.addRow(self.server_forge_check)
        firewall_btn = QPushButton("Open firewall port 25565")
        firewall_btn.clicked.connect(self._open_firewall_port)
        srv_form.addRow(firewall_btn)
        layout.addWidget(server_group)

        # Game Properties
        gp_group = QGroupBox("Game Properties")
        gp_form = QFormLayout(gp_group)
        gp_form.setLabelAlignment(Qt.AlignLeft)

        self.srv_difficulty_combo = QComboBox()
        self.srv_difficulty_combo.addItems(["peaceful", "easy", "normal", "hard"])
        self.srv_difficulty_combo.setCurrentText("easy")
        gp_form.addRow("Difficulty", self.srv_difficulty_combo)

        self.srv_gamemode_combo = QComboBox()
        self.srv_gamemode_combo.addItems(["survival", "creative", "adventure", "spectator"])
        gp_form.addRow("Default gamemode", self.srv_gamemode_combo)

        self.srv_max_players_spin = QSpinBox()
        self.srv_max_players_spin.setRange(1, 1000)
        self.srv_max_players_spin.setValue(20)
        gp_form.addRow("Max players", self.srv_max_players_spin)

        self.srv_pvp_check = QCheckBox("PvP")
        self.srv_pvp_check.setChecked(True)
        gp_form.addRow(self.srv_pvp_check)

        self.srv_spawn_monsters_check = QCheckBox("Spawn monsters")
        self.srv_spawn_monsters_check.setChecked(True)
        gp_form.addRow(self.srv_spawn_monsters_check)

        self.srv_cmd_blocks_check = QCheckBox("Enable command blocks")
        gp_form.addRow(self.srv_cmd_blocks_check)

        self.srv_cheats_check = QCheckBox("Allow cheats (op yourself)")
        self.srv_cheats_check.setChecked(True)
        gp_form.addRow(self.srv_cheats_check)

        apply_props_btn = QPushButton("Apply to server now")
        apply_props_btn.clicked.connect(self._apply_game_props_now)
        gp_form.addRow(apply_props_btn)

        layout.addWidget(gp_group)

        gp_hint = QLabel("Gamerules (keepInventory, etc.) can be set in-game with /gamerule.")
        gp_hint.setWordWrap(True)
        gp_hint.setStyleSheet("color: #7f8792; font-size: 11px;")
        layout.addWidget(gp_hint)

        layout.addStretch()
        scroll.setWidget(content)

        tab_layout = QVBoxLayout(tab)
        tab_layout.setContentsMargins(0, 0, 0, 0)
        tab_layout.addWidget(scroll)
        return tab

    def _build_settings_tab(self):
        tab = QWidget()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)

        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        # Paths
        paths_group = QGroupBox("Paths")
        paths_form = QFormLayout(paths_group)
        paths_form.setLabelAlignment(Qt.AlignLeft)

        self.base_dir_edit, base_browse = self._path_row()
        base_browse.clicked.connect(lambda: self.browse_path(self.base_dir_edit))
        self.base_dir_edit.editingFinished.connect(self.refresh_versions)
        paths_form.addRow("Base dir", self._row_widget(self.base_dir_edit, base_browse))

        self.game_dir_edit, game_browse = self._path_row()
        game_browse.clicked.connect(lambda: self.browse_path(self.game_dir_edit))
        paths_form.addRow("Game dir", self._row_widget(self.game_dir_edit, game_browse))

        self.servers_dir_edit, servers_browse = self._path_row()
        servers_browse.clicked.connect(lambda: self.browse_path(self.servers_dir_edit))
        paths_form.addRow("Servers dir", self._row_widget(self.servers_dir_edit, servers_browse))

        self.java_edit, java_browse = self._path_row()
        java_browse.clicked.connect(lambda: self.browse_file(self.java_edit))
        paths_form.addRow("Java path", self._row_widget(self.java_edit, java_browse))

        layout.addWidget(paths_group)

        # Launch options
        launch_group = QGroupBox("Launch options")
        launch_form = QFormLayout(launch_group)
        launch_form.setLabelAlignment(Qt.AlignLeft)

        self.xmx_edit = QLineEdit()
        self.xmx_edit.setPlaceholderText("Override (e.g. 4G)")
        launch_form.addRow("Xmx override", self.xmx_edit)

        self.xms_edit = QLineEdit()
        self.xms_edit.setPlaceholderText("1G")
        launch_form.addRow("Xms", self.xms_edit)

        self.xss_edit = QLineEdit()
        self.xss_edit.setPlaceholderText("1M")
        launch_form.addRow("Xss", self.xss_edit)

        resolution_row = QWidget()
        res_layout = QHBoxLayout(resolution_row)
        res_layout.setContentsMargins(0, 0, 0, 0)
        self.width_spin = QSpinBox()
        self.width_spin.setRange(0, 10000)
        self.width_spin.setSpecialValueText("Auto")
        self.height_spin = QSpinBox()
        self.height_spin.setRange(0, 10000)
        self.height_spin.setSpecialValueText("Auto")
        res_layout.addWidget(self.width_spin)
        res_layout.addWidget(self.height_spin)
        launch_form.addRow("Resolution", resolution_row)

        self.demo_check = QCheckBox("Demo mode")
        self.dry_run_check = QCheckBox("Dry run")
        self.official_flags_check = QCheckBox("Use official JVM flags")
        self.official_flags_check.setChecked(True)
        launch_form.addRow(self.demo_check)
        launch_form.addRow(self.dry_run_check)
        launch_form.addRow(self.official_flags_check)

        layout.addWidget(launch_group)

        # Download options
        download_group = QGroupBox("Download options")
        dl_form = QFormLayout(download_group)
        dl_form.setLabelAlignment(Qt.AlignLeft)
        self.no_assets_check = QCheckBox("Skip assets")
        self.include_server_check = QCheckBox("Include server.jar")
        self.include_mappings_check = QCheckBox("Include mappings")
        self.verify_check = QCheckBox("Verify SHA1 (slow)")
        self.show_snapshots_check = QCheckBox("Show snapshots in download list")
        self.show_snapshots_check.toggled.connect(self._populate_version_combo)
        dl_form.addRow(self.no_assets_check)
        dl_form.addRow(self.include_server_check)
        dl_form.addRow(self.include_mappings_check)
        dl_form.addRow(self.verify_check)
        dl_form.addRow(self.show_snapshots_check)
        layout.addWidget(download_group)

        layout.addStretch()
        scroll.setWidget(content)

        tab_layout = QVBoxLayout(tab)
        tab_layout.setContentsMargins(0, 0, 0, 0)
        tab_layout.addWidget(scroll)
        return tab

    def _path_row(self):
        edit = QLineEdit()
        browse = QToolButton()
        browse.setText("...")
        return edit, browse

    def _row_widget(self, edit, button):
        widget = QWidget()
        layout = QHBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        layout.addWidget(edit, 1)
        layout.addWidget(button)
        return widget

    def _refresh_mods(self):
        base_dir = self.base_dir_edit.text().strip()
        if not base_dir:
            return
        mods_dir = Path(base_dir) / "mods"
        mods_dir.mkdir(parents=True, exist_ok=True)

        # Determine MC version filter
        show_all = self.mods_show_all_check.isChecked()
        mc_version = None
        if not show_all:
            version_name = self.installed_combo.currentText()
            if version_name and version_name != "No versions installed":
                mc_version = _resolve_mc_version(version_name)

        self.mods_list.blockSignals(True)
        self.mods_list.clear()

        jars = sorted(mods_dir.glob("*.jar"))
        disabled = sorted(mods_dir.glob("*.jar.disabled"))

        def _matches_version(filename):
            if show_all or not mc_version:
                return True
            mod_ver = _extract_mod_mc_version(filename)
            return mod_ver is None or mod_ver == mc_version

        shown = 0
        for jar in jars:
            if not _matches_version(jar.name):
                continue
            item = QListWidgetItem(jar.stem)
            item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked)
            item.setData(Qt.UserRole, str(jar))
            self.mods_list.addItem(item)
            shown += 1
        for jar in disabled:
            if not _matches_version(jar.name):
                continue
            name = jar.name.removesuffix(".jar.disabled")
            item = QListWidgetItem(name)
            item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Unchecked)
            item.setData(Qt.UserRole, str(jar))
            self.mods_list.addItem(item)
            shown += 1

        if shown == 0:
            msg = "No mods found" if show_all else f"No mods for {mc_version or 'selected version'}"
            item = QListWidgetItem(msg)
            item.setFlags(Qt.NoItemFlags)
            self.mods_list.addItem(item)

        self.mods_list.blockSignals(False)

    def _on_mod_toggled(self, item):
        file_path = Path(item.data(Qt.UserRole))
        if not file_path or not file_path.exists():
            return
        try:
            if item.checkState() == Qt.Checked:
                new_path = file_path.with_name(file_path.name.removesuffix(".disabled"))
            else:
                new_path = file_path.with_name(file_path.name + ".disabled")
            file_path.rename(new_path)
            self.mods_list.blockSignals(True)
            item.setData(Qt.UserRole, str(new_path))
            self.mods_list.blockSignals(False)
        except OSError as e:
            self.status_label.setText(f"Failed to toggle mod: {e}")

    def _open_firewall_port(self):
        if sys.platform == "win32":
            import ctypes
            result = ctypes.windll.shell32.ShellExecuteW(
                None, "runas", "netsh",
                "advfirewall firewall add rule name=\"Minecraft Server\" "
                "dir=in action=allow protocol=TCP localport=25565",
                None, 1,
            )
            if result > 32:
                QMessageBox.information(self, "Firewall", "Firewall rule added (port 25565 TCP).")
            else:
                QMessageBox.warning(self, "Firewall", "Could not add firewall rule. Try running as administrator.")
        elif sys.platform == "darwin":
            QMessageBox.information(
                self, "Firewall",
                "On macOS the system prompts automatically when Java first accepts connections.\n"
                "No manual action needed."
            )
        else:
            QMessageBox.information(
                self, "Firewall",
                "Run this command in a terminal:\n\n"
                "sudo ufw allow 25565/tcp"
            )

    # ── Game properties ───────────────────────────────────────

    def _write_server_properties(self, server_dir):
        """Write/update server.properties with current game properties settings."""
        server_dir = Path(server_dir)
        server_dir.mkdir(parents=True, exist_ok=True)
        props_path = server_dir / "server.properties"
        updates = {
            "difficulty": self.srv_difficulty_combo.currentText(),
            "gamemode": self.srv_gamemode_combo.currentText(),
            "max-players": str(self.srv_max_players_spin.value()),
            "pvp": "true" if self.srv_pvp_check.isChecked() else "false",
            "spawn-monsters": "true" if self.srv_spawn_monsters_check.isChecked() else "false",
            "enable-command-block": "true" if self.srv_cmd_blocks_check.isChecked() else "false",
        }
        if props_path.exists():
            lines = props_path.read_text(encoding="utf-8").splitlines()
            new_lines = []
            written = set()
            for line in lines:
                if "=" in line and not line.startswith("#"):
                    key = line.split("=", 1)[0].strip()
                    if key in updates:
                        new_lines.append(f"{key}={updates[key]}")
                        written.add(key)
                        continue
                new_lines.append(line)
            for key, val in updates.items():
                if key not in written:
                    new_lines.append(f"{key}={val}")
            props_path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
        else:
            lines = [f"{k}={v}" for k, v in updates.items()]
            props_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

        # ops.json — add/remove player as op based on "Allow cheats" toggle
        self._update_ops_json(server_dir)

    def _update_ops_json(self, server_dir):
        import uuid as _uuid
        import hashlib as _hashlib
        ops_path = Path(server_dir) / "ops.json"
        username = self.username_edit.text().strip() or "Player"
        # Matches Minecraft's UUID.nameUUIDFromBytes("OfflinePlayer:<name>")
        _d = bytearray(_hashlib.md5(f"OfflinePlayer:{username}".encode("utf-8")).digest())
        _d[6] = (_d[6] & 0x0F) | 0x30
        _d[8] = (_d[8] & 0x3F) | 0x80
        offline_uuid = str(_uuid.UUID(bytes=bytes(_d)))

        try:
            ops = json.loads(ops_path.read_text(encoding="utf-8")) if ops_path.exists() else []
        except (OSError, json.JSONDecodeError):
            ops = []

        # Remove any existing entry for this username/uuid
        ops = [e for e in ops if e.get("name") != username and e.get("uuid") != offline_uuid]

        if self.srv_cheats_check.isChecked():
            ops.append({"uuid": offline_uuid, "name": username, "level": 4, "bypassesPlayerLimit": False})

        ops_path.write_text(json.dumps(ops, indent=2, ensure_ascii=True), encoding="utf-8")

    def _apply_game_props_now(self):
        base_dir = self._ensure_base_dir()
        if not base_dir:
            return
        servers_dir = self._resolve_servers_dir(base_dir)
        version_id = self.installed_combo.currentText()
        if not version_id or version_id == "No versions installed":
            version_id = self.version_combo.currentText()
        if not version_id or version_id == "Loading...":
            QMessageBox.warning(self, "No version", "Select a version first.")
            return
        server_dir = Path(servers_dir) / version_id
        self._write_server_properties(server_dir)
        self.status_label.setText(f"server.properties updated for {version_id}.")

    # ── Auto-update ───────────────────────────────────────────

    def _start_update_checker(self):
        self._update_checker = UpdateChecker()
        self._update_checker.update_available.connect(self._on_update_available)
        self._update_checker.start()

    def _on_update_available(self, tag, url):
        self._update_url = url
        self._update_btn = QPushButton(f"Update {tag} available — click to install")
        self._update_btn.setStyleSheet(
            "color: #3fb950; border: none; font-weight: 600; padding: 0 8px;"
        )
        self._update_btn.setCursor(Qt.PointingHandCursor)
        self._update_btn.clicked.connect(self._do_update)
        self.statusBar().addPermanentWidget(self._update_btn)

    def _do_update(self):
        if not getattr(sys, 'frozen', False):
            QMessageBox.information(
                self, "Update",
                "Running from source — use git pull to update."
            )
            return
        reply = QMessageBox.question(
            self, "Update",
            "Download and install the update now?\nThe app will restart automatically.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes,
        )
        if reply != QMessageBox.Yes:
            return

        current = Path(sys.executable)
        self.status_label.setText("Downloading update...")
        try:
            from urllib.request import urlopen
            with urlopen(self._update_url, timeout=120) as resp:
                data = resp.read()

            if sys.platform == "win32":
                new_exe = current.with_name("launcher_new.exe")
                new_exe.write_bytes(data)
                bat = current.with_name("update.bat")
                bat.write_text(
                    "@echo off\n"
                    "timeout /t 2 /nobreak >nul\n"
                    f"move /y \"{new_exe}\" \"{current}\"\n"
                    f"start \"\" \"{current}\"\n"
                    "del \"%~f0\"\n",
                    encoding="ascii",
                )
                subprocess.Popen(["cmd", "/c", str(bat)], close_fds=True)
                QApplication.instance().quit()
            else:
                import stat as _stat
                tmp = current.with_name(current.name + ".new")
                tmp.write_bytes(data)
                tmp.chmod(tmp.stat().st_mode | _stat.S_IEXEC | _stat.S_IXGRP | _stat.S_IXOTH)
                os.replace(str(tmp), str(current))
                os.execv(str(current), sys.argv)
        except Exception as exc:
            QMessageBox.warning(self, "Update failed", str(exc))
            self.status_label.setText("Update failed.")

    def _open_mods_folder(self):
        base_dir = self.base_dir_edit.text().strip()
        if not base_dir:
            return
        mods_dir = Path(base_dir) / "mods"
        mods_dir.mkdir(parents=True, exist_ok=True)
        if sys.platform == "win32":
            os.startfile(str(mods_dir))
        elif sys.platform == "darwin":
            subprocess.run(["open", str(mods_dir)])
        else:
            subprocess.run(["xdg-open", str(mods_dir)])

    # ── Shaders ───────────────────────────────────────────────

    def _refresh_shaderpacks(self):
        base_dir = self.base_dir_edit.text().strip()
        if not base_dir:
            return
        sp_dir = Path(base_dir) / "shaderpacks"
        sp_dir.mkdir(parents=True, exist_ok=True)

        self.shaderpacks_list.blockSignals(True)
        self.shaderpacks_list.clear()

        zips = sorted(sp_dir.glob("*.zip"))
        disabled = sorted(sp_dir.glob("*.zip.disabled"))

        if not zips and not disabled:
            item = QListWidgetItem("No shader packs found")
            item.setFlags(Qt.NoItemFlags)
            self.shaderpacks_list.addItem(item)
        else:
            for z in zips:
                item = QListWidgetItem(z.stem)
                item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsUserCheckable)
                item.setCheckState(Qt.Checked)
                item.setData(Qt.UserRole, str(z))
                self.shaderpacks_list.addItem(item)
            for z in disabled:
                name = z.name.removesuffix(".zip.disabled")
                item = QListWidgetItem(name)
                item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsUserCheckable)
                item.setCheckState(Qt.Unchecked)
                item.setData(Qt.UserRole, str(z))
                self.shaderpacks_list.addItem(item)

        self.shaderpacks_list.blockSignals(False)

    def _on_shaderpack_toggled(self, item):
        file_path = Path(item.data(Qt.UserRole))
        if not file_path or not file_path.exists():
            return
        try:
            if item.checkState() == Qt.Checked:
                new_path = file_path.with_name(
                    file_path.name.removesuffix(".disabled")
                )
            else:
                new_path = file_path.with_name(file_path.name + ".disabled")
            file_path.rename(new_path)
            self.shaderpacks_list.blockSignals(True)
            item.setData(Qt.UserRole, str(new_path))
            self.shaderpacks_list.blockSignals(False)
        except OSError as e:
            self.status_label.setText(f"Failed to toggle shader pack: {e}")

    def _open_shaderpacks_folder(self):
        base_dir = self.base_dir_edit.text().strip()
        if not base_dir:
            return
        sp_dir = Path(base_dir) / "shaderpacks"
        sp_dir.mkdir(parents=True, exist_ok=True)
        if sys.platform == "win32":
            os.startfile(str(sp_dir))
        elif sys.platform == "darwin":
            subprocess.run(["open", str(sp_dir)])
        else:
            subprocess.run(["xdg-open", str(sp_dir)])

    def _on_install_shader_mod(self):
        base_dir = self._ensure_base_dir()
        if not base_dir:
            return
        version_name = self.installed_combo.currentText()
        if not version_name or version_name == "No versions installed":
            QMessageBox.warning(
                self, "No version",
                "Select an installed version first."
            )
            return

        loader = _detect_version_loader(version_name)
        mc_version = _resolve_mc_version(version_name)

        if loader == "vanilla":
            reply = QMessageBox.question(
                self, "Shader Mod",
                "Shaders require a mod loader.\n\n"
                "Install Fabric + Iris + Sodium automatically?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.Yes,
            )
            if reply != QMessageBox.Yes:
                return
            # Chain: install Fabric first, then shader mods after it finishes
            self._pending_shader_install = {
                "mc_version": mc_version,
                "base_dir": base_dir,
                "loader": "fabric",
            }
            extra = [version_name, "--base-dir", base_dir]
            args = self._script_args(self.fabric_installer_path, extra)
            self.tabs.setCurrentIndex(0)
            self.start_main_process(args, f"Installing Fabric for {version_name}...")
            return

        extra = [mc_version, "--base-dir", base_dir, "--loader", loader]
        args = self._script_args(self.shader_mod_installer_path, extra)
        self.tabs.setCurrentIndex(0)
        self.start_main_process(
            args, f"Installing shader mod ({loader}) for {mc_version}..."
        )

    def _show_optifine_guide(self):
        QMessageBox.information(
            self, "OptiFine Guide",
            "OptiFine cannot be downloaded automatically.\n\n"
            "1. Visit https://optifine.net/downloads\n"
            "2. Download the .jar for your Minecraft version\n"
            "3. Run the .jar (double click) to install it\n"
            "4. The OptiFine version will appear in the launcher\n\n"
            "For Forge: in the installer choose \"Extract\" and\n"
            "put the .jar in the mods/ folder."
        )

    def _search_shaderpacks(self):
        query = self.shader_search_edit.text().strip()
        if self._shader_searcher is not None and self._shader_searcher.isRunning():
            return

        mc_version = ""
        version_name = self.installed_combo.currentText()
        if version_name and version_name != "No versions installed":
            mc_version = _resolve_mc_version(version_name)

        self.shader_search_btn.setEnabled(False)
        self.status_label.setText("Searching shader packs...")
        self._shader_searcher = ModrinthShaderSearcher(query, mc_version, self)
        self._shader_searcher.finished.connect(self._on_shader_search_done)
        self._shader_searcher.start()

    def _on_shader_search_done(self, hits):
        self.shader_search_btn.setEnabled(True)
        self.shader_results_list.clear()

        if not hits:
            item = QListWidgetItem("No results found")
            item.setFlags(Qt.NoItemFlags)
            self.shader_results_list.addItem(item)
            self.status_label.setText("No shader packs found.")
            return

        for h in hits:
            downloads = h["downloads"]
            if downloads >= 1_000_000:
                dl_str = f"{downloads / 1_000_000:.1f}M"
            elif downloads >= 1_000:
                dl_str = f"{downloads / 1_000:.0f}K"
            else:
                dl_str = str(downloads)

            item = QListWidgetItem()
            item.setFlags(Qt.NoItemFlags)
            self.shader_results_list.addItem(item)

            row_widget = QWidget()
            row_layout = QHBoxLayout(row_widget)
            row_layout.setContentsMargins(4, 2, 4, 2)
            row_layout.setSpacing(8)

            label = QLabel(
                f"<b>{h['title']}</b> by {h['author']}  "
                f"<span style='color:#7f8792;'>({dl_str} downloads)</span>"
            )
            label.setTextFormat(Qt.RichText)
            row_layout.addWidget(label, 1)

            btn = QPushButton("Download")
            slug = h["slug"]
            btn.clicked.connect(
                lambda checked, s=slug: self._download_shaderpack(s)
            )
            row_layout.addWidget(btn)

            item.setSizeHint(row_widget.sizeHint())
            self.shader_results_list.setItemWidget(item, row_widget)

        self.status_label.setText(f"Found {len(hits)} shader packs.")

    def _download_shaderpack(self, slug):
        base_dir = self.base_dir_edit.text().strip()
        if not base_dir:
            return
        if self._shader_downloader is not None and self._shader_downloader.isRunning():
            self.status_label.setText("A download is already in progress.")
            return

        sp_dir = Path(base_dir) / "shaderpacks"
        sp_dir.mkdir(parents=True, exist_ok=True)

        mc_version = ""
        version_name = self.installed_combo.currentText()
        if version_name and version_name != "No versions installed":
            mc_version = _resolve_mc_version(version_name)

        self.status_label.setText(f"Downloading shader pack: {slug}...")
        self._shader_downloader = ShaderPackDownloader(
            slug, mc_version, str(sp_dir), self
        )
        self._shader_downloader.finished.connect(self._on_shaderpack_downloaded)
        self._shader_downloader.start()

    def _on_shaderpack_downloaded(self, success, message):
        if success:
            self.status_label.setText(message)
            self._refresh_shaderpacks()
        else:
            self.status_label.setText(f"Download failed: {message}")

    # ── Username validation ───────────────────────────────────

    def _on_username_changed(self, text):
        self._username_is_taken = None
        self.username_status_label.setText("")
        self.username_status_label.setToolTip("")
        self._username_check_timer.start()

    def _do_username_check(self):
        username = self.username_edit.text().strip()
        if not username or len(username) < 3 or username == self._last_checked_username:
            return
        if self._username_checker is not None and self._username_checker.isRunning():
            return
        self._last_checked_username = username
        self._username_checker = UsernameChecker(username, self)
        self._username_checker.finished.connect(self._on_username_check_result)
        self._username_checker.start()

    def _on_username_check_result(self, result):
        username = self.username_edit.text().strip()
        checker_name = self._username_checker.username if self._username_checker else ""
        if username != checker_name:
            return
        if result["taken"] is True:
            self._username_is_taken = True
            self.username_status_label.setText("X")
            self.username_status_label.setStyleSheet("color: #f85149; font-weight: 700;")
            self.username_status_label.setToolTip(
                f"Premium account exists: {result['correct_name']} ({result['uuid']})\n"
                "You may have issues joining offline-mode servers with this name."
            )
            self.status_label.setText(
                f"Warning: \"{result['correct_name']}\" is a premium account."
            )
        elif result["taken"] is False:
            self._username_is_taken = False
            self.username_status_label.setText("OK")
            self.username_status_label.setStyleSheet("color: #3fb950; font-weight: 700;")
            self.username_status_label.setToolTip("Username is available (no premium account).")
            self.status_label.setText("Username available.")
        else:
            self._username_is_taken = None
            self.username_status_label.setText("?")
            self.username_status_label.setStyleSheet("color: #d29922; font-weight: 700;")
            self.username_status_label.setToolTip(f"Could not check: {result['error']}")

    # ── Manifest / versions ────────────────────────────────────

    def _fetch_manifest(self):
        self.manifest_fetcher = ManifestFetcher()
        self.manifest_fetcher.finished.connect(self._on_manifest_loaded)
        self.manifest_fetcher.start()

    def _on_manifest_loaded(self, versions):
        self.all_manifest_versions = versions
        self._populate_version_combo()
        if not versions:
            self.status_label.setText("Could not load version list.")

    def _populate_version_combo(self):
        show_snapshots = (
            self.show_snapshots_check.isChecked()
            if hasattr(self, "show_snapshots_check")
            else False
        )
        self.version_combo.clear()
        for v in self.all_manifest_versions:
            vtype = v.get("type", "")
            if vtype == "release" or (show_snapshots and vtype == "snapshot"):
                self.version_combo.addItem(v.get("id", ""))
        self.version_combo.setEnabled(self.version_combo.count() > 0)
        if self.version_combo.count() > 0:
            self.status_label.setText(
                f"Loaded {self.version_combo.count()} versions."
            )

    # ── Settings ───────────────────────────────────────────────

    def _load_settings(self):
        settings = load_settings()
        self.base_dir_edit.setText(settings.get("base_dir", str(default_base_dir())))
        self.game_dir_edit.setText(settings.get("game_dir", ""))
        self.servers_dir_edit.setText(settings.get("servers_dir", ""))
        self.java_edit.setText(settings.get("java_path", ""))
        self.username_edit.setText(settings.get("username", "Player"))
        # RAM combo
        ram = settings.get("ram", "4G")
        idx = self.ram_combo.findText(ram)
        if idx >= 0:
            self.ram_combo.setCurrentIndex(idx)
        self.xmx_edit.setText(settings.get("xmx", ""))
        self.xms_edit.setText(settings.get("xms", ""))
        self.xss_edit.setText(settings.get("xss", ""))
        self.width_spin.setValue(settings.get("width", 0))
        self.height_spin.setValue(settings.get("height", 0))
        self.demo_check.setChecked(settings.get("demo", False))
        self.dry_run_check.setChecked(settings.get("dry_run", False))
        self.official_flags_check.setChecked(settings.get("official_flags", True))
        self.no_assets_check.setChecked(settings.get("no_assets", False))
        self.include_server_check.setChecked(settings.get("include_server", False))
        self.include_mappings_check.setChecked(settings.get("include_mappings", False))
        self.verify_check.setChecked(settings.get("verify", False))
        self.show_snapshots_check.setChecked(settings.get("show_snapshots", False))
        self.server_accept_eula_check.setChecked(settings.get("server_accept_eula", True))
        self.server_gui_check.setChecked(settings.get("server_gui", False))
        self.server_restart_check.setChecked(settings.get("server_restart", True))
        self.server_offline_check.setChecked(settings.get("server_offline_mode", True))
        self.server_fabric_check.setChecked(settings.get("server_fabric", False))
        self.server_forge_check.setChecked(settings.get("server_forge", False))
        idx = self.srv_difficulty_combo.findText(settings.get("srv_difficulty", "easy"))
        if idx >= 0:
            self.srv_difficulty_combo.setCurrentIndex(idx)
        idx = self.srv_gamemode_combo.findText(settings.get("srv_gamemode", "survival"))
        if idx >= 0:
            self.srv_gamemode_combo.setCurrentIndex(idx)
        self.srv_max_players_spin.setValue(settings.get("srv_max_players", 20))
        self.srv_pvp_check.setChecked(settings.get("srv_pvp", True))
        self.srv_spawn_monsters_check.setChecked(settings.get("srv_spawn_monsters", True))
        self.srv_cmd_blocks_check.setChecked(settings.get("srv_cmd_blocks", False))
        self.srv_cheats_check.setChecked(settings.get("srv_cheats", True))
        if not self.servers_dir_edit.text().strip():
            self.servers_dir_edit.setText(
                str(Path(self.base_dir_edit.text().strip() or default_base_dir()) / "servers")
            )
        self._pending_selected_version = settings.get("selected_version", "")

    def _save_settings(self):
        settings = {
            "base_dir": self.base_dir_edit.text().strip(),
            "game_dir": self.game_dir_edit.text().strip(),
            "servers_dir": self.servers_dir_edit.text().strip(),
            "java_path": self.java_edit.text().strip(),
            "username": self.username_edit.text().strip(),
            "ram": self.ram_combo.currentText(),
            "xmx": self.xmx_edit.text().strip(),
            "xms": self.xms_edit.text().strip(),
            "xss": self.xss_edit.text().strip(),
            "width": self.width_spin.value(),
            "height": self.height_spin.value(),
            "demo": self.demo_check.isChecked(),
            "dry_run": self.dry_run_check.isChecked(),
            "official_flags": self.official_flags_check.isChecked(),
            "no_assets": self.no_assets_check.isChecked(),
            "include_server": self.include_server_check.isChecked(),
            "include_mappings": self.include_mappings_check.isChecked(),
            "verify": self.verify_check.isChecked(),
            "show_snapshots": self.show_snapshots_check.isChecked(),
            "server_accept_eula": self.server_accept_eula_check.isChecked(),
            "server_gui": self.server_gui_check.isChecked(),
            "server_restart": self.server_restart_check.isChecked(),
            "server_offline_mode": self.server_offline_check.isChecked(),
            "server_fabric": self.server_fabric_check.isChecked(),
            "server_forge": self.server_forge_check.isChecked(),
            "srv_difficulty": self.srv_difficulty_combo.currentText(),
            "srv_gamemode": self.srv_gamemode_combo.currentText(),
            "srv_max_players": self.srv_max_players_spin.value(),
            "srv_pvp": self.srv_pvp_check.isChecked(),
            "srv_spawn_monsters": self.srv_spawn_monsters_check.isChecked(),
            "srv_cmd_blocks": self.srv_cmd_blocks_check.isChecked(),
            "srv_cheats": self.srv_cheats_check.isChecked(),
            "selected_version": self.installed_combo.currentText(),
        }
        save_settings(settings)

    def closeEvent(self, event):
        self._save_settings()
        if self.manifest_fetcher is not None and self.manifest_fetcher.isRunning():
            self.manifest_fetcher.quit()
            self.manifest_fetcher.wait(3000)
        super().closeEvent(event)

    # ── Browse helpers ─────────────────────────────────────────

    def browse_path(self, line_edit):
        path = QFileDialog.getExistingDirectory(self, "Select folder")
        if path:
            line_edit.setText(path)
            if line_edit is self.base_dir_edit:
                if not self.servers_dir_edit.text().strip():
                    self.servers_dir_edit.setText(str(Path(path) / "servers"))
                self.refresh_versions()

    def browse_file(self, line_edit):
        path, _ = QFileDialog.getOpenFileName(self, "Select file")
        if path:
            line_edit.setText(path)

    # ── Version list ───────────────────────────────────────────

    def refresh_versions(self):
        base_dir = self.base_dir_edit.text().strip()
        if not base_dir:
            return
        current = self.installed_combo.currentText()
        pending = getattr(self, "_pending_selected_version", "")
        preferred = pending or current
        versions = list_installed_versions(base_dir)
        self.installed_combo.clear()
        if versions:
            self.installed_combo.addItems(versions)
            idx = self.installed_combo.findText(preferred)
            if idx >= 0:
                self.installed_combo.setCurrentIndex(idx)
        else:
            self.installed_combo.addItem("No versions installed")
            self.installed_combo.setEnabled(False)
            return
        self.installed_combo.setEnabled(True)
        self._pending_selected_version = ""

    # ── Script helpers ─────────────────────────────────────────

    def _script_args(self, script_path, extra_args):
        """Build the args list to run a script, supporting both frozen and source modes."""
        if getattr(sys, 'frozen', False):
            return [sys.executable, '--script', Path(script_path).stem] + extra_args
        return [sys.executable, str(script_path)] + extra_args

    # ── Script checks ──────────────────────────────────────────

    def _ensure_scripts(self):
        for path in (
            self.downloader_path,
            self.launcher_path,
            self.server_downloader_path,
            self.server_launcher_path,
        ):
            if not path.exists():
                QMessageBox.critical(self, "Missing file", str(path))
                return False
        return True

    def _ensure_base_dir(self):
        base_dir = self.base_dir_edit.text().strip()
        if not base_dir:
            QMessageBox.warning(self, "Missing base dir", "Set a base dir first.")
            return None
        return base_dir

    def _resolve_servers_dir(self, base_dir):
        servers_dir = self.servers_dir_edit.text().strip()
        if servers_dir:
            return servers_dir
        return str(Path(base_dir) / "servers")

    def _get_ram(self):
        """Get RAM value: dev override takes priority, otherwise combo."""
        override = self.xmx_edit.text().strip()
        if override:
            return override
        return self.ram_combo.currentText()

    # ── Actions ────────────────────────────────────────────────

    def on_download_clicked(self):
        if not self._ensure_scripts():
            return
        base_dir = self._ensure_base_dir()
        if not base_dir:
            return

        version_id = self.version_combo.currentText()
        if not version_id or version_id == "Loading...":
            QMessageBox.warning(self, "No version", "Wait for versions to load or select one.")
            return

        extra = [version_id, "--base-dir", base_dir]
        if self.no_assets_check.isChecked():
            extra.append("--no-assets")
        if self.include_server_check.isChecked():
            extra.append("--include-server")
        if self.include_mappings_check.isChecked():
            extra.append("--include-mappings")
        if self.verify_check.isChecked():
            extra.append("--verify")
        args = self._script_args(self.downloader_path, extra)

        # Auto-show logs during download
        self.tabs.setCurrentIndex(0)
        self.start_main_process(args, f"Downloading {version_id}...")

    def on_launch_clicked(self):
        if self._username_is_taken:
            username = self.username_edit.text().strip()
            reply = QMessageBox.warning(
                self,
                "Username conflict",
                f'The username "{username}" belongs to a premium Minecraft account.\n\n'
                "You may be unable to join offline-mode servers or face login prompts.\n\n"
                "Do you want to launch anyway?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if reply != QMessageBox.Yes:
                return
        if not self._ensure_scripts():
            return
        base_dir = self._ensure_base_dir()
        if not base_dir:
            return
        version_id = self.installed_combo.currentText()
        if not version_id or version_id == "No versions installed":
            QMessageBox.warning(
                self, "No version",
                "No version installed. Download one first."
            )
            return

        extra = [version_id, "--base-dir", base_dir]
        game_dir = self.game_dir_edit.text().strip()
        if game_dir:
            extra += ["--game-dir", game_dir]
        java_path = self.java_edit.text().strip()
        if java_path:
            extra += ["--java", java_path]
        username = self.username_edit.text().strip()
        if username:
            extra += ["--username", username]
        ram = self._get_ram()
        extra += ["--xmx", ram]
        if self.xms_edit.text().strip():
            extra += ["--xms", self.xms_edit.text().strip()]
        if self.xss_edit.text().strip():
            extra += ["--xss", self.xss_edit.text().strip()]
        if self.width_spin.value() > 0 and self.height_spin.value() > 0:
            extra += ["--width", str(self.width_spin.value())]
            extra += ["--height", str(self.height_spin.value())]
        if self.demo_check.isChecked():
            extra.append("--demo")
        if self.dry_run_check.isChecked():
            extra.append("--dry-run")
        if not self.official_flags_check.isChecked():
            extra.append("--no-official-jvm-flags")
        args = self._script_args(self.launcher_path, extra)

        self.start_main_process(args, f"Launching {version_id}...")

    def on_install_fabric_clicked(self):
        base_dir = self._ensure_base_dir()
        if not base_dir:
            return
        version_id = self.version_combo.currentText()
        if not version_id or version_id == "Loading...":
            QMessageBox.warning(self, "No version", "Select a version to install Fabric for.")
            return
        extra = [version_id, "--base-dir", base_dir]
        args = self._script_args(self.fabric_installer_path, extra)
        self.tabs.setCurrentIndex(0)
        self.start_main_process(args, f"Installing Fabric for {version_id}...")

    def on_install_forge_clicked(self):
        base_dir = self._ensure_base_dir()
        if not base_dir:
            return
        version_id = self.version_combo.currentText()
        if not version_id or version_id == "Loading...":
            QMessageBox.warning(self, "No version", "Select a version to install Forge for.")
            return
        extra = [version_id, "--base-dir", base_dir]
        args = self._script_args(self.forge_installer_path, extra)
        self.tabs.setCurrentIndex(0)
        self.start_main_process(args, f"Installing Forge for {version_id}...")

    def on_download_server_clicked(self):
        if not self._ensure_scripts():
            return
        base_dir = self._ensure_base_dir()
        if not base_dir:
            return
        servers_dir = self._resolve_servers_dir(base_dir)

        version_id = self.version_combo.currentText()
        if not version_id or version_id == "Loading...":
            QMessageBox.warning(self, "No version", "Wait for versions to load or select one.")
            return

        self.tabs.setCurrentIndex(0)
        if self.server_fabric_check.isChecked():
            extra = [version_id, "--servers-dir", servers_dir, "--server"]
            args = self._script_args(self.fabric_installer_path, extra)
            self.start_main_process(args, f"Downloading Fabric server {version_id}...")
        elif self.server_forge_check.isChecked():
            extra = [version_id, "--servers-dir", servers_dir, "--server"]
            args = self._script_args(self.forge_installer_path, extra)
            self.start_main_process(args, f"Installing Forge server {version_id}...")
        else:
            extra = [version_id, "--servers-dir", servers_dir]
            if self.verify_check.isChecked():
                extra.append("--verify")
            if self.include_mappings_check.isChecked():
                extra.append("--include-mappings")
            args = self._script_args(self.server_downloader_path, extra)
            self.start_main_process(args, f"Downloading server {version_id}...")

    def on_launch_server_clicked(self):
        if not self._ensure_scripts():
            return
        base_dir = self._ensure_base_dir()
        if not base_dir:
            return
        servers_dir = self._resolve_servers_dir(base_dir)
        version_id = self.installed_combo.currentText()
        if not version_id or version_id == "No versions installed":
            version_id = self.version_combo.currentText()
        if not version_id or version_id == "Loading...":
            QMessageBox.warning(self, "No version", "Select a version.")
            return

        # Apply game properties to server.properties before launch
        self._write_server_properties(Path(servers_dir) / version_id)

        extra = [version_id, "--servers-dir", servers_dir, "--minecraft-dir", base_dir]
        java_path = self.java_edit.text().strip()
        if java_path:
            extra += ["--java", java_path]
        ram = self._get_ram()
        extra += ["--xmx", ram]
        if self.xms_edit.text().strip():
            extra += ["--xms", self.xms_edit.text().strip()]
        if self.server_accept_eula_check.isChecked():
            extra.append("--accept-eula")
        if self.server_gui_check.isChecked():
            extra.append("--gui")
        if self.server_restart_check.isChecked():
            extra.append("--restart-if-running")
        if self.server_offline_check.isChecked():
            extra.append("--offline-mode")
        if self.dry_run_check.isChecked():
            extra.append("--dry-run")
        args = self._script_args(self.server_launcher_path, extra)

        self.start_server_process(args, f"Launching server {version_id}...")

    # ── Process management ─────────────────────────────────────

    def start_main_process(self, args, label):
        if self.main_process.state() != QProcess.NotRunning:
            QMessageBox.warning(self, "Busy", "Another task is already running.")
            return

        self._save_settings()
        self.log_output.appendPlainText(f"$ {format_cmd([str(a) for a in args])}")
        self.status_label.setText(label)
        self._set_main_busy(True)

        env = QProcessEnvironment.systemEnvironment()
        env.insert("PYTHONUNBUFFERED", "1")
        self.main_process.setProcessEnvironment(env)
        self.main_process.setWorkingDirectory(str(self.script_dir))
        self.main_process.start(args[0], [str(a) for a in args[1:]])

    def start_server_process(self, args, label):
        if self.server_process.state() != QProcess.NotRunning:
            QMessageBox.warning(self, "Server running", "Server process is already running.")
            return

        self._save_settings()
        self.log_output.appendPlainText(f"$ {format_cmd([str(a) for a in args])}")
        self.status_label.setText(label)
        self._set_server_busy(True)

        env = QProcessEnvironment.systemEnvironment()
        env.insert("PYTHONUNBUFFERED", "1")
        self.server_process.setProcessEnvironment(env)
        self.server_process.setWorkingDirectory(str(self.script_dir))
        self.server_process.start(args[0], [str(a) for a in args[1:]])

    def _set_main_busy(self, busy):
        for widget in (
            self.download_button,
            self.install_fabric_button,
            self.install_forge_button,
            self.install_shader_mod_btn,
            self.launch_button,
            self.download_server_button,
        ):
            widget.setEnabled(not busy)
        if self.server_process.state() == QProcess.NotRunning:
            self.launch_server_button.setEnabled(not busy)
        # Fade-in the PLAY button when it becomes enabled
        if not busy:
            self._play_opacity.setOpacity(0.0)
            anim = QPropertyAnimation(self._play_opacity, b"opacity")
            anim.setDuration(300)
            anim.setStartValue(0.0)
            anim.setEndValue(1.0)
            anim.setEasingCurve(QEasingCurve.OutCubic)
            anim.start()
            self._play_fade_anim = anim

    def _set_server_busy(self, busy):
        self.launch_server_button.setEnabled(
            (not busy) and self.main_process.state() == QProcess.NotRunning
        )

    def on_main_process_output(self):
        data = bytes(self.main_process.readAllStandardOutput()).decode(
            "utf-8", errors="replace"
        )
        if data:
            self.log_output.appendPlainText(data.rstrip())

    def on_server_process_output(self):
        data = bytes(self.server_process.readAllStandardOutput()).decode(
            "utf-8", errors="replace"
        )
        if data:
            self.log_output.appendPlainText(data.rstrip())

    def on_main_process_finished(self, exit_code, _status):
        if exit_code == 0:
            self.status_label.setText("Done.")
        else:
            self.status_label.setText(f"Finished with error (code {exit_code}).")
        self._set_main_busy(False)
        self._set_server_busy(self.server_process.state() != QProcess.NotRunning)
        self.refresh_versions()

        # Chain: auto-install shader mods after Fabric install
        pending = getattr(self, "_pending_shader_install", None)
        if pending and exit_code == 0:
            self._pending_shader_install = None
            mc_ver = pending["mc_version"]
            base_dir = pending["base_dir"]
            loader = pending["loader"]
            extra = [mc_ver, "--base-dir", base_dir, "--loader", loader]
            args = self._script_args(self.shader_mod_installer_path, extra)
            self.start_main_process(
                args, f"Installing Iris + Sodium for {mc_ver}..."
            )
        elif pending:
            self._pending_shader_install = None

    def on_server_process_finished(self, exit_code, _status):
        self.status_label.setText(f"Server stopped (code {exit_code}).")
        self._set_server_busy(False)
        self.refresh_versions()


def main():
    app = QApplication(sys.argv)
    window = LauncherWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
