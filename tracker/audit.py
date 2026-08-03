"""Append-only access log.

The research application's rule is that no path to patient data may bypass the
audit trail, and this app reads patient data, so it keeps its own. The log holds
one JSON object per line recording *that* a fetch happened, never *what* it
returned: no names, no MRNs, no session identifiers.

Logging is strictly best effort. Every function here swallows its own errors,
because a full disk or a read-only folder must degrade a therapist's audit trail,
not their ability to see what reports are due.
"""
from __future__ import annotations

import getpass
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import __version__

logger = logging.getLogger(__name__)

EVENT_FETCH = "api_fetch"
EVENT_START = "app_start"
EVENT_EXPORT = "export"


class AccessLog:
    """Append-only JSONL log of patient-data access."""

    def __init__(self, path: Optional[Path]) -> None:
        """
        Args:
            path: Destination file. Parent directories are created if possible.
                None disables logging entirely.
        """
        self.path = Path(path) if path else None
        self.enabled = False
        if self.path is None:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.enabled = True
        except OSError as exc:
            logger.warning("Access logging unavailable: %s", exc)

    @staticmethod
    def _operator() -> str:
        try:
            return getpass.getuser()
        except (OSError, KeyError):
            return "unknown"

    def _append(self, record: Dict[str, Any]) -> bool:
        if not self.enabled or self.path is None:
            return False
        try:
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, default=str) + "\n")
        except (OSError, TypeError, ValueError) as exc:
            logger.warning("Access logging failed: %s", exc)
            return False
        return True

    def _base(self, event: str) -> Dict[str, Any]:
        return {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event": event,
            "operator": self._operator(),
            "app_version": __version__,
        }

    def log_start(self, project_id: str = "") -> bool:
        """Record that the app was launched."""
        record = self._base(EVENT_START)
        record["project_id"] = project_id
        return self._append(record)

    def log_fetch(
        self,
        operation: str,
        project_id: str,
        n_sessions: Optional[int],
        duration_seconds: float,
        status: str,
        error: str = "",
        start_date: str = "",
        end_date: str = "",
    ) -> bool:
        """Record one patient-data fetch, successful or failed.

        Args:
            operation: API method called.
            project_id: Project queried. Not a patient identifier.
            n_sessions: How many sessions came back, or None on failure.
            duration_seconds: Wall-clock duration.
            status: "success" or "error".
            error: Exception summary, truncated. Never contains patient data,
                because the API returns identifiers in payloads rather than in
                exception messages, but it is truncated regardless.
            start_date: Query window start.
            end_date: Query window end.
        """
        record = self._base(EVENT_FETCH)
        record.update(
            {
                "operation": operation,
                "project_id": project_id,
                "n_sessions": n_sessions,
                "duration_seconds": round(float(duration_seconds), 3),
                "status": status,
                "error": str(error)[:300],
                "window_start": start_date,
                "window_end": end_date,
            }
        )
        return self._append(record)

    def log_export(self, n_rows: int, filename: str = "") -> bool:
        """Record that data was exported to a file.

        Export was approved for clinical use on 2026-07-30. It is the only way
        patient data leaves the app, so it must appear in the audit trail. The
        row count and file name are recorded; the rows themselves never are.
        """
        record = self._base(EVENT_EXPORT)
        record["n_rows"] = int(n_rows)
        record["filename"] = str(filename)[:120]
        return self._append(record)

    def read_records(self) -> List[Dict[str, Any]]:
        """Read the log back, skipping any malformed line."""
        if self.path is None or not self.path.is_file():
            return []
        records = []
        try:
            text = self.path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            logger.warning("Could not read access log: %s", exc)
            return []
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                parsed = json.loads(line)
            except ValueError:
                continue
            if isinstance(parsed, dict):
                records.append(parsed)
        return records


__all__ = ["EVENT_EXPORT", "EVENT_FETCH", "EVENT_START", "AccessLog"]
