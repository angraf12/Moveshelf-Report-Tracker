# Rollout log

Who was told what, and when. Newest first.

This is the record of what went out to real people: announcements, pilot
invitations, and the replies that changed the plan. `CHANGELOG.md` says what
changed in the code; this file says what the outside world was told about it.

**Two rules for entries here.** No real server paths, because this repository is
public: never name where the app is distributed, and let `APP_FOLDER` in
`docs/site.local.json` hold the real value. And no patient identifiers, ever.

**Entries are a record, not current policy.** An email is true on the day it is
sent. Where a later decision has overtaken something an email claimed, the entry
says so inline rather than being edited to match, because the point of a log is
to show what people were actually told.

---

## 2026-09-03 — Install briefing for the engineers at the other sites

**Scheduled, not yet delivered.** Update this entry with what actually happened,
particularly the Windows behaviour, which is the whole reason for holding it.

Audience: the Motion Analysis Center engineers at the other sites, who will put
0.3.0 on their PTs' machines and then be the ones asked about it. Follows
directly from Ross Chafetz's 2026-07-28 request to get it in front of the PTs at
those sites; the engineers are the layer in between, and they were never briefed.

Format: about twenty minutes, casual, demo first and install steps second. The
running order is `docs/briefings/engineer-briefing-2026-09-03.html`, a single
self-contained page opened in a browser rather than slides. It is a briefing for
us, not a therapist-facing document, so it is not built by `docs/build_docs.py`
and is never distributed to users.

**What it covers.** What the tool is and the read-only, loopback, one-key-per-person
shape of it; a live demo in a fixed order; the seven-business-day clock drawn on a
September 2026 calendar strip, weekends and Labor Day skipped; the status buckets
with the app's own wording; why some referral types owe no report, with the
CHI-Gait numbers; the six install steps; what the app talks to and what lands on
disk; the questions their PTs will ask them; and the ask at the end.

**Three things the engineers must not get wrong,** each given its own moment:

- **Time remaining, never work remaining.** Moveshelf has no progress signal at
  all, so "Awaiting processing" is about data processing and never about the
  therapist. An engineer repeating the old "not started" wording hands a PT an
  accusation.
- **"My sessions only" is a display convenience, not a security control.** The
  site's data is already in the page. Describing it as a restriction is a promise
  the app does not keep.
- **Copy it to a local folder before running it,** with the reason rather than just
  the rule: the app writes beside itself, so a personal key would land beside
  it. The refusal to start is a guard rail, not the explanation.

**Correcting the 28 July email.** That email told this same group there was
"deliberately no export." Export landed two days later. The briefing corrects it
explicitly rather than hoping nobody remembers, and restates the three controls
that go with it.

**What is being asked for back.** Whether it runs on a managed machine at all —
SmartScreen wording, antivirus quarantine, outright blocks. That is still the
largest unknown about this build and it cannot be answered from one machine. Then
therapist-name coverage at each site, which referral types owe no report there,
and any local holiday, Good Friday being the likely one.

---

## 2026-07-28 — Prototype announced to the PT group

From Adam Graf, to the therapist group. First distribution of the tool to
anyone. Version was the packaged build verified the same day.

**What the email said.**

- The prototype is available internally, in a `ReportTracker` folder.
- What it does: shows each PT which gait reports are due and how many business
  days are left, counting from the date processing was completed. Read-only,
  never changes anything in Moveshelf.
- Setup, about five minutes, once per person:
  1. **Copy the whole folder to your Desktop and work from your copy.**
     Everything the app saves, including the Moveshelf key, goes into the folder
     it sits in, so running it from a network folder would put the key where others
     could open it. The app refuses to run from there for that reason.
  2. Open `Setup Guide.html` from your copy and follow it; it covers everything
     with pictures.
  3. Generate a personal Moveshelf API key: profile symbol on the blue Moveshelf
     bar, right of HELP, then SETTINGS, scroll to API Keys, Application ID
     `ReportTracker`, Generate API Key, Download Key. The email warned about the
     browser's own profile button sitting just above Moveshelf's, and gave
     "User settings" as the page title that confirms you are in the right place.
  4. Move the downloaded key file into the ReportTracker folder on the Desktop,
     next to `MoveshelfReportTracker.exe`.
  5. Run the exe. SmartScreen warns on first run: More info, then Run anyway.
     Expected, because the build is in-house and unsigned.
  6. First run asks for site (the Gait one for your location, e.g. CHI-Gait) and
     which therapist you are, and remembers both.
- Notes: the key is personal, like a password, never shared or emailed, and
  revocable instantly from the same Settings page with no IT ticket. Asked for
  feedback on anything confusing, wrong or missing, and to be told if it will
  not start or Windows blocks it entirely.

**One claim in that email is now out of date.** It said "the app is read-only and
there is deliberately no export, so patient information stays on your screen."
That was true on 2026-07-28 and was reversed two days later: CSV export was
approved for clinical use by Ross Chafetz on 2026-07-30, with the three controls
described in `PLAN.md` §9. Read-only against Moveshelf is still true and always
will be. Anyone reading this entry as a statement of current behaviour would be
wrong about export.

**Reply, same day, 13:23 — Ross Chafetz** (Corporate Director of Motion Analysis
Centers): asked that the tool be shared with Robbie and Spencer so their PTs can
trial it, and said everyone is very excited. Phrased as "share the python
script"; what is actually handed over is the `ReportTracker` folder with the built
exe and the setup guide, which is the same thing from the recipient's side and
avoids anyone needing Python.

**What this entry set in motion.** Pilot widened from the one or two friendly PTs
in `PLAN.md` §11 step 7 to two additional sites. The export decision landed two
days later and came out of this same round of feedback.
