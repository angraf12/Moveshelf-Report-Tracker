"""Browser tests for behavior that only exists once the page is laid out.



Some bugs are invisible to a server-side test because they depend on real

layout. The horizontal scrollbar above the table is the example that prompted

this file: every server test passed while the strip hid itself on every single

render, because it measured a container that was still ``display: none`` and so

reported zero width. Only a real browser could catch that.



Requires playwright::



    pip install playwright

    python -m playwright install chromium



Skipped automatically when it is not installed, so the core suite still runs

anywhere. Run just these with ``pytest -m browser``.

"""

from __future__ import annotations



import threading

import time



import pytest



pytest.importorskip("playwright", reason="playwright not installed")



from playwright.sync_api import sync_playwright  # noqa: E402



from tracker.server import AppState, build_server  # noqa: E402



pytestmark = pytest.mark.browser

# Must match OTHER_NAME in app.js. The "Other name..." entry is not a person.
OTHER_NAME = "__other_name__"





def set_therapist(page, name):

    """Choose a therapist. Uses "Other name..." when the name is not listed.



    The picker is a <select> so the whole list can be browsed. A brief attempt at

    an <input list="..."> shipped a regression: browsers filter datalist

    suggestions against the current value, so once a name was saved it was the

    only one offered.

    """

    values = page.eval_on_selector_all("#me option", "els => els.map(e => e.value)")

    if name in values:

        page.select_option("#me", name)

    else:

        page.select_option("#me", OTHER_NAME)

        page.fill("#me-other", name)

        page.dispatch_event("#me-other", "change")

    page.wait_for_timeout(400)





def make_rows(n: int = 12):

    """Synthetic rows. No PHI, no network."""

    return [

        {

            "session_id": f"s{i}",

            # Long enough to match the widest real names seen at CHI-Gait,

            # so the layout tests exercise a realistic worst case.

            "subject_id": f"Rodriguez Martinez, Demo {i:02d}",

            "mrn": f"90000{i:02d}",

            "session_date": "2026-07-01",

            "therapist": "Dawson, Renata",

            "therapist_raw": "Dawson, Renata, MPT",

            "referral_type": "Kinematics gait analysis",
            "referring_physician": "Referrer, Demo MD",
            "foot_model": i % 3 == 0,

            "processing_completed": "2026-07-06",

            "pt_evaluation": None,

            "pdf_to_emr": None,

            "interpretation": None,

            "due_date": "2026-07-15",

            "days_left": -2 + i,

            "days_since_session": 12,

            "days_since_processing": 9,

            "status": "overdue" if i < 3 else "on_track",

            "sort_rank": 0,

            "url": "",

        }

        for i in range(n)

    ]





@pytest.fixture(scope="module")

def live(tmp_path_factory):

    """A running server with synthetic rows, plus a Chromium page."""

    state = AppState(tmp_path_factory.mktemp("ui"))

    state.settings.project_id = "p1"

    state.settings.project_name = "Demo-Gait"

    state.rows = make_rows()

    state.therapists = ["Dawson, Renata"]

    state.referral_types = ["Kinematics gait analysis"]

    state.ever_loaded = True

    state.last_refresh = time.time()



    # Saving a setting triggers a server-side refresh. There is no API client

    # here, so that would fail, set key_error and swap the table for the "set up

    # your key" screen. These tests are about the page, not about fetching, so

    # the fetch is a no-op and the synthetic rows stay put.

    state.refresh = lambda: None



    httpd, token, port = build_server(state, port=0)

    threading.Thread(target=httpd.serve_forever, daemon=True).start()



    with sync_playwright() as pw:

        browser = pw.chromium.launch()

        # Deliberately narrow: the table fits comfortably at laptop width

        # now, and these tests need it to actually overflow.

        page = browser.new_page(viewport={"width": 900, "height": 800})

        page.goto(f"http://127.0.0.1:{port}/?t={token}")

        page.wait_for_selector("#tbl tbody tr", timeout=15000)

        page.wait_for_timeout(400)

        yield {"page": page, "state": state}

        browser.close()



    httpd.shutdown()

    httpd.server_close()





class TestTopScrollbar:

    """The proxy scrollbar above the table.



    Reported twice by the user before it worked, because the first attempt

    looked correct in the source and never rendered.

    """



    def test_the_strip_is_visible(self, live):

        assert live["page"].locator("#hbar").is_visible()



    def test_the_strip_sits_above_the_table(self, live):

        page = live["page"]

        bar = page.locator("#hbar").bounding_box()

        table = page.locator("#tablewrap").bounding_box()

        assert bar and table

        assert bar["y"] < table["y"], "the whole point is that it is at the top"



    def test_the_strip_is_tall_enough_to_show_a_scrollbar(self, live):

        # A Windows scrollbar is 15-17px. A shorter strip clips it, and the

        # control looks missing even though it is technically there.

        box = live["page"].locator("#hbar").bounding_box()

        assert box and box["height"] >= 16



    def test_the_strip_is_sized_to_the_table(self, live):

        """This is the assertion that would have caught the original bug.



        The width was never set, because it was computed while the container

        was still hidden and therefore measured as zero.

        """

        metrics = live["page"].evaluate(

            """() => {

                const inner = document.getElementById('hbar-inner');

                const wrap = document.getElementById('tablewrap');

                return {inner: inner.style.width, wrap: wrap.scrollWidth,

                        client: wrap.clientWidth};

            }"""

        )

        assert metrics["wrap"] > metrics["client"], "test needs a table that overflows"

        assert metrics["inner"] == f"{metrics['wrap']}px"



    def test_the_strip_can_actually_scroll(self, live):

        metrics = live["page"].evaluate(

            """() => {

                const bar = document.getElementById('hbar');

                return {scroll: bar.scrollWidth, client: bar.clientWidth};

            }"""

        )

        assert metrics["scroll"] > metrics["client"]



    @staticmethod

    def _half_scroll(page):

        """Half of however far the table can actually scroll.



        A fixed pixel target silently clamps when the table is narrower than the

        test assumed, which turns a real assertion into a tautology.

        """

        amount = page.evaluate(

            """() => {

                const w = document.getElementById('tablewrap');

                return Math.floor((w.scrollWidth - w.clientWidth) / 2);

            }"""

        )

        assert amount > 0, "test needs a table that overflows"

        return amount



    def test_scrolling_the_strip_scrolls_the_table(self, live):

        page = live["page"]

        amount = self._half_scroll(page)

        page.evaluate(f"document.getElementById('hbar').scrollLeft = {amount}")

        page.wait_for_timeout(150)

        assert page.evaluate(

            "document.getElementById('tablewrap').scrollLeft"

        ) == amount



    def test_scrolling_the_table_scrolls_the_strip(self, live):

        page = live["page"]

        amount = self._half_scroll(page)

        page.evaluate(f"document.getElementById('tablewrap').scrollLeft = {amount}")

        page.wait_for_timeout(150)

        assert page.evaluate("document.getElementById('hbar').scrollLeft") == amount



    def test_the_two_do_not_fight_each_other(self, live):

        # Each one's scroll handler moves the other, so without a guard they

        # would feed back and either jitter or lock up.

        page = live["page"]

        amount = self._half_scroll(page)

        page.evaluate(f"document.getElementById('hbar').scrollLeft = {amount}")

        page.wait_for_timeout(250)

        both = page.evaluate(

            """() => [document.getElementById('hbar').scrollLeft,

                      document.getElementById('tablewrap').scrollLeft]"""

        )

        assert both == [amount, amount]



    def test_it_stays_on_screen_when_the_page_is_scrolled_down(self, live):

        # The original complaint: having to scroll to the bottom of a long page

        # to reach the only scrollbar.

        page = live["page"]

        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")

        page.wait_for_timeout(250)

        box = page.locator("#hbar").bounding_box()

        assert box and 0 <= box["y"] < 800

        page.evaluate("window.scrollTo(0, 0)")





class TestTableRenders:

    def test_rows_reached_the_page(self, live):

        assert live["page"].locator("#tbl tbody tr").count() == 12



    def test_status_is_stated_in_words_not_only_colour(self, live):

        # Accessibility rule from PLAN.md section 7.

        text = live["page"].locator("#tbl tbody tr").first.inner_text()

        assert "overdue" in text.lower()



    def test_no_loading_panel_once_data_has_arrived(self, live):

        assert not live["page"].locator("#loading").is_visible()





class TestColumnsFitWithoutScrolling:

    """Requested 2026-07-28: see "Days proc." without scrolling sideways.



    The table used to be capped at 1300px regardless of window size, and carried

    a "12 business days ago" sub-line that repeated the adjacent column. Both are

    gone. These pin the result at real screen widths so a future style change

    cannot quietly push the column back off screen.

    """



    LAST_REQUIRED_COLUMN = "Days Since Processed"

    @pytest.fixture(autouse=True)
    def realistic_rows(self, live):
        """Reset to known-wide rows before measuring.

        The page fixture is shared across the module, so an earlier class may
        have left narrower data behind. Layout assertions have to control their
        own content or they measure whatever ran last.
        """
        live["state"].rows = make_rows()
        live["state"].therapists = ["Dawson, Renata"]
        live["state"].settings.my_therapist = ""
        live["state"].settings.mine_only = False
        page = live["page"]
        page.reload()
        page.wait_for_selector("#tbl tbody tr", timeout=15000)
        page.wait_for_timeout(400)



    def columns(self, page):

        return page.evaluate(

            """() => {

                const wrap = document.getElementById('tablewrap');

                const right = wrap.getBoundingClientRect().right;

                return {

                    edge: right,

                    cols: [...document.querySelectorAll('#tbl thead th')].map(th => ({

                        name: th.textContent.trim().replace(/ [\u25b2\u25bc]$/, ''),

                        right: th.getBoundingClientRect().right,

                    })),

                };

            }"""

        )



    @pytest.mark.parametrize("width", [1280, 1452, 1920])

    def test_days_since_processing_is_visible(self, live, width):

        page = live["page"]

        page.set_viewport_size({"width": width, "height": 800})

        page.wait_for_timeout(400)

        info = self.columns(page)

        target = [c for c in info["cols"]

                  if self.LAST_REQUIRED_COLUMN.lower() in c["name"].lower()]

        assert target, f"column {self.LAST_REQUIRED_COLUMN} is missing entirely"

        assert target[0]["right"] <= info["edge"] + 1, (

            f'"{self.LAST_REQUIRED_COLUMN}" is cut off at {width}px: ends at '

            f'{target[0]["right"]:.0f}, visible to {info["edge"]:.0f}'

        )



    def test_the_whole_table_fits_at_laptop_width(self, live):

        page = live["page"]

        page.set_viewport_size({"width": 1280, "height": 800})

        page.wait_for_timeout(400)

        m = page.evaluate(
            """() => {
                const w = document.getElementById('tablewrap');
                return {need: w.scrollWidth, have: w.clientWidth};
            }"""
        )
        assert m["need"] <= m["have"] + 1, (
            f"table needs {m['need']}px but only {m['have']}px is visible at 1280, "
            f"overflowing by {m['need'] - m['have']}px"
        )



    def test_referral_type_is_truncated_rather_than_wide(self, live):

        # It repeats on nearly every row; the full value lives in a tooltip.

        page = live["page"]

        page.set_viewport_size({"width": 1280, "height": 800})

        page.wait_for_timeout(300)

        # Referring MD and Referral type both truncate, so pick by header.
        idx = page.eval_on_selector_all(
            "#tbl thead th",
            "els => els.findIndex(e => e.dataset.k === 'referral_type')")
        cell = page.locator(f"#tbl tbody tr:first-child td:nth-child({idx + 1})")

        assert cell.get_attribute("title") == "Kinematics gait analysis"

        box = cell.bounding_box()

        assert box and box["width"] <= 170





class TestBucketLabels:

    """Asked 2026-07-28: what do "On track" and "Not started" represent?



    "Not started" read as "the therapist has not started the report", which

    points at the wrong person: it actually means data processing is unfinished,

    so the clock has not begun. Renamed to "Awaiting processing", and every

    bucket now explains itself on hover.

    """



    def test_the_awaiting_processing_tile_is_named_for_the_real_blocker(self, live):

        page = live["page"]

        page.set_viewport_size({"width": 1452, "height": 820})

        page.wait_for_timeout(300)

        tile = page.locator('#tiles .tile[data-status="not_started"]')

        assert tile.count() == 1

        text = tile.inner_text().lower()

        assert "awaiting processing" in text

        assert "not started" not in text



    def test_every_tile_explains_itself_on_hover(self, live):

        page = live["page"]

        titles = page.eval_on_selector_all(

            "#tiles .tile", "els => els.map(e => e.title)"

        )

        assert titles, "no tiles rendered"

        assert all(len(t) > 20 for t in titles), titles



    def test_the_on_track_tooltip_does_not_claim_work_has_begun(self, live):

        # The app cannot know that, so it must not imply it.

        title = live["page"].get_attribute(

            '#tiles .tile[data-status="on_track"]', "title"

        ).lower()

        assert "time remains" in title

        assert "not that the report has been started" in title



    def test_the_awaiting_processing_tooltip_names_the_right_person(self, live):

        title = live["page"].get_attribute(

            '#tiles .tile[data-status="not_started"]', "title"

        ).lower()

        assert "processing" in title

        assert "nothing is owed by the therapist yet" in title





class TestClearingFilters:

    """Asked 2026-07-28: after clicking a tile, how do you get the full list back?



    Only two ways existed, neither discoverable: click the same tile again, or

    press Esc. A filtered table looks exactly like a short worklist, so someone

    could believe they had two reports due when they had twenty.



    Refresh deliberately does not clear filters. It means "fetch newer data",

    costs a round trip, and changing the view as a side effect would surprise.

    """



    def reset(self, page):

        page.set_viewport_size({"width": 1452, "height": 820})

        page.keyboard.press("Escape")

        page.wait_for_timeout(200)



    def visible_rows(self, page):

        return page.locator("#tbl tbody tr").count()



    def test_the_bar_is_hidden_when_nothing_is_filtered(self, live):

        page = live["page"]

        self.reset(page)

        assert not page.locator("#activefilters").is_visible()



    def test_clicking_a_tile_reveals_the_bar_and_names_the_filter(self, live):

        page = live["page"]

        self.reset(page)

        page.click('#tiles .tile[data-status="overdue"]')

        page.wait_for_timeout(250)

        bar = page.locator("#activefilters")

        assert bar.is_visible()

        assert "Overdue" in bar.inner_text()



    def test_clear_filters_restores_the_full_list(self, live):

        page = live["page"]

        self.reset(page)

        everything = self.visible_rows(page)

        page.click('#tiles .tile[data-status="overdue"]')

        page.wait_for_timeout(250)

        filtered = self.visible_rows(page)

        assert filtered < everything, "test needs the filter to actually narrow"



        page.click("#activefilters .clearall")

        page.wait_for_timeout(250)

        assert self.visible_rows(page) == everything

        assert not page.locator("#activefilters").is_visible()



    def test_a_search_term_also_shows_in_the_bar(self, live):

        page = live["page"]

        self.reset(page)

        page.fill("#q", "Patient 01")

        page.wait_for_timeout(250)

        assert "Patient 01" in page.locator("#activefilters").inner_text()

        page.click("#activefilters .clearall")

        page.wait_for_timeout(250)

        assert page.input_value("#q") == ""



    def test_each_chip_can_be_removed_on_its_own(self, live):

        page = live["page"]

        self.reset(page)

        page.click('#tiles .tile[data-status="overdue"]')

        page.fill("#q", "Patient")

        page.wait_for_timeout(250)

        assert page.locator("#activefilters .chip").count() == 2



        page.locator("#activefilters .chip button").first.click()

        page.wait_for_timeout(250)

        assert page.locator("#activefilters .chip").count() == 1

        page.click("#activefilters .clearall")

        page.wait_for_timeout(200)



    def test_escape_still_works(self, live):

        page = live["page"]

        self.reset(page)

        everything = self.visible_rows(page)

        page.click('#tiles .tile[data-status="overdue"]')

        page.wait_for_timeout(250)

        page.keyboard.press("Escape")

        page.wait_for_timeout(250)

        assert self.visible_rows(page) == everything



    def test_clicking_the_same_tile_again_still_toggles_it_off(self, live):

        page = live["page"]

        self.reset(page)

        everything = self.visible_rows(page)

        page.click('#tiles .tile[data-status="overdue"]')

        page.wait_for_timeout(250)

        page.click('#tiles .tile[data-status="overdue"]')

        page.wait_for_timeout(250)

        assert self.visible_rows(page) == everything



    def test_the_tile_tooltip_says_it_toggles(self, live):

        page = live["page"]

        self.reset(page)

        title = page.get_attribute('#tiles .tile[data-status="overdue"]', "title")

        assert "Click again to show all" in title





class TestStickyHeaderAndIdentity:

    """Two gaps found while reviewing before the PT pilot."""



    def test_column_headers_stay_put_when_scrolling_a_long_list(self, live):

        """Headers used to scroll away, so you lost track of which column was which.



        They are sticky, but sticky positions against the nearest scrolling

        ancestor. With the page doing the scrolling, that was the wrong element.

        The table now scrolls inside its own box.

        """

        page = live["page"]

        page.set_viewport_size({"width": 1452, "height": 700})

        page.wait_for_timeout(300)



        before = page.locator("#tbl thead th").first.bounding_box()

        page.evaluate(

            "document.getElementById('tablewrap').scrollTop = "

            "document.getElementById('tablewrap').scrollHeight"

        )

        page.wait_for_timeout(300)

        after = page.locator("#tbl thead th").first.bounding_box()



        assert before and after

        assert abs(after["y"] - before["y"]) < 3, (

            f"header moved from y={before['y']:.0f} to y={after['y']:.0f}"

        )

        assert page.locator("#tbl thead th").first.is_visible()



    def test_the_table_scrolls_inside_its_own_box(self, live):

        page = live["page"]

        page.set_viewport_size({"width": 1452, "height": 700})

        page.wait_for_timeout(300)

        assert page.evaluate(

            """() => {

                const w = document.getElementById('tablewrap');

                return w.scrollHeight > w.clientHeight;

            }"""

        )



    def test_the_identity_prompt_appears_when_nobody_is_identified(self, live):

        page, state = live["page"], live["state"]

        state.settings.my_therapist = ""

        state.therapists = ["Dawson, Renata", "Whitfield, Marlo"]

        page.reload()

        page.wait_for_selector("#tbl tbody tr", timeout=15000)

        page.wait_for_timeout(400)

        assert page.locator("#identity").is_visible()

        assert "Which therapist are you?" in page.locator("#identity").inner_text()



    def test_it_offers_every_therapist_seen(self, live):

        options = live["page"].eval_on_selector_all(

            "#identity-pick option", "els => els.map(e => e.value)"

        )

        assert options == ["Dawson, Renata", "Whitfield, Marlo"]



    def test_not_now_dismisses_it(self, live):

        page = live["page"]

        page.click("#identity-skip")

        page.wait_for_timeout(200)

        assert not page.locator("#identity").is_visible()



    def test_choosing_a_name_turns_on_my_sessions_only(self, live):

        page, state = live["page"], live["state"]

        page.reload()

        page.wait_for_selector("#tbl tbody tr", timeout=15000)

        page.wait_for_timeout(400)

        page.select_option("#identity-pick", "Dawson, Renata")

        page.click("#identity-set")

        page.wait_for_timeout(600)



        assert state.settings.my_therapist == "Dawson, Renata"

        assert state.settings.mine_only is True

        assert page.is_checked("#mine")

        assert not page.locator("#identity").is_visible()



    def test_it_does_not_nag_once_someone_is_identified(self, live):

        page = live["page"]

        page.reload()

        page.wait_for_selector("#tbl tbody tr", timeout=15000)

        page.wait_for_timeout(400)

        assert not page.locator("#identity").is_visible()

        # Put the fixture back for any later test.

        live["state"].settings.my_therapist = ""

        live["state"].settings.mine_only = False





class TestStaleDataGuard:

    """A tab left open past midnight showed yesterday's countdowns as current."""



    def test_the_page_knows_the_local_date(self, live):

        _, script = None, live["page"].content()

        js = live["page"].evaluate("typeof localToday === 'function'")

        assert js



    def test_it_warns_and_shows_a_date_when_the_data_is_from_another_day(self, live):

        page, state = live["page"], live["state"]

        page.reload()

        page.wait_for_selector("#tbl tbody tr", timeout=15000)

        page.wait_for_timeout(400)

        assert page.locator("#banners .banner.error").count() == 0



        # Pretend the fetch happened on an earlier date.

        original = state.to_payload

        state.to_payload = lambda: {**original(), "today": "2020-01-01"}

        try:

            page.evaluate("load()")

            page.wait_for_timeout(700)

            banners = page.locator("#banners").inner_text()

            assert "2020-01-01" in banners

            assert "out of date" in banners

        finally:

            state.to_payload = original





class TestTileCountsFollowTheFilter:

    """Reported from user testing 2026-07-28.



    With "My sessions only" ticked, the tiles still counted every therapist, so a

    PT saw the department's overdue number sitting directly above their own,

    much shorter, list. A count with no stated owner is worse than no count.

    """



    def setup_rows(self, live):

        """Two therapists with different status mixes."""

        rows = []

        for i in range(6):

            r = dict(make_rows(1)[0])

            r["session_id"] = f"a{i}"

            r["therapist"] = "Dawson, Renata"

            r["status"] = "overdue" if i < 4 else "on_track"

            rows.append(r)

        for i in range(4):

            r = dict(make_rows(1)[0])

            r["session_id"] = f"b{i}"

            r["therapist"] = "Whitfield, Marlo"

            r["status"] = "overdue" if i < 1 else "on_track"

            rows.append(r)

        live["state"].rows = rows

        live["state"].therapists = ["Dawson, Renata", "Whitfield, Marlo"]

        live["state"].settings.my_therapist = ""

        live["state"].settings.mine_only = False



        page = live["page"]

        page.set_viewport_size({"width": 1452, "height": 820})

        page.reload()

        page.wait_for_selector("#tbl tbody tr", timeout=15000)

        page.wait_for_timeout(400)

        page.click("#identity-skip")

        return page



    def count(self, page, status):

        return int(page.inner_text(f'#tiles .tile[data-status="{status}"] .n'))



    def test_unfiltered_counts_cover_everyone(self, live):

        page = self.setup_rows(live)

        assert self.count(page, "overdue") == 5   # 4 + 1

        assert self.count(page, "on_track") == 5  # 2 + 3



    def test_the_caption_says_the_counts_are_for_everyone(self, live):

        page = live["page"]

        assert "every therapist" in page.inner_text("#scope")



    def test_choosing_a_therapist_changes_the_counts(self, live):

        page = live["page"]

        page.check("#mine")

        page.wait_for_timeout(200)

        set_therapist(page, "Dawson, Renata")

        assert self.count(page, "overdue") == 4

        assert self.count(page, "on_track") == 2



    def test_the_caption_names_the_therapist(self, live):

        assert "Dawson, Renata" in live["page"].inner_text("#scope")



    def test_the_counts_match_the_rows_underneath(self, live):

        page = live["page"]

        shown = page.locator("#tbl tbody tr").count()

        total = self.count(page, "overdue") + self.count(page, "on_track")

        assert shown == total



    def test_unticking_restores_the_department_counts(self, live):

        page = live["page"]

        page.uncheck("#mine")

        page.wait_for_timeout(400)

        assert self.count(page, "overdue") == 5

        assert "every therapist" in page.inner_text("#scope")



    def test_a_search_also_narrows_the_counts(self, live):

        page = live["page"]

        page.fill("#q", "Whitfield")

        page.wait_for_timeout(400)

        assert self.count(page, "overdue") == 1

        assert "matching your filters" in page.inner_text("#scope")

        page.fill("#q", "")

        page.wait_for_timeout(300)



    def test_clicking_a_tile_does_not_zero_the_others(self, live):

        # The tile filter is excluded from the scope on purpose, or there would

        # be no way to see the other counts once you clicked one.

        page = live["page"]

        page.click('#tiles .tile[data-status="overdue"]')

        page.wait_for_timeout(400)

        assert self.count(page, "on_track") == 5

        page.keyboard.press("Escape")

        page.wait_for_timeout(300)



    def test_a_therapist_name_cannot_inject_markup(self, live):

        # Names come from Moveshelf, so the caption must escape them.

        page, state = live["page"], live["state"]

        state.therapists = ["<img src=x onerror=alert(1)>"]

        state.rows = [dict(make_rows(1)[0], therapist="<img src=x onerror=alert(1)>")]

        state.settings.my_therapist = "<img src=x onerror=alert(1)>"

        state.settings.mine_only = True

        page.reload()

        page.wait_for_selector("#tbl tbody tr", timeout=15000)

        page.wait_for_timeout(400)

        assert page.locator("#scope img").count() == 0

        assert "<img" in page.inner_text("#scope")





class TestColumnHeadingsAreSpeltOut:

    """Users could not tell what "Days Seen" and "Days Proc." meant."""



    def test_the_count_columns_say_what_they_count(self, live):

        page = live["page"]

        page.set_viewport_size({"width": 1452, "height": 820})

        page.wait_for_timeout(300)

        headings = page.eval_on_selector_all(

            "#tbl thead th", "els => els.map(e => e.textContent.trim())"

        )

        # The headings are stacked with <br>, so textContent runs the two
        # lines together. Compare on collapsed whitespace.
        joined = " | ".join(" ".join(h.split()) for h in headings)
        joined = joined.replace("DaysSince", "Days Since")

        assert "Days Since Seen" in joined

        assert "Days Since Processed" in joined

        assert "Days Proc." not in joined

        assert "Days Seen |" not in joined



    def test_each_one_explains_itself_on_hover(self, live):

        page = live["page"]

        titles = page.eval_on_selector_all(

            # Only the day columns claim to count business days. Foot Model
            # shares the .num class for centring but holds a tick, not a count.
            "#tbl thead th[data-k^='days_since']", "els => els.map(e => e.title)"

        )

        assert all("Business days" in t for t in titles), titles





class TestTherapistBox:

    """How the name list is built, and the two gaps found on 2026-07-28.



    The list holds whoever appears on sessions in the current lookback window, so

    someone back from leave is simply absent. Their saved name used to keep

    filtering while the box showed "(who are you?)": the screen disagreed with

    itself. Separately, sites that record no therapist on a session left those

    sessions belonging to nobody.

    """



    def build(self, live, rows, therapists, saved="", mine=True):

        state = live["state"]

        state.rows = rows

        state.therapists = therapists

        state.settings.my_therapist = saved

        state.settings.mine_only = mine

        page = live["page"]

        page.set_viewport_size({"width": 1452, "height": 820})

        page.reload()

        # Wait for the view, not for a row: filtering to someone with no

        # sessions correctly yields an empty table, which is one of the cases

        # under test here.

        page.wait_for_selector("#main", state="visible", timeout=15000)

        page.wait_for_timeout(400)

        return page



    def rows_for(self, names):

        out = []

        for i, name in enumerate(names):

            row = dict(make_rows(1)[0])

            row["session_id"] = f"r{i}"

            row["therapist"] = name

            row["status"] = "overdue"

            out.append(row)

        return out



    def visible_options(self, page):

        """Only what a user can actually pick, ignoring the placeholder."""

        return page.eval_on_selector_all(

            "#me option",

            "els => els.filter(e => e.value && e.value !== '__other_name__')"

            "        .map(e => e.value)",

        )



    def test_every_therapist_in_the_data_is_offered(self, live):

        page = self.build(

            live, self.rows_for(["Dawson, Renata", "Whitfield, Marlo"]),

            ["Dawson, Renata", "Whitfield, Marlo"],

        )

        assert self.visible_options(page) == ["Dawson, Renata", "Whitfield, Marlo"]



    def test_every_therapist_is_still_offered_after_one_is_chosen(self, live):

        """The regression: choosing a name used to leave it the only option."""

        page = self.build(

            live, self.rows_for(["Dawson, Renata", "Whitfield, Marlo"]),

            ["Dawson, Renata", "Whitfield, Marlo"],

        )

        set_therapist(page, "Dawson, Renata")

        assert self.visible_options(page) == ["Dawson, Renata", "Whitfield, Marlo"]

        set_therapist(page, "Whitfield, Marlo")

        assert self.visible_options(page) == ["Dawson, Renata", "Whitfield, Marlo"]



    def test_all_names_survive_a_reload_with_one_saved(self, live):

        page = self.build(

            live, self.rows_for(["Dawson, Renata", "Whitfield, Marlo"]),

            ["Dawson, Renata", "Whitfield, Marlo"], saved="Dawson, Renata",

        )

        assert self.visible_options(page) == ["Dawson, Renata", "Whitfield, Marlo"]

        assert page.input_value("#me") == "Dawson, Renata"



    def test_a_name_can_be_typed_in_even_if_it_is_not_listed(self, live):

        page = self.build(live, self.rows_for(["Dawson, Renata"]), ["Dawson, Renata"])

        set_therapist(page, "Newstarter, Sam")

        assert live["state"].settings.my_therapist == "Newstarter, Sam"



    def test_a_saved_name_absent_from_the_window_is_still_shown(self, live):

        # This is the self-contradiction: filtering by someone the box could not

        # display, so it read "(who are you?)" while the table was filtered.

        page = self.build(

            live, self.rows_for(["Dawson, Renata"]), ["Dawson, Renata"],

            saved="Onleave, Alex",

        )

        assert page.input_value("#me") == "Onleave, Alex"

        labels = page.eval_on_selector_all("#me option", "els => els.map(e => e.textContent)")

        assert any("no sessions in this period" in x for x in labels)



    def test_and_it_explains_why_there_are_no_rows(self, live):

        page = live["page"]

        note = page.locator("#me-note")

        assert note.is_visible()

        assert "No sessions for Onleave, Alex in this period" in note.inner_text()



    def test_unassigned_sessions_are_offered_when_any_exist(self, live):

        page = self.build(

            live, self.rows_for(["Dawson, Renata", "", ""]), ["Dawson, Renata"],

        )

        assert "(no therapist recorded)" in self.visible_options(page)



    def test_selecting_unassigned_shows_exactly_those_sessions(self, live):

        page = live["page"]

        set_therapist(page, "(no therapist recorded)")

        assert page.locator("#tbl tbody tr").count() == 2

        assert int(page.inner_text('#tiles .tile[data-status="overdue"] .n')) == 2



    def test_the_caption_says_so_in_words(self, live):

        assert "no therapist recorded" in live["page"].inner_text("#scope")



    def test_the_filter_chip_says_so_too(self, live):

        assert "No therapist recorded" in live["page"].inner_text("#activefilters")



    def test_unassigned_is_not_offered_when_every_session_has_a_therapist(self, live):

        page = self.build(

            live, self.rows_for(["Dawson, Renata", "Whitfield, Marlo"]),

            ["Dawson, Renata", "Whitfield, Marlo"],

        )

        assert "(no therapist recorded)" not in self.visible_options(page)



    def test_an_unassigned_session_is_never_counted_as_someone_elses(self, live):

        page = self.build(

            live, self.rows_for(["Dawson, Renata", ""]), ["Dawson, Renata"],

        )

        set_therapist(page, "Dawson, Renata")

        assert page.locator("#tbl tbody tr").count() == 1



    def test_the_box_and_the_table_never_disagree(self, live):

        # Whatever the box says, the rows must match it.

        page = self.build(

            live, self.rows_for(["Dawson, Renata", "Whitfield, Marlo", ""]),

            ["Dawson, Renata", "Whitfield, Marlo"],

        )

        for name, expected in [("Dawson, Renata", 1), ("Whitfield, Marlo", 1),

                               ("(no therapist recorded)", 1), ("Nobody, Here", 0)]:

            set_therapist(page, name)

            assert page.input_value("#me") == name

            assert page.locator("#tbl tbody tr").count() == expected, name



class TestExport:
    """Export to Excel, approved for clinical use by Ross Chafetz 2026-07-30.

    This reverses the "no export" decision of 2026-07-27. The controls that make
    it defensible are: it exports only what is on screen, every export is written
    to the audit log, and values that Excel would treat as formulas are neutered.
    """

    @pytest.fixture(autouse=True)
    def rows(self, live):
        live["state"].rows = make_rows(4)
        live["state"].settings.mine_only = False
        live["state"].settings.my_therapist = ""
        page = live["page"]
        page.set_viewport_size({"width": 1452, "height": 820})
        page.reload()
        page.wait_for_selector("#tbl tbody tr", timeout=15000)
        page.wait_for_timeout(400)

    def csv_for(self, page):
        """Run the page's own CSV builder over the currently visible rows."""
        return page.evaluate("buildCsv(visibleRows())")

    def test_the_button_is_there(self, live):
        assert live["page"].locator("#export").is_visible()

    def test_the_header_row_names_every_column(self, live):
        header = self.csv_for(live["page"]).splitlines()[0]
        for label in ("Subject ID", "MRN", "Therapist", "Referring MD",
                      "Days since processed", "Session link"):
            assert f'"{label}"' in header, label

    def test_one_line_per_visible_row(self, live):
        lines = [x for x in self.csv_for(live["page"]).splitlines() if x.strip()]
        assert len(lines) == 1 + live["page"].locator("#tbl tbody tr").count()

    def test_it_exports_only_what_is_on_screen(self, live):
        page = live["page"]
        page.click('#tiles .tile[data-status="overdue"]')
        page.wait_for_timeout(300)
        shown = page.locator("#tbl tbody tr").count()
        lines = [x for x in self.csv_for(page).splitlines() if x.strip()]
        assert len(lines) == 1 + shown
        page.keyboard.press("Escape")
        page.wait_for_timeout(300)

    def test_status_is_written_in_words(self, live):
        assert '"Overdue"' in self.csv_for(live["page"])

    def test_the_pdf_column_is_gone(self, live):
        header = self.csv_for(live["page"]).splitlines()[0]
        assert "PDF" not in header

    def test_referring_physician_is_included(self, live):
        assert "Referrer, Demo MD" in self.csv_for(live["page"])

    @pytest.mark.parametrize("dangerous", ["=cmd|'/c calc'!A1", "+1+1", "-2+3", "@SUM(A1)"])
    def test_a_value_excel_would_run_as_a_formula_is_neutered(self, live, dangerous):
        # CSV injection: Excel executes a cell beginning with = + - or @.
        out = live["page"].evaluate("v => csvCell(v)", dangerous)
        assert out.startswith("\"'"), out

    def test_quotes_are_escaped_rather_than_breaking_the_row(self, live):
        assert live["page"].evaluate("v => csvCell(v)", 'a "quoted" name') == (
            '"a ""quoted"" name"'
        )

    def test_an_ordinary_value_is_not_mangled(self, live):
        assert live["page"].evaluate("v => csvCell(v)", "Smith") == '"Smith"'

    def test_exporting_nothing_warns_instead_of_writing_an_empty_file(self, live):
        page = live["page"]
        page.fill("#q", "zzz-no-such-patient")
        page.wait_for_timeout(300)
        assert page.locator("#tbl tbody tr").count() == 0
        page.click("#export")
        page.wait_for_timeout(300)
        assert "Nothing to export" in page.inner_text("#banners")
        page.fill("#q", "")
        page.wait_for_timeout(300)


class TestVersionIsVisible:
    """Asked 2026-08-03 how to tell whether the latest version is running.

    The honest answer was that you could not: the version reached the page in the
    payload and was printed once to the console window, which people close.
    Nothing updates itself, so someone can sit on an old build indefinitely and
    report behaviour that was fixed weeks earlier.
    """

    def test_the_footer_shows_a_version(self, live):
        page = live["page"]
        page.set_viewport_size({"width": 1452, "height": 820})
        page.wait_for_timeout(300)
        text = page.inner_text("#version")
        assert text.startswith("v"), text
        assert any(ch.isdigit() for ch in text), text

    def test_it_matches_the_version_the_server_reports(self, live):
        page = live["page"]
        from tracker import __version__
        assert page.inner_text("#version") == f"v{__version__}"

    def test_it_is_on_screen_without_hunting_for_it(self, live):
        assert live["page"].locator("#version").is_visible()
