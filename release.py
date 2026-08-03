"""Build and check everything that goes out to users.

    python release.py            build and verify, then print what to do next
    python release.py --check    verify only, change nothing

Runs the full sequence in order and stops at the first failure:

    1. the test suite
    2. the executable
    3. the therapist-facing documents
    4. checks on what was produced

Step 4 exists because of a real mistake: the setup guide was once copied to the
distribution folder straight from ``docs/``, which is a *template* full of
``{{APP_FOLDER}}`` placeholders and empty screenshot boxes. It looked fine in the
folder listing and was wrong for every user. These checks would have caught it,
so nothing reaches the distribution folder without passing them.

This does not copy anything to the distribution folder or push to GitHub. Those are
deliberate, outward-facing steps; see RELEASE.md.
"""
from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
STAGING = Path(r"C:\Users\agraf\ReportTracker-Release")

EXPECTED = ["MoveshelfReportTracker.exe", "Setup Guide.html",
            "READ ME FIRST.txt", "holidays.txt.example"]


def run(label: str, args: list[str]) -> bool:
    print(f"\n=== {label} ===")
    result = subprocess.run(args, cwd=ROOT)
    if result.returncode != 0:
        print(f"FAILED: {label}")
        return False
    return True


def check_outputs() -> list[str]:
    """Everything that must be true of the folder before it is handed out."""
    problems: list[str] = []

    for name in EXPECTED:
        if not (STAGING / name).is_file():
            problems.append(f"missing from the staging folder: {name}")

    guide = STAGING / "Setup Guide.html"
    if guide.is_file():
        html = guide.read_text(encoding="utf-8", errors="replace")

        # The mistake this whole function exists to prevent.
        left = sorted(set(re.findall(r"\{\{[A-Z_]+\}\}", html)))
        if left:
            problems.append(
                f"guide still has unfilled placeholders {left}: it looks like the "
                f"template from docs/ was copied instead of the built version"
            )
        if "shot empty" in html:
            problems.append("guide has an empty screenshot box")
        if html.count("data:image") < 2:
            problems.append(
                f"guide has {html.count('data:image')} embedded screenshots, expected 2"
            )
        if len(html) < 30_000:
            problems.append(
                f"guide is only {len(html):,} bytes; with screenshots it should be "
                f"around 47,000. Probably the unbuilt template."
            )

    readme = STAGING / "READ ME FIRST.txt"
    if readme.is_file():
        text = readme.read_text(encoding="utf-8", errors="replace")
        left = sorted(set(re.findall(r"\{\{[A-Z_]+\}\}", text)))
        if left:
            problems.append(f"READ ME FIRST still has placeholders {left}")

    exe = STAGING / "MoveshelfReportTracker.exe"
    built = ROOT / "dist" / "MoveshelfReportTracker.exe"
    if exe.is_file() and built.is_file():
        if exe.read_bytes() != built.read_bytes():
            problems.append(
                "the staged .exe differs from dist/: it was not refreshed by this build"
            )

    # A key must never be sitting in the folder about to be handed out.
    for stray in STAGING.glob("*"):
        low = stray.name.lower()
        if "api" in low and "key" in low or low.endswith((".dat", ".key")):
            problems.append(f"CREDENTIAL in the staging folder: {stray.name}")
    for stray in ("settings.json", "therapists.csv", "logs"):
        if (STAGING / stray).exists():
            problems.append(f"personal runtime file in the staging folder: {stray}")

    return problems


def version() -> str:
    text = (ROOT / "tracker" / "__init__.py").read_text(encoding="utf-8")
    match = re.search(r'__version__\s*=\s*"([^"]+)"', text)
    return match.group(1) if match else "unknown"


def main() -> int:
    check_only = "--check" in sys.argv
    print(f"Report Tracker {version()}")

    if not check_only:
        if not run("tests", [sys.executable, "-m", "pytest", "tests/", "-q"]):
            return 1
        if not run("executable", [sys.executable, "build.py"]):
            return 1
        if not run("documents", [sys.executable, "docs/build_docs.py"]):
            return 1

        # build.py writes to dist/; build_docs.py writes the documents. Nothing
        # else puts the executable where it gets handed out, and the check below
        # is what noticed.
        print("\n=== staging the executable ===")
        built = ROOT / "dist" / "MoveshelfReportTracker.exe"
        try:
            STAGING.mkdir(parents=True, exist_ok=True)
            shutil.copy2(built, STAGING / built.name)
            print(f"  copied {built.name} -> {STAGING}")
        except OSError as exc:
            print(f"FAILED to stage the executable: {exc}")
            return 1

    print("\n=== checking what will be handed out ===")
    problems = check_outputs()
    if problems:
        for problem in problems:
            print(f"  PROBLEM: {problem}")
        print("\nDo not distribute this.")
        return 1

    for name in EXPECTED:
        size = (STAGING / name).stat().st_size
        print(f"  OK  {name:<30} {size:>10,} bytes")

    print(f"\nEverything checks out. Staging folder:\n  {STAGING}")
    print("\nNext, from RELEASE.md:")
    print("  1. Copy those four files to the distribution folder.")
    print("  2. Commit and push to GitHub.")
    print("  3. Tell users what changed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
