"""Parsing Moveshelf sessions into report-tracking rows.

Two things about the Moveshelf payload are easy to get wrong, so both are handled
in one place here and nowhere else:

1. **Session metadata is doubly nested.** ``session["metadata"]`` is a JSON string
   holding an object with a ``metadata`` key holding another object with the real
   values.
2. **Patient metadata is only singly nested.** ``patient["metadata"]`` is a JSON
   string holding the values directly. Applying the session-shaped unwrap to a
   patient loses everything.

Both confirmed live against shriners/CHI-Gait on 2026-07-27.

Field choices, also confirmed against live data:

- ``sessioninfo-therapist`` is the therapist. ``sessioninfo-evaluating-pt`` exists
  in the schema but was empty in all 67 sessions probed, so it is not used.
- ``sessioninfo-pt-evaluation-date`` is the completion marker that stops the
  seven-business-day clock.
- ``sessioninfo-interpretation-completed`` is displayed but never used in deadline
  math, because live values include dates in the future and it appears to record a
  scheduled interpretation rather than a completed one.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from enum import Enum
from typing import Any, Dict, FrozenSet, Iterable, List, Optional

from .businessdays import (
    REPORT_DEADLINE_BUSINESS_DAYS,
    business_days_between,
    due_date,
    parse_iso_date,
)

# Metadata keys, all confirmed present in live data.
KEY_THERAPIST = "sessioninfo-therapist"
KEY_PROCESSING_COMPLETED = "sessioninfo-date-processing-completed"
KEY_PT_EVALUATION = "sessioninfo-pt-evaluation-date"
KEY_PDF_TO_EMR = "sessioninfo-pdf-to-emr-date"
KEY_INTERPRETATION = "sessioninfo-interpretation-completed"
KEY_CANCELLATION = "sessioninfo-cancellation"
KEY_REFERRAL_TYPE = "sessioninfo-referral-type"
KEY_REFERRING_PHYSICIAN = "sessioninfo-referring-physician"
KEY_EHR_ID = "ehr-id"

# Live data contains "Cancelled", "No Show" and the lowercase "no show", so the
# comparison is always case-folded and whitespace-collapsed.
_CANCELLATION_LABELS = frozenset({"cancelled", "no show", "noshow", "no-show"})


class Status(str, Enum):
    """How urgent a session's report is. Ordered most to least urgent.

    A caution that cost a round of confusion: **Moveshelf records nothing about a
    report being in progress.** Every workflow field is a completed-milestone
    date, verified across all 296 session metadata keys on 442 sessions. So none
    of these buckets can distinguish an untouched report from a nearly finished
    one. They describe time remaining, never work remaining.

    ``NOT_STARTED`` in particular does **not** mean "the therapist has not
    started". It means *data processing* is not finished, so the seven-day clock
    has not begun and nothing is owed by the therapist yet. It is displayed as
    "Awaiting processing" for exactly that reason. The wire value stays
    ``not_started`` so saved settings and stored data keep working.
    """

    OVERDUE = "overdue"
    DUE_TODAY = "due_today"
    DUE_SOON = "due_soon"
    ON_TRACK = "on_track"
    NOT_STARTED = "not_started"
    NO_REPORT = "no_report"
    BACKLOG = "backlog"
    DONE = "done"


# Sort rank, so the row a therapist should do next is always on top.
STATUS_ORDER = {
    Status.OVERDUE: 0,
    Status.DUE_TODAY: 1,
    Status.DUE_SOON: 2,
    Status.ON_TRACK: 3,
    Status.NOT_STARTED: 4,
    Status.NO_REPORT: 5,
    Status.BACKLOG: 6,
    Status.DONE: 7,
}

# "Due soon" means this many business days or fewer remain, excluding today.
DUE_SOON_THRESHOLD = 2

# Sessions overdue by more than this many business days are treated as backlog
# rather than as this week's work.
#
# Measured at CHI-Gait over 365 days: of 356 sessions with processing complete,
# 119 had none of the three EMR dates filled, at a median of 111 business days
# past processing. Confirmed with the clinical lead that these reports were in
# fact completed and only the completion dates were never entered in Moveshelf.
# Left in the Overdue bucket they would bury the handful of reports a therapist
# can actually act on this week, so they are separated instead. Nothing is
# hidden: the Backlog count is always shown and the split is a user toggle.
BACKLOG_THRESHOLD_DAYS = 30


def _loads(value: Any) -> Any:
    """json.loads a string, pass a dict through, return {} for anything else."""
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (ValueError, TypeError):
            return {}
    return {}


def session_metadata(raw_session: Dict[str, Any]) -> Dict[str, Any]:
    """Unwrap the doubly nested session metadata. Returns {} on anything odd."""
    outer = _loads(raw_session.get("metadata"))
    if not isinstance(outer, dict):
        return {}
    inner = _loads(outer.get("metadata"))
    if isinstance(inner, dict) and inner:
        return inner
    # Tolerate a future flattening of the payload rather than returning nothing.
    return outer


def patient_metadata(raw_session: Dict[str, Any]) -> Dict[str, Any]:
    """Unwrap the singly nested patient metadata. Returns {} on anything odd."""
    patient = raw_session.get("patient") or {}
    parsed = _loads(patient.get("metadata"))
    return parsed if isinstance(parsed, dict) else {}


def _text(value: Any) -> str:
    """Coerce a metadata value to a trimmed string, taking the first of a list."""
    if isinstance(value, (list, tuple)):
        value = value[0] if value else ""
    if value is None:
        return ""
    return str(value).strip()


def is_cancellation_label(value: Any) -> bool:
    """Does this cancellation value mean the session did not happen?"""
    text = " ".join(_text(value).lower().split())
    return text in _CANCELLATION_LABELS


@dataclass(frozen=True)
class Session:
    """One Moveshelf session, reduced to what report tracking needs."""

    session_id: str
    project_id: str
    subject_id: str
    mrn: str
    session_date: Optional[date]
    therapist_raw: str
    processing_completed: Optional[date]
    pt_evaluation: Optional[date]
    pdf_to_emr: Optional[date]
    interpretation: Optional[date]
    cancellation: str
    referral_type: str = ""
    referring_physician: str = ""
    patient_id: str = ""

    @property
    def is_cancelled(self) -> bool:
        """Cancelled or no-show, so no report is owed."""
        return is_cancellation_label(self.cancellation)

    @property
    def is_complete(self) -> bool:
        """The PT evaluation is in the EMR, which stops the clock."""
        return self.pt_evaluation is not None

    @property
    def clock_started(self) -> bool:
        """Processing is complete, so the seven business days are running."""
        return self.processing_completed is not None


def parse_session(raw_session: Dict[str, Any], project_id: str) -> Optional[Session]:
    """Build a Session from one raw API session dict.

    Args:
        raw_session: One element of ``getFilteredProjectSessions``.
        project_id: The project the session came from, needed to build its URL.

    Returns:
        The parsed Session, or None if it has no id and so cannot be identified
        or linked. Every other field degrades to empty or None rather than
        failing, because one malformed session must not cost a therapist their
        whole worklist.
    """
    session_id = _text(raw_session.get("id"))
    if not session_id:
        return None

    meta = session_metadata(raw_session)
    pmeta = patient_metadata(raw_session)
    patient = raw_session.get("patient") or {}

    return Session(
        session_id=session_id,
        project_id=project_id,
        subject_id=_text(patient.get("name")),
        mrn=_text(pmeta.get(KEY_EHR_ID)),
        session_date=parse_iso_date(raw_session.get("date")),
        therapist_raw=_text(meta.get(KEY_THERAPIST)),
        processing_completed=parse_iso_date(meta.get(KEY_PROCESSING_COMPLETED)),
        pt_evaluation=parse_iso_date(meta.get(KEY_PT_EVALUATION)),
        pdf_to_emr=parse_iso_date(meta.get(KEY_PDF_TO_EMR)),
        interpretation=parse_iso_date(meta.get(KEY_INTERPRETATION)),
        cancellation=_text(meta.get(KEY_CANCELLATION)),
        referral_type=_text(meta.get(KEY_REFERRAL_TYPE)),
        referring_physician=_text(meta.get(KEY_REFERRING_PHYSICIAN)),
        patient_id=_text(patient.get("id")),
    )


def parse_sessions(
    raw_sessions: List[Dict[str, Any]], project_id: str
) -> List[Session]:
    """Parse many sessions, dropping cancelled ones and any without an id."""
    out = []
    for raw in raw_sessions or []:
        parsed = parse_session(raw, project_id)
        if parsed is not None and not parsed.is_cancelled:
            out.append(parsed)
    return out


def requires_report(session: Session, no_report_types: Iterable[str] = ()) -> bool:
    """Does this session's referral type call for a PT report at all?

    Several referral types never produce a report. Measured at CHI-Gait over 365
    days: of 301 "Kinematics gait analysis" sessions only 14 lacked a PT
    evaluation date, but 40 of 44 "Video & Pedobarograph only" and 24 of 33
    "Video only" sessions lacked one, because none was ever owed. Counting those
    as overdue would swamp the real worklist.

    The comparison is an **exclusion** list rather than an inclusion list, so a
    referral type nobody has configured yet defaults to requiring a report. That
    fails toward showing a therapist too much rather than silently hiding work.

    Args:
        session: The parsed session.
        no_report_types: Referral types that do not require a report. Compared
            case-insensitively with whitespace collapsed.
    """
    if not no_report_types:
        return True
    normalized = " ".join(session.referral_type.lower().split())
    if not normalized:
        return True
    excluded = {" ".join(str(t).lower().split()) for t in no_report_types}
    return normalized not in excluded


def classify(
    session: Session,
    today: date,
    holidays: FrozenSet[date] = frozenset(),
    backlog_after: Optional[int] = None,
    no_report_types: Iterable[str] = (),
) -> Status:
    """Which urgency bucket this session's report falls into.

    Args:
        session: The parsed session.
        today: Local calendar date to measure against.
        holidays: Observed holidays.
        backlog_after: When set, a report overdue by more than this many business
            days is classified BACKLOG rather than OVERDUE. None keeps every
            overdue report in one bucket. See ``BACKLOG_THRESHOLD_DAYS``.
        no_report_types: Referral types that never require a report.
        subject_url_template: Route for a patient page. See ``subject_url``. See
            ``requires_report``.
    """
    if session.is_complete:
        return Status.DONE
    if not requires_report(session, no_report_types):
        return Status.NO_REPORT
    if not session.clock_started:
        return Status.NOT_STARTED

    days_left = business_days_between(
        today, due_date(session.processing_completed, holidays), holidays
    )
    if days_left < 0:
        if backlog_after is not None and -days_left > backlog_after:
            return Status.BACKLOG
        return Status.OVERDUE
    if days_left == 0:
        return Status.DUE_TODAY
    if days_left <= DUE_SOON_THRESHOLD:
        return Status.DUE_SOON
    return Status.ON_TRACK


def _iso(value: Optional[date]) -> Optional[str]:
    return value.isoformat() if value else None


def to_row(
    session: Session,
    today: date,
    holidays: FrozenSet[date] = frozenset(),
    therapist: Optional[str] = None,
    site_url: str = "",
    backlog_after: Optional[int] = None,
    no_report_types: Iterable[str] = (),
    subject_url_template: str = "",
) -> Dict[str, Any]:
    """Flatten a Session into the dict the web page renders.

    Args:
        session: The parsed session.
        today: The local calendar date to measure against.
        holidays: Observed holidays.
        therapist: Normalized therapist name. Falls back to the raw value.
        site_url: Moveshelf base URL, used to build the clickable session link.
        backlog_after: Business days overdue past which a report is treated as
            backlog rather than as this week's work. None disables the split.
        no_report_types: Referral types that never require a report.
        subject_url_template: Route for a patient page. See ``subject_url``.

    Returns:
        A JSON-serializable dict. Business-day counts are None when the source
        date is missing, so the page can render a dash rather than a wrong zero.
    """
    status = classify(session, today, holidays, backlog_after, no_report_types)
    due = due_date(session.processing_completed, holidays) if session.clock_started else None

    # No countdown for finished reports, or for referral types that never owed
    # one: a number there would imply a deadline that does not exist.
    days_left = (
        business_days_between(today, due, holidays)
        if due is not None
        and not session.is_complete
        and status is not Status.NO_REPORT
        else None
    )
    days_since_session = (
        business_days_between(session.session_date, today, holidays)
        if session.session_date
        else None
    )
    days_since_processing = (
        business_days_between(session.processing_completed, today, holidays)
        if session.clock_started
        else None
    )

    return {
        "session_id": session.session_id,
        "subject_id": session.subject_id,
        "mrn": session.mrn,
        "session_date": _iso(session.session_date),
        "therapist": therapist if therapist is not None else session.therapist_raw,
        "therapist_raw": session.therapist_raw,
        "processing_completed": _iso(session.processing_completed),
        "pt_evaluation": _iso(session.pt_evaluation),
        "pdf_to_emr": _iso(session.pdf_to_emr),
        "interpretation": _iso(session.interpretation),
        "referral_type": session.referral_type,
        "referring_physician": session.referring_physician,
        "due_date": _iso(due),
        "days_left": days_left,
        "days_since_session": days_since_session,
        "days_since_processing": days_since_processing,
        "status": status.value,
        "sort_rank": STATUS_ORDER[status],
        "url": session_url(session, site_url),
        "subject_url": subject_url(session, site_url, subject_url_template),
    }


def session_url(session: Session, site_url: str) -> str:
    """Build the Moveshelf page URL for a session, or "" if not derivable."""
    if not site_url or not session.project_id or not session.session_id:
        return ""
    return (
        f"{site_url.rstrip('/')}/project/{session.project_id}"
        f"/session/{session.session_id}"
    )


def subject_url(session: Session, site_url: str, template: str) -> str:
    """Build the Moveshelf page URL for the patient, or "" if not derivable.

    The route is supplied as a template rather than hard-coded. Unlike the
    session route it is not documented in the SDK; the working shape was
    confirmed against the live web app on 2026-07-28. Keeping it in one setting
    means a future change on Moveshelf's side is a config fix rather than a
    rebuild for every user.

    Args:
        session: The parsed session.
        site_url: Moveshelf web app base URL.
        template: Format string accepting ``{site}``, ``{project}`` and
            ``{patient}``. Empty disables subject links entirely.

    Returns:
        The URL, or "" when anything needed is missing or the template is bad.
    """
    if not template or not site_url or not session.project_id or not session.patient_id:
        return ""
    try:
        return template.format(
            site=site_url.rstrip("/"),
            project=session.project_id,
            patient=session.patient_id,
        )
    except (KeyError, IndexError, ValueError):
        # A malformed template must cost the link, never the worklist.
        return ""


def sort_rows(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Most urgent first: by status, then fewest days left, then oldest session."""
    return sorted(
        rows,
        key=lambda r: (
            r["sort_rank"],
            r["days_left"] if r["days_left"] is not None else 9999,
            r["session_date"] or "9999-99-99",
        ),
    )


def summarize(rows: List[Dict[str, Any]]) -> Dict[str, int]:
    """Count rows per status, for the tiles across the top of the page."""
    counts = {status.value: 0 for status in Status}
    for row in rows:
        counts[row["status"]] = counts.get(row["status"], 0) + 1
    return counts


__all__ = [
    "BACKLOG_THRESHOLD_DAYS",
    "DUE_SOON_THRESHOLD",
    "REPORT_DEADLINE_BUSINESS_DAYS",
    "STATUS_ORDER",
    "Session",
    "Status",
    "classify",
    "is_cancellation_label",
    "parse_session",
    "parse_sessions",
    "requires_report",
    "patient_metadata",
    "session_metadata",
    "session_url",
    "subject_url",
    "sort_rows",
    "summarize",
    "to_row",
]
