"""Tests for session parsing and status classification.

The payload shapes here mirror what shriners/CHI-Gait actually returned on
2026-07-27, including the two nesting depths and the lowercase "no show".
"""
from __future__ import annotations

import json
from datetime import date

import pytest

from tracker.businessdays import add_business_days
from tracker.model import (
    Session,
    Status,
    classify,
    is_cancellation_label,
    parse_session,
    parse_sessions,
    patient_metadata,
    requires_report,
    session_metadata,
    session_url,
    subject_url,
    sort_rows,
    summarize,
    to_row,
)

MON = date(2026, 7, 6)          # processing completed
DUE = date(2026, 7, 15)         # seven business days later, a Wednesday
SITE = "https://shriners.moveshelf.com/"


def raw_session(
    sid="U2Vzc2lvbjEyMw==",
    session_date="2026-07-01T00:00:00+00:00",
    name="Demo, Patient A",
    mrn="9000001",
    **fields,
):
    """Build a payload shaped exactly like the live API returns."""
    meta = {
        "sessioninfo-therapist": "Dawson, Renata, MPT",
        "sessioninfo-date-processing-completed": "",
        "sessioninfo-pt-evaluation-date": "",
        "sessioninfo-pdf-to-emr-date": "",
        "sessioninfo-interpretation-completed": "",
        "sessioninfo-cancellation": "",
    }
    meta.update(fields)
    return {
        "id": sid,
        "date": session_date,
        # Doubly nested, as a JSON string. This is the shape that trips people up.
        "metadata": json.dumps({"metadata": json.dumps(meta)}),
        "patient": {
            "id": "UGF0aWVudDE=",
            "name": name,
            # Singly nested, as a JSON string. Different from the session above.
            "metadata": json.dumps({"ehr-id": mrn, "subject-sex": "Female"}),
        },
        "projectPath": "/demo/",
    }


class TestMetadataUnwrapping:
    def test_session_metadata_is_doubly_nested(self):
        raw = raw_session(**{"sessioninfo-therapist": "Whitfield, Marlo, PT, PCS"})
        assert session_metadata(raw)["sessioninfo-therapist"] == "Whitfield, Marlo, PT, PCS"

    def test_patient_metadata_is_singly_nested(self):
        assert patient_metadata(raw_session(mrn="1234567"))["ehr-id"] == "1234567"

    def test_session_metadata_accepts_a_plain_dict(self):
        raw = {"metadata": {"metadata": {"sessioninfo-therapist": "X"}}}
        assert session_metadata(raw)["sessioninfo-therapist"] == "X"

    def test_session_metadata_tolerates_a_flattened_payload(self):
        # Insurance against the API dropping a nesting level.
        raw = {"metadata": json.dumps({"sessioninfo-therapist": "X"})}
        assert session_metadata(raw)["sessioninfo-therapist"] == "X"

    @pytest.mark.parametrize("bad", [None, "", "not json", 42, [], "{"])
    def test_malformed_metadata_returns_empty_not_an_exception(self, bad):
        assert session_metadata({"metadata": bad}) == {}
        assert patient_metadata({"patient": {"metadata": bad}}) == {}

    def test_missing_patient_key_entirely(self):
        assert patient_metadata({"id": "x"}) == {}


class TestParseSession:
    def test_extracts_every_displayed_field(self):
        raw = raw_session(
            **{
                "sessioninfo-date-processing-completed": "2026-07-06",
                "sessioninfo-pt-evaluation-date": "2026-07-10",
                "sessioninfo-pdf-to-emr-date": "2026-07-10",
                "sessioninfo-interpretation-completed": "2026-08-04",
            }
        )
        s = parse_session(raw, "proj1")
        assert s.subject_id == "Demo, Patient A"
        assert s.mrn == "9000001"
        assert s.session_date == date(2026, 7, 1)
        assert s.therapist_raw == "Dawson, Renata, MPT"
        assert s.processing_completed == date(2026, 7, 6)
        assert s.pt_evaluation == date(2026, 7, 10)
        assert s.pdf_to_emr == date(2026, 7, 10)
        assert s.interpretation == date(2026, 8, 4)
        assert s.project_id == "proj1"

    def test_blank_dates_become_none_not_epoch(self):
        s = parse_session(raw_session(), "p")
        assert s.processing_completed is None
        assert s.pt_evaluation is None
        assert not s.clock_started
        assert not s.is_complete

    def test_a_session_without_an_id_is_dropped(self):
        assert parse_session(raw_session(sid=""), "p") is None

    def test_a_garbage_date_does_not_crash_the_row(self):
        # The real "0007-01-12" typo, seen live in a return-to-clinic field.
        s = parse_session(
            raw_session(**{"sessioninfo-date-processing-completed": "0007-01-12"}), "p"
        )
        assert s is not None
        assert s.processing_completed is None

    def test_list_valued_metadata_takes_the_first_element(self):
        s = parse_session(
            raw_session(**{"sessioninfo-therapist": ["Marsden, Delia, MPT, PCS"]}), "p"
        )
        assert s.therapist_raw == "Marsden, Delia, MPT, PCS"


class TestCancellation:
    @pytest.mark.parametrize(
        "value", ["Cancelled", "cancelled", "CANCELLED", "No Show", "No show", "no show"]
    )
    def test_recognized_case_insensitively(self, value):
        assert is_cancellation_label(value)

    @pytest.mark.parametrize("value", ["", None, "  ", "Completed", "Showed"])
    def test_valid_sessions_are_not_flagged(self, value):
        assert not is_cancellation_label(value)

    def test_lowercase_no_show_from_live_data_is_excluded(self):
        # 7 of 67 live CHI-Gait sessions carried exactly this value.
        raws = [
            raw_session(sid="a", **{"sessioninfo-cancellation": "no show"}),
            raw_session(sid="b"),
        ]
        parsed = parse_sessions(raws, "p")
        assert [s.session_id for s in parsed] == ["b"]

    def test_sessions_without_ids_are_also_dropped(self):
        assert parse_sessions([raw_session(sid=""), raw_session(sid="ok")], "p") != []
        assert len(parse_sessions([raw_session(sid=""), raw_session(sid="ok")], "p")) == 1


def session_with(processing=None, pt_eval=None):
    return Session(
        session_id="s", project_id="p", subject_id="Demo", mrn="1",
        session_date=date(2026, 7, 1), therapist_raw="T",
        processing_completed=processing, pt_evaluation=pt_eval,
        pdf_to_emr=None, interpretation=None, cancellation="",
    )


class TestClassify:
    def test_done_when_pt_evaluation_is_filled(self):
        s = session_with(processing=MON, pt_eval=date(2026, 7, 8))
        assert classify(s, date(2026, 7, 30)) is Status.DONE

    def test_done_wins_even_when_overdue(self):
        # Finished late is still finished. It must not stay red forever.
        s = session_with(processing=MON, pt_eval=date(2026, 7, 28))
        assert classify(s, date(2026, 8, 30)) is Status.DONE

    def test_not_started_when_processing_is_blank(self):
        assert classify(session_with(), date(2026, 7, 30)) is Status.NOT_STARTED

    def test_not_started_never_becomes_overdue(self):
        # The clock has not started, so no deadline can have passed.
        s = session_with()
        assert classify(s, date(2027, 1, 1)) is Status.NOT_STARTED

    @pytest.mark.parametrize(
        "today,expected",
        [
            (date(2026, 7, 6), Status.ON_TRACK),    # 7 left
            (date(2026, 7, 10), Status.ON_TRACK),   # 3 left
            (date(2026, 7, 13), Status.DUE_SOON),   # 2 left
            (date(2026, 7, 14), Status.DUE_SOON),   # 1 left
            (date(2026, 7, 15), Status.DUE_TODAY),  # due date itself
            (date(2026, 7, 16), Status.OVERDUE),
            (date(2026, 7, 31), Status.OVERDUE),
        ],
    )
    def test_the_full_countdown(self, today, expected):
        assert classify(session_with(processing=MON), today) is expected

    def test_a_holiday_pushes_the_deadline_out(self):
        s = session_with(processing=MON)
        holidays = frozenset({date(2026, 7, 8)})
        assert classify(s, date(2026, 7, 15)) is Status.DUE_TODAY
        assert classify(s, date(2026, 7, 15), holidays) is Status.DUE_SOON


class TestToRow:
    def test_counts_and_due_date_on_the_due_day(self):
        row = to_row(session_with(processing=MON), DUE, site_url=SITE)
        assert row["due_date"] == "2026-07-15"
        assert row["days_left"] == 0
        assert row["days_since_processing"] == 7
        assert row["status"] == "due_today"

    def test_overdue_is_a_negative_days_left(self):
        row = to_row(session_with(processing=MON), date(2026, 7, 17))
        assert row["days_left"] == -2
        assert row["status"] == "overdue"

    def test_missing_dates_give_none_not_a_misleading_zero(self):
        row = to_row(session_with(), date(2026, 7, 15))
        assert row["days_left"] is None
        assert row["days_since_processing"] is None
        assert row["due_date"] is None
        assert row["days_since_session"] == 10

    def test_completed_rows_carry_no_countdown(self):
        row = to_row(session_with(processing=MON, pt_eval=date(2026, 7, 9)), DUE)
        assert row["status"] == "done"
        assert row["days_left"] is None

    def test_normalized_therapist_is_used_but_raw_is_kept(self):
        s = session_with()
        row = to_row(s, DUE, therapist="Dawson, Renata")
        assert row["therapist"] == "Dawson, Renata"
        assert row["therapist_raw"] == "T"

    def test_row_is_json_serializable(self):
        json.dumps(to_row(session_with(processing=MON), DUE, site_url=SITE))

    def test_row_carries_no_unexpected_patient_fields(self):
        # Only what the page displays should ever reach the browser.
        row = to_row(session_with(processing=MON), DUE)
        assert set(row) == {
            "session_id", "subject_id", "mrn", "session_date", "therapist",
            "therapist_raw", "processing_completed", "pt_evaluation", "pdf_to_emr",
            "interpretation", "referral_type", "due_date", "days_left",
            "days_since_session", "days_since_processing", "status", "sort_rank",
            "url", "subject_url",
        }


class TestSessionUrl:
    def test_builds_a_clickable_link(self):
        s = session_with()
        assert session_url(s, SITE) == (
            "https://shriners.moveshelf.com/project/p/session/s"
        )

    def test_handles_a_base_url_without_a_trailing_slash(self):
        assert session_url(session_with(), "https://x.moveshelf.com").endswith(
            "/project/p/session/s"
        )

    def test_empty_when_no_site_configured(self):
        assert session_url(session_with(), "") == ""


class TestSortAndSummarize:
    def build(self, today=date(2026, 7, 15)):
        # Due dates relative to today = Wed 2026-07-15:
        #   proc Jul 13 -> due Jul 22, 5 left  -> on track
        #   proc Jul  1 -> due Jul 10, 3 past  -> overdue
        #   proc Jul  6 -> due Jul 15, 0 left  -> due today
        #   proc Jul  7 -> due Jul 16, 1 left  -> due soon
        sessions = [
            session_with(processing=date(2026, 7, 13)),             # on track
            session_with(processing=date(2026, 7, 1)),              # overdue
            session_with(),                                          # not started
            session_with(processing=MON),                            # due today
            session_with(processing=MON, pt_eval=date(2026, 7, 9)),  # done
            session_with(processing=date(2026, 7, 7)),               # due soon
        ]
        return [to_row(s, today) for s in sessions]

    def test_most_urgent_first(self):
        order = [r["status"] for r in sort_rows(self.build())]
        assert order == [
            "overdue", "due_today", "due_soon", "on_track", "not_started", "done"
        ]

    def test_summarize_counts_every_status(self):
        counts = summarize(self.build())
        assert counts["overdue"] == 1
        assert counts["due_today"] == 1
        assert counts["done"] == 1
        assert sum(counts.values()) == 6

    def test_summarize_reports_zero_rather_than_omitting_a_status(self):
        assert summarize([]) == {s.value: 0 for s in Status}


class TestBacklogSplit:
    """Overdue reports older than the threshold are separated, not hidden.

    Rationale recorded in model.BACKLOG_THRESHOLD_DAYS: at CHI-Gait a third of
    processing-complete sessions had no EMR date at a median of 111 business
    days, and those reports were in fact finished, only never dated. Left in
    Overdue they bury the handful a therapist can act on this week.
    """

    def test_off_by_default_in_the_pure_function(self):
        # classify() only splits when asked, so the model stays honest.
        s = session_with(processing=MON)
        assert classify(s, date(2026, 12, 1)) is Status.OVERDUE

    def test_recent_overdue_stays_overdue(self):
        s = session_with(processing=MON)
        # due 2026-07-15; 5 business days past due
        assert classify(s, date(2026, 7, 22), backlog_after=30) is Status.OVERDUE

    def test_far_past_due_becomes_backlog(self):
        s = session_with(processing=MON)
        assert classify(s, date(2026, 12, 1), backlog_after=30) is Status.BACKLOG

    def test_exactly_at_the_threshold_is_still_overdue(self):
        s = session_with(processing=MON)
        due = date(2026, 7, 15)
        today = add_business_days(due, 30)
        assert classify(s, today, backlog_after=30) is Status.OVERDUE
        assert classify(s, add_business_days(due, 31), backlog_after=30) is Status.BACKLOG

    def test_never_applies_to_something_not_yet_due(self):
        s = session_with(processing=MON)
        assert classify(s, date(2026, 7, 10), backlog_after=30) is Status.ON_TRACK

    def test_never_applies_to_a_finished_report(self):
        s = session_with(processing=MON, pt_eval=date(2026, 7, 9))
        assert classify(s, date(2026, 12, 1), backlog_after=30) is Status.DONE

    def test_never_applies_when_the_clock_never_started(self):
        assert classify(session_with(), date(2026, 12, 1), backlog_after=30) is (
            Status.NOT_STARTED
        )

    def test_backlog_sorts_below_everything_actionable(self):
        rows = [
            to_row(session_with(processing=MON), date(2026, 12, 1), backlog_after=30),
            to_row(session_with(processing=date(2026, 11, 6)), date(2026, 12, 1),
                   backlog_after=30),
        ]
        assert [r["status"] for r in sort_rows(rows)] == ["overdue", "backlog"]

    def test_backlog_rows_keep_their_real_days_left(self):
        # The count must stay truthful; only the bucket changes.
        row = to_row(session_with(processing=MON), date(2026, 12, 1), backlog_after=30)
        assert row["status"] == "backlog"
        assert row["days_left"] < -30

    def test_summarize_counts_backlog_separately(self):
        rows = [
            to_row(session_with(processing=MON), date(2026, 12, 1), backlog_after=30),
            to_row(session_with(processing=date(2026, 11, 6)), date(2026, 12, 1),
                   backlog_after=30),
        ]
        counts = summarize(rows)
        assert counts["backlog"] == 1
        assert counts["overdue"] == 1


class TestReferralTypeAndNoReport:
    """Referral types that never produce a report must not read as overdue.

    Measured at CHI-Gait over 365 days: 301 "Kinematics gait analysis" sessions
    had only 14 without a PT evaluation date, but 40 of 44 "Video & Pedobarograph
    only" and 24 of 33 "Video only" lacked one, because no report was ever owed.
    """

    def referral(self, kind, processing=date(2026, 1, 1)):
        return Session(
            session_id="s", project_id="p", subject_id="Demo", mrn="1",
            session_date=date(2026, 7, 1), therapist_raw="T",
            processing_completed=processing, pt_evaluation=None,
            pdf_to_emr=None, interpretation=None, cancellation="",
            referral_type=kind,
        )

    def test_referral_type_is_parsed(self):
        raw = raw_session(**{"sessioninfo-referral-type": "Video only"})
        assert parse_session(raw, "p").referral_type == "Video only"

    def test_missing_referral_type_is_empty_not_none(self):
        assert parse_session(raw_session(), "p").referral_type == ""

    def test_an_excluded_type_needs_no_report(self):
        s = self.referral("Video only")
        assert classify(s, date(2026, 7, 27), no_report_types=["Video only"]) is (
            Status.NO_REPORT
        )

    def test_kinematics_still_counts_when_others_are_excluded(self):
        s = self.referral("Kinematics gait analysis")
        assert classify(s, date(2026, 7, 27),
                        no_report_types=["Video only", "Video & Pedobarograph only"]) is (
            Status.OVERDUE
        )

    def test_matching_ignores_case_and_extra_whitespace(self):
        s = self.referral("  VIDEO   ONLY ")
        assert classify(s, date(2026, 7, 27), no_report_types=["video only"]) is (
            Status.NO_REPORT
        )

    def test_an_unknown_referral_type_still_requires_a_report(self):
        # Exclusion list, so a new type fails toward showing work, not hiding it.
        s = self.referral("Some New Referral Type 2027")
        assert classify(s, date(2026, 7, 27), no_report_types=["Video only"]) is (
            Status.OVERDUE
        )

    def test_a_blank_referral_type_still_requires_a_report(self):
        s = self.referral("")
        assert classify(s, date(2026, 7, 27), no_report_types=["Video only"]) is (
            Status.OVERDUE
        )

    def test_no_report_beats_not_started(self):
        s = self.referral("Video only", processing=None)
        assert classify(s, date(2026, 7, 27), no_report_types=["Video only"]) is (
            Status.NO_REPORT
        )

    def test_a_finished_report_still_reads_as_done(self):
        s = Session("s", "p", "Demo", "1", date(2026, 7, 1), "T",
                    date(2026, 1, 1), date(2026, 1, 8), None, None, "", "Video only")
        assert classify(s, date(2026, 7, 27), no_report_types=["Video only"]) is (
            Status.DONE
        )

    def test_no_report_rows_carry_no_countdown(self):
        # A number here would imply a deadline that does not exist.
        row = to_row(self.referral("Video only"), date(2026, 7, 27),
                     no_report_types=["Video only"])
        assert row["days_left"] is None
        assert row["status"] == "no_report"
        assert row["referral_type"] == "Video only"

    def test_no_report_sorts_below_everything_actionable(self):
        rows = [
            to_row(self.referral("Video only"), date(2026, 7, 27),
                   no_report_types=["Video only"]),
            to_row(self.referral("Kinematics gait analysis"), date(2026, 7, 27),
                   no_report_types=["Video only"]),
        ]
        assert [r["status"] for r in sort_rows(rows)] == ["overdue", "no_report"]

    def test_empty_exclusion_list_changes_nothing(self):
        s = self.referral("Video only")
        assert classify(s, date(2026, 7, 27)) is Status.OVERDUE
        assert requires_report(s) is True


class TestSubjectLink:
    """Clicking the patient's name should open the patient, not this session.

    The route is a setting rather than a constant: unlike the session route it is
    not documented in the SDK, so a wrong guess must be fixable without a
    rebuild.
    """

    TEMPLATE = "{site}/project/{project}/subject/{patient}"

    def with_patient(self, patient_id="UGF0aWVudDEyMw=="):
        return Session(
            session_id="s1", project_id="p1", subject_id="Demo, Patient A", mrn="1",
            session_date=date(2026, 7, 1), therapist_raw="T",
            processing_completed=MON, pt_evaluation=None, pdf_to_emr=None,
            interpretation=None, cancellation="", referral_type="",
            patient_id=patient_id,
        )

    def test_the_patient_id_is_parsed_from_the_payload(self):
        raw = raw_session()
        assert parse_session(raw, "p").patient_id == "UGF0aWVudDE="

    def test_a_missing_patient_id_is_empty_not_none(self):
        raw = raw_session()
        raw["patient"].pop("id")
        assert parse_session(raw, "p").patient_id == ""

    def test_it_builds_the_expected_url(self):
        assert subject_url(self.with_patient(), SITE, self.TEMPLATE) == (
            "https://shriners.moveshelf.com/project/p1/subject/UGF0aWVudDEyMw=="
        )

    def test_it_tolerates_a_base_url_without_a_trailing_slash(self):
        assert subject_url(
            self.with_patient(), "https://x.moveshelf.com", self.TEMPLATE
        ).startswith("https://x.moveshelf.com/project/")

    def test_an_empty_template_turns_subject_links_off(self):
        assert subject_url(self.with_patient(), SITE, "") == ""

    def test_no_patient_id_means_no_link(self):
        assert subject_url(self.with_patient(patient_id=""), SITE, self.TEMPLATE) == ""

    def test_no_site_url_means_no_link(self):
        assert subject_url(self.with_patient(), "", self.TEMPLATE) == ""

    @pytest.mark.parametrize(
        "template",
        ["{site}/{nosuchfield}", "{site}/{0}", "{site}/{project", "{}"],
    )
    def test_a_broken_template_costs_the_link_not_the_worklist(self, template):
        # A typo in settings.json must never take the app down.
        assert subject_url(self.with_patient(), SITE, template) == ""

    def test_a_different_route_shape_works_too(self):
        # The whole point of it being a template.
        assert subject_url(
            self.with_patient(), SITE, "{site}/project/{project}/subjects/{patient}"
        ).endswith("/subjects/UGF0aWVudDEyMw==")

    def test_the_row_carries_both_links_and_they_differ(self):
        row = to_row(self.with_patient(), DUE, site_url=SITE,
                     subject_url_template=self.TEMPLATE)
        assert row["url"].endswith("/session/s1")
        assert row["subject_url"].endswith("/subject/UGF0aWVudDEyMw==")
        assert row["url"] != row["subject_url"]

    def test_the_row_has_no_subject_link_by_default(self):
        # to_row's default is off, so nothing links until it is configured.
        assert to_row(self.with_patient(), DUE, site_url=SITE)["subject_url"] == ""
