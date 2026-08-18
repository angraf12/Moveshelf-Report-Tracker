"""Build the therapist-facing documents for distribution.

    python docs/build_docs.py [output folder]

Takes the tracked sources in ``docs/`` and writes finished copies into the folder
you hand out. Without an argument that folder is ``ReportTracker-Release`` in
your home directory, or ``REPORT_TRACKER_STAGING`` if it is set. Two things
happen:

**Screenshots are embedded.** The pictures go into the HTML as base64, so the
guide stays a single file: nothing to copy alongside it, and no broken image
icons when someone opens it from a network folder. Put them here, PNG or JPG::

    docs/screenshots/profile-menu.png   the Moveshelf bar with the profile menu
                                        open, showing SETTINGS
    docs/screenshots/api-keys.png       the User settings page showing the
                                        API Keys panel

**Site details are filled in.** The tracked sources contain placeholders such as
``{{APP_FOLDER}}`` rather than a real server path, because this repository is
public. Real values live in ``docs/site.local.json``, which is gitignored. Copy
``docs/site.example.json`` to it and fill it in. Without that file the documents
still build, with readable generic wording instead.

The sources are never modified, so this can be re-run whenever a screenshot or a
site detail changes.

SECURITY: do not photograph a generated API key. The long string Moveshelf shows
after "Generate API Key" is a live credential. Take the API Keys screenshot
before generating one, or after the value has left the screen.
"""
from __future__ import annotations

import base64
import json
import mimetypes
import os
import re
import sys
from pathlib import Path
from typing import Dict, Optional

HERE = Path(__file__).resolve().parent
SHOTS = HERE / "screenshots"
SITE_LOCAL = HERE / "site.local.json"

# Same rule as the placeholders above: this repository is public, so no
# maintainer's home directory is written down here. release.py passes its own
# staging folder as argv[1], so this default only applies to a standalone run.
DEFAULT_OUT = Path(
    os.environ.get("REPORT_TRACKER_STAGING") or Path.home() / "ReportTracker-Release"
)

# source file -> name it is given in the distributed folder
DOCUMENTS = {
    "setup_guide.html": "Setup Guide.html",
    "READ ME FIRST.txt": "READ ME FIRST.txt",
}

SLOTS = {
    "profile-menu": "The profile symbol is on the blue Moveshelf bar, not in your browser.",
    "api-keys": "The API Keys panel, near the bottom of the User settings page.",
}

# Used when site.local.json is absent, so a fresh clone still builds something
# sensible rather than leaving raw {{PLACEHOLDERS}} on the page.
FALLBACKS = {
    "APP_FOLDER": r"<the ReportTracker folder your gait lab provides>",
    "CONTACT": "Contact your gait lab systems engineer.",
}

SUFFIXES = (".png", ".jpg", ".jpeg")
PLACEHOLDER = re.compile(r"\{\{([A-Z_]+)\}\}")


def load_site() -> Dict[str, str]:
    """Real site values, or the generic fallbacks when none are configured."""
    values = dict(FALLBACKS)
    if not SITE_LOCAL.is_file():
        print(f"  no {SITE_LOCAL.name}; using generic wording")
        return values
    try:
        data = json.loads(SITE_LOCAL.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"  ! {SITE_LOCAL.name} could not be read ({exc}); using generic wording")
        return values
    for key, value in data.items():
        if not key.startswith("_") and isinstance(value, str):
            values[key] = value
    print(f"  site details from {SITE_LOCAL.name}")
    return values


def find_image(name: str) -> Optional[Path]:
    for suffix in SUFFIXES:
        candidate = SHOTS / f"{name}{suffix}"
        if candidate.is_file():
            return candidate
    return None


def as_data_uri(path: Path) -> str:
    kind = mimetypes.guess_type(path.name)[0] or "image/png"
    return f"data:{kind};base64,{base64.b64encode(path.read_bytes()).decode('ascii')}"


def embed_screenshots(html: str) -> tuple[str, list[str]]:
    missing = []
    for name, caption in SLOTS.items():
        marker = f'<figure class="shot empty" id="shot-{name}">'
        if marker not in html:
            continue
        image = find_image(name)
        if image is None:
            missing.append(name)
            continue
        start = html.index(marker)
        end = html.index("</figure>", start) + len("</figure>")
        html = html[:start] + (
            f'<figure class="shot" id="shot-{name}">'
            f'<img alt="{caption}" src="{as_data_uri(image)}">'
            f"<figcaption>{caption}</figcaption></figure>"
        ) + html[end:]
        print(f"  + {name:<14} {image.name} ({image.stat().st_size / 1024:,.0f} KB)")
    return html, missing


def fill_placeholders(text: str, values: Dict[str, str]) -> tuple[str, set]:
    unknown = set()

    def swap(match: "re.Match[str]") -> str:
        key = match.group(1)
        if key not in values:
            unknown.add(key)
            return match.group(0)
        return values[key]

    return PLACEHOLDER.sub(swap, text), unknown


def main() -> int:
    out_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_OUT
    print(f"Building into {out_dir}")
    values = load_site()

    problems = []
    for source_name, out_name in DOCUMENTS.items():
        source = HERE / source_name
        if not source.is_file():
            print(f"  ! missing source {source}")
            problems.append(source_name)
            continue

        text = source.read_text(encoding="utf-8")
        if source.suffix == ".html":
            text, missing = embed_screenshots(text)
            problems.extend(f"screenshot {n}" for n in missing)
        text, unknown = fill_placeholders(text, values)
        problems.extend(f"unknown placeholder {{{{{k}}}}}" for k in unknown)

        try:
            out_dir.mkdir(parents=True, exist_ok=True)
            (out_dir / out_name).write_text(text, encoding="utf-8")
        except OSError as exc:
            print(f"  ! could not write {out_name}: {exc}")
            problems.append(out_name)
            continue
        size = (out_dir / out_name).stat().st_size / 1024
        print(f"  wrote {out_name}  ({size:,.0f} KB)")

    if problems:
        print("\nUnfinished:")
        for item in problems:
            print(f"  - {item}")
        return 1
    print("\nDone. Copy the folder contents to the distribution folder.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
