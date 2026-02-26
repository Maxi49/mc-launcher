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
    QFrame,
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
    QToolButton,
    QVBoxLayout,
    QWidget,
)

try:
    import requests
except ImportError:
    requests = None

from mc_common import format_cmd, check_username_taken, default_minecraft_dir

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


class LauncherWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Minecraft Launcher")
        self.resize(620, 700)

        self.script_dir = Path(__file__).resolve().parent
        self.downloader_path = self.script_dir / "scripts" / "download_version.py"
        self.launcher_path = self.script_dir / "scripts" / "launch_client.py"
        self.server_downloader_path = self.script_dir / "scripts" / "download_server.py"
        self.server_launcher_path = self.script_dir / "scripts" / "launch_server.py"

        self.all_manifest_versions = []
        self.manifest_fetcher = None
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

    # ── UI ──────────────────────────────────────────────────────

    def _build_ui(self):
        # Scroll area so everything works at any window size
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(14)

        # Header
        header = QLabel("Minecraft Launcher")
        header.setStyleSheet("font-size: 22px; font-weight: 700; color: #58a6ff;")
        header.setAlignment(Qt.AlignCenter)
        layout.addWidget(header)

        # ── Essential controls ─────────────────────────────────

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
        layout.addLayout(top_row)

        # Installed versions (play row)
        play_row = QHBoxLayout()
        play_row.addWidget(QLabel("Version:"))
        self.installed_combo = QComboBox()
        self.installed_combo.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        play_row.addWidget(self.installed_combo, 1)
        layout.addLayout(play_row)

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
        layout.addLayout(dl_row)

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
        # Opacity effect for fade-in animation
        self._play_opacity = QGraphicsOpacityEffect(self.launch_button)
        self._play_opacity.setOpacity(1.0)
        self.launch_button.setGraphicsEffect(self._play_opacity)
        layout.addWidget(self.launch_button)

        # ── Mods panel (collapsible) ──────────────────────────
        self.mods_toggle = QCheckBox("Mods")
        self.mods_toggle.toggled.connect(self._toggle_mods_panel)
        layout.addWidget(self.mods_toggle)

        self.mods_panel = QWidget()
        mods_layout = QVBoxLayout(self.mods_panel)
        mods_layout.setContentsMargins(0, 0, 0, 0)
        mods_layout.setSpacing(8)

        self.mods_list = QListWidget()
        self.mods_list.itemChanged.connect(self._on_mod_toggled)
        mods_layout.addWidget(self.mods_list)

        mods_buttons = QHBoxLayout()
        refresh_mods_btn = QPushButton("Refresh")
        refresh_mods_btn.clicked.connect(self._refresh_mods)
        open_mods_btn = QPushButton("Open mods folder")
        open_mods_btn.clicked.connect(self._open_mods_folder)
        mods_buttons.addWidget(refresh_mods_btn)
        mods_buttons.addWidget(open_mods_btn)
        mods_layout.addLayout(mods_buttons)

        self.mods_panel.setVisible(False)
        layout.addWidget(self.mods_panel)

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

        # Separator between main section and logs/dev
        separator = QFrame()
        separator.setFrameShape(QFrame.HLine)
        separator.setStyleSheet("QFrame { color: #2d3548; margin: 4px 0; }")
        layout.addWidget(separator)

        # ── Logs (collapsible) ─────────────────────────────────

        self.log_toggle = QCheckBox("Show logs")
        self.log_toggle.toggled.connect(self._toggle_logs)
        layout.addWidget(self.log_toggle)

        self.log_output = QPlainTextEdit()
        self.log_output.setReadOnly(True)
        self.log_output.setMaximumBlockCount(2000)
        self.log_output.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.log_output.setMinimumHeight(80)
        self.log_output.setVisible(False)
        layout.addWidget(self.log_output, 1)

        # ── Dev settings toggle ────────────────────────────────

        self.dev_toggle = QCheckBox("Dev Settings")
        self.dev_toggle.toggled.connect(self._toggle_dev_settings)
        layout.addWidget(self.dev_toggle)

        # Dev settings panel (hidden by default)
        self.dev_panel = QWidget()
        dev_layout = QVBoxLayout(self.dev_panel)
        dev_layout.setContentsMargins(0, 0, 0, 0)
        dev_layout.setSpacing(8)

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

        dev_layout.addWidget(paths_group)

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

        dev_layout.addWidget(launch_group)

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
        dev_layout.addWidget(download_group)

        # Server options
        server_group = QGroupBox("Server options")
        srv_form = QFormLayout(server_group)
        srv_form.setLabelAlignment(Qt.AlignLeft)
        self.server_accept_eula_check = QCheckBox("Auto accept EULA")
        self.server_gui_check = QCheckBox("Enable server GUI")
        self.server_restart_check = QCheckBox("Restart if running")
        self.server_offline_check = QCheckBox("Offline mode (online-mode=false)")
        srv_form.addRow(self.server_accept_eula_check)
        srv_form.addRow(self.server_gui_check)
        srv_form.addRow(self.server_restart_check)
        srv_form.addRow(self.server_offline_check)
        firewall_btn = QPushButton("Open firewall port 25565")
        firewall_btn.clicked.connect(self._open_firewall_port)
        srv_form.addRow(firewall_btn)
        dev_layout.addWidget(server_group)

        self.dev_panel.setVisible(False)
        layout.addWidget(self.dev_panel)

        # Bottom stretch so content hugs the top when small
        layout.addStretch(0)

        scroll.setWidget(content)
        self.setCentralWidget(scroll)

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

    def _toggle_dev_settings(self, visible):
        if visible:
            self.dev_panel.setMaximumHeight(0)
            self.dev_panel.setVisible(True)
            anim = QPropertyAnimation(self.dev_panel, b"maximumHeight")
            anim.setDuration(250)
            anim.setStartValue(0)
            anim.setEndValue(2000)
            anim.setEasingCurve(QEasingCurve.OutCubic)
            anim.start()
            self._dev_anim = anim  # prevent GC
        else:
            anim = QPropertyAnimation(self.dev_panel, b"maximumHeight")
            anim.setDuration(250)
            anim.setStartValue(self.dev_panel.height())
            anim.setEndValue(0)
            anim.setEasingCurve(QEasingCurve.OutCubic)
            anim.finished.connect(lambda: self.dev_panel.setVisible(False))
            anim.start()
            self._dev_anim = anim

    def _toggle_logs(self, visible):
        if visible:
            self.log_output.setMaximumHeight(0)
            self.log_output.setVisible(True)
            anim = QPropertyAnimation(self.log_output, b"maximumHeight")
            anim.setDuration(250)
            anim.setStartValue(0)
            anim.setEndValue(2000)
            anim.setEasingCurve(QEasingCurve.OutCubic)
            anim.start()
            self._log_anim = anim
        else:
            anim = QPropertyAnimation(self.log_output, b"maximumHeight")
            anim.setDuration(250)
            anim.setStartValue(self.log_output.height())
            anim.setEndValue(0)
            anim.setEasingCurve(QEasingCurve.OutCubic)
            anim.finished.connect(lambda: self.log_output.setVisible(False))
            anim.start()
            self._log_anim = anim

    def _toggle_mods_panel(self, visible):
        if visible:
            self.mods_panel.setMaximumHeight(0)
            self.mods_panel.setVisible(True)
            anim = QPropertyAnimation(self.mods_panel, b"maximumHeight")
            anim.setDuration(250)
            anim.setStartValue(0)
            anim.setEndValue(2000)
            anim.setEasingCurve(QEasingCurve.OutCubic)
            anim.start()
            self._mods_anim = anim
            self._refresh_mods()
        else:
            anim = QPropertyAnimation(self.mods_panel, b"maximumHeight")
            anim.setDuration(250)
            anim.setStartValue(self.mods_panel.height())
            anim.setEndValue(0)
            anim.setEasingCurve(QEasingCurve.OutCubic)
            anim.finished.connect(lambda: self.mods_panel.setVisible(False))
            anim.start()
            self._mods_anim = anim

    def _refresh_mods(self):
        base_dir = self.base_dir_edit.text().strip()
        if not base_dir:
            return
        mods_dir = Path(base_dir) / "mods"
        mods_dir.mkdir(parents=True, exist_ok=True)

        self.mods_list.blockSignals(True)
        self.mods_list.clear()

        jars = sorted(mods_dir.glob("*.jar"))
        disabled = sorted(mods_dir.glob("*.jar.disabled"))

        if not jars and not disabled:
            item = QListWidgetItem("No mods found")
            item.setFlags(Qt.NoItemFlags)
            self.mods_list.addItem(item)
        else:
            for jar in jars:
                item = QListWidgetItem(jar.stem)
                item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsUserCheckable)
                item.setCheckState(Qt.Checked)
                item.setData(Qt.UserRole, str(jar))
                self.mods_list.addItem(item)
            for jar in disabled:
                name = jar.name.removesuffix(".jar.disabled")
                item = QListWidgetItem(name)
                item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsUserCheckable)
                item.setCheckState(Qt.Unchecked)
                item.setData(Qt.UserRole, str(jar))
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

        args = [sys.executable, str(self.downloader_path), version_id, "--base-dir", base_dir]
        if self.no_assets_check.isChecked():
            args.append("--no-assets")
        if self.include_server_check.isChecked():
            args.append("--include-server")
        if self.include_mappings_check.isChecked():
            args.append("--include-mappings")
        if self.verify_check.isChecked():
            args.append("--verify")

        # Auto-show logs during download
        self.log_toggle.setChecked(True)
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

        args = [sys.executable, str(self.launcher_path), version_id, "--base-dir", base_dir]

        game_dir = self.game_dir_edit.text().strip()
        if game_dir:
            args += ["--game-dir", game_dir]
        java_path = self.java_edit.text().strip()
        if java_path:
            args += ["--java", java_path]
        username = self.username_edit.text().strip()
        if username:
            args += ["--username", username]

        ram = self._get_ram()
        args += ["--xmx", ram]

        if self.xms_edit.text().strip():
            args += ["--xms", self.xms_edit.text().strip()]
        if self.xss_edit.text().strip():
            args += ["--xss", self.xss_edit.text().strip()]
        if self.width_spin.value() > 0 and self.height_spin.value() > 0:
            args += ["--width", str(self.width_spin.value())]
            args += ["--height", str(self.height_spin.value())]
        if self.demo_check.isChecked():
            args.append("--demo")
        if self.dry_run_check.isChecked():
            args.append("--dry-run")
        if not self.official_flags_check.isChecked():
            args.append("--no-official-jvm-flags")

        self.start_main_process(args, f"Launching {version_id}...")

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

        args = [sys.executable, str(self.server_downloader_path), version_id, "--servers-dir", servers_dir]
        if self.verify_check.isChecked():
            args.append("--verify")
        if self.include_mappings_check.isChecked():
            args.append("--include-mappings")

        self.log_toggle.setChecked(True)
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

        args = [
            sys.executable,
            str(self.server_launcher_path),
            version_id,
            "--servers-dir", servers_dir,
            "--minecraft-dir", base_dir,
        ]
        java_path = self.java_edit.text().strip()
        if java_path:
            args += ["--java", java_path]
        ram = self._get_ram()
        args += ["--xmx", ram]
        if self.xms_edit.text().strip():
            args += ["--xms", self.xms_edit.text().strip()]
        if self.server_accept_eula_check.isChecked():
            args.append("--accept-eula")
        if self.server_gui_check.isChecked():
            args.append("--gui")
        if self.server_restart_check.isChecked():
            args.append("--restart-if-running")
        if self.server_offline_check.isChecked():
            args.append("--offline-mode")
        if self.dry_run_check.isChecked():
            args.append("--dry-run")

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
