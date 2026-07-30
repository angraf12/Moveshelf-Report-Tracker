"""Tests for the local server, focused on the security controls.

These run a real server on a real loopback port and talk to it over HTTP, since
the controls being tested (Host validation, cookie handling, cache headers) live
in the HTTP layer and would be invisible to a mocked test.
"""
from __future__ import annotations

import http.client
import json
import threading
from datetime import date

import pytest

from tracker.model import Session
from tracker.server import COOKIE_NAME, AppState, build_server


@pytest.fixture
def server(tmp_path):
    """A running server with two rows of synthetic data and no API client."""
    state = AppState(tmp_path)
    state.settings.project_id = "proj1"
    state.settings.project_name = "Demo-Gait"
    state.settings.site_url = "https://demo.moveshelf.com/"
    state.rows = [
        {
            "session_id": "s1", "subject_id": "Demo, Patient A", "mrn": "9000001",
            "session_date": "2026-07-01", "therapist": "Dawson, Renata",
            "therapist_raw": "Dawson, Renata, MPT",
            "processing_completed": "2026-07-06", "pt_evaluation": None,
            "pdf_to_emr": None, "interpretation": None, "due_date": "2026-07-15",
            "days_left": -2, "days_since_session": 12, "days_since_processing": 9,
            "referral_type": "Kinematics gait analysis",
            "status": "overdue", "sort_rank": 0,
            "url": "https://demo.moveshelf.com/project/proj1/session/s1",
        },
    ]
    state.therapists = ["Dawson, Renata"]
    httpd, token, port = build_server(state, port=0)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield {"port": port, "token": token, "state": state, "httpd": httpd}
    httpd.shutdown()
    httpd.server_close()


def request(server, path, method="GET", cookie=None, host=None, body=None):
    conn = http.client.HTTPConnection("127.0.0.1", server["port"], timeout=5)
    headers = {"Host": host or f"127.0.0.1:{server['port']}"}
    if cookie:
        headers["Cookie"] = f"{COOKIE_NAME}={cookie}"
    payload = None
    if body is not None:
        payload = json.dumps(body)
        headers["Content-Type"] = "application/json"
    conn.request(method, path, body=payload, headers=headers)
    response = conn.getresponse()
    data = response.read()
    result = (response.status, dict(response.getheaders()), data)
    conn.close()
    return result


def authed(server, path, method="GET", body=None):
    return request(server, path, method, cookie=server["token"], body=body)


class TestTokenAuth:
    def test_no_token_is_rejected(self, server):
        status, _, _ = request(server, "/api/state")
        assert status == 401

    def test_a_wrong_token_is_rejected(self, server):
        status, _, _ = request(server, "/api/state", cookie="not-the-token")
        assert status == 401

    def test_the_right_token_works(self, server):
        status, _, _ = authed(server, "/api/state")
        assert status == 200

    def test_the_initial_url_sets_a_cookie_and_redirects(self, server):
        status, headers, _ = request(server, f"/?t={server['token']}")
        assert status == 302
        assert headers["Location"] == "/"
        cookie = headers["Set-Cookie"]
        assert server["token"] in cookie
        assert "HttpOnly" in cookie and "SameSite=Strict" in cookie

    def test_a_wrong_token_in_the_url_does_not_authenticate(self, server):
        status, _, _ = request(server, "/?t=wrong")
        assert status == 401

    def test_the_data_endpoint_is_protected_too(self, server):
        # The whole point: PHI must not be reachable without the token.
        status, _, body = request(server, "/api/state")
        assert status == 401
        assert b"9000001" not in body


class TestHostValidation:
    """DNS rebinding defense. An attacker's request carries their hostname."""

    def test_a_foreign_host_header_is_refused(self, server):
        status, _, body = request(server, "/api/state",
                                  cookie=server["token"], host="evil.example.com")
        assert status == 403
        assert b"9000001" not in body

    def test_localhost_is_allowed(self, server):
        status, _, _ = request(server, "/api/state", cookie=server["token"],
                               host=f"localhost:{server['port']}")
        assert status == 200

    def test_host_is_checked_before_the_token(self, server):
        # Otherwise a rebinding attempt could probe for a valid token.
        status, _, _ = request(server, "/api/state", host="evil.example.com")
        assert status == 403

    def test_post_endpoints_check_the_host_too(self, server):
        status, _, _ = request(server, "/api/settings", "POST",
                               cookie=server["token"], host="evil.example.com",
                               body={"lookback_days": 30})
        assert status == 403


class TestResponseHeaders:
    def test_nothing_is_cacheable(self, server):
        _, headers, _ = authed(server, "/api/state")
        assert "no-store" in headers["Cache-Control"]
        assert headers["Pragma"] == "no-cache"

    def test_the_page_itself_is_also_no_store(self, server):
        _, headers, _ = authed(server, "/")
        assert "no-store" in headers["Cache-Control"]

    def test_security_headers_are_present(self, server):
        _, headers, _ = authed(server, "/api/state")
        assert headers["X-Content-Type-Options"] == "nosniff"
        assert headers["Referrer-Policy"] == "no-referrer"
        assert "default-src 'self'" in headers["Content-Security-Policy"]


class TestRoutes:
    def test_state_returns_the_rows(self, server):
        status, _, body = authed(server, "/api/state")
        payload = json.loads(body)
        assert status == 200
        assert payload["status"] == "ready"
        assert payload["project_name"] == "Demo-Gait"
        assert len(payload["rows"]) == 1
        assert payload["summary"]["overdue"] == 1

    def test_the_page_and_its_assets_are_served(self, server):
        for path, needle in (("/", b"Report Tracker"),
                             ("/static/app.js", b"STATUS_META"),
                             ("/static/styles.css", b"--overdue")):
            status, _, body = authed(server, path)
            assert status == 200, path
            assert needle in body, path

    def test_unknown_paths_are_404(self, server):
        status, _, _ = authed(server, "/api/nope")
        assert status == 404

    def test_the_top_scrollbar_is_wired_to_the_table(self, server):
        """The proxy scrollbar needs three pieces or it silently does nothing.

        CSS cannot put a scrollbar at the top of a scroll box, so the strip above
        the table is a separate element kept in step by JavaScript. If the markup
        and the script drift apart the strip just sits there inert, which is easy
        to miss and annoying to use.
        """
        _, _, page = authed(server, "/")
        for element in (b'id="hbar"', b'id="hbar-inner"', b'id="tablewrap"'):
            assert element in page, element

        _, _, script = authed(server, "/static/app.js")
        for hook in (b"syncScrollbars", b"wireScrollSync", b"hbar-inner", b"tablewrap"):
            assert hook in script, hook

        _, _, css = authed(server, "/static/styles.css")
        assert b".hbar{overflow-x:auto" in css
        assert b".tablewrap{overflow:auto" in css

    def test_settings_are_applied_and_persisted(self, server, tmp_path):
        status, _, body = authed(server, "/api/settings", "POST",
                                 body={"lookback_days": 180})
        assert status == 200
        assert json.loads(body)["lookback_days"] == 180
        assert (tmp_path / "settings.json").is_file()

    def test_an_out_of_range_lookback_is_clamped(self, server):
        _, _, body = authed(server, "/api/settings", "POST",
                            body={"lookback_days": 99999})
        assert json.loads(body)["lookback_days"] == 1095

    def test_junk_in_a_settings_body_is_ignored(self, server):
        _, _, body = authed(server, "/api/settings", "POST",
                            body={"lookback_days": "banana"})
        assert json.loads(body)["lookback_days"] == 90

    def test_ping_keeps_the_server_alive(self, server):
        server["state"].last_seen = 0
        status, _, _ = authed(server, "/api/ping")
        assert status == 200
        assert server["state"].last_seen > 0


class TestStaticFileEscape:
    @pytest.mark.parametrize(
        "path",
        ["/static/../config.py", "/static/../../main.py",
         "/static/..%2f..%2fmain.py", "/static/"],
    )
    def test_cannot_read_outside_the_web_folder(self, server, path):
        status, _, body = authed(server, path)
        assert status in (403, 404)
        assert b"secretKey" not in body


class TestPayloadShape:
    def test_no_unexpected_keys_reach_the_browser(self, server):
        _, _, body = authed(server, "/api/state")
        payload = json.loads(body)
        assert set(payload) == {
            "status", "loading", "ever_loaded",
            "app_name", "version", "folder", "key_error", "location_error", "error",
            "projects", "project_id", "project_name", "lookback_days",
            "my_therapist", "mine_only", "backlog_enabled", "backlog_days",
            "therapists", "referral_types", "no_report_referral_types",
            "holidays_loaded", "holidays_file", "holiday_names",
            "today", "last_refresh", "rows", "summary",
        }

    def test_no_credential_reaches_the_browser(self, server):
        # A real key is 64 hex characters, so look for anything of that shape
        # rather than for a field name.
        import re

        _, _, body = authed(server, "/api/state")
        assert b"secretKey" not in body
        assert not re.search(rb"[0-9a-fA-F]{64}", body), "something key-shaped leaked"


class TestStateWithoutAKey:
    def test_missing_key_reports_needs_key_rather_than_crashing(self, tmp_path):
        state = AppState(tmp_path)
        state.refresh()
        payload = state.to_payload()
        assert payload["status"] == "needs_key"
        assert str(tmp_path) in payload["key_error"]
        assert payload["rows"] == []


class TestRefreshBuildsRows:
    def test_a_full_refresh_produces_sorted_rows(self, tmp_path, monkeypatch):
        """Drive AppState.refresh with a stubbed client, no network."""
        state = AppState(tmp_path)
        state.settings.project_id = "proj1"

        class StubClient:
            def fetch_sessions(self, project_id, start_date, end_date):
                return [{"id": "raw1"}]

            def list_projects(self):
                return []

        state.client = StubClient()
        monkeypatch.setattr("tracker.server.load_api_key", lambda folder: {"secretKey": "x"})
        monkeypatch.setattr(
            "tracker.server.parse_sessions",
            lambda raw, pid: [
                Session("s2", pid, "Demo, Patient B", "9000002", date(2026, 7, 1),
                        "Dawson, Renata, MPT", date(2026, 1, 1), None, None, None, ""),
                Session("s1", pid, "Demo, Patient A", "9000001", date(2026, 7, 1),
                        "Dawson, Renata, PT, MPT", None, None, None, None, ""),
            ],
        )
        state.refresh()

        assert state.error == ""
        # The 2026-01-01 session is months past due, so with the backlog toggle
        # on by default it lands in Backlog rather than burying the Overdue tile.
        assert [r["status"] for r in state.rows] == ["not_started", "backlog"]
        # Both spellings normalize to one therapist, which is the whole point.
        assert state.therapists == ["Dawson, Renata"]
        assert (tmp_path / "therapists.csv").is_file()

        # Turning the toggle off puts it back in Overdue. Nothing is lost.
        state.apply_settings({"backlog_enabled": False})
        state.refresh()
        assert sorted(r["status"] for r in state.rows) == ["not_started", "overdue"]


class TestFirstLoadRace:
    """The opening fetch runs in a background thread, so the page can be asked
    for state before any data exists.

    Reported 2026-07-28: the tracker opened completely blank and only filled in
    after clicking Refresh. The page had rendered an empty table during the
    15-second opening fetch and never looked again. "No data yet" has to be
    distinguishable from "no data at all", or an empty table reads as good news.
    """

    def test_state_reports_loading_before_the_first_fetch_finishes(self, tmp_path):
        state = AppState(tmp_path)
        state.settings.project_id = "proj1"

        started, release = threading.Event(), threading.Event()

        class SlowClient:
            def fetch_sessions(self, project_id, start_date, end_date):
                started.set()
                release.wait(timeout=5)
                return []

            def list_projects(self):
                return []

        state.client = SlowClient()
        worker = threading.Thread(target=state.refresh, daemon=True)
        worker.start()
        assert started.wait(timeout=5)

        # Mid-fetch: this is exactly what the browser saw when it rendered blank.
        payload = state.to_payload()
        assert payload["loading"] is True
        assert payload["ever_loaded"] is False
        assert payload["rows"] == []

        release.set()
        worker.join(timeout=5)

        after = state.to_payload()
        assert after["loading"] is False
        assert after["ever_loaded"] is True

    def test_ever_loaded_is_set_even_when_the_fetch_fails(self, tmp_path):
        # Otherwise a failing key would leave the page spinning forever instead
        # of showing the error.
        state = AppState(tmp_path)
        state.refresh()
        assert state.ever_loaded is True
        assert state.loading is False
        assert state.to_payload()["status"] == "needs_key"

    def test_a_completed_refresh_is_not_reported_as_loading(self, server):
        payload = json.loads(authed(server, "/api/state")[2])
        assert payload["loading"] is False

    def test_the_page_can_show_a_loading_state(self, server):
        _, _, page = authed(server, "/")
        assert b'id="loading"' in page
        _, _, script = authed(server, "/static/app.js")
        # Must poll while loading, or it would sit on the spinner forever.
        assert b"pollTimer" in script
        assert b"ever_loaded" in script
