# CLAUDE.md — Agent Reference (Ground Truth)

**Moveshelf Report Tracker v0.1.0** — a standalone tool that shows physical therapists which
gait reports are due and how soon.

Read [PLAN.md](PLAN.md) first. It holds the verified data model, the security posture, and
every decision made so far with its reasoning. This file is the short operational summary.

## What this is, and what it is not

A clinical operations tool. It intentionally displays PHI (patient names, MRNs) to the
therapist responsible for those patients. That makes it the opposite of the research
projects created from `project-template`, which work only with de-identified data.

- Read-only. It never writes to Moveshelf.
- Local. A tiny HTTP server bound to `127.0.0.1`, rendered in the user's browser.
- **No export. No CSV, no Print button.** Decided 2026-07-27, see PLAN.md §9. Do not add
  one back without an explicit decision.
- Not a research tool. No analysis, no waveforms, no Excel.

## Relationship to Moveshelf-Query-Server-Side

**Independent.** This repo does not import `query_app` and must not start. The only shared
dependency is the public `moveshelf-api` package. Reason: `query_app` pulls in pandas,
openpyxl and optionally TensorFlow and PyMC, which would make the executable enormous.

Two pieces are deliberately copied rather than shared, each carrying a comment that says so:
the session-endpoint date-window paging logic, and the cancelled/no-show predicate. If you
fix a bug in either, check whether the research app has the same bug.

**Never modify the research app from this repo.**

## Layout

```
tracker/
  businessdays.py   deadline math, pure functions, the piece everything trusts
  model.py          API payload -> Session -> display row, status buckets
  names.py          therapist name normalizing
  config.py         key + settings discovery next to the exe, DPAPI at rest
  api.py            session query + date-window paging insurance
  audit.py          append-only JSONL access log
  server.py         loopback HTTP server, token auth, Host validation
  web/              index.html, app.js, styles.css (bundled as data when frozen)
main.py             entry point: start server, open browser
tests/              pytest, no network
mockup/index.html   design mockup, invented data, not shipped
hooks/pre-commit    PHI and credential guard
```

## Credentials

The key file Moveshelf gives you is JSON: `{"application", "secretKey"}`, where `secretKey`
is 64 hex characters. `config.load_api_key` accepts that file, a bare pasted key in
`api_key.txt`, or the DPAPI-encrypted `api_key.dat` it converts to after the first
successful connection.

**The API URL is the US regional endpoint**, `https://api.us.moveshelf.com/graphql`. The
SDK's own default (`api.moveshelf.com`) is the wrong host for this deployment.

The SDK only accepts a *path* to a key file, never a key string, so an encrypted-at-rest key
has to touch disk to be usable. `config.temporary_key_file` writes it to the user's temp
directory for the duration of one constructor call and removes it in a `finally`. That is a
documented tradeoff, not an oversight.

## The data model, verified live 2026-07-27

Source call is `getFilteredProjectSessions`, which returns only matching sessions with the
patient embedded. One call per project per refresh.

**The trap:** session metadata is doubly nested (`metadata` is a JSON string holding an
object with a `metadata` key holding another object), but patient metadata is only singly
nested. Both are handled in `model.py` and nowhere else.

| Displayed | Field |
|---|---|
| Subject ID | `patient.name` |
| MRN | `patient.metadata -> ehr-id` |
| Session date | `session.date`, an ISO datetime at UTC midnight |
| Therapist | `sessioninfo-therapist` |
| Processing completed | `sessioninfo-date-processing-completed` |
| PT evaluation in EMR | `sessioninfo-pt-evaluation-date` |
| PDF data in EMR | `sessioninfo-pdf-to-emr-date` |
| Interpretation completed | `sessioninfo-interpretation-completed` |
| Referral type | `sessioninfo-referral-type` |

Facts that are easy to get wrong:

- **All four workflow fields are dates, not yes/no flags.** "Has a parseable date" is the test.
- **`sessioninfo-pt-evaluation-date` is the only field that stops the clock.**
- **`sessioninfo-interpretation-completed` holds future dates** and appears to record a
  scheduled interpretation. Display it, never compute with it.
- **`sessioninfo-evaluating-pt` is empty in live data.** Do not use it.
- **Therapist names have multiple spellings for one person.** Never match a therapist on the
  raw string; go through `names.normalize`.
- **Cancellation values include a lowercase `"no show"`.** Always compare case-folded.
- **Live data contains impossible dates** such as `0007-01-12`. Every parse is defensive and
  returns None rather than raising.

## The deadline math

Counting convention, applied everywhere: `business_days_between(a, b)` counts business days
**after** `a` up to and **including** `b`. So same-day is 0 and Monday to Tuesday is 1.

```
due        = add_business_days(processing_completed, 7)
days_since = business_days_between(processing_completed, today)
days_left  = business_days_between(today, due)
# invariant, enforced by test:  days_left == 7 - days_since
```

Business days are Monday through Friday minus nine **computed** holidays: New Year's Day,
MLK Day, Memorial Day, Independence Day, Labor Day, Thanksgiving, the Friday after,
Christmas Eve and Christmas Day. Nothing needs updating each year.

- **`observed()` shifts a Saturday holiday to Friday and a Sunday holiday to Monday.**
  Skipping this would make Christmas 2027 a no-op. It can put New Year's Day in the
  previous December.
- **Christmas Eve and Christmas Day collide about one year in three.** A date-keyed dict
  would swallow one silently. `builtin_holidays` moves the extra day in the direction the
  colliding shift was heading, so two consecutive weekdays are always granted.
  `colliding_years()` must stay empty; there is a test asserting exactly nine holidays for
  every year 2020-2060.
- **Good Friday is NOT observed** (confirmed 2026-07-28). `good_friday()` is kept only as a
  helper for a site that wants it. Do not add it back to `builtin_holidays` without asking.
- `holidays.txt` is optional and only adjusts the set: a bare date adds, a `-`-prefixed
  date removes. Never make it required again; the app must work with no files beside the key.

Status buckets: overdue (`days_left < 0`), due today (`== 0`), due soon (`<= 2`), on track
(3+ left), `NOT_STARTED`, no_report, backlog, done (PT evaluation filled). Done always wins,
so a late-but-finished report stops being red.

**No progress signal exists.** Verified across all 296 session metadata keys on 442 sessions:
Moveshelf records only completed-milestone dates. There is no draft, assigned, in-progress or
status field. The app can report time remaining, never work remaining, and no label may imply
otherwise. `Status.NOT_STARTED` does **not** mean the therapist has not started; it means data
processing is unfinished, so the clock has not begun. It displays as **"Awaiting processing"**.
The wire value stays `not_started` so saved settings keep working. Every bucket carries a
plain-language `desc` in `STATUS_META` (app.js), surfaced as a tile tooltip.

**No report needed.** Many referral types never produce a report. At CHI-Gait only 14 of 301
"Kinematics gait analysis" sessions lacked a PT evaluation date, against 40 of 44 "Video &
Pedobarograph only" and 30 of 31 research sessions. `config.DEFAULT_NO_REPORT_REFERRAL_TYPES`
seeds eight excluded types, confirmed with the clinical lead 2026-07-27. It is an
**exclusion** list on purpose, so an unrecognized referral type still requires a report:
fail toward showing work, never toward hiding it. Kinematics gait analysis,
Kinematics/video sports analysis (97750) and Gait PT eval without kinematics all require one
and must never be added to the default.

**Backlog.** Anything more than `BACKLOG_THRESHOLD_DAYS` (30 business days) past due goes
to its own bucket, via `classify(..., backlog_after=N)`. Off in the pure function, on by
default in the app (`Settings.backlog_enabled`). Reason: at CHI-Gait a third of
processing-complete sessions had no EMR date at a median of 111 business days past
processing, and those reports were finished, only never dated. Never hide the count, never
alter `days_left`; only the bucket changes.

## Links into Moveshelf

Two routes, both built in `model.py` and nowhere else:

| Link | Route | Source |
|---|---|---|
| Session (whole row) | `{site}/project/{project}/session/{session}` | Confirmed by the SDK, which documents `/project/<id>/sessions` |
| Subject (patient name) | `{site}/project/{project}/subject/{patient}` | **Not documented anywhere.** Verified against the live web app 2026-07-28 |

The subject route lives in `Settings.subject_url_template` rather than in code,
because it is undocumented and therefore not guaranteed stable. A change on
Moveshelf's side is then a one-line settings fix instead of a rebuild for every
user. Empty template turns subject links off and names render as plain text. A
malformed template costs the link only, never the worklist; there is a test for
that.

`patient_id` comes straight off `session["patient"]["id"]` and is the same
opaque form as the session id.

## Conventions

Same as the research app: type hints on every signature, Args/Returns docstrings, no bare
`except`, `snake_case` modules, `PascalCase` classes. Logging over `print`.

Additional rules specific to this repo:

1. **Never let a single malformed session break the worklist.** Parsing degrades to None or
   empty, never raises.
2. **Never put a patient identifier in a URL.** It would land in browser history. All data
   moves in JSON response bodies.
3. **Never widen what reaches the browser.** `to_row` has a test asserting its exact key set.
4. **No real staff or patient names in committed source.** Test fixtures use synthetic names
   that mirror the structure of real values.
5. **Every API fetch is audited** to `logs/access.jsonl`, best-effort, with no patient
   identifiers. Logging must never break a fetch.

## Testing

```bash
python -m pytest tests/ -q
```

286 tests, no network. The server tests run a real loopback server and assert the security
controls (token, Host validation, cache headers, static-path escape) over real HTTP.

## Status

**Working end to end, packaged, and verified as a frozen exe (2026-07-28).** 354 tests pass.

Done: `businessdays.py`, `model.py`, `names.py`, `config.py`, `api.py`, `audit.py`,
`server.py`, the web UI, `main.py`, scaffolding, design mockup.

Run it: `python main.py`, or `python main.py --folder <path>` to point at a different
key/settings folder for testing.

**Packaged and verified 2026-07-28.** `python build.py` produces
`dist/MoveshelfReportTracker.exe` (11.7 MB, one file). Verified running as a frozen build:
bundled web assets served from `sys._MEIPASS`, token auth and Host validation still
enforced, 101 live rows fetched from CHI-Gait. `README.md` is written for therapists.

Next:
1. **Test the exe on a machine that is not the build machine**, ideally a managed hospital
   one. SmartScreen and antivirus behavior is still unknown and is the biggest remaining
   deployment risk. It is unsigned.
2. **Add the API-key screenshot** to README.md (placeholder marked in the text).
3. Pilot with one or two friendly PTs.

Build notes: `tracker/web/` must be bundled as data (`--add-data ...;web`), and the console
is kept on purpose so a startup failure is readable. `main.py` line-buffers stdout because a
frozen build otherwise hides its output when piped.

Known and deliberate: a refresh of ~100 sessions takes roughly 15-18 seconds, which is the
Moveshelf call, not our code. The page shows a loading state. Worth revisiting only if PTs
complain.
