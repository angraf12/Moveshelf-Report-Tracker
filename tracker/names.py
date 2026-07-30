"""Therapist name normalizing.

Live Moveshelf data spells the same therapist several ways. On one CHI-Gait
sample, "Dawson, Renata, MPT" appeared 26 times and "Dawson, Renata, PT, MPT"
9 times, for one person. A naive "my sessions only" filter would have hidden a
quarter of that therapist's workload, which is the exact failure this whole
application exists to prevent, so the filter must never match on the raw string.

The approach mirrors the research application's surgeon normalizer: a small
user-editable CSV maps raw spellings to a canonical name, generated pre-filled
with a best guess so the therapist only has to correct it. A blank or missing
canonical name means pass the raw value through unchanged, so the file is always
safe to leave half-finished.
"""
from __future__ import annotations

import csv
from pathlib import Path
from typing import Dict, Iterable, List, Optional

# Credential tokens stripped when guessing a canonical name. Compared case-
# insensitively with punctuation removed, so "PT", "pt" and "P.T." all match.
_CREDENTIALS = frozenset(
    {
        "pt", "dpt", "mpt", "mspt", "spt", "pta",
        "ot", "otr", "otrl", "cota",
        "ms", "ma", "mba", "med", "msc", "bs", "ba",
        "phd", "edd", "dsc", "scd",
        "md", "do", "np", "pa", "pac", "rn", "bsn", "msn",
        "pcs", "ocs", "ncs", "gcs", "scs", "cscs", "atc", "cpo", "faapmr",
    }
)


def _is_credential(part: str) -> bool:
    """Is this comma-separated part purely a credential such as "PT, PCS"?"""
    tokens = [t for t in part.replace(".", " ").replace("-", " ").split() if t]
    if not tokens:
        return True
    return all(t.lower() in _CREDENTIALS for t in tokens)


def _strip_trailing_credentials(part: str) -> str:
    """Drop credential words from the end of a part, e.g. "Renata MPT"."""
    tokens = part.split()
    while tokens and tokens[-1].replace(".", "").lower() in _CREDENTIALS:
        tokens.pop()
    return " ".join(tokens)


def canonical_guess(raw: str) -> str:
    """Best guess at a canonical "Last, First" for a raw therapist string.

    Handles the shapes seen in live data ("Dawson, Renata, MPT",
    "Whitfield, Marlo, PT, PCS", "Marsden, Delia, PT, MPT, PCS") by dropping
    credential-only segments and any trailing credential words.

    Args:
        raw: The raw ``sessioninfo-therapist`` value.

    Returns:
        The guessed canonical name, or the trimmed input if no confident guess is
        possible. Never raises and never returns an empty string for non-empty
        input, because silently losing a name is worse than a mediocre guess.
    """
    text = " ".join((raw or "").split())
    if not text:
        return ""

    parts = [p.strip() for p in text.split(",")]
    kept = [_strip_trailing_credentials(p) for p in parts if not _is_credential(p)]
    kept = [p for p in kept if p]

    if not kept:
        return text
    if len(kept) == 1:
        return kept[0]
    return f"{kept[0]}, {kept[1]}"


def load_therapist_map(path: Optional[Path]) -> Dict[str, str]:
    """Load the raw-to-canonical therapist mapping.

    Expects a CSV with ``raw_name`` and ``canonical_name`` columns. Rows with a
    blank canonical name are skipped, which makes them pass through unchanged.

    Args:
        path: Path to ``therapists.csv``, or None.

    Returns:
        Mapping from raw name to canonical name. Empty if the file is missing or
        unreadable, which degrades to using raw names.
    """
    if path is None:
        return {}
    try:
        text = Path(path).read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError):
        return {}

    # The generated template starts with explanatory "#" lines, which would
    # otherwise be read as the header row.
    lines = [ln for ln in text.splitlines() if not ln.lstrip().startswith("#")]

    mapping: Dict[str, str] = {}
    try:
        for row in csv.DictReader(lines):
            raw = (row.get("raw_name") or "").strip()
            canonical = (row.get("canonical_name") or "").strip()
            if raw and canonical:
                mapping[raw] = canonical
    except csv.Error:
        return mapping
    return mapping


def normalize(raw: str, mapping: Optional[Dict[str, str]] = None) -> str:
    """Resolve a raw therapist name to its canonical form.

    Args:
        raw: Raw name from session metadata.
        mapping: Loaded ``therapists.csv`` mapping. An explicit entry always wins
            over the automatic guess, so the therapist can override it.

    Returns:
        The canonical name.
    """
    text = " ".join((raw or "").split())
    if not text:
        return ""
    if mapping:
        mapped = mapping.get(text)
        if mapped:
            return mapped
    return canonical_guess(text)


def build_mapping_rows(raw_names: Iterable[str]) -> List[Dict[str, str]]:
    """Build pre-filled mapping rows for every distinct raw name, sorted.

    Sorting by the guessed canonical name groups the variants of one person
    together, so the therapist can see at a glance what will be merged.
    """
    distinct = sorted({" ".join((n or "").split()) for n in raw_names if n and n.strip()})
    rows = [{"raw_name": n, "canonical_name": canonical_guess(n)} for n in distinct]
    return sorted(rows, key=lambda r: (r["canonical_name"].lower(), r["raw_name"].lower()))


def write_mapping_template(path: Path, raw_names: Iterable[str]) -> bool:
    """Write ``therapists.csv`` pre-filled with a guess for each raw name.

    Never overwrites an existing file, so a therapist's corrections survive every
    later run.

    Args:
        path: Destination path.
        raw_names: Every raw therapist string seen in the fetched sessions.

    Returns:
        True if a file was written, False if one already existed or the write
        failed. Failure is not an error: the app falls back to guessing.
    """
    path = Path(path)
    if path.exists():
        return False
    rows = build_mapping_rows(raw_names)
    if not rows:
        return False
    try:
        with path.open("w", encoding="utf-8", newline="") as handle:
            handle.write(
                "# Merge different spellings of the same person by giving them the\n"
                "# same canonical_name. Leave canonical_name blank to use the raw\n"
                "# name unchanged. This file is never overwritten once it exists.\n"
            )
            writer = csv.DictWriter(handle, fieldnames=["raw_name", "canonical_name"])
            writer.writeheader()
            writer.writerows(rows)
    except OSError:
        return False
    return True


__all__ = [
    "build_mapping_rows",
    "canonical_guess",
    "load_therapist_map",
    "normalize",
    "write_mapping_template",
]
