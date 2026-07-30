# Moveshelf Report Tracker: Plan

**A standalone Windows executable that shows physical therapists which gait reports are due, and how soon.**

Status: **working end to end against live Moveshelf data.** 286 tests pass.
Remaining before it can reach a therapist: the PyInstaller build and the README.
Last updated: 2026-07-27

---

## 1. The problem

PTs have **7 business days from the "Processing completed" date** to finish their report. Today that
deadline lives only in their heads and in the Moveshelf Sessions table, which shows raw dates and no
countdown. The result is reports that quietly slip past due.

This app answers one question in under five seconds of looking at it: *what do I owe, and by when?*

## 2. Scope

**In scope**

- One executable a PT double-clicks. No Python install, no command line.
- Reads sessions from Moveshelf using the PT's own API key.
- Shows the eight source columns plus computed business-day counts and a due date.
- Sorting, searching, "my sessions only", and color-coded urgency.
- Setup instructions a non-technical user can follow alone.

**Out of scope (deliberately)**

- Writing anything back to Moveshelf. This app is strictly read-only.
- Any data analysis, waveforms, Excel export pipelines, or research features.
- Any dependency on the `query_app` research package (see §4).

## 3. Verified data model

Probed live against `shriners/CHI-Gait`, 67 sessions over 2026-06-12 to 2026-07-27, on 2026-07-27.

The right API call is `getFilteredProjectSessions`, which returns **only matching sessions** with the
patient embedded, rather than whole subjects with their full session history. One call per project
per refresh.

Each session comes back as `{id, date, metadata, patient, projectPath}` with
`patient = {id, metadata, name}`. Session metadata is doubly nested: `metadata` is a JSON string
containing a `metadata` key holding another JSON object with the real values.

| Screen column | Source field | Fill rate | Notes |
|---|---|---|---|
| Subject ID | `patient.name` | 67/67 | |
| MRN | `patient.metadata -> ehr-id` | | |
| Session date | `session.date` | 67/67 | ISO datetime at UTC midnight, take the first 10 chars |
| Therapist | `sessioninfo-therapist` | 66/67 | See name-variant problem below |
| Processing completed | `sessioninfo-date-processing-completed` | 55/67 | `YYYY-MM-DD` |
| PT evaluation in EMR | `sessioninfo-pt-evaluation-date` | 24/67 | `YYYY-MM-DD`. **This is the done marker.** |
| PDF data in EMR | `sessioninfo-pdf-to-emr-date` | 24/67 | `YYYY-MM-DD` |
| Interpretation completed | `sessioninfo-interpretation-completed` | 15/67 | `YYYY-MM-DD`, see caveat below |

Four findings from the probe that change the design:

1. **All four workflow fields are dates, not yes/no flags.** The project data dictionary describes
   `sessioninfo-interpretation-completed` as a string, which is misleading. Treat them all as dates,
   and treat "has a parseable date" as the completion test.

2. **"Interpretation completed" holds future dates** (2026-08-04 and 2026-08-18 were present on
   2026-07-27), so it appears to record a *scheduled* interpretation, not a completed one. It is
   displayed as a plain date and is explicitly **not** used in any deadline math. Worth confirming
   with the team what it actually means.

3. **`sessioninfo-evaluating-pt` is empty in all 67 sessions.** `sessioninfo-therapist` is the real
   therapist field. Do not use the other one.

4. **Therapist names have inconsistent spellings for the same person.** *(Names below are anonymized; the counts are real.)* "Dawson, Renata, MPT"
   appears 26 times and "Dawson, Renata, PT, MPT" 9 times. A naive "my sessions only" filter would
   silently hide a third of someone's workload, which is exactly the failure this app exists to
   prevent. Handled in §6.

Also observed: `sessioninfo-return-to-clinic` contained `0007-01-12`, an obvious typo. Every date
parse must be defensive and never crash the app on bad input.

## 4. How this stays separate from the research app

**A new sibling repo: `Github/Moveshelf-Report-Tracker/`.** Not a folder inside
`Moveshelf-Query-Server-Side`, and not built from `project-template` (that template is for
de-identified research analysis and ships a hook that assumes PHI never appears, which is the
opposite of this app's job).

**No import of `query_app`.** The only shared dependency is the public `moveshelf-api` package from
PyPI, exactly as the research app depends on it. That package is already what talks to Moveshelf, so
nothing is being reinvented at the transport layer.

Rationale: `query_app` pulls in pandas, openpyxl, and optionally TensorFlow, PyMC, and matplotlib.
Bundling that into a PyInstaller executable would produce a 300 MB-plus binary that takes minutes to
start, for a tool whose entire job is one HTTP request and some date arithmetic. Independence here is
worth more than reuse.

**What gets copied rather than shared, and why that is acceptable:**

- The date-window paging logic from `api_client.get_filtered_project_sessions`. The Moveshelf session
  endpoint has no cursor, silently truncates at its limit, and returns 502s rather than truncating
  when a window is too large. That logic was expensive to learn and is easy to get wrong.
  It is roughly 60 lines, and it will be copied with a comment pointing at the original.
  *In practice this app queries about 90 days for one site, which is well under 100 sessions, so
  splitting will almost never fire. It is carried as insurance, not as a hot path.*
- The cancelled/no-show predicate, which must stay case-insensitive. The probe found the lowercase
  variant `"no show"` in live data, alongside the `"Cancelled"` and `"No Show"` forms the research
  app already handles.

**What this repo must never contain:** a real API key, a real `holidays.txt` with site data, or any
captured session output. A PHI pre-commit hook adapted from `project-template/hooks/pre-commit` goes
in on day one, before the first commit.

The research app is not modified by this work at all.

## 5. Architecture

```
Moveshelf-Report-Tracker/
├── README.md                  # PT-facing setup guide (§9)
├── PLAN.md                    # this file
├── CLAUDE.md                  # agent ground truth for this repo
├── requirements.txt           # moveshelf-api, and nothing heavy
├── build.py                   # PyInstaller one-file build
├── holidays.txt.example       # ships next to the exe, user-editable
├── tracker/
│   ├── __init__.py
│   ├── config.py              # finds api_key.txt + settings.json next to the exe
│   ├── api.py                 # thin session query + paging insurance
│   ├── model.py               # Session dataclass, parsing, status buckets
│   ├── businessdays.py        # the deadline math (pure, heavily tested)
│   ├── names.py               # therapist name normalizing
│   ├── audit.py               # append-only JSONL access log
│   ├── server.py              # local HTTP server, JSON endpoints
│   └── web/                   # index.html, app.js, styles.css
├── tests/
└── main.py                    # entry point: start server, open browser
```

**Runtime flow**

1. Double-click `MoveshelfReportTracker.exe`.
2. It reads `api_key.txt` from its own folder. Missing or invalid: a friendly setup page opens in the
   browser explaining exactly how to fix it, rather than a console stack trace.
3. It asks Moveshelf which projects the key can access. One project: use it. More than one: show a
   picker, and remember the choice in `settings.json` next to the exe, changeable later from a
   dropdown in the header.
4. It queries sessions for the configured lookback window (default 90 days).
5. It starts a local server on `127.0.0.1` at an unused port and opens the default browser.
6. The page renders. A Refresh button re-queries live.
7. Closing the browser tab leaves the server running. The app exits from a Quit button in the page
   and from a console window close, and it also self-exits after a period with no browser polling so
   a stray process cannot linger.

Binding is to `127.0.0.1` only, never `0.0.0.0`, so nothing is reachable from the network.

## 6. The computation

**Business days** are Monday through Friday, minus nine holidays that are **computed, not
listed**, so they never need updating for a new year. Confirmed by the clinical lead
2026-07-28:

| Holiday | Rule |
|---|---|
| New Year's Day | 1 January |
| Martin Luther King Day | third Monday in January |
| Memorial Day | last Monday in May |
| Independence Day | 4 July |
| Labor Day | first Monday in September |
| Thanksgiving Day | fourth Thursday in November |
| Friday after Thanksgiving | the day after |
| Christmas Eve | 24 December |
| Christmas Day | 25 December |

Three details that are all tested, because each one is silently wrong if missed:

- **Fixed-date holidays shift to the weekday actually observed**, Saturday to the Friday
  before and Sunday to the Monday after. Without this, Christmas 2027 (a Saturday) would
  do nothing at all and every report due that week would be a day tight. It can also place
  New Year's Day in the *previous* December, as on 31 December 2027.
- **Christmas Eve and Christmas Day collide in roughly one year in three** once shifted,
  and a dict keyed by date would silently swallow one of them. Resolved by moving the
  extra day in whichever direction the colliding shift was already heading, so two
  consecutive weekdays are always granted: Thursday 23 and Friday 24 December in 2027,
  Monday 25 and Tuesday 26 in 2028. Affected years to 2040: 2027, 2028, 2032, 2034, 2038.
  A standing test asserts every year from 2020 to 2060 yields exactly nine holidays.
- **Good Friday is not observed.** It was briefly included from an earlier, vaguer
  instruction ("Easter"), then confirmed out on 2026-07-28. `good_friday()` remains as a
  helper, because a site that does observe it cannot list a moving date in a static file.
  Note that Easter Sunday itself could never affect a deadline, being a Sunday.

`holidays.txt` next to the app is now optional and only adjusts that set: a bare date adds a day off,
and a date prefixed with `-` removes a built-in one for a site that does not observe it. A missing
file is the normal case. The page states which holidays are in force under the table, because an
invisible rule that shifts deadlines is exactly the kind of thing a user should be able to check
rather than take on trust.

For a session:

```
due_date        = processing_completed + 7 business days
days_left       = business days from today to due_date   (negative = overdue)
days_since_seen = business days from session_date to today
days_since_proc = business days from processing_completed to today
```

`days_since_seen` and `days_since_proc` are the two columns requested. `days_left` and `due_date` are
added because "3 business days left, due Thursday" is far more actionable than "you are 4 days in".
Both framings appear; `days_left` drives the sort and the color.

**Status buckets**, in sort order:

| Bucket | Rule | Meaning |
|---|---|---|
| Overdue | `days_left < 0`, within the backlog threshold | Past the deadline and actionable |
| Backlog | `days_left < -30` business days, toggleable | Past due so long it is almost certainly finished work whose date was never entered |
| No report needed | Referral type is on the exclusion list | Video, pedobarography, research and similar visits that never produce a report |

Note what none of these can express. Moveshelf records only completed-milestone dates, so
there is no way to tell an untouched report from a nearly finished one. Every bucket describes
**time remaining, never work remaining**, and the labels are worded accordingly: the bucket for
sessions whose processing is unfinished is called **"Awaiting processing"**, not "Not started",
because the hold-up is the processing step rather than the therapist.
| Due today | `days_left == 0` | Last day |
| Due soon | `days_left` 1 or 2 | Act this week |
| On track | `days_left >= 3` | Fine |
| Not started | `processing_completed` empty | Clock has not started, nothing owed yet |
| Done | `pt_evaluation_date` is a valid date | Completed, hidden by default |

Cancelled and no-show sessions are excluded entirely, using a case-insensitive check on
`sessioninfo-cancellation` that covers `"Cancelled"`, `"No Show"`, and the lowercase `"no show"` seen
in live data. Seven of the 67 probed sessions were no-shows, so this matters.

**Therapist name normalizing.** A `therapists.csv` next to the exe maps raw strings to a canonical
name, in the same spirit as the research app's surgeon normalizer:

```csv
raw_name,canonical_name
"Dawson, Renata, MPT","Dawson, Renata"
"Dawson, Renata, PT, MPT","Dawson, Renata"
```

The app generates this file on first run, pre-filled with every distinct raw name it saw and a
best-guess canonical (surname plus first name, credentials stripped), so the PT only has to correct
it rather than write it. Unmapped names pass through unchanged. If normalizing collapses two names
that the app is not confident about, it still shows both raw spellings in a tooltip, so nothing is
hidden without a trace.

## 7. The interface

**Layout, top to bottom**

1. **Header.** App name, the site dropdown, "Last refreshed 8:04 AM", a Refresh button, and a Quit
   button.
2. **Status strip.** Four large clickable tiles: Overdue, Due today, Due soon, On track. Each shows a
   count. Clicking one filters the table to that bucket. This is the "how bad is it" glance.
3. **Controls.** A search box (matches subject ID, MRN, and therapist), a "My sessions only" toggle
   that remembers who you are, a "Show completed" toggle, and a lookback selector.
4. **Table.** Every column sortable by clicking its header. Default sort is most urgent first, which
   is `days_left` ascending, with ties broken by oldest session date, so the row at the top is
   genuinely the one to do next.
5. **Footer.** Row count only. **No CSV export and no Print button**, by decision: see §9.

**Color.** Urgency runs deep red (overdue), orange (due today), amber (due soon), teal (on track),
gray (not started). Two rules, both non-negotiable in a clinical tool:

- Color is never the only signal. Every row carries a text label and an icon as well, so the table is
  fully readable in grayscale, on a projector, and by the roughly 1 in 12 men with a color vision
  deficiency. Red and green are never the sole distinction between two states.
- Contrast meets WCAG AA against both light and dark backgrounds, and the page follows the operating
  system light/dark setting.

**Things that make it pleasant rather than merely functional**

- **Click a row to open that session in Moveshelf.** The URL pattern is already known:
  `https://shriners.moveshelf.com/project/<project_id>/session/<session_id>`. See it, click it, fix
  it. This is the single highest-value interaction in the app.
- **Copy MRN** button on each row, since the next thing a PT does is paste it into the EMR.
- **Empty state that rewards you.** When nothing is overdue or due soon, the table area shows a large
  "You're all caught up" panel rather than an empty grid.
- **Relative dates in plain language.** "Due Thursday" and "2 days overdue" alongside the raw date,
  not instead of it.
- **Sticky table header** so columns stay labeled while scrolling.
- **Keyboard**: `/` focuses search, `r` refreshes, `Esc` clears filters.
*Considered and rejected 2026-07-27: a personal on-time summary ("18 of 19 reports on time this
month"). Not built at all rather than built and disabled. A tool that can be read as scoring staff
gets avoided, and the countdown already supplies all the motivation needed.*

**Charts.** The table is the product, and the status tiles are counts rather than plots. If a trend
chart is added later (weekly on-time rate, for example), the `dataviz` skill gets loaded before any
chart code is written.

### The backlog split

Measured live at CHI-Gait on 2026-07-27, over 365 days: of 356 sessions with
processing complete, **119 (33%) had none of the three EMR dates filled, at a
median of 111 business days past processing**. Confirmed with the clinical lead
that those reports were in fact completed and only the completion date was never
entered in Moveshelf. The fill rate is also falling: 72% of 2025 sessions carry a
PT evaluation date, 60% of 2026 sessions.

Left alone, a third of every worklist would be permanently red, which would bury
the handful of reports a therapist can actually act on this week and teach people
to ignore the app. So anything more than **30 business days** past due moves to a
separate Backlog bucket, controlled by a checkbox that is **on by default** and
persisted per user.

Three properties make this safe rather than a way of hiding a problem:

- The Backlog tile always shows its count, so the number is never concealed.
- Unchecking the box puts every row straight back into Overdue. Verified live: 38
  overdue becomes 25 overdue plus 13 backlog at a 90-day lookback.
- `days_left` stays truthful on backlog rows. Only the bucket changes, never the
  arithmetic.

Note the threshold is 30 **business** days, roughly six calendar weeks. The label
on the checkbox says so explicitly, and `backlog_days` in `settings.json` changes
it without a rebuild.

### Referral type decides whether a report is owed at all

The single largest source of false overdues. Measured at CHI-Gait over 365 days
(440 sessions), counting sessions with processing complete but no PT evaluation
date:

| Referral type | Sessions | No PT eval | Report owed? |
|---|---:|---:|---|
| Kinematics gait analysis | 301 | 14 (5%) | **Yes** |
| Video & Pedobarograph only | 44 | 40 (91%) | No |
| Video only | 33 | 24 (73%) | No |
| Research (grant reimbursed) 1 hr | 26 | 25 (96%) | No |
| Isokinetic | 10 | 8 (80%) | No |
| Kinematics/video sports analysis (97750) | 8 | 5 (63%) | **Yes** |
| Foot analysis (97750) | 5 | 4 (80%) | No |
| Research (not reimbursed) 1 hr / 2 hr | 5 | 5 (100%) | No |
| Gait PT eval without kinematics | 2 | 0 | **Yes** |
| Competencies | 1 | 0 | No |

The real kinematics report backlog is **14 of 301, about 5%**. Everything else
was never owed a report. Without this the app would report roughly 118 overdue at
a one-year lookback, almost all of it noise, and no therapist would trust it
twice.

`config.DEFAULT_NO_REPORT_REFERRAL_TYPES` seeds the eight excluded types on first
run, confirmed with the clinical lead on 2026-07-27. Three properties keep it
safe:

- It is an **exclusion** list, so a referral type nobody has configured defaults
  to requiring a report. New vocabulary fails toward showing work, never hiding it.
- It only seeds when `settings.json` has no such key. An explicitly empty list
  means the user ticked everything and is respected.
- The page exposes every referral type seen, with counts, as tick boxes, so the
  list is visible and correctable rather than buried in code. Referral vocabularies
  differ by site, so nothing in the default is assumed true anywhere else.

Sessions of an excluded type get a `No report needed` status, carry no countdown
(a number there would imply a deadline that does not exist), and are excluded from
Overdue.

## 8. Risks, and what to do about them

| Risk | Impact | Mitigation |
|---|---|---|
| **Unsigned .exe blocked by SmartScreen or hospital AV** | The app never reaches a single PT | The largest deployment risk by far, and not a coding problem. Needs an answer before build: is there an IT path to a signed binary or an approved software distribution channel? Fallback is a documented "More info, Run anyway" click-through, which some managed machines disable outright. **Raise with IT early.** |
| API key is a PHI-bearing credential stored in plain text next to the exe | Credential leak | README states plainly: do not put the exe folder on a network drive, do not email the key, each PT generates their own. Key is never logged, never sent anywhere but Moveshelf, never written into exports. |
| Patient names and MRNs on screen and in CSV exports | PHI exposure | No caching of session data to disk by default. Exports go to a location the user picks, with a filename that flags it as containing PHI. Screen data lives in memory only. |
| Untracked access to patient data | Audit gap | The research app's rule is that no path to patient data bypasses the audit trail. This app follows it: every query appends an `api_fetch` record to its own `logs/access.jsonl` next to the exe, with operator, project, session count, duration, and status, and never patient identifiers. Best effort, so an unwritable folder degrades to no logging rather than a crash. |
| Wrong "done" field, so rows stay red after the work is finished | Users stop trusting it | `pt_evaluation_date` is the confirmed marker, but the rule lives in one config setting so it can change without a rebuild. |
| Therapist name variants hide a PT's own work | Missed deadlines, the exact failure mode this app prevents | `therapists.csv` normalizing, generated pre-filled, with raw spellings still visible on hover. |
| Malformed dates in source data | Crash or nonsense math | Every parse is defensive and returns null on failure. A row with an unparseable date renders as "unknown" and sorts to the Not started bucket rather than taking the app down. |
| Clock skew and time zones | Off-by-one on due dates | Session dates arrive as UTC midnight. All comparisons use local calendar dates only, never wall-clock times. |

## 9. Security and compliance posture

Nothing here is a substitute for review by the Shriners privacy and security office, which almost
certainly must approve any new application that touches PHI regardless of how it is built. See the
first open question in §11. What follows is the technical posture that review will ask about.

**What is true of this design regardless of the interface choice**

- **No new vendor and no new data flow.** The only network destination is the Moveshelf API, which the
  site already uses under its existing agreement. No analytics, no telemetry, no error reporting
  service, no CDN. The page loads zero external resources, so it works with the network cable
  unplugged apart from the initial fetch.
- **Read-only.** The app never writes to Moveshelf.
- **Minimum necessary.** The query is scoped to one site and a rolling window (default 90 days), and
  requests only the eight fields shown plus the identifiers needed to display and link a row.
- **Nothing persists by default.** Session data lives in memory for the life of the process. The only
  files written next to the exe are `settings.json`, `therapists.csv`, `holidays.txt`, and
  `logs/access.jsonl`, none of which contain patient identifiers.
- **Audited.** Every fetch appends an access record (operator, project, session count, duration,
  status) to `logs/access.jsonl`, with no patient identifiers in it.
- **The API key is the real credential risk**, and it is independent of the UI. Plain text next to the
  exe means anyone who can read that folder can impersonate that PT against Moveshelf. Mitigations:
  each PT generates their own key, the folder is local and never a network or synced drive, and the key
  is encrypted at rest with Windows DPAPI so that only that Windows user account can decrypt it. The
  key is never logged, never displayed after entry, and never written into an export.

**What the browser specifically adds, and how each is handled**

| Browser-specific risk | Handling |
|---|---|
| Any local process running as that user could connect to the server port | Every request requires a random per-run bearer token. The exe opens the browser once with the token in the URL, the server sets it as an `HttpOnly`, `SameSite=Strict` cookie, then redirects to a clean URL. Without the token the server returns 401 and serves nothing. |
| A malicious website could reach `127.0.0.1` via DNS rebinding, which defeats plain CORS | The server rejects any request whose `Host` header is not `127.0.0.1:<port>` or `localhost:<port>`. A rebinding attack carries the attacker's domain in `Host` and is refused. CORS is set to deny all origins, and the port is randomized per run. |
| The browser could write responses containing PHI to its on-disk cache | Every response carries `Cache-Control: no-store, no-cache` and `Pragma: no-cache`. Nothing is cacheable. |
| PHI could land in browser history or in a synced browser profile | No patient identifier ever appears in a URL. All data moves in JSON response bodies. History records only `127.0.0.1:<port>`, which is not PHI, so profile sync is harmless. |
| **Browser extensions with "read all site data" permission can read the page, including PHI** | This one has no clean technical fix, and it is the single genuine advantage a native desktop app would have. It is the item most worth raising with IT, who typically already manage extension allowlists on hospital machines. Worth noting the exposure is comparable to the PTs' existing browser use of Moveshelf itself, which displays the same patient data in the same browser. |
| A tab left open on an unattended workstation displays PHI | The page blanks itself and requires a click to reveal after a few minutes of inactivity, and the server shuts down when the browser stops polling. Neither replaces workstation lock policy. |
| Plain HTTP rather than HTTPS | Loopback traffic never reaches a network interface, so there is no transmission to intercept, but a checklist-driven security review may still flag the word "HTTP". Worth pre-empting in writing rather than arguing after a rejection. |

**Why the browser is nonetheless the right choice here.** The static-HTML-file option is meaningfully
worse for PHI, because it writes a file containing patient names and MRNs to disk, where it persists
and can be picked up by OneDrive or a backup agent. The Qt option removes the extension risk but costs
a much larger unsigned binary, which makes the deployment problem in §8 harder, and unsigned binaries
are the risk most likely to actually stop this project. The local server keeps PHI in memory, adds no
new file on disk, and reuses a browser the PTs already view this same data in.

**No data leaves the app. Decided 2026-07-27.** There is no CSV export and no Print button. Both would
create PHI outside the app's control, a file on disk and a piece of paper, and the conservative
position is the one that is easiest to defend in a security review. The practical consequence is that
the only way patient data leaves this app is a human reading the screen.

One honest caveat, because it should not be discovered later in a review: **a browser can always print
or save a page with Ctrl+P and Ctrl+S, and that cannot be blocked by the application.** Removing the
buttons removes the sanctioned workflow and the encouragement, which is the point, but it is not a
technical control. Anyone writing this up for the privacy office should describe it as "the
application provides no export function" rather than "export is prevented". The same is true of a
screenshot, and of the Moveshelf web interface the PTs already use.

If a supervisor later needs a roll-up across therapists, the right answer is a report generated from
the existing research application, which already has an audited export path, rather than adding an
export to this one.

## 10. Setup instructions for PTs (README outline)

Written for someone who has never used a command line. Every step gets a screenshot.

**Step 1. Generate your own API key.** *(Screen confirmed 2026-07-27.)*

1. Log in at `https://shriners.moveshelf.com` and click your avatar at the top right, then
   **User settings**. The address bar will read `shriners.moveshelf.com/profile/<your-username>`.
2. Scroll to the **API Keys** panel at the bottom.
3. In the **Application ID** box, type `ReportTracker`, then click **Generate API Key**.
4. **Copy the key immediately.** It is shown once. If you lose it, revoke it and generate a new one.

Three things the instructions must stress, because the screen makes them easy to get wrong:

- **Use a dedicated Application ID, not an existing key.** The panel lists existing keys by App ID,
  each with its own Revoke button. A key named `ReportTracker` can be revoked on its own without
  breaking anything else you use Moveshelf for. Reusing a general-purpose key means revoking it later
  breaks everything at once.
- **The Application ID is a label you choose, not a password**, and it is not the key itself. People
  routinely paste the label into the app instead of the key.
- **Revoke it the moment you no longer need it,** or if you think it has been seen by anyone else, or
  if you change roles. The trash icon next to the App ID does it instantly, and no IT ticket is
  needed. This is the single most important sentence in the whole README.

Your key inherits your own Moveshelf access, so it can see exactly the sites you can see and nothing
more. It is not an elevated credential, but it is enough to read patient data, so it is treated like
a password: never emailed, never shared, never put on a network drive, one per person.

**Step 2 onward.**

2. **Make a folder** for the app, for example `C:\MoveshelfReportTracker`. Keep it local. Not a network
   drive, and not inside OneDrive or any other synced folder.
3. **Save the key.** Create `api_key.txt` in that folder and paste the key in as the only line. No
   quotes, no extra spaces. On first run the app re-encrypts it in place with Windows DPAPI so that
   only your Windows account can read it, and the plain text version is removed.
4. **Put the exe in the same folder.**
5. **Double-click it.** A browser tab opens with your reports. The first run asks which site you work
   at, if your key covers more than one.
6. **Optional:** edit `holidays.txt` to add your site's observed holidays, and `therapists.csv` to
   merge different spellings of your name.

Plus a troubleshooting section covering: Windows warned me about the file; nothing opened; it says my
key is invalid (most often the Application ID was pasted instead of the key); I lost my key; my name
does not appear in the therapist filter; the numbers look wrong over a holiday; how do I revoke a key.

## 11. Build order

1. `businessdays.py` plus tests. Pure functions, no I/O, the piece most likely to be subtly wrong, and
   the piece everything else trusts. Includes holiday loading, weekend skipping, and the
   negative/overdue direction.
2. `model.py` plus tests, using saved fixture JSON, with no network involved. Parsing, the doubly
   nested metadata, cancelled filtering, status bucketing, defensive dates.
3. `api.py` plus `config.py`. First real end-to-end fetch against CHI-Gait, printed to the console.
4. `server.py` plus the web UI. This is where most of the visible work is.
5. `audit.py`, `names.py`, and the first-run setup flow.
6. `build.py`, a PyInstaller build, and a test on a clean machine that has no Python installed. **This
   step must not be left to the end as a formality**, because it is where the SmartScreen and AV
   problems surface.
7. README with screenshots, then a pilot with one or two friendly PTs before any wider release.

## 12. Open questions

**Still open**

1. **What does `sessioninfo-interpretation-completed` actually mean,** given it holds future dates? It
   is display-only and drives no deadline math, so it is safe either way, but the column label should
   eventually match reality.

**Deferred, not blocking**

2. **Approval and code signing at Shriners.** Deferred by decision on 2026-07-27. Development
   proceeds; §9 is written so it can be handed to a reviewer whenever that conversation happens.
   Nothing about the build depends on the answer except the final distribution step.

**Resolved 2026-07-27**

- No CSV export and no Print button (§9).
- API key generation screen confirmed and written up in §10.
- **No on-time summary.** Not built at all, rather than built and disabled. Dead code behind a flag is
  still code a reviewer has to read and a maintainer has to carry.
- **Personal tool, no supervisor view.** "My sessions only" is therefore a plain toggle over the
  site's sessions rather than a permission boundary. Note that this is a display convenience, not a
  security control: the data for the whole site is already in the page, so the toggle must never be
  described as restricting what someone can see. Access is bounded by what the person's own API key
  grants, which is the only boundary that means anything.
