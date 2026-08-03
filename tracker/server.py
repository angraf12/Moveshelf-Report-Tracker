"""Local web server.

Binds to ``127.0.0.1`` on a random port and serves the report table to the user's
own browser. The security controls here are the ones PLAN.md section 9 commits
to, so they are not optional decoration:

- **Loopback only.** Never ``0.0.0.0``. Nothing is reachable from the network.
- **Per-run bearer token.** Any process on the machine can connect to a loopback
  port, so connecting is not the same as being authorized. The exe opens the
  browser once with the token in the query string, the server moves it into an
  ``HttpOnly``, ``SameSite=Strict`` cookie and redirects to a clean URL, so the
  token never lingers in browser history.
- **Host header validation.** A malicious site can point a hostname it controls
  at 127.0.0.1 (DNS rebinding) and thereby defeat ordinary CORS. Such a request
  carries the attacker's hostname in ``Host``, so requiring loopback in ``Host``
  refuses it.
- **No caching.** Every response is ``no-store``, so PHI never reaches the
  browser's disk cache.
- **No patient identifier in any URL.** All data moves in JSON response bodies,
  so nothing identifying can land in history or a synced browser profile.
- **Idle shutdown.** The server exits when the page stops checking in, so a
  forgotten process is not left holding patient data in memory.
"""
from __future__ import annotations

import json
import logging
import mimetypes
import secrets
import sys
import threading
import time
from datetime import date, timedelta
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, urlparse

from . import APP_NAME, __version__
from .api import ApiError, MoveshelfClient, SessionQueryTruncated
from .audit import AccessLog
from .businessdays import builtin_holidays, load_holidays
from .config import (
    NETWORK_LOCATION_MESSAGE,
    ConfigError,
    Settings,
    app_dir,
    holidays_path,
    load_api_key,
    is_network_location,
    load_settings,
    log_path,
    store_key_encrypted,
    therapists_path,
)
from .model import parse_sessions, sort_rows, summarize, to_row
from .names import load_therapist_map, normalize, write_mapping_template

logger = logging.getLogger(__name__)

COOKIE_NAME = "rt_token"
# How long without a check-in from the page before the server exits on its own.
IDLE_SHUTDOWN_SECONDS = 30 * 60
MAX_BODY_BYTES = 64 * 1024


def web_dir() -> Path:
    """Folder holding the static page assets, in source and when frozen."""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", ".")) / "web"
    return Path(__file__).resolve().parent / "web"


class AppState:
    """Everything the page renders, refreshed under a lock.

    One instance per run. Guards every mutation so the threaded server cannot
    interleave two refreshes.
    """

    def __init__(self, folder: Optional[Path] = None) -> None:
        self.folder = Path(folder) if folder else app_dir()
        self.lock = threading.Lock()
        self.access_log = AccessLog(log_path(self.folder))
        self.settings: Settings = load_settings(self.folder)
        self.client: Optional[MoveshelfClient] = None
        self.rows: List[Dict[str, Any]] = []
        self.therapists: List[str] = []
        self.referral_types: List[str] = []
        self.projects: List[Dict[str, str]] = []
        self.last_refresh: Optional[float] = None
        self.error: str = ""
        self.key_error: str = ""
        self.location_error: str = ""
        # The first fetch runs in a background thread so the browser can open
        # immediately rather than after a 15 second wait. These two let the page
        # tell "no data yet" apart from "no data at all", so it can show a
        # loading state and poll instead of rendering a convincingly empty table.
        self.loading = False
        self.ever_loaded = False
        self.holidays_loaded = False
        self.holidays_file = False
        self.holiday_names: List[Dict[str, str]] = []
        self.last_seen = time.time()

    # -- connection ------------------------------------------------------

    def connect(self) -> bool:
        """Build the API client from the stored key. Records why it failed."""
        if self.client is not None:
            return True

        # Checked before the key is read or written, so running from a network
        # folder leaves nothing behind in it.
        if is_network_location(self.folder):
            self.location_error = NETWORK_LOCATION_MESSAGE.format(folder=self.folder)
            return False

        try:
            payload = load_api_key(self.folder)
        except ConfigError as exc:
            self.key_error = str(exc)
            return False
        try:
            self.client = MoveshelfClient(
                payload, self.settings.api_url, self.access_log
            )
        except ApiError as exc:
            self.key_error = str(exc)
            return False

        # Convert the plain key to an encrypted-at-rest copy now that it is known
        # to work. Doing it only after a successful connection means a bad key is
        # never encrypted into something the user cannot inspect and fix.
        store_key_encrypted(payload, self.folder)
        self.key_error = ""
        self.access_log.log_start(self.settings.project_id)
        return True

    def load_projects(self) -> None:
        if self.client and not self.projects:
            self.projects = self.client.list_projects()

    # -- refresh ---------------------------------------------------------

    def refresh(self) -> None:
        """Re-fetch sessions and rebuild every row. Records errors, never raises."""
        self.loading = True
        try:
            self._refresh()
        finally:
            self.loading = False
            self.ever_loaded = True

    def _refresh(self) -> None:
        """The body of ``refresh``, under the lock."""
        with self.lock:
            self.error = ""
            if not self.connect():
                return
            if not self.settings.is_configured:
                try:
                    self.load_projects()
                except ApiError as exc:
                    self.error = str(exc)
                return

            today = date.today()
            start = today - timedelta(days=self.settings.lookback_days)
            try:
                raw = self.client.fetch_sessions(
                    self.settings.project_id,
                    start_date=start.isoformat(),
                    end_date=today.isoformat(),
                )
            except SessionQueryTruncated as exc:
                self.error = str(exc)
                return
            except ApiError as exc:
                self.error = str(exc)
                return

            sessions = parse_sessions(raw, self.settings.project_id)

            # Offer the name-merge file pre-filled, once, without ever
            # overwriting a therapist's own corrections.
            write_mapping_template(
                therapists_path(self.folder), [s.therapist_raw for s in sessions]
            )
            mapping = load_therapist_map(therapists_path(self.folder))

            holidays = load_holidays(holidays_path(self.folder))
            self.holidays_loaded = bool(holidays)
            self.holidays_file = holidays_path(self.folder).is_file()
            # Name this year's holidays so the page can say what is in force
            # rather than leaving the user to trust an invisible rule.
            self.holiday_names = [
                {"date": day.isoformat(), "name": name}
                for day, name in sorted(builtin_holidays(today.year).items())
                if day in holidays
            ]

            rows = [
                to_row(
                    session,
                    today,
                    holidays,
                    therapist=normalize(session.therapist_raw, mapping),
                    site_url=self.settings.site_url,
                    backlog_after=(
                        self.settings.backlog_days
                        if self.settings.backlog_enabled else None
                    ),
                    no_report_types=self.settings.no_report_referral_types,
                    subject_url_template=self.settings.subject_url_template,
                )
                for session in sessions
            ]
            self.rows = sort_rows(rows)
            self.therapists = sorted(
                {r["therapist"] for r in self.rows if r["therapist"]}, key=str.lower
            )
            # Every referral type seen, so the page can offer the full list to
            # tick rather than making the user remember what exists.
            self.referral_types = sorted(
                {r["referral_type"] for r in self.rows if r["referral_type"]},
                key=str.lower,
            )
            self.last_refresh = time.time()

    # -- serialization ---------------------------------------------------

    def status(self) -> str:
        if self.location_error:
            return "bad_location"
        if self.key_error:
            return "needs_key"
        if not self.settings.is_configured:
            return "needs_project"
        return "ready"

    def to_payload(self) -> Dict[str, Any]:
        """The single JSON document the page renders from."""
        return {
            "status": self.status(),
            "loading": self.loading,
            "ever_loaded": self.ever_loaded,
            "app_name": APP_NAME,
            "version": __version__,
            "folder": str(self.folder),
            "key_error": self.key_error,
            "location_error": self.location_error,
            "error": self.error,
            "projects": self.projects,
            "project_id": self.settings.project_id,
            "project_name": self.settings.project_name,
            "lookback_days": self.settings.lookback_days,
            "my_therapist": self.settings.my_therapist,
            "mine_only": self.settings.mine_only,
            "backlog_enabled": self.settings.backlog_enabled,
            "backlog_days": self.settings.backlog_days,
            "therapists": self.therapists,
            "referral_types": self.referral_types,
            "no_report_referral_types": list(
                self.settings.no_report_referral_types
            ),
            "holidays_loaded": self.holidays_loaded,
            "holidays_file": self.holidays_file,
            "holiday_names": self.holiday_names,
            "today": date.today().isoformat(),
            "last_refresh": self.last_refresh,
            "rows": self.rows,
            "summary": summarize(self.rows),
        }

    def apply_settings(self, data: Dict[str, Any]) -> None:
        """Apply and persist settings sent by the page."""
        with self.lock:
            if "project_id" in data:
                new_id = str(data.get("project_id") or "")
                if new_id != self.settings.project_id:
                    self.rows = []
                    self.last_refresh = None
                self.settings.project_id = new_id
                self.settings.project_name = str(data.get("project_name") or "")
            if "lookback_days" in data:
                try:
                    self.settings.lookback_days = max(
                        7, min(1095, int(data["lookback_days"]))
                    )
                except (TypeError, ValueError):
                    pass
            if "my_therapist" in data:
                self.settings.my_therapist = str(data.get("my_therapist") or "")
            if "mine_only" in data:
                self.settings.mine_only = bool(data.get("mine_only"))
            if "no_report_referral_types" in data:
                values = data.get("no_report_referral_types")
                if isinstance(values, list):
                    self.settings.no_report_referral_types = [
                        str(v) for v in values if str(v).strip()
                    ]
            if "backlog_enabled" in data:
                self.settings.backlog_enabled = bool(data.get("backlog_enabled"))
            if "backlog_days" in data:
                try:
                    self.settings.backlog_days = max(
                        1, min(999, int(data["backlog_days"]))
                    )
                except (TypeError, ValueError):
                    pass
            self.settings.save()


class Handler(BaseHTTPRequestHandler):
    """Request handler. ``state``, ``token`` and ``allowed_hosts`` are set by serve()."""

    server_version = f"ReportTracker/{__version__}"
    state: AppState
    token: str
    allowed_hosts: Tuple[str, ...] = ()

    # -- plumbing --------------------------------------------------------

    def log_message(self, fmt: str, *args: Any) -> None:
        """Silence the default stderr access log. It would echo request paths."""
        logger.debug("%s %s", self.address_string(), fmt % args)

    def _send(
        self,
        status: HTTPStatus,
        body: bytes,
        content_type: str,
        extra_headers: Optional[Dict[str, str]] = None,
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        # PHI must never reach the browser's disk cache.
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
        self.send_header("Pragma", "no-cache")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self' data:; base-uri 'none'; "
            "form-action 'none'; frame-ancestors 'none'",
        )
        for key, value in (extra_headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, payload: Dict[str, Any], status: HTTPStatus = HTTPStatus.OK) -> None:
        self._send(status, json.dumps(payload).encode("utf-8"), "application/json")

    def _host_is_loopback(self) -> bool:
        """Reject DNS-rebinding attempts, which arrive with a foreign Host header."""
        host = (self.headers.get("Host") or "").strip().lower()
        return host in self.allowed_hosts

    def _cookie_token(self) -> str:
        raw = self.headers.get("Cookie")
        if not raw:
            return ""
        try:
            cookie = SimpleCookie(raw)
        except Exception:
            return ""
        morsel = cookie.get(COOKIE_NAME)
        return morsel.value if morsel else ""

    def _authorized(self) -> bool:
        return secrets.compare_digest(self._cookie_token(), self.token)

    def _read_json(self) -> Dict[str, Any]:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except (TypeError, ValueError):
            return {}
        if length <= 0 or length > MAX_BODY_BYTES:
            return {}
        try:
            parsed = json.loads(self.rfile.read(length).decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return {}
        return parsed if isinstance(parsed, dict) else {}

    # -- routes ----------------------------------------------------------

    def do_GET(self) -> None:  # noqa: N802  (stdlib naming)
        if not self._host_is_loopback():
            self._send(HTTPStatus.FORBIDDEN, b"Forbidden", "text/plain")
            return

        parsed = urlparse(self.path)
        path = parsed.path

        # First visit: the exe supplies the token in the query string. Move it
        # into an HttpOnly cookie and redirect to a clean URL so the token does
        # not sit in browser history.
        if path == "/":
            supplied = (parse_qs(parsed.query).get("t") or [""])[0]
            if supplied and secrets.compare_digest(supplied, self.token):
                self._send(
                    HTTPStatus.FOUND,
                    b"",
                    "text/plain",
                    {
                        "Location": "/",
                        "Set-Cookie": (
                            f"{COOKIE_NAME}={self.token}; HttpOnly; SameSite=Strict; Path=/"
                        ),
                    },
                )
                return

        if not self._authorized():
            self._send(HTTPStatus.UNAUTHORIZED, b"Unauthorized", "text/plain")
            return

        self.state.last_seen = time.time()

        if path in ("/", "/index.html"):
            self._serve_static("index.html")
        elif path.startswith("/static/"):
            self._serve_static(path[len("/static/"):])
        elif path == "/api/state":
            self._json(self.state.to_payload())
        elif path == "/api/ping":
            self._json({"ok": True})
        else:
            self._send(HTTPStatus.NOT_FOUND, b"Not found", "text/plain")

    def _drain_body(self) -> None:
        """Read and discard the request body.

        Replying to a POST without consuming its body can abort the connection
        while the client is still writing, which surfaces as a confusing network
        error rather than the 401 or 403 we actually sent.
        """
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except (TypeError, ValueError):
            return
        if 0 < length <= MAX_BODY_BYTES:
            try:
                self.rfile.read(length)
            except OSError:
                pass

    def do_POST(self) -> None:  # noqa: N802
        if not self._host_is_loopback():
            self._drain_body()
            self._send(HTTPStatus.FORBIDDEN, b"Forbidden", "text/plain")
            return
        if not self._authorized():
            self._drain_body()
            self._send(HTTPStatus.UNAUTHORIZED, b"Unauthorized", "text/plain")
            return

        self.state.last_seen = time.time()
        path = urlparse(self.path).path

        if path == "/api/refresh":
            self.state.refresh()
            self._json(self.state.to_payload())
        elif path == "/api/settings":
            self.state.apply_settings(self._read_json())
            self.state.refresh()
            self._json(self.state.to_payload())
        elif path == "/api/exported":
            # The page writes the file itself, so the rows never come back to
            # the server. This records only that it happened.
            data = self._read_json()
            try:
                n_rows = int(data.get("n_rows") or 0)
            except (TypeError, ValueError):
                n_rows = 0
            self.state.access_log.log_export(n_rows, str(data.get("filename") or ""))
            self._json({"ok": True})
        elif path == "/api/quit":
            self._json({"ok": True})
            threading.Thread(target=self.server.shutdown, daemon=True).start()
        else:
            self._send(HTTPStatus.NOT_FOUND, b"Not found", "text/plain")

    def _serve_static(self, name: str) -> None:
        """Serve a bundled asset, refusing any path that escapes the web folder."""
        root = web_dir().resolve()
        try:
            target = (root / name).resolve()
            target.relative_to(root)
        except (ValueError, OSError):
            self._send(HTTPStatus.FORBIDDEN, b"Forbidden", "text/plain")
            return
        if not target.is_file():
            self._send(HTTPStatus.NOT_FOUND, b"Not found", "text/plain")
            return
        content_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        if content_type.startswith("text/") or content_type.endswith("javascript"):
            content_type += "; charset=utf-8"
        try:
            self._send(HTTPStatus.OK, target.read_bytes(), content_type)
        except OSError:
            self._send(HTTPStatus.INTERNAL_SERVER_ERROR, b"Read error", "text/plain")


def build_server(state: AppState, port: int = 0) -> Tuple[ThreadingHTTPServer, str, int]:
    """Create the loopback server.

    Args:
        state: Shared application state.
        port: 0 asks the OS for a free port, which is the normal case.

    Returns:
        ``(server, token, port)``.
    """
    token = secrets.token_urlsafe(32)
    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    bound_port = httpd.server_address[1]

    Handler.state = state
    Handler.token = token
    Handler.allowed_hosts = (
        f"127.0.0.1:{bound_port}",
        f"localhost:{bound_port}",
    )
    return httpd, token, bound_port


def start_idle_watchdog(httpd: ThreadingHTTPServer, state: AppState) -> threading.Thread:
    """Shut the server down once the page stops checking in."""

    def watch() -> None:
        while True:
            time.sleep(30)
            if time.time() - state.last_seen > IDLE_SHUTDOWN_SECONDS:
                logger.info("No activity for %s s; shutting down", IDLE_SHUTDOWN_SECONDS)
                httpd.shutdown()
                return

    thread = threading.Thread(target=watch, daemon=True)
    thread.start()
    return thread


__all__ = [
    "COOKIE_NAME",
    "IDLE_SHUTDOWN_SECONDS",
    "AppState",
    "Handler",
    "build_server",
    "start_idle_watchdog",
    "web_dir",
]
