# Releasing an update

Every time something changes and users should get it. Takes about ten minutes,
most of it waiting for the build.

---

## 1. Write down what changed, first

Add an entry at the top of [CHANGELOG.md](CHANGELOG.md) **before** building, while
you still remember why. Say what prompted the change, not only what changed: with
a clinical tool the reason is usually the part someone needs later.

Bump `__version__` in `tracker/__init__.py`:

- **patch** (0.2.0 → 0.2.1) for a fix nobody has to be told about
- **minor** (0.2.0 → 0.3.0) for anything users will notice
- **major** for a change that breaks how people already work

The version is written into every audit log line, so it is how you tell which
build produced which record.

---

## 2. Build and check

```
python release.py
```

This runs the tests, builds the executable, builds the documents, and then checks
what it produced. It stops at the first failure and copies nothing anywhere.

**The checks are not a formality.** The setup guide in `docs/` is a *template*
containing `{{APP_FOLDER}}` placeholders and empty screenshot boxes; the real one
is generated. That template was once copied to the distribution folder by mistake. It
looked completely normal in a folder listing and was useless to every user. The
checks catch exactly that, plus an executable that was not rebuilt, any
credential or personal settings file that has drifted into the staging folder,
and any exported worklist. That last one matters most: an export is a CSV of
subject IDs and MRNs, it lands wherever your browser last saved a download, and
nothing about its name marks it as patient data.

Everything lands in `ReportTracker-Release` in your home directory. Set
`REPORT_TRACKER_STAGING` to use a different folder; `release.py` and
`docs/build_docs.py` both follow it, and the real path is deliberately not
written down here because this repository is public.

To verify without rebuilding: `python release.py --check`

---

## 3. Update the distribution folder

Copy the four files from the staging folder to the distribution folder. Its path is the
`APP_FOLDER` value in `docs/site.local.json`, which is gitignored: real server
paths do not belong in a public repository, and it is the same file the setup
guide is built from, so there is only one place to correct if the folder moves.

- `MoveshelfReportTracker.exe`
- `Setup Guide.html`
- `READ ME FIRST.txt`
- `holidays.txt.example`

Then confirm the copy actually landed, rather than assuming:

```powershell
$dist = (Get-Content docs\site.local.json | ConvertFrom-Json).APP_FOLDER
$local = $env:REPORT_TRACKER_STAGING
if (-not $local) { $local = Join-Path $HOME "ReportTracker-Release" }
foreach ($f in @("MoveshelfReportTracker.exe","Setup Guide.html","READ ME FIRST.txt","holidays.txt.example")) {
  $a = (Get-FileHash "$local\$f").Hash
  $b = (Get-FileHash "$dist\$f").Hash
  "{0,-30} {1}" -f $f, $(if ($a -eq $b) { "identical" } else { "MISMATCH" })
}
```

**Never put anything else in that folder.** No key file, no `settings.json`, no
`logs`, and above all no exported CSV. `release.py` checks the staging folder for
all of these, but it cannot see the distribution folder.

---

## 4. Push to GitHub

The repository is **public**, so check before committing that nothing site
specific went in. Real values live in `docs/site.local.json`, which is gitignored;
the tracked documents hold placeholders instead.

```
git status
git add -A
git commit -m "0.3.0: what changed in one line"
git push
```

The pre-commit hook blocks names and MRN-shaped numbers, and refuses to stage a
credential or a personal runtime file. If it fires, read what it found rather
than reaching for `--no-verify`.

Tag a release users were told about:

```
git tag -a v0.3.0 -m "0.3.0"
git push --tags
```

---

## 5. Tell people

Users **must copy the new `.exe` over their own copy**; nothing updates itself.
Their key, settings and therapist list live beside it and are untouched by
replacing the executable.

A short note works:

> Report Tracker 0.3.0 is in the usual folder. Copy `MoveshelfReportTracker.exe`
> over the one in your folder, replacing it. Your key and settings stay as they
> are. What changed: ...

---

## Quick reference

| Task | Command |
|---|---|
| Tests only | `python -m pytest tests/ -q` |
| Tests without a browser | `python -m pytest tests/ -q -m "not browser"` |
| Build the exe | `python build.py` |
| Build the documents | `python docs/build_docs.py` |
| Everything, with checks | `python release.py` |
| Check without building | `python release.py --check` |

## If the browser tests error

The browser tests share one Chromium and one page across the whole module, which
keeps them fast but means they are coupled. Seen once: every test in a later class
erroring at setup, while each passed when run on its own, and three consecutive
full runs afterwards were clean.

The trigger appears to be another Chromium already running, for instance one left
behind by a manual check. Re-run before investigating. If they keep erroring:

```
python -m pytest tests/ -q -m "not browser"   # everything else still covered
python -m pytest tests/test_ui_browser.py -q  # then these on their own
```

Do not add a retry to `release.py` to paper over this. A release gate that
retries until it passes is not a gate.

## If a release goes wrong

The distribution folder holds one version at a time, so **keep the previous `.exe`**
until the new one is confirmed working. To roll back, copy the old executable
back into the distribution folder and tell people to do the same. Settings and keys are
unaffected, so a rollback costs nothing but the copy.
