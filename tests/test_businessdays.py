"""Tests for the deadline math.

Reference calendar used throughout: July 2026.

    Mo Tu We Th Fr Sa Su
        1  2  3  4  5      (Jul 1 is a Wednesday)
     6  7  8  9 10 11 12
    13 14 15 16 17 18 19
    20 21 22 23 24 25 26
    27 28 29 30 31
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from tracker.businessdays import (
    REPORT_DEADLINE_BUSINESS_DAYS,
    add_business_days,
    builtin_holidays,
    default_holidays,
    easter_sunday,
    good_friday,
    observed,
    business_days_between,
    due_date,
    is_business_day,
    load_holidays,
    parse_iso_date,
)

MON = date(2026, 7, 6)
TUE = date(2026, 7, 7)
FRI = date(2026, 7, 10)
SAT = date(2026, 7, 11)
SUN = date(2026, 7, 12)
NEXT_MON = date(2026, 7, 13)


class TestParseIsoDate:
    def test_plain_date(self):
        assert parse_iso_date("2026-07-06") == MON

    def test_iso_datetime_is_truncated(self):
        assert parse_iso_date("2026-07-06T00:00:00+00:00") == MON

    def test_list_valued_metadata_uses_first_element(self):
        assert parse_iso_date(["2026-07-06"]) == MON

    def test_passes_through_a_real_date(self):
        assert parse_iso_date(MON) == MON

    @pytest.mark.parametrize(
        "value", ["", "   ", None, 42, [], {}, "not a date", "2026-13-45", "07/06/2026"]
    )
    def test_junk_returns_none_without_raising(self, value):
        assert parse_iso_date(value) is None

    def test_rejects_the_real_typo_seen_in_live_data(self):
        # sessioninfo-return-to-clinic contained this on a live CHI-Gait session.
        assert parse_iso_date("0007-01-12") is None

    def test_rejects_absurd_future_year(self):
        assert parse_iso_date("9999-01-01") is None


class TestIsBusinessDay:
    def test_weekday_is_a_business_day(self):
        assert is_business_day(MON)

    @pytest.mark.parametrize("day", [SAT, SUN])
    def test_weekend_is_not(self, day):
        assert not is_business_day(day)

    def test_holiday_is_not(self):
        assert not is_business_day(MON, {MON})

    def test_accepts_a_plain_list_of_holidays(self):
        assert not is_business_day(MON, [MON])


class TestBusinessDaysBetween:
    def test_same_day_is_zero(self):
        assert business_days_between(MON, MON) == 0

    def test_consecutive_weekdays(self):
        assert business_days_between(MON, TUE) == 1

    def test_across_a_weekend(self):
        assert business_days_between(FRI, NEXT_MON) == 1

    def test_a_full_week(self):
        assert business_days_between(MON, NEXT_MON) == 5

    def test_weekend_days_contribute_nothing(self):
        assert business_days_between(FRI, SAT) == 0
        assert business_days_between(FRI, SUN) == 0

    def test_is_negative_when_reversed(self):
        assert business_days_between(TUE, MON) == -1
        assert business_days_between(NEXT_MON, MON) == -5

    def test_holiday_is_excluded(self):
        assert business_days_between(MON, NEXT_MON, {date(2026, 7, 8)}) == 4

    def test_holiday_falling_on_a_weekend_changes_nothing(self):
        assert business_days_between(MON, NEXT_MON, {SAT}) == 5

    def test_holiday_on_the_start_date_changes_nothing(self):
        # The range is exclusive of start, so a holiday there is irrelevant.
        assert business_days_between(MON, NEXT_MON, {MON}) == 5

    def test_holiday_outside_the_range_changes_nothing(self):
        assert business_days_between(MON, TUE, {date(2026, 8, 3)}) == 1

    def test_long_span_matches_a_brute_force_count(self):
        start, end = date(2026, 1, 1), date(2026, 12, 31)
        holidays = {date(2026, 7, 3), date(2026, 11, 26), date(2026, 12, 25)}
        brute = sum(
            1
            for i in range(1, (end - start).days + 1)
            if is_business_day(start + timedelta(days=i), holidays)
        )
        assert business_days_between(start, end, holidays) == brute


class TestAddBusinessDays:
    def test_zero_returns_the_start_unchanged(self):
        assert add_business_days(MON, 0) == MON

    def test_zero_does_not_move_off_a_weekend(self):
        assert add_business_days(SAT, 0) == SAT

    def test_one_day(self):
        assert add_business_days(MON, 1) == TUE

    def test_skips_the_weekend(self):
        assert add_business_days(FRI, 1) == NEXT_MON

    def test_seven_business_days_from_a_monday(self):
        # Jul 7, 8, 9, 10, 13, 14, 15
        assert add_business_days(MON, 7) == date(2026, 7, 15)

    def test_skips_a_holiday(self):
        assert add_business_days(MON, 7, {date(2026, 7, 8)}) == date(2026, 7, 16)

    def test_starting_on_a_weekend_lands_on_a_weekday(self):
        assert add_business_days(SAT, 1) == NEXT_MON

    def test_result_is_always_a_business_day(self):
        holidays = {date(2026, 7, 8), date(2026, 7, 16)}
        for offset in range(30):
            for n in range(1, 12):
                result = add_business_days(MON + timedelta(days=offset), n, holidays)
                assert is_business_day(result, holidays)

    def test_negative_is_rejected(self):
        with pytest.raises(ValueError):
            add_business_days(MON, -1)


class TestDueDate:
    def test_uses_the_seven_day_deadline(self):
        assert due_date(MON) == add_business_days(MON, REPORT_DEADLINE_BUSINESS_DAYS)

    def test_deadline_is_seven(self):
        assert REPORT_DEADLINE_BUSINESS_DAYS == 7


class TestTheInvariantTheAppRestsOn:
    """days_left == deadline - days_since_processing, for every day and holiday set.

    The status color, the sort order and the countdown all assume these two
    numbers stay consistent. If this breaks, the app lies to a clinician.
    """

    @pytest.mark.parametrize(
        "holidays",
        [
            frozenset(),
            frozenset({date(2026, 7, 8)}),
            frozenset({date(2026, 7, 8), date(2026, 7, 9), date(2026, 7, 16)}),
            frozenset({SAT, SUN}),
        ],
    )
    def test_holds_for_every_today_across_two_months(self, holidays):
        processing = MON
        due = due_date(processing, holidays)
        for offset in range(0, 60):
            today = processing + timedelta(days=offset)
            days_since = business_days_between(processing, today, holidays)
            days_left = business_days_between(today, due, holidays)
            assert days_left == REPORT_DEADLINE_BUSINESS_DAYS - days_since, (
                f"broke on {today} with holidays {sorted(holidays)}"
            )

    def test_due_today_means_exactly_seven_business_days_elapsed(self):
        processing = MON
        due = due_date(processing)
        assert business_days_between(processing, due) == REPORT_DEADLINE_BUSINESS_DAYS
        assert business_days_between(due, due) == 0


class TestLoadHolidays:
    """These cover file parsing, so they switch the built-in list off.

    Built-in behavior is covered by TestBuiltinHolidays below.
    """

    def test_reads_dates_ignoring_comments_and_blanks(self, tmp_path):
        f = tmp_path / "holidays.txt"
        f.write_text(
            "# Observed holidays 2026\n"
            "2026-07-03\n"
            "\n"
            "2026-11-26   # Thanksgiving\n"
            "   \n"
            "2026-12-25\n",
            encoding="utf-8",
        )
        assert load_holidays(f, include_builtin=False) == frozenset(
            {date(2026, 7, 3), date(2026, 11, 26), date(2026, 12, 25)}
        )

    def test_a_bad_line_costs_one_holiday_not_the_file(self, tmp_path):
        f = tmp_path / "holidays.txt"
        f.write_text("2026-07-03\nnot-a-date\n2026-12-25\n", encoding="utf-8")
        assert load_holidays(f, include_builtin=False) == frozenset(
            {date(2026, 7, 3), date(2026, 12, 25)}
        )

    def test_missing_file_with_builtins_off_is_empty(self, tmp_path):
        assert load_holidays(tmp_path / "nope.txt", include_builtin=False) == frozenset()

    def test_none_path_is_allowed(self):
        assert load_holidays(None, include_builtin=False) == frozenset()

    def test_tolerates_a_utf8_bom(self, tmp_path):
        f = tmp_path / "holidays.txt"
        f.write_bytes("﻿2026-07-03\n".encode("utf-8"))
        assert load_holidays(f, include_builtin=False) == frozenset({date(2026, 7, 3)})
