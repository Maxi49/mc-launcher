"""Platform abstraction layer — centralizes all sys.platform checks."""

import os
import subprocess
import sys


def is_windows():
    return sys.platform == "win32"


def is_macos():
    return sys.platform == "darwin"


def is_linux():
    return sys.platform not in ("win32", "darwin")


def open_folder(path):
    """Open a folder in the system file manager."""
    path = str(path)
    if is_windows():
        os.startfile(path)
    elif is_macos():
        subprocess.Popen(["open", path])
    else:
        subprocess.Popen(["xdg-open", path])


def kill_process_tree(pid):
    """Kill a process and all its children.

    On Windows uses taskkill /T /F (needed because console processes
    ignore WM_CLOSE sent by QProcess.terminate()).
    On Unix sends SIGTERM.
    Returns True if the command succeeded.
    """
    if is_windows():
        proc = subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            capture_output=True,
            text=True,
        )
    else:
        proc = subprocess.run(
            ["kill", "-TERM", str(pid)],
            capture_output=True,
            text=True,
        )
    return proc.returncode == 0


def forge_run_script_name():
    """Return the Forge run script name for the current platform."""
    return "run.bat" if is_windows() else "run.sh"


def java_executable_names():
    """Return the Java executable names to search for on this platform."""
    if is_windows():
        return ("javaw.exe", "java.exe", "javaw", "java")
    return ("java",)
