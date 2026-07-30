"""Business-day arithmetic for report deadlines.

Pure functions, no I/O beyond reading the holiday file. This module is the piece
everything else trusts, so it is deliberately small and heavily tested.

Counting convention, chosen once and applied everywhere:

    business_days_between(a, b) counts business days AFTER a, up to and
    including b.

So ``business_days_between(d, d) == 0``, and Monday to Tuesday is 1. That makes
the counts additive, which gives the invariant the whole app rests on::

    due        = add_business_days(processing_completed, 7)
    days_since = business_days_between(processing_completed, today)
    days_left  = business_days_between(today, due)
    # and therefore, always:
    days_left == 7 - days_since

A business day is Monday through Friday and not listed in the holiday file.
"""
from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from typing import Dict, FrozenSet, Iterable, Optional

# The seven-business-day report deadline.
REPORT_DEADLINE_BUSINESS_DAYS = 7

# Dates outside this range are treated as data-entry errors rather than real
# dates. Live Moveshelf data contains at least one such value ("0007-01-12" in
# a return-to-clinic field), and letting it through would produce nonsensical
# counts and a very slow loop.
_MIN_YEAR = 1990
_MAX_YEAR = 2100

SATURDAY = 5


def parse_iso_date(value: object) -> Optional[date]:
    """Parse a ``YYYY-MM-DD`` (or ISO datetime) value, or return None.

    Never raises. Anything unparseable, empty, out of plausible range, or of the
    wrong type comes back as None so a single bad field cannot take down the app.

    Args:
        value: Typically a string from Moveshelf metadata. Lists are accepted and
            their first element used, since some metadata fields are list-valued.

    Returns:
        The parsed date, or None.
    """
    if isinstance(value, (list, tuple)):
        value = value[0] if value else None
    if isinstance(value, date):
        return value if _MIN_YEAR <= value.year <= _MAX_YEAR else None
    if not isinstance(value, str):
        return None
    text = value.strip()
    if len(text) < 10:
        return None
    try:
        parsed = date.fromisoformat(text[:10])
    except ValueError:
        return None
    if not _MIN_YEAR <= parsed.year <= _MAX_YEAR:
        return None
    return parsed


def easter_sunday(year: int) -> date:
    """Easter Sunday for a Gregorian year, by the anonymous Gregorian computus.

    Args:
        year: Gregorian year.

    Returns:
        The date of Easter Sunday, which is always a Sunday.
    """
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    lunar = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * lunar) // 451
    month, day = divmod(h + lunar - 7 * m + 114, 31)
    return date(year, month, day + 1)


def good_friday(year: int) -> date:
    """The Friday before Easter Sunday.

    Easter Sunday itself falls on a weekend and so can never affect a
    business-day count. Good Friday is the weekday actually observed, which is
    what "Easter" means for a deadline calculation. Change this to
    ``+ timedelta(days=1)`` if the site observes Easter Monday instead.
    """
    return easter_sunday(year) - timedelta(days=2)


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    """The nth given weekday of a month. ``n = -1`` means the last one."""
    if n > 0:
        first = date(year, month, 1)
        offset = (weekday - first.weekday()) % 7
        return first + timedelta(days=offset + 7 * (n - 1))
    if month == 12:
        last = date(year, 12, 31)
    else:
        last = date(year, month + 1, 1) - timedelta(days=1)
    return last - timedelta(days=(last.weekday() - weekday) % 7)


def observed(day: date) -> date:
    """Shift a fixed-date holiday to the weekday it is actually observed.

    US practice: a Saturday holiday is observed the preceding Friday, a Sunday
    holiday the following Monday. This matters, because a holiday left on a
    weekend has no effect at all on a business-day count, which would quietly
    make a deadline one day too tight.
    """
    if day.weekday() == SATURDAY:
        return day - timedelta(days=1)
    if day.weekday() == SATURDAY + 1:
        return day + timedelta(days=1)
    return day


def _step_weekday(day: date, forward: bool) -> date:
    """The next weekday before or after ``day``, skipping the weekend."""
    delta = timedelta(days=1 if forward else -1)
    current = day + delta
    while current.weekday() >= SATURDAY:
        current += delta
    return current


def builtin_holidays(year: int) -> Dict[date, str]:
    """The observed holidays for one year, as ``{date: name}``.

    The schedule was confirmed by the clinical lead on 2026-07-28::

        New Year's Day             1 January
        Martin Luther King Day     third Monday in January
        Memorial Day               last Monday in May
        Independence Day           4 July
        Labor Day                  first Monday in September
        Thanksgiving Day           fourth Thursday in November
        Friday after Thanksgiving  the day after
        Christmas Eve              24 December
        Christmas Day              25 December

    Every one is computed rather than listed, so no file needs updating for a new
    year. Fixed-date holidays shift to the weekday they are observed on; the
    Monday, Thursday and Friday ones already fall on weekdays.

    **Good Friday is deliberately not here.** It was included briefly, then
    confirmed out on 2026-07-28. ``good_friday()`` is kept because a site that
    does observe it cannot practically list a moving date in a static file: add
    ``good_friday(year): "Good Friday"`` to the dict below to restore it.

    New Year's Day can be observed on 31 December of the *previous* year, when 1
    January falls on a Saturday, so the returned dates are not guaranteed to lie
    inside ``year``.

    **Christmas Eve and Christmas Day collide in roughly one year in three** once
    weekend shifting is applied, and the collision is resolved by moving Christmas
    Eve to an adjacent weekday so two days are always granted. The direction
    follows whichever shift caused the clash, which is what employers do in
    practice:

    - Christmas on a Saturday shifts *back* onto Friday 24 December, so the extra
      day goes back as well: Thursday 23 and Friday 24 (2027, 2032, 2038).
    - Christmas Eve on a Sunday shifts *forward* onto Monday 25 December, so the
      extra day goes forward: Monday 25 and Tuesday 26 (2028, 2034).
    """
    thanksgiving = _nth_weekday(year, 11, 3, 4)
    christmas = observed(date(year, 12, 25))
    christmas_eve = observed(date(year, 12, 24))

    if christmas_eve == christmas:
        # Move whichever way the colliding shift was already heading, so the two
        # days off stay consecutive and bracket the real date.
        if date(year, 12, 25).weekday() == SATURDAY:
            christmas_eve = _step_weekday(christmas, forward=False)
        else:
            christmas_eve = _step_weekday(christmas, forward=True)

    return {
        observed(date(year, 1, 1)): "New Year's Day",
        _nth_weekday(year, 1, 0, 3): "Martin Luther King Day",
        _nth_weekday(year, 5, 0, -1): "Memorial Day",
        observed(date(year, 7, 4)): "Independence Day",
        _nth_weekday(year, 9, 0, 1): "Labor Day",
        thanksgiving: "Thanksgiving Day",
        thanksgiving + timedelta(days=1): "Friday after Thanksgiving",
        christmas_eve: "Christmas Eve",
        christmas: "Christmas Day",
    }


# How many holidays every year should yield. Any shortfall means two of them
# landed on the same date and one was silently lost, which is a bug.
HOLIDAYS_PER_YEAR = 9


def colliding_years(first_year: int, last_year: int) -> Dict[int, int]:
    """Years where two holidays still share a date, and how many were lost.

    Should always be empty: ``builtin_holidays`` resolves the only realistic
    clash, between Christmas Eve and Christmas Day. Kept as a standing check,
    because a silently swallowed dict key would cost a day off with no symptom
    other than a slightly tighter deadline.
    """
    out: Dict[int, int] = {}
    for year in range(first_year, last_year + 1):
        lost = HOLIDAYS_PER_YEAR - len(builtin_holidays(year))
        if lost:
            out[year] = lost
    return out


def default_holidays(first_year: int, last_year: int) -> Dict[date, str]:
    """Built-in holidays across a span of years, inclusive.

    The span is widened by one year at each end so that a holiday observed
    across a year boundary (31 December for New Year's Day) is never missed.
    """
    out: Dict[date, str] = {}
    for year in range(first_year - 1, last_year + 2):
        out.update(builtin_holidays(year))
    return out


def load_holidays(
    path: Optional[Path],
    first_year: Optional[int] = None,
    last_year: Optional[int] = None,
    include_builtin: bool = True,
) -> FrozenSet[date]:
    """Build the holiday set: built-in holidays, adjusted by an optional file.

    The file holds one ``YYYY-MM-DD`` per line. A line beginning with ``-``
    *removes* that date, which is how a site drops a built-in holiday it does not
    observe. Blank lines, ``#`` comments and unparseable lines are ignored, so a
    typo costs one holiday rather than the whole file.

    Args:
        path: Path to ``holidays.txt``, or None for built-ins only.
        first_year: First year to generate built-ins for. Defaults to two years
            before today, which comfortably covers any lookback window.
        last_year: Last year. Defaults to next year.
        include_builtin: False for the file's contents alone.

    Returns:
        Frozen set of holiday dates. Never raises: an unreadable file leaves the
        built-in holidays in place.
    """
    today = date.today()
    first_year = first_year if first_year is not None else today.year - 2
    last_year = last_year if last_year is not None else today.year + 1

    holidays = set(default_holidays(first_year, last_year)) if include_builtin else set()

    if path is None:
        return frozenset(holidays)
    try:
        text = Path(path).read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError):
        return frozenset(holidays)

    for line in text.splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        removing = line.startswith("-")
        parsed = parse_iso_date(line[1:].strip() if removing else line)
        if parsed is None:
            continue
        holidays.discard(parsed) if removing else holidays.add(parsed)
    return frozenset(holidays)


def is_business_day(day: date, holidays: Iterable[date] = ()) -> bool:
    """Is this a working day: a weekday that is not an observed holiday?"""
    if day.weekday() >= SATURDAY:
        return False
    holiday_set = holidays if isinstance(holidays, (set, frozenset)) else set(holidays)
    return day not in holiday_set


def _weekdays_in_range(start: date, end: date) -> int:
    """Count Mon-Fri days in the inclusive range [start, end]. Assumes start <= end."""
    total_days = (end - start).days + 1
    full_weeks, remainder = divmod(total_days, 7)
    count = full_weeks * 5
    for offset in range(remainder):
        if (start + timedelta(days=offset)).weekday() < SATURDAY:
            count += 1
    return count


def business_days_between(
    start: date, end: date, holidays: Iterable[date] = ()
) -> int:
    """Business days after ``start`` up to and including ``end``.

    Negative when ``end`` precedes ``start``, which is how overdue is expressed.
    Computed arithmetically rather than by iterating days, so a wildly wrong
    input date costs nothing.

    Args:
        start: The earlier date.
        end: The later date.
        holidays: Dates to exclude in addition to weekends.

    Returns:
        Signed count of business days.
    """
    if end == start:
        return 0
    if end < start:
        return -business_days_between(end, start, holidays)

    first = start + timedelta(days=1)
    count = _weekdays_in_range(first, end)

    holiday_set = holidays if isinstance(holidays, (set, frozenset)) else set(holidays)
    for holiday in holiday_set:
        if first <= holiday <= end and holiday.weekday() < SATURDAY:
            count -= 1
    return count


def add_business_days(start: date, n: int, holidays: Iterable[date] = ()) -> date:
    """Advance ``n`` business days from ``start``.

    ``n == 0`` returns ``start`` unchanged, even if ``start`` is itself a weekend
    or holiday, because the caller is asking for a deadline offset rather than
    for the next working day.

    Args:
        start: Starting date.
        n: Number of business days to advance. Must not be negative.
        holidays: Dates to skip in addition to weekends.

    Returns:
        The resulting date, which is always a business day when ``n > 0``.

    Raises:
        ValueError: If ``n`` is negative.
    """
    if n < 0:
        raise ValueError("add_business_days does not go backwards")
    holiday_set = holidays if isinstance(holidays, (set, frozenset)) else set(holidays)
    current = start
    remaining = n
    while remaining > 0:
        current += timedelta(days=1)
        if current.weekday() < SATURDAY and current not in holiday_set:
            remaining -= 1
    return current


def due_date(
    processing_completed: date,
    holidays: Iterable[date] = (),
    deadline: int = REPORT_DEADLINE_BUSINESS_DAYS,
) -> date:
    """The date a report is due, given when processing was completed."""
    return add_business_days(processing_completed, deadline, holidays)


__all__ = [
    "REPORT_DEADLINE_BUSINESS_DAYS",
    "add_business_days",
    "HOLIDAYS_PER_YEAR",
    "builtin_holidays",
    "colliding_years",
    "business_days_between",
    "default_holidays",
    "due_date",
    "easter_sunday",
    "good_friday",
    "is_business_day",
    "load_holidays",
    "observed",
    "parse_iso_date",
]
