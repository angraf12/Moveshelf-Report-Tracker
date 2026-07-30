"""Locating and loading everything that lives next to the executable.

The app is a single file a therapist drops in a folder, so every runtime file is
found relative to that folder rather than a package directory or the working
directory (which is whatever Explorer felt like when the exe was double-clicked).

Credential handling, in order of preference:

1. ``api_key.dat``  -- the key encrypted with Windows DPAPI, readable only by the
   Windows account that wrote it. This is what the app converts to on first run.
2. ``mvshlf-api-key.json`` -- the file Moveshelf hands you when you click
   Generate API Key. Confirmed format: ``{"application": ..., "secretKey": ...}``.
3. ``api_key.txt`` -- a fallback for people who paste the raw key into a text
   file, which they will, whatever the instructions say.

All three are accepted because the alternative is a support call. Whichever is
found gets normalized to the same in-memory dict.
"""
from __future__ import annotations

import base64
import json
import logging
import os
import re
import sys
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

logger = logging.getLogger(__name__)

# Confirmed live 2026-07-27: Shriners is on the US regional endpoint. The
# SDK's own default (api.moveshelf.com) is the wrong host for this deployment.
DEFAULT_API_URL = "https://api.us.moveshelf.com/graphql"

# Where a session link points. This is the web app, not the API host.
DEFAULT_SITE_URL = "https://shriners.moveshelf.com/"

# Route to a patient's page. Verified against the live web app on 2026-07-28:
# clicking a name opens the correct patient.
#
# It stays a setting rather than becoming a constant, because unlike the session
# route it is not documented in the SDK and is therefore not guaranteed stable.
# If Moveshelf ever changes it, this is a one-line fix in settings.json rather
# than a new build for every user. Set it to "" to turn subject links off.
DEFAULT_SUBJECT_URL_TEMPLATE = "{site}/project/{project}/subject/{patient}"

DEFAULT_LOOKBACK_DAYS = 90

# Referral types that do not produce a PT report, so their sessions never count
# as overdue. Confirmed with the clinical lead on 2026-07-27 and corroborated by
# completion rates at CHI-Gait over 365 days (sessions lacking a PT evaluation
# date): Video only 24/33, Video & Pedobarograph only 40/44, Research 30/31,
# Isokinetic 8/10, Foot analysis 4/5.
#
# This is only a starting point. It seeds the tick boxes on first run so nobody
# has to configure eight of them by hand, and it is overwritten the moment a user
# changes the panel. Referral vocabularies differ by site, so nothing here is
# assumed to be true anywhere else.
#
# Deliberately NOT excluded, per the same conversation: "Kinematics gait
# analysis", "Kinematics/video sports analysis (97750)" and "Gait PT eval without
# kinematics" all require a report.
DEFAULT_NO_REPORT_REFERRAL_TYPES = [
    "Video only",
    "Video & Pedobarograph only",
    "Foot analysis (97750)",
    "Research (grant reimbursed)- 1 hr",
    "Research (not reimbursed)- 1 hr",
    "Research (not reimbursed)- 2 hr",
    "Isokinetic",
    "Competencies",
]

KEY_ENCRYPTED = "api_key.dat"
KEY_JSON = "mvshlf-api-key.json"
KEY_TEXT = "api_key.txt"
SETTINGS_FILE = "settings.json"
HOLIDAYS_FILE = "holidays.txt"
THERAPISTS_FILE = "therapists.csv"
LOG_DIR = "logs"

# A Moveshelf secret key is 64 hex characters.
_RAW_KEY_PATTERN = re.compile(r"^[0-9a-fA-F]{64}$")


class ConfigError(Exception):
    """Raised when the app cannot start, with a message meant for a therapist."""


def app_dir() -> Path:
    """The folder holding the executable, or the repo root when run from source."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


DRIVE_REMOTE = 4


def is_network_location(folder: Path) -> bool:
    """Is this folder a UNC network path or a mapped network drive?

    Someone will inevitably run the app straight from a network folder.
    Everything it stores would then land in that folder,
    including ``api_key.dat``. Detecting it lets the app stop before writing
    anything at all.

    Returns False on any uncertainty. A false positive would block a legitimate
    setup, and some sites redirect home folders to a server, so this only matches
    the two unambiguous cases: a UNC path, and a drive letter Windows itself
    reports as remote.
    """
    # Test the path as given as well as resolved. resolve() can rewrite an
    # unreachable UNC path into something that no longer looks like one, and a
    # missed network path is the case that matters.
    candidates = [Path(folder)]
    try:
        candidates.append(Path(folder).resolve())
    except (OSError, ValueError):
        pass

    for candidate in candidates:
        text = str(candidate)
        if text.startswith("\\\\") or text.startswith("//"):
            return True
        if str(candidate.drive).startswith("\\\\"):
            return True

    resolved = candidates[-1]
    if sys.platform != "win32":
        return False

    drive = resolved.drive
    if not drive or not drive.endswith(":"):
        return False
    try:
        import ctypes

        return ctypes.windll.kernel32.GetDriveTypeW(f"{drive}\\") == DRIVE_REMOTE
    except (OSError, AttributeError, ValueError):
        return False


NETWORK_LOCATION_MESSAGE = (
    "Report Tracker is running from a network folder:\n\n"
    "    {folder}\n\n"
    "It will not run from here, because everything it saves would go into that "
    "network folder, including your Moveshelf API key. Your key is a password "
    "that can read patient information, and it must never sit on a drive other "
    "people can open.\n\n"
    "To fix this, copy MoveshelfReportTracker.exe to a folder on your own "
    "computer, for example C:\\MoveshelfReportTracker, and run it from there."
)


# --------------------------------------------------------------------------
# Windows DPAPI. Encrypts at rest so a copied file is useless on another
# machine or under another Windows account.
# --------------------------------------------------------------------------

def _dpapi_available() -> bool:
    return sys.platform == "win32"


def _dpapi(encrypt: bool, data: bytes) -> Optional[bytes]:
    """Call CryptProtectData or CryptUnprotectData. Returns None on any failure."""
    if not _dpapi_available():
        return None
    try:
        import ctypes
        from ctypes import wintypes

        class Blob(ctypes.Structure):
            _fields_ = [("cbData", wintypes.DWORD),
                        ("pbData", ctypes.POINTER(ctypes.c_char))]

        def to_blob(raw: bytes) -> Blob:
            buf = ctypes.create_string_buffer(raw, len(raw))
            return Blob(len(raw), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))

        crypt32 = ctypes.windll.crypt32
        func = crypt32.CryptProtectData if encrypt else crypt32.CryptUnprotectData
        source, result = to_blob(data), Blob()
        ok = func(
            ctypes.byref(source), None, None, None, None, 0, ctypes.byref(result)
        )
        if not ok:
            return None
        try:
            return ctypes.string_at(result.pbData, result.cbData)
        finally:
            ctypes.windll.kernel32.LocalFree(result.pbData)
    except (OSError, AttributeError, ValueError) as exc:
        logger.warning("DPAPI unavailable (%s); key will not be encrypted", exc)
        return None


def encrypt_key(payload: Dict[str, Any]) -> Optional[str]:
    """DPAPI-encrypt a key payload to base64 text, or None if unavailable."""
    blob = _dpapi(True, json.dumps(payload).encode("utf-8"))
    return base64.b64encode(blob).decode("ascii") if blob else None


def decrypt_key(text: str) -> Optional[Dict[str, Any]]:
    """Reverse ``encrypt_key``. Returns None if it cannot be decrypted here."""
    try:
        blob = base64.b64decode(text.strip().encode("ascii"), validate=True)
    except (ValueError, TypeError):
        return None
    raw = _dpapi(False, blob)
    if not raw:
        return None
    try:
        parsed = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None
    return parsed if isinstance(parsed, dict) else None


# --------------------------------------------------------------------------
# Finding and normalizing the key
# --------------------------------------------------------------------------

def _normalize_key_payload(text: str) -> Optional[Dict[str, Any]]:
    """Accept either the JSON Moveshelf provides or a bare pasted key."""
    stripped = text.strip()
    if not stripped:
        return None

    if stripped.startswith("{"):
        try:
            parsed = json.loads(stripped)
        except ValueError:
            return None
        if isinstance(parsed, dict) and parsed.get("secretKey"):
            return {
                "secretKey": str(parsed["secretKey"]).strip(),
                "application": str(parsed.get("application") or "ReportTracker"),
            }
        return None

    first = stripped.splitlines()[0].strip().strip('"').strip("'")
    if _RAW_KEY_PATTERN.match(first):
        return {"secretKey": first, "application": "ReportTracker"}
    return None


def load_api_key(folder: Optional[Path] = None) -> Dict[str, Any]:
    """Find and load the API key from the app folder.

    Returns:
        Dict with ``secretKey`` and ``application``.

    Raises:
        ConfigError: With a message written for a therapist, not a developer,
            naming the folder that was searched and what to put in it.
    """
    folder = Path(folder) if folder else app_dir()

    encrypted = folder / KEY_ENCRYPTED
    if encrypted.is_file():
        try:
            payload = decrypt_key(encrypted.read_text(encoding="utf-8"))
        except OSError:
            payload = None
        if payload and payload.get("secretKey"):
            return payload
        raise ConfigError(
            f"Your saved API key in {encrypted.name} could not be read. This "
            f"normally means it was created by a different Windows user or on a "
            f"different computer. Delete {encrypted.name} and save your key "
            f"again as {KEY_JSON}."
        )

    for name in (KEY_JSON, KEY_TEXT):
        candidate = folder / name
        if not candidate.is_file():
            continue
        try:
            payload = _normalize_key_payload(candidate.read_text(encoding="utf-8-sig"))
        except (OSError, UnicodeDecodeError) as exc:
            raise ConfigError(f"Could not read {candidate.name}: {exc}") from exc
        if payload:
            return payload
        raise ConfigError(
            f"{candidate.name} does not contain a valid API key. Make sure you "
            f"copied the key itself and not the Application ID. The key is 64 "
            f"characters long. Easiest fix: generate a new key in Moveshelf and "
            f"save the file it gives you into this folder as {KEY_JSON}."
        )

    raise ConfigError(
        f"No API key found in {folder}.\n\n"
        f"To fix this: log in to Moveshelf, click your picture at the top right, "
        f"choose User settings, scroll to API Keys, type ReportTracker as the "
        f"Application ID and click Generate API Key. Save the file it gives you "
        f"into this folder as {KEY_JSON}."
    )


def store_key_encrypted(payload: Dict[str, Any], folder: Optional[Path] = None) -> bool:
    """Encrypt the key at rest and remove the plain text copies.

    Best effort. If DPAPI is unavailable the plain files are left exactly as they
    are, because a working app with a plain key beats a broken app.

    Returns:
        True if the key was encrypted and the plain copies removed.
    """
    folder = Path(folder) if folder else app_dir()
    encoded = encrypt_key(payload)
    if not encoded:
        return False
    try:
        (folder / KEY_ENCRYPTED).write_text(encoded, encoding="utf-8")
    except OSError as exc:
        logger.warning("Could not write encrypted key: %s", exc)
        return False

    for name in (KEY_JSON, KEY_TEXT):
        try:
            (folder / name).unlink(missing_ok=True)
        except OSError as exc:
            logger.warning("Could not remove plain key file %s: %s", name, exc)
    return True


@contextmanager
def temporary_key_file(payload: Dict[str, Any]) -> Iterator[str]:
    """Yield a short-lived key file path for the Moveshelf SDK.

    The SDK only accepts a *path* to a JSON file, so an encrypted-at-rest key has
    to touch disk briefly to be usable at all. The file is created in the user's
    own temp directory with owner-only permissions and deleted in a ``finally``,
    so it exists for roughly the duration of one constructor call.

    This is a deliberate, documented tradeoff rather than an oversight: without
    it there is no way to use the SDK at all, and the alternative is leaving the
    key in plain text permanently.
    """
    handle, path = tempfile.mkstemp(prefix="mvshlf_", suffix=".json")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as fh:
            json.dump(payload, fh)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
        yield path
    finally:
        try:
            os.unlink(path)
        except OSError as exc:
            logger.warning("Could not remove temporary key file: %s", exc)


# --------------------------------------------------------------------------
# Settings
# --------------------------------------------------------------------------

@dataclass
class Settings:
    """Per-user choices, persisted next to the executable."""

    project_id: str = ""
    project_name: str = ""
    lookback_days: int = DEFAULT_LOOKBACK_DAYS
    my_therapist: str = ""
    mine_only: bool = False
    backlog_enabled: bool = True
    backlog_days: int = 30
    # Referral types that never require a PT report. An exclusion list, so
    # an unrecognized referral type defaults to requiring one.
    no_report_referral_types: List[str] = field(
        default_factory=lambda: list(DEFAULT_NO_REPORT_REFERRAL_TYPES)
    )
    api_url: str = DEFAULT_API_URL
    site_url: str = DEFAULT_SITE_URL
    subject_url_template: str = DEFAULT_SUBJECT_URL_TEMPLATE
    _folder: Optional[Path] = field(default=None, repr=False, compare=False)

    @property
    def is_configured(self) -> bool:
        return bool(self.project_id)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "project_id": self.project_id,
            "project_name": self.project_name,
            "lookback_days": self.lookback_days,
            "my_therapist": self.my_therapist,
            "mine_only": self.mine_only,
            "backlog_enabled": self.backlog_enabled,
            "backlog_days": self.backlog_days,
            "no_report_referral_types": list(self.no_report_referral_types),
            "api_url": self.api_url,
            "site_url": self.site_url,
            "subject_url_template": self.subject_url_template,
        }

    def save(self) -> bool:
        """Persist to ``settings.json``. Best effort, never raises."""
        folder = self._folder or app_dir()
        try:
            (folder / SETTINGS_FILE).write_text(
                json.dumps(self.to_dict(), indent=2), encoding="utf-8"
            )
        except (OSError, TypeError) as exc:
            logger.warning("Could not save settings: %s", exc)
            return False
        return True


def _coerce_int(value: Any, fallback: int, low: int, high: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return fallback
    return max(low, min(high, number))


def load_settings(folder: Optional[Path] = None) -> Settings:
    """Load settings, falling back to defaults for anything missing or invalid."""
    folder = Path(folder) if folder else app_dir()
    settings = Settings(_folder=folder)

    try:
        raw = json.loads((folder / SETTINGS_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeDecodeError):
        return settings
    if not isinstance(raw, dict):
        return settings

    settings.project_id = str(raw.get("project_id") or "")
    settings.project_name = str(raw.get("project_name") or "")
    settings.my_therapist = str(raw.get("my_therapist") or "")
    settings.mine_only = bool(raw.get("mine_only", False))
    settings.backlog_enabled = bool(raw.get("backlog_enabled", True))
    settings.backlog_days = _coerce_int(
        raw.get("backlog_days"), 30, low=1, high=999
    )
    # An absent key means "never configured", so seed the default. An explicitly
    # empty list means the user ticked everything, which must be respected.
    if "no_report_referral_types" in raw:
        excluded = raw.get("no_report_referral_types")
        settings.no_report_referral_types = (
            [str(x) for x in excluded if str(x).strip()]
            if isinstance(excluded, list) else []
        )
    settings.api_url = str(raw.get("api_url") or DEFAULT_API_URL)
    settings.site_url = str(raw.get("site_url") or DEFAULT_SITE_URL)
    if "subject_url_template" in raw:
        settings.subject_url_template = str(raw.get("subject_url_template") or "")
    settings.lookback_days = _coerce_int(
        raw.get("lookback_days"), DEFAULT_LOOKBACK_DAYS, low=7, high=1095
    )
    return settings


def holidays_path(folder: Optional[Path] = None) -> Path:
    return (Path(folder) if folder else app_dir()) / HOLIDAYS_FILE


def therapists_path(folder: Optional[Path] = None) -> Path:
    return (Path(folder) if folder else app_dir()) / THERAPISTS_FILE


def log_path(folder: Optional[Path] = None) -> Path:
    return (Path(folder) if folder else app_dir()) / LOG_DIR / "access.jsonl"


__all__ = [
    "DEFAULT_API_URL",
    "DEFAULT_SUBJECT_URL_TEMPLATE",
    "NETWORK_LOCATION_MESSAGE",
    "is_network_location",
    "DEFAULT_NO_REPORT_REFERRAL_TYPES",
    "DEFAULT_LOOKBACK_DAYS",
    "DEFAULT_SITE_URL",
    "ConfigError",
    "Settings",
    "app_dir",
    "decrypt_key",
    "encrypt_key",
    "holidays_path",
    "load_api_key",
    "load_settings",
    "log_path",
    "store_key_encrypted",
    "temporary_key_file",
    "therapists_path",
]
