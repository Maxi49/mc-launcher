"""Build script — run with: python build.py"""
import sys
import PyInstaller.__main__

sep = ";" if sys.platform == "win32" else ":"
if sys.platform == "win32":
    name = "launcher-windows"
elif sys.platform == "darwin":
    name = "launcher-macos"
else:
    name = "launcher-linux"

PyInstaller.__main__.run([
    "launcher_ui.py",
    "--onefile",
    "--windowed",
    f"--name={name}",
    f"--add-data=scripts{sep}scripts",
    f"--add-data=mc_common.py{sep}.",
    f"--add-data=version.py{sep}.",
    f"--add-data=core{sep}core",
    f"--add-data=ui{sep}ui",
    "--noconfirm",
    "--clean",
])
