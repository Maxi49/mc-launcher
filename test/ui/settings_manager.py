"""Settings persistence for the launcher."""

import json
from pathlib import Path


class SettingsManager:
    """Load and save launcher settings from/to a JSON file."""

    def __init__(self, settings_file):
        self._file = Path(settings_file)
        self._data = {}

    def load(self):
        """Load settings from disk. Returns the settings dict."""
        if self._file.exists():
            try:
                self._data = json.loads(self._file.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                self._data = {}
        else:
            self._data = {}
        return self._data

    def save(self, data=None):
        """Save settings to disk."""
        if data is not None:
            self._data = data
        self._file.write_text(
            json.dumps(self._data, ensure_ascii=True, indent=2), encoding="utf-8"
        )

    @property
    def data(self):
        return self._data
