"""Build the single-file Windows executable.

    python build.py

Produces ``dist/MoveshelfReportTracker.exe``. Drop it in a folder next to the
user's ``mvshlf-api-key.json`` and double-click.

Notes for whoever maintains this:

- ``tracker/web`` must be bundled as *data*, not code. PyInstaller will not pick
  up HTML, CSS and JS on its own. ``server.web_dir()` already looks in
  ``sys._MEIPASS/web`` when frozen, so the destination name has to stay ``web``.
- The console window is deliberately kept. It is where a startup failure prints
  a readable message, and where the local address appears if the browser does
  not open on its own. A windowed build turns any early failure into nothing
  happening at all, which is the worst thing to hand a non-technical user.
- The result is unsigned. On managed hospital machines SmartScreen or antivirus
  may block it outright, and that is the single largest deployment risk for this
  project. Test on a machine that is not the build machine before promising
  anyone a date.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
NAME = "MoveshelfReportTracker"


def main() -> int:
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        print("PyInstaller is not installed. Run:  pip install pyinstaller")
        return 1

    web = ROOT / "tracker" / "web"
    if not (web / "index.html").is_file():
        print(f"Cannot find the web assets at {web}")
        return 1

    # PyInstaller separates source from destination with ';' on Windows.
    separator = ";" if sys.platform == "win32" else ":"

    for stale in (ROOT / "build", ROOT / "dist"):
        if stale.exists():
            shutil.rmtree(stale, ignore_errors=True)

    command = [
        sys.executable, "-m", "PyInstaller",
        "--onefile",
        "--name", NAME,
        "--add-data", f"{web}{separator}web",
        "--console",
        "--noconfirm",
        "--clean",
        str(ROOT / "main.py"),
    ]
    print("Running:\n  " + " ".join(command) + "\n")
    result = subprocess.run(command, cwd=ROOT)
    if result.returncode != 0:
        return result.returncode

    exe = ROOT / "dist" / f"{NAME}.exe"
    if not exe.is_file():
        print(f"Build reported success but {exe} is missing")
        return 1

    size_mb = exe.stat().st_size / (1024 * 1024)
    print(f"\nBuilt {exe}  ({size_mb:.1f} MB)")
    print("\nNext:")
    print("  1. Copy it into a folder holding mvshlf-api-key.json.")
    print("  2. Test it on a machine WITHOUT Python installed.")
    print("  3. Expect a SmartScreen warning: it is unsigned.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
