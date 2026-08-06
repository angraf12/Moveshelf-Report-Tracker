# Changelog

What changed, when, and why. Newest first.

Each entry says what prompted the change, because with a clinical tool the reason
usually matters more than the diff.

---

## 0.3.0 — 2026-08-06

More feedback from users.

### Added
- **Foot Model column**, last in the table, ticked when a foot model was
  collected. Those rows also carry a faint tint so a scan down the table shows
  them. Foot model processing waits on x-ray measurements that can take extra
  days, so a late row of this kind means something different from a late row
  without one.

  Source is the `sessioninfo-data-collected` multiselect. Two traps, both found
  in live data and both covered by tests: the field is a **dict**
  `{"value": [...], "multiselect": True}` where `multiselect` is sometimes the
  string `"True"`, and **"Foot model" and "Foot pressure" are separate options**,
  so a substring match on "foot" counts 290 of 439 sessions instead of 95.

### Changed
- **Days Since Seen** narrowed, and both Days headings now stack on two lines.
- **Both Days columns are centred.**
- **The Subject ID column is capped** and truncates with the full name on hover.
  It was unbounded, so one long patient name widened the whole table and pushed
  the last columns off screen.
- Cell padding trimmed by a pixel. With 13 columns that is 26px across the
  table, which was cheaper than truncating names further.

Everything still fits on one screen with no sideways scrolling at 1280, 1452 and
1920, which is checked by test rather than by eye.

### Deliberately not changed
- **The 7 business day deadline is the same for foot model cases.** The question
  was raised and the numbers were run: across 224 kinematics sessions with a
  completed PT evaluation, foot model cases take a median of 9 business days
  against 7, and 43% finish inside 7 days against 52%. Two extra days would put
  them level at 54%.

  The decision on 2026-08-06 was to flag only, not to change any deadline. The
  flag makes a late row explainable without the app quietly moving the goalposts.
  If this is revisited, 2 extra days is the figure the data supports; 3 gives
  61% and 5 gives 68%, which are policy choices rather than data-driven ones.

---

## 0.2.1 — 2026-08-03

### Added
- **The version is shown in the footer.** Asked "how do I know I am viewing the
  latest version?" and the honest answer was that you could not: the version was
  in the payload and printed once to the console window, which people close. It
  now reads `v0.2.1` beside the row count.

  This is not cosmetic. Nothing updates itself, so a user can sit on an old build
  indefinitely and give feedback on behaviour that was already fixed. Checking
  which build someone is on has to be a glance, not an investigation.

---

## 0.2.0 — 2026-07-30

Feedback from the first round of real users.

### Added
- **Export to Excel.** A button below the table writes the rows currently shown to
  a `.csv` file. Approved for clinical use by Ross Chafetz on 2026-07-30, which
  reverses the "no export" decision of 2026-07-27.

  Three controls make it defensible and none should be removed: it exports
  **only the visible rows**, so filtering first exports just those; every export
  is written to `logs/access.jsonl` with the row count and filename but never the
  rows; and values beginning `=`, `+`, `-` or `@` are prefixed with an apostrophe
  so Excel cannot execute them as formulas.
- **Referring physician column**, from `sessioninfo-referring-physician`, placed
  immediately after Therapist as users asked. Filled on 410 of 442 sampled
  sessions.

### Removed
- **PDF in EMR column.** Nobody was using it.

### Changed
- Documentation everywhere that claimed the app has no export. That claim
  appeared in the security posture intended for a reviewer, so leaving it would
  have been worse than having no documentation at all.
- Truncated columns narrowed to 104px so the whole table still fits on a
  1280-pixel screen with the extra column. Verified at 1280, 1452 and 1920.

### Fixed
- Layout tests were order-dependent: they measured whatever rows an earlier test
  class had left in the shared page fixture. They now set up their own data.

---

## 0.1.0 — 2026-07-27 to 2026-07-30

First working version, built and refined against live Moveshelf data.

### The app
- Reads sessions through `getFilteredProjectSessions` and shows a worklist sorted
  by urgency, counting the 7 business days a report is allowed after processing
  completes.
- Runs as a single Windows executable serving a local page to the user's browser.
  Read-only; it never writes to Moveshelf.
- Status buckets: overdue, due today, due soon, on track, awaiting processing,
  no report needed, backlog, done.

### What the data forced
- **Referral type decides whether a report is owed at all.** Video, pedobarography,
  research and isokinetic visits do not produce one. At CHI-Gait that is 32 of 95
  recent sessions; without accounting for it the overdue count was inflated
  several times over.
- **Therapist names are free text with multiple spellings per person** at every
  one of four sites sampled. Names are normalized and merged through
  `therapists.csv`, or "my sessions" would hide part of someone's own workload.
- **Some sites record no therapist at all** on a large share of sessions, 45% at
  one site, so the picker offers "(no therapist recorded)".
- **Moveshelf records no in-progress state**, only completion dates, so no bucket
  can distinguish an untouched report from a nearly finished one. The bucket for
  unfinished processing is called "Awaiting processing", not "Not started",
  because the hold-up is the processing step rather than the therapist.
- **Nine holidays** computed rather than listed, including the Christmas Eve and
  Christmas Day collision that occurs about one year in three.

### Security
- Refuses to run from a network drive, checked before the key is read or written,
  so a run from one leaves nothing behind there.
- API key encrypted at rest with Windows DPAPI after the first successful run.
- Loopback only, per-run bearer token, `Host` header validation against DNS
  rebinding, `no-store` on every response, and no patient identifier in any URL.
- Every fetch and every export recorded in `logs/access.jsonl`, with no patient
  identifiers in it.

### Known limitations
- The executable is unsigned, so Windows SmartScreen warns on first run. This is
  the largest deployment risk and is not a code problem.
- A refresh of about 100 sessions takes 15 to 18 seconds; that is the Moveshelf
  call, not the app.
