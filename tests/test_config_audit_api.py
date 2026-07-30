"""Tests for config loading, the access log, and API paging.

No network. The API tests drive a fake SDK so the paging and truncation logic can
be exercised without hitting Moveshelf.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from tracker import api as api_mod
from tracker.api import ApiError, MoveshelfClient, SessionQueryTruncated
from tracker.audit import EVENT_FETCH, AccessLog
from tracker.config import (
    DEFAULT_API_URL,
    DEFAULT_NO_REPORT_REFERRAL_TYPES,
    DEFAULT_LOOKBACK_DAYS,
    NETWORK_LOCATION_MESSAGE,
    ConfigError,
    Settings,
    is_network_location,
    _normalize_key_payload,
    decrypt_key,
    encrypt_key,
    load_api_key,
    load_settings,
    temporary_key_file,
)

VALID_KEY = "a" * 64


class TestKeyNormalizing:
    def test_the_json_moveshelf_gives_you(self):
        text = json.dumps({"application": "ReportTracker", "secretKey": VALID_KEY})
        assert _normalize_key_payload(text) == {
            "secretKey": VALID_KEY,
            "application": "ReportTracker",
        }

    def test_a_bare_pasted_key(self):
        assert _normalize_key_payload(VALID_KEY)["secretKey"] == VALID_KEY

    def test_a_pasted_key_with_quotes_and_whitespace(self):
        assert _normalize_key_payload(f'  "{VALID_KEY}"  \n')["secretKey"] == VALID_KEY

    def test_the_application_id_pasted_by_mistake_is_rejected(self):
        # The single most likely setup error: the label is right above the key.
        assert _normalize_key_payload("ReportTracker") is None

    @pytest.mark.parametrize(
        "text", ["", "   ", "not a key", "{}", '{"application": "x"}', "{bad json"]
    )
    def test_junk_is_rejected(self, text):
        assert _normalize_key_payload(text) is None

    def test_a_key_of_the_wrong_length_is_rejected(self):
        assert _normalize_key_payload("abc123") is None


class TestLoadApiKey:
    def test_reads_the_moveshelf_json_file(self, tmp_path):
        (tmp_path / "mvshlf-api-key.json").write_text(
            json.dumps({"application": "RT", "secretKey": VALID_KEY}), encoding="utf-8"
        )
        assert load_api_key(tmp_path)["secretKey"] == VALID_KEY

    def test_reads_a_pasted_key_text_file(self, tmp_path):
        (tmp_path / "api_key.txt").write_text(VALID_KEY, encoding="utf-8")
        assert load_api_key(tmp_path)["secretKey"] == VALID_KEY

    def test_json_wins_over_text(self, tmp_path):
        (tmp_path / "mvshlf-api-key.json").write_text(
            json.dumps({"secretKey": "b" * 64}), encoding="utf-8"
        )
        (tmp_path / "api_key.txt").write_text(VALID_KEY, encoding="utf-8")
        assert load_api_key(tmp_path)["secretKey"] == "b" * 64

    def test_missing_key_names_the_folder_and_says_what_to_do(self, tmp_path):
        with pytest.raises(ConfigError) as excinfo:
            load_api_key(tmp_path)
        message = str(excinfo.value)
        assert str(tmp_path) in message
        assert "User settings" in message and "Generate API Key" in message

    def test_the_application_id_mistake_gets_a_targeted_message(self, tmp_path):
        (tmp_path / "api_key.txt").write_text("ReportTracker", encoding="utf-8")
        with pytest.raises(ConfigError) as excinfo:
            load_api_key(tmp_path)
        assert "Application ID" in str(excinfo.value)

    def test_tolerates_a_utf8_bom(self, tmp_path):
        (tmp_path / "api_key.txt").write_bytes(f"﻿{VALID_KEY}".encode("utf-8"))
        assert load_api_key(tmp_path)["secretKey"] == VALID_KEY


@pytest.mark.skipif(sys.platform != "win32", reason="DPAPI is Windows only")
class TestEncryptionAtRest:
    def test_round_trips(self):
        payload = {"secretKey": VALID_KEY, "application": "RT"}
        encrypted = encrypt_key(payload)
        assert encrypted and VALID_KEY not in encrypted
        assert decrypt_key(encrypted) == payload

    def test_garbage_does_not_decrypt_and_does_not_raise(self):
        assert decrypt_key("not base64 at all") is None
        assert decrypt_key("aGVsbG8=") is None


class TestTemporaryKeyFile:
    def test_writes_then_always_removes_the_file(self):
        payload = {"secretKey": VALID_KEY}
        with temporary_key_file(payload) as path:
            assert json.loads(open(path, encoding="utf-8").read()) == payload
            captured = path
        assert not __import__("os").path.exists(captured)

    def test_removed_even_when_the_body_raises(self):
        captured = None
        with pytest.raises(RuntimeError):
            with temporary_key_file({"secretKey": VALID_KEY}) as path:
                captured = path
                raise RuntimeError("boom")
        assert captured and not __import__("os").path.exists(captured)


class TestSettings:
    def test_defaults_when_no_file_exists(self, tmp_path):
        settings = load_settings(tmp_path)
        assert settings.lookback_days == DEFAULT_LOOKBACK_DAYS
        assert settings.api_url == DEFAULT_API_URL
        assert not settings.is_configured

    def test_round_trips(self, tmp_path):
        settings = Settings(_folder=tmp_path)
        settings.project_id = "proj1"
        settings.project_name = "CHI-Gait"
        settings.my_therapist = "Dawson, Renata"
        assert settings.save()
        loaded = load_settings(tmp_path)
        assert loaded.project_id == "proj1"
        assert loaded.project_name == "CHI-Gait"
        assert loaded.is_configured

    def test_a_corrupt_settings_file_falls_back_to_defaults(self, tmp_path):
        (tmp_path / "settings.json").write_text("{not json", encoding="utf-8")
        assert load_settings(tmp_path).lookback_days == DEFAULT_LOOKBACK_DAYS

    @pytest.mark.parametrize(
        "value,expected", [(5, 7), (99999, 1095), ("abc", 90), (None, 90), (120, 120)]
    )
    def test_lookback_is_clamped_to_something_sane(self, tmp_path, value, expected):
        (tmp_path / "settings.json").write_text(
            json.dumps({"lookback_days": value}), encoding="utf-8"
        )
        assert load_settings(tmp_path).lookback_days == expected


class TestAccessLog:
    def test_writes_one_json_line_per_fetch(self, tmp_path):
        log = AccessLog(tmp_path / "logs" / "access.jsonl")
        assert log.log_fetch("op", "proj1", 42, 1.2345, "success")
        records = log.read_records()
        assert len(records) == 1
        assert records[0]["event"] == EVENT_FETCH
        assert records[0]["n_sessions"] == 42
        assert records[0]["duration_seconds"] == 1.234
        assert records[0]["operator"]

    def test_appends_rather_than_overwriting(self, tmp_path):
        log = AccessLog(tmp_path / "access.jsonl")
        log.log_start("p")
        log.log_fetch("op", "p", 1, 0.1, "success")
        assert len(log.read_records()) == 2

    def test_records_carry_no_patient_identifiers(self, tmp_path):
        log = AccessLog(tmp_path / "access.jsonl")
        log.log_fetch("op", "proj1", 3, 0.5, "success", start_date="2026-01-01")
        allowed = {
            "timestamp", "event", "operator", "app_version", "operation",
            "project_id", "n_sessions", "duration_seconds", "status", "error",
            "window_start", "window_end",
        }
        assert set(log.read_records()[0]) == allowed

    def test_an_error_is_recorded_and_truncated(self, tmp_path):
        log = AccessLog(tmp_path / "access.jsonl")
        log.log_fetch("op", "p", None, 0.1, "error", error="x" * 5000)
        assert len(log.read_records()[0]["error"]) == 300

    def test_an_unwritable_path_disables_logging_without_raising(self, tmp_path):
        blocker = tmp_path / "blocker"
        blocker.write_text("i am a file", encoding="utf-8")
        log = AccessLog(blocker / "sub" / "access.jsonl")
        assert log.enabled is False
        assert log.log_fetch("op", "p", 1, 0.1, "success") is False

    def test_disabled_when_no_path(self):
        log = AccessLog(None)
        assert log.log_start() is False
        assert log.read_records() == []

    def test_malformed_lines_are_skipped_on_read(self, tmp_path):
        path = tmp_path / "access.jsonl"
        log = AccessLog(path)
        log.log_start("p")
        with path.open("a", encoding="utf-8") as fh:
            fh.write("{not json\n\n")
        log.log_start("p")
        assert len(log.read_records()) == 2


class FakeSdk:
    """Stands in for MoveshelfApi, recording the windows it was asked for."""

    def __init__(self, sessions_by_window=None, total=0, fail_with=None):
        self.calls = []
        self.total = total
        self.fail_with = fail_with
        self.sessions_by_window = sessions_by_window or {}

    def getUserProjects(self):
        return [
            {"id": "p2", "name": "zeta"},
            {"id": "p1", "name": "Alpha"},
            {"id": "", "name": "no id"},
        ]

    def getFilteredProjectSessions(self, project_id, start_date, end_date, **kwargs):
        self.calls.append((start_date, end_date))
        if self.fail_with is not None and len(self.calls) == 1:
            raise self.fail_with
        if self.sessions_by_window:
            return self.sessions_by_window.get((start_date, end_date), [])
        return [{"id": f"s{i}", "date": start_date} for i in range(self.total)]


def client_with(fake):
    client = MoveshelfClient.__new__(MoveshelfClient)
    client.api = fake
    client.access_log = None
    return client


class TestListProjects:
    def test_sorted_by_name_and_missing_ids_dropped(self):
        projects = client_with(FakeSdk()).list_projects()
        assert [p["name"] for p in projects] == ["Alpha", "zeta"]


class TestFetchSessions:
    def test_a_short_page_is_returned_as_is(self):
        fake = FakeSdk(total=5)
        rows = client_with(fake).fetch_sessions("p", "2026-01-01", "2026-03-31", limit=100)
        assert len(rows) == 5
        assert len(fake.calls) == 1

    def test_a_full_page_triggers_a_date_split(self):
        # 3 on the first call, then a short page for each half.
        fake = FakeSdk(
            sessions_by_window={
                ("2026-01-01", "2026-01-09"): [{"id": "a", "date": "2026-01-02"}],
                ("2026-01-10", "2026-01-17"): [{"id": "b", "date": "2026-01-11"}],
            },
            total=0,
        )
        original = fake.getFilteredProjectSessions

        def full_then_split(project_id, start_date, end_date, **kwargs):
            if (start_date, end_date) == ("2026-01-01", "2026-01-17"):
                fake.calls.append((start_date, end_date))
                return [{"id": f"x{i}", "date": start_date} for i in range(3)]
            return original(project_id, start_date, end_date, **kwargs)

        fake.getFilteredProjectSessions = full_then_split
        rows = client_with(fake).fetch_sessions("p", "2026-01-01", "2026-01-17", limit=3)
        assert {r["id"] for r in rows} == {"a", "b"}

    def test_the_halves_tile_the_window_with_no_gap(self):
        fake = FakeSdk(total=0)
        calls = []

        def record(project_id, start_date, end_date, **kwargs):
            calls.append((start_date, end_date))
            return [{"id": "x", "date": start_date}] if len(calls) > 1 else [
                {"id": f"y{i}", "date": start_date} for i in range(2)
            ]

        fake.getFilteredProjectSessions = record
        client_with(fake).fetch_sessions("p", "2026-01-01", "2026-01-11", limit=2)
        # endDate is inclusive, so the second half must start the day after the first ends.
        assert calls[1] == ("2026-01-01", "2026-01-06")
        assert calls[2] == ("2026-01-07", "2026-01-11")

    def test_an_unsplittable_full_page_raises_rather_than_lying(self):
        fake = FakeSdk(total=2)
        with pytest.raises(SessionQueryTruncated):
            client_with(fake).fetch_sessions(
                "p", "2026-01-01", "2026-01-01", limit=2
            )

    def test_auto_split_off_raises_on_a_full_page(self):
        fake = FakeSdk(total=2)
        with pytest.raises(SessionQueryTruncated):
            client_with(fake).fetch_sessions(
                "p", "2026-01-01", "2026-03-01", limit=2, auto_split=False
            )

    def test_an_oversized_response_failure_is_recovered_by_splitting(self):
        fake = FakeSdk(total=1, fail_with=Exception("HTTPError 502 Bad Gateway"))
        rows = client_with(fake).fetch_sessions("p", "2026-01-01", "2026-01-11", limit=50)
        assert len(fake.calls) == 3
        assert rows

    def test_an_auth_failure_is_never_retried_into_a_fan_out(self):
        fake = FakeSdk(total=1, fail_with=Exception("401 Unauthorized"))
        with pytest.raises(ApiError):
            client_with(fake).fetch_sessions("p", "2026-01-01", "2026-01-11", limit=50)
        assert len(fake.calls) == 1

    def test_fetches_are_audited(self, tmp_path):
        client = client_with(FakeSdk(total=2))
        client.access_log = AccessLog(tmp_path / "access.jsonl")
        client.fetch_sessions("p", "2026-01-01", "2026-01-31", limit=100)
        record = client.access_log.read_records()[0]
        assert record["status"] == "success"
        assert record["n_sessions"] == 2
        assert record["window_start"] == "2026-01-01"

    def test_a_failed_fetch_is_also_audited(self, tmp_path):
        client = client_with(FakeSdk(fail_with=Exception("401 Unauthorized")))
        client.access_log = AccessLog(tmp_path / "access.jsonl")
        with pytest.raises(ApiError):
            client.fetch_sessions("p", "2026-01-01", "2026-01-31", limit=100)
        assert client.access_log.read_records()[0]["status"] == "error"


class TestOversizedDetection:
    @pytest.mark.parametrize(
        "message",
        ["HTTP 502", "504 gateway", "Max retries exceeded", "Read timed out",
         "Connection reset by peer"],
    )
    def test_transport_failures_are_worth_splitting(self, message):
        assert api_mod._is_oversized_response_error(Exception(message))

    @pytest.mark.parametrize(
        "message", ["400 Bad Request", "401 Unauthorized", "403 Forbidden",
                    "Unauthorized", "some other error"],
    )
    def test_application_failures_are_not(self, message):
        assert not api_mod._is_oversized_response_error(Exception(message))


class TestDefaultNoReportReferralTypes:
    """The default list seeds first-run setup without overriding a real choice.

    Confirmed with the clinical lead 2026-07-27: video, pedobarography, foot
    analysis, research and isokinetic sessions do not produce a PT report, while
    all three kinematics referral types do.
    """

    def test_a_fresh_install_gets_the_default_list(self, tmp_path):
        settings = load_settings(tmp_path)
        assert settings.no_report_referral_types == DEFAULT_NO_REPORT_REFERRAL_TYPES

    def test_an_explicitly_empty_list_is_respected(self, tmp_path):
        # The user ticked every box. That is a real choice, not "unconfigured".
        (tmp_path / "settings.json").write_text(
            json.dumps({"no_report_referral_types": []}), encoding="utf-8"
        )
        assert load_settings(tmp_path).no_report_referral_types == []

    def test_a_user_list_wins_over_the_default(self, tmp_path):
        (tmp_path / "settings.json").write_text(
            json.dumps({"no_report_referral_types": ["Only This One"]}), encoding="utf-8"
        )
        assert load_settings(tmp_path).no_report_referral_types == ["Only This One"]

    def test_a_settings_file_predating_the_feature_is_seeded(self, tmp_path):
        (tmp_path / "settings.json").write_text(
            json.dumps({"lookback_days": 30}), encoding="utf-8"
        )
        assert load_settings(tmp_path).no_report_referral_types == (
            DEFAULT_NO_REPORT_REFERRAL_TYPES
        )

    def test_junk_in_the_key_falls_back_to_excluding_nothing(self, tmp_path):
        (tmp_path / "settings.json").write_text(
            json.dumps({"no_report_referral_types": "not a list"}), encoding="utf-8"
        )
        assert load_settings(tmp_path).no_report_referral_types == []

    def test_the_three_kinematics_types_are_never_excluded(self):
        # These require a report. Excluding one would hide real overdue work.
        lowered = {t.lower() for t in DEFAULT_NO_REPORT_REFERRAL_TYPES}
        for required in (
            "kinematics gait analysis",
            "kinematics/video sports analysis (97750)",
            "gait pt eval without kinematics",
        ):
            assert required not in lowered

    def test_the_default_round_trips_through_save(self, tmp_path):
        settings = load_settings(tmp_path)
        assert settings.save()
        assert load_settings(tmp_path).no_report_referral_types == (
            DEFAULT_NO_REPORT_REFERRAL_TYPES
        )


def _unc_path() -> Path:
    """A genuine UNC path, built from chr(92).

    Written this way because an earlier version of this test was silently
    checking a single-backslash path: the escaping had been eaten before the
    test ever ran, so it proved nothing.

    The server name is fictional on purpose. This repository is public, and a
    real internal hostname does not need to be in it.
    """
    b = chr(92)
    path = Path(b + b + "fileserver" + b + "Apps" + b + "Report Tracker")
    assert str(path).startswith(b + b), "fixture is not a UNC path"
    return path


class TestNetworkDriveGuard:
    """Someone will eventually run the app straight from a network folder.

    Everything it saves would then land in that folder, including api_key.dat,
    which is a credential that can read patient data. The check happens before
    the key is read or written, so a run from one leaves nothing behind.
    """

    def test_a_local_folder_is_fine(self, tmp_path):
        assert is_network_location(tmp_path) is False

    def test_a_unc_path_is_refused(self):
        assert is_network_location(
            _unc_path()
        ) is True

    def test_a_forward_slash_unc_path_is_refused(self):
        assert is_network_location(Path("//server/apps/app")) is True

    def test_an_unresolvable_path_does_not_raise(self):
        # Uncertainty must never block a legitimate local setup.
        assert is_network_location(Path("Z:\does\not\exist\at\all")) in (True, False)

    def test_the_message_names_the_folder_and_the_fix(self):
        text = NETWORK_LOCATION_MESSAGE.format(folder=r"\server\apps")
        assert r"\server\apps" in text
        assert "API key" in text
        assert "copy" in text.lower()


class TestNetworkDriveStopsBeforeWritingAnything:
    def test_no_key_is_read_or_written_when_running_from_a_network_drive(self, tmp_path, monkeypatch):
        from tracker.server import AppState

        # A real key sitting beside the exe, as it would be after a naive copy.
        (tmp_path / "mvshlf-api-key.json").write_text(
            json.dumps({"application": "RT", "secretKey": VALID_KEY}), encoding="utf-8"
        )
        monkeypatch.setattr("tracker.server.is_network_location", lambda folder: True)

        state = AppState(tmp_path)
        state.refresh()

        assert state.status() == "bad_location"
        assert state.to_payload()["location_error"]
        # Nothing encrypted, nothing settled, no client built.
        assert not (tmp_path / "api_key.dat").exists()
        assert not (tmp_path / "settings.json").exists()
        assert state.client is None
