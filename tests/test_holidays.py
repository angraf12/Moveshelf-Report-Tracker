"""Tests for the built-in holiday calendar.

Nine holidays, confirmed by the clinical lead on 2026-07-28: New Year's Day,
Martin Luther King Day, Memorial Day, Independence Day, Labor Day, Thanksgiving,
the Friday after Thanksgiving, Christmas Eve and Christmas Day. All are computed
rather than listed, so the app never needs its holiday file updated for a new
year.

Good Friday was briefly included, from an earlier and less specific instruction,
and confirmed out on 2026-07-28. ``good_friday()`` survives as a helper for any
site that does observe it, since a moving date cannot practically be listed in a
static file, and the Easter tests below still cover it.
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from tracker.businessdays import (
    HOLIDAYS_PER_YEAR,
    REPORT_DEADLINE_BUSINESS_DAYS,
    colliding_years,
    add_business_days,
    builtin_holidays,
    business_days_between,
    default_holidays,
    due_date,
    easter_sunday,
    good_friday,
    is_business_day,
    load_holidays,
    observed,
)


class TestEaster:
    """Anonymous Gregorian computus, checked against published Easter dates."""

    @pytest.mark.parametrize(
        "year,expected",
        [
            (2024, date(2024, 3, 31)),
            (2025, date(2025, 4, 20)),
            (2026, date(2026, 4, 5)),
            (2027, date(2027, 3, 28)),
            (2028, date(2028, 4, 16)),
            (2030, date(2030, 4, 21)),
            (2038, date(2038, 4, 25)),  # the latest Easter can ever fall
        ],
    )
    def test_known_easter_dates(self, year, expected):
        assert easter_sunday(year) == expected

    def test_easter_is_always_a_sunday(self):
        for year in range(1990, 2101):
            assert easter_sunday(year).weekday() == 6, year

    def test_good_friday_is_always_a_friday(self):
        for year in range(1990, 2101):
            assert good_friday(year).weekday() == 4, year

    def test_good_friday_is_two_days_before_easter(self):
        assert good_friday(2026) == date(2026, 4, 3)
        assert easter_sunday(2026) - good_friday(2026) == timedelta(days=2)

    def test_easter_sunday_alone_could_never_move_a_deadline(self):
        """Why Good Friday is used instead: a Sunday is already skipped."""
        sunday = easter_sunday(2026)
        assert not is_business_day(sunday)
        span = (date(2026, 4, 1), date(2026, 4, 8))
        assert business_days_between(*span, {sunday}) == business_days_between(*span)


class TestObserved:
    def test_a_weekday_holiday_is_unchanged(self):
        assert observed(date(2026, 12, 25)) == date(2026, 12, 25)  # a Friday

    def test_a_saturday_holiday_moves_to_the_friday_before(self):
        assert observed(date(2027, 12, 25)) == date(2027, 12, 24)

    def test_a_sunday_holiday_moves_to_the_monday_after(self):
        assert observed(date(2022, 12, 25)) == date(2022, 12, 26)

    def test_the_result_is_always_a_weekday(self):
        for offset in range(400):
            assert observed(date(2026, 1, 1) + timedelta(days=offset)).weekday() < 5


class TestBuiltinHolidays:
    def test_the_full_schedule_for_2026(self):
        """Every date here checked against a published 2026 calendar."""
        assert builtin_holidays(2026) == {
            date(2026, 1, 1): "New Year's Day",
            date(2026, 1, 19): "Martin Luther King Day",
            date(2026, 5, 25): "Memorial Day",
            date(2026, 7, 3): "Independence Day",       # 4 July is a Saturday
            date(2026, 9, 7): "Labor Day",
            date(2026, 11, 26): "Thanksgiving Day",
            date(2026, 11, 27): "Friday after Thanksgiving",
            date(2026, 12, 24): "Christmas Eve",
            date(2026, 12, 25): "Christmas Day",
        }

    @pytest.mark.parametrize(
        "year,day", [(2025, 20), (2026, 19), (2027, 18), (2028, 17)]
    )
    def test_mlk_day_is_the_third_monday_in_january(self, year, day):
        found = [d for d, n in builtin_holidays(year).items()
                 if n == "Martin Luther King Day"][0]
        assert found == date(year, 1, day)
        assert found.weekday() == 0

    @pytest.mark.parametrize(
        "year,expected",
        [
            (2025, date(2025, 7, 4)),   # a Friday
            (2026, date(2026, 7, 3)),   # 4th is a Saturday, observed Friday
            (2027, date(2027, 7, 5)),   # 4th is a Sunday, observed Monday
            (2028, date(2028, 7, 4)),   # a Tuesday
        ],
    )
    def test_independence_day_shifts_off_the_weekend(self, year, expected):
        assert [d for d, n in builtin_holidays(year).items()
                if n == "Independence Day"][0] == expected

    @pytest.mark.parametrize(
        "year,day", [(2025, 27), (2026, 26), (2027, 25), (2028, 23)]
    )
    def test_thanksgiving_is_the_fourth_thursday_in_november(self, year, day):
        found = [d for d, n in builtin_holidays(year).items()
                 if n == "Thanksgiving Day"][0]
        assert found == date(year, 11, day)
        assert found.weekday() == 3

    def test_the_friday_after_thanksgiving_is_always_the_next_day(self):
        for year in range(2024, 2041):
            days = builtin_holidays(year)
            thanks = [d for d, n in days.items() if n == "Thanksgiving Day"][0]
            friday = [d for d, n in days.items() if n == "Friday after Thanksgiving"][0]
            assert friday == thanks + timedelta(days=1)
            assert friday.weekday() == 4

    def test_christmas_eve_is_included(self):
        assert date(2026, 12, 24) in builtin_holidays(2026)

    @pytest.mark.parametrize(
        "year,day", [(2025, 26), (2026, 25), (2027, 31), (2028, 29)]
    )
    def test_memorial_day_is_the_last_monday_in_may(self, year, day):
        found = [d for d, n in builtin_holidays(year).items() if n == "Memorial Day"][0]
        assert found == date(year, 5, day)
        assert found.weekday() == 0

    @pytest.mark.parametrize("year,day", [(2025, 1), (2026, 7), (2027, 6), (2028, 4)])
    def test_labor_day_is_the_first_monday_in_september(self, year, day):
        found = [d for d, n in builtin_holidays(year).items() if n == "Labor Day"][0]
        assert found == date(year, 9, day)
        assert found.weekday() == 0

    def test_christmas_on_a_saturday_is_observed_on_the_friday(self):
        assert date(2027, 12, 24) in builtin_holidays(2027)

    def test_new_year_on_a_saturday_is_observed_the_previous_december(self):
        # 1 Jan 2028 is a Saturday, so the day off is Friday 31 Dec 2027.
        assert date(2027, 12, 31) in builtin_holidays(2028)

    def test_every_builtin_holiday_falls_on_a_weekday(self):
        # A holiday left on a weekend would silently do nothing at all.
        for year in range(2020, 2041):
            for day in builtin_holidays(year):
                assert day.weekday() < 5, (year, day)

    def test_nine_every_single_year(self):
        for year in range(2020, 2061):
            assert len(builtin_holidays(year)) == HOLIDAYS_PER_YEAR, year

    def test_no_year_ever_loses_a_holiday_to_a_collision(self):
        # A swallowed dict key would cost a day off with no symptom other than a
        # quietly tighter deadline, so this is a standing check.
        assert colliding_years(2020, 2060) == {}

    def test_good_friday_is_not_observed(self):
        # Confirmed out on 2026-07-28. The helper stays, the holiday does not.
        for year in range(2020, 2041):
            assert good_friday(year) not in builtin_holidays(year), year


class TestChristmasCollision:
    """Christmas Eve and Christmas Day clash in about one year in three.

    Weekend shifting puts them on the same date: when Christmas falls on a
    Saturday both land on Friday 24 December, and when Christmas Eve falls on a
    Sunday both land on Monday 25 December. Two days off must still be granted,
    so the extra one moves in whichever direction the colliding shift was already
    heading, which is what employers do in practice.
    """

    def test_a_saturday_christmas_puts_the_pair_on_thursday_and_friday(self):
        # 2027: 25 Dec is a Saturday, shifting back onto Friday 24 Dec.
        days = builtin_holidays(2027)
        assert days[date(2027, 12, 23)] == "Christmas Eve"
        assert days[date(2027, 12, 24)] == "Christmas Day"

    def test_a_sunday_christmas_eve_puts_the_pair_on_monday_and_tuesday(self):
        # 2028: 24 Dec is a Sunday, shifting forward onto Monday 25 Dec.
        days = builtin_holidays(2028)
        assert days[date(2028, 12, 25)] == "Christmas Day"
        assert days[date(2028, 12, 26)] == "Christmas Eve"

    @pytest.mark.parametrize("year", [2027, 2028, 2032, 2034, 2038])
    def test_every_known_clash_year_still_grants_two_days(self, year):
        days = builtin_holidays(year)
        pair = sorted(d for d, n in days.items()
                      if n in ("Christmas Eve", "Christmas Day"))
        assert len(pair) == 2, year
        assert all(d.weekday() < 5 for d in pair), year

    def test_the_two_days_are_always_consecutive_weekdays(self):
        for year in range(2020, 2061):
            days = builtin_holidays(year)
            pair = sorted(d for d, n in days.items()
                          if n in ("Christmas Eve", "Christmas Day"))
            assert len(pair) == 2, year
            gap = (pair[1] - pair[0]).days
            # One day apart, or three across a weekend (Friday then Monday).
            assert gap in (1, 3), (year, pair)
            assert all(d.weekday() < 5 for d in pair), year

    def test_a_site_can_add_the_day_back_in_holidays_txt(self, tmp_path):
        f = tmp_path / "holidays.txt"
        f.write_text("2027-12-23\n", encoding="utf-8")
        holidays = load_holidays(f, 2027, 2027)
        assert date(2027, 12, 23) in holidays
        assert date(2027, 12, 24) in holidays

    def test_default_holidays_reaches_across_the_year_boundary(self):
        assert date(2027, 12, 31) in default_holidays(2028, 2028)


class TestHolidaysAffectDeadlines:
    def test_christmas_pushes_a_due_date_out(self):
        holidays = load_holidays(None, 2026, 2027)
        start = date(2026, 12, 21)  # the Monday of Christmas week
        assert add_business_days(start, 7, holidays) > add_business_days(start, 7)

    def test_the_invariant_still_holds_with_builtin_holidays(self):
        holidays = load_holidays(None, 2026, 2027)
        processing = date(2026, 12, 21)
        due = due_date(processing, holidays)
        for offset in range(40):
            today = processing + timedelta(days=offset)
            assert business_days_between(today, due, holidays) == (
                REPORT_DEADLINE_BUSINESS_DAYS
                - business_days_between(processing, today, holidays)
            )


class TestHolidayFileOverrides:
    def test_the_file_adds_to_the_builtin_list(self, tmp_path):
        f = tmp_path / "holidays.txt"
        f.write_text("2026-11-26\n", encoding="utf-8")
        holidays = load_holidays(f, 2026, 2026)
        assert date(2026, 11, 26) in holidays  # added
        assert date(2026, 12, 25) in holidays  # built-in survives

    def test_a_leading_minus_removes_a_builtin_holiday(self, tmp_path):
        f = tmp_path / "holidays.txt"
        f.write_text("# we work Good Friday\n-2026-04-03\n", encoding="utf-8")
        holidays = load_holidays(f, 2026, 2026)
        assert date(2026, 4, 3) not in holidays
        assert date(2026, 12, 25) in holidays

    def test_removing_a_date_that_is_not_there_is_harmless(self, tmp_path):
        f = tmp_path / "holidays.txt"
        f.write_text("-2026-06-15\n", encoding="utf-8")
        assert date(2026, 12, 25) in load_holidays(f, 2026, 2026)

    def test_no_file_still_gives_the_builtin_holidays(self, tmp_path):
        holidays = load_holidays(tmp_path / "nope.txt", 2026, 2026)
        assert date(2026, 12, 25) in holidays
        assert len(holidays) >= 5

    def test_an_unreadable_file_leaves_the_builtins_in_place(self, tmp_path):
        # A directory where a file is expected: read fails, holidays survive.
        d = tmp_path / "holidays.txt"
        d.mkdir()
        assert date(2026, 12, 25) in load_holidays(d, 2026, 2026)
