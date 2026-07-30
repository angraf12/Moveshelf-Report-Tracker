"""Moveshelf session fetching.

Wraps the public ``moveshelf-api`` SDK. This app does not import the research
``query_app`` package (see CLAUDE.md), so the date-window paging logic below is a
deliberate copy of ``query_app/api_client.py::get_filtered_project_sessions``,
kept because it encodes three things that are expensive to rediscover:

1. ``getFilteredProjectSessions`` has **no offset or cursor**. The SDK's default
   ``limit=500`` silently returns only the most recent 500 sessions, with no
   indication that anything was dropped.
2. A window holding more data than the server can serialize does not truncate,
   it **fails with repeated 502s**. So detecting a full page is not sufficient on
   its own; oversized-response failures need the same date-splitting remedy.
3. ``endDate`` is **inclusive** on this endpoint, unlike the subjects endpoint
   where it is exclusive. Halves are therefore ``[start, mid]`` and
   ``[mid + 1 day, end]``, which tile the range with no gap and no overlap.

If you fix a bug here, check whether the research app has the same bug.

In practice this app queries about 90 days for one site, which is well under 100
sessions, so splitting will essentially never fire. It is insurance, not a hot
path.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from moveshelf_api.api import MoveshelfApi

from .audit import AccessLog
from .config import temporary_key_file

logger = logging.getLogger(__name__)

# Page size, not a result cap. Kept well below the point where responses start
# failing: measured on the research app, 2,622 sessions served fine but 8,611
# returned repeated 502s. The constraint is response size, not the limit value.
SESSION_PAGE_SIZE = 2000

API_TIMEOUT_SECONDS = 180

_OVERSIZED_SIGNATURES = (
    "502", "504", "too many", "max retries", "timed out", "timeout",
    "connection reset", "connection aborted",
)


class ApiError(Exception):
    """A fetch failed in a way worth showing the user."""


class SessionQueryTruncated(ApiError):
    """The result was truncated and could not be recovered by splitting."""


def _is_oversized_response_error(exc: BaseException) -> bool:
    """Does this look like a response too big to serve, rather than a bad request?

    Deliberately conservative. A false positive turns one failed request into a
    fan-out of smaller failed requests, so this matches transport and gateway
    failures only, never an application-level rejection like a bad filter or an
    expired key.
    """
    text = f"{type(exc).__name__} {exc}".lower()
    if any(code in text for code in ("400", "401", "403", "unauthor", "forbidden")):
        return False
    return any(sig in text for sig in _OVERSIZED_SIGNATURES)


def _parse_day(value: Optional[str]) -> Optional[datetime]:
    if not value or not isinstance(value, str):
        return None
    try:
        return datetime.strptime(value.strip()[:10], "%Y-%m-%d")
    except (ValueError, TypeError):
        return None


class MoveshelfClient:
    """Thin, audited client for the two calls this app makes."""

    def __init__(
        self,
        key_payload: Dict[str, Any],
        api_url: str,
        access_log: Optional[AccessLog] = None,
    ) -> None:
        """
        Args:
            key_payload: ``{"secretKey": ..., "application": ...}``.
            api_url: Regional GraphQL endpoint.
            access_log: Audit log. Fetches are recorded even if None is passed,
                by doing nothing, but callers should always supply one.

        Raises:
            ApiError: If the SDK rejects the key.
        """
        self.access_log = access_log
        try:
            # The SDK only accepts a path, so the key touches disk for the
            # duration of this constructor. See config.temporary_key_file.
            with temporary_key_file(key_payload) as key_file:
                self.api = MoveshelfApi(
                    api_key_file=key_file,
                    api_url=api_url,
                    timeout=API_TIMEOUT_SECONDS,
                )
        except Exception as exc:
            raise ApiError(
                "Moveshelf rejected the API key, or could not be reached. Check "
                "that the key has not been revoked and that you are on a network "
                f"that can reach {api_url}."
            ) from exc

    def list_projects(self) -> List[Dict[str, str]]:
        """Every project this key can see, as ``{id, name}``, sorted by name.

        Returns:
            Possibly empty list.

        Raises:
            ApiError: If the call fails.
        """
        try:
            projects = self.api.getUserProjects()
        except Exception as exc:
            raise ApiError(f"Could not load your Moveshelf sites: {exc}") from exc

        cleaned = [
            {"id": str(p.get("id") or ""), "name": str(p.get("name") or "")}
            for p in projects or []
            if p.get("id")
        ]
        return sorted(cleaned, key=lambda p: p["name"].lower())

    def fetch_sessions(
        self,
        project_id: str,
        start_date: str,
        end_date: str,
        limit: int = SESSION_PAGE_SIZE,
        auto_split: bool = True,
    ) -> List[Dict[str, Any]]:
        """Fetch sessions in a date window, paging by date if necessary.

        Args:
            project_id: Project to query.
            start_date: Inclusive window start, ``YYYY-MM-DD``.
            end_date: Inclusive window end, ``YYYY-MM-DD``.
            limit: Page size per request.
            auto_split: Halve the window and recurse on a full page or an
                oversized-response failure. False gives raw single-request
                behavior, for tests.

        Returns:
            Session dicts, each with an embedded ``patient``, sorted by date.

        Raises:
            SessionQueryTruncated: If a full page cannot be recovered by
                splitting. Silently returning a partial worklist would let a
                therapist miss a report, so this surfaces instead.
            ApiError: On any other failure.
        """
        start_dt, end_dt = _parse_day(start_date), _parse_day(end_date)
        can_split = (
            auto_split and start_dt is not None and end_dt is not None
            and start_dt < end_dt
        )

        try:
            sessions = self._fetch_once(project_id, start_date, end_date, limit)
        except ApiError:
            raise
        except Exception as exc:
            if not (can_split and _is_oversized_response_error(exc)):
                raise ApiError(f"Could not load sessions: {exc}") from exc
            logger.warning(
                "Window %s..%s looks too large to serve (%s); splitting",
                start_date, end_date, type(exc).__name__,
            )
        else:
            if len(sessions) < limit:
                return sessions
            if not can_split:
                raise SessionQueryTruncated(
                    f"Moveshelf returned a full page of {limit} sessions for "
                    f"{start_date} to {end_date}, so the list is incomplete and "
                    f"the window cannot be split further. Shorten the lookback."
                )

        # endDate is inclusive, so these halves tile the range exactly.
        mid_dt = start_dt + (end_dt - start_dt) // 2
        mid = mid_dt.strftime("%Y-%m-%d")
        next_day = (mid_dt + timedelta(days=1)).strftime("%Y-%m-%d")

        merged: Dict[str, Dict[str, Any]] = {}
        for chunk_start, chunk_end in ((start_date, mid), (next_day, end_date)):
            for session in self.fetch_sessions(
                project_id, chunk_start, chunk_end, limit=limit, auto_split=auto_split
            ):
                session_id = session.get("id")
                if session_id:
                    merged[session_id] = session
        return sorted(merged.values(), key=lambda s: str(s.get("date") or ""))

    def _fetch_once(
        self, project_id: str, start_date: str, end_date: str, limit: int
    ) -> List[Dict[str, Any]]:
        """One raw request, audited on both success and failure."""
        started = time.time()
        try:
            sessions = self.api.getFilteredProjectSessions(
                project_id,
                start_date=start_date,
                end_date=end_date,
                include_additional_data=False,
                limit=limit,
            )
        except Exception as exc:
            if self.access_log:
                self.access_log.log_fetch(
                    operation="getFilteredProjectSessions",
                    project_id=project_id,
                    n_sessions=None,
                    duration_seconds=time.time() - started,
                    status="error",
                    error=f"{type(exc).__name__}: {exc}",
                    start_date=start_date,
                    end_date=end_date,
                )
            raise

        sessions = sessions or []
        if self.access_log:
            self.access_log.log_fetch(
                operation="getFilteredProjectSessions",
                project_id=project_id,
                n_sessions=len(sessions),
                duration_seconds=time.time() - started,
                status="success",
                start_date=start_date,
                end_date=end_date,
            )
        return sessions


__all__ = [
    "SESSION_PAGE_SIZE",
    "ApiError",
    "MoveshelfClient",
    "SessionQueryTruncated",
]
