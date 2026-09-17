# Moveshelf Report Tracker

**See which of your gait reports are due, and by when.**

You have 7 business days from the date processing is completed to finish your report.
This app reads your sessions from Moveshelf and shows you a countdown for each one, so
nothing slips past without you noticing.

It only reads. It never changes anything in Moveshelf.

---

## Getting the app

**There is no `.exe` in this GitHub repository.** The repository holds the source code.
The executable, `MoveshelfReportTracker.exe`, is built from that source and distributed
internally. It is not committed here and not offered as a GitHub download.

| You are | Where to get it |
|---|---|
| A therapist or engineer at a Shriners Children's Motion Analysis Center | Ask your site's motion analysis engineer for the `ReportTracker` folder. It contains the `.exe`, a `Setup Guide.html` and a `READ ME FIRST.txt`. Then follow [Setting it up](#setting-it-up-about-five-minutes-once) below. |
| Anyone else, reading this on GitHub | Run it from source, or build the `.exe` yourself. See [Running or building from source](#running-or-building-from-source) at the end. You need your own Moveshelf API key either way. |

The setup steps below are written for therapists who already have the `ReportTracker` folder.

---

## Setting it up (about five minutes, once)

### 1. Get your own API key

An API key is how the app proves to Moveshelf that it is you. You make your own.

1. Log in to Moveshelf.
2. Click the round profile symbol **on the blue Moveshelf bar**, to the right of
   **HELP** and the bell. Note there are two round buttons near the top right, one above
   the other: the upper one in the grey bar is your *web browser's* own profile button,
   which is the wrong one. The Moveshelf symbol is a small coloured pattern and looks
   different for every person.
3. In the menu that drops down, click **SETTINGS**. You are in the right place when the
   page is titled **User settings** and shows your name and email.
4. Scroll down to the **API Keys** box.
5. In the box labelled **Application ID\***, type `ReportTracker`.
6. Click **Generate API Key**.
7. Click **Download Key**. This is the step people miss; nothing is saved until you
   click it.
8. Move the downloaded file out of your Downloads folder into the folder you make in
   step 2 below, next to the `.exe`.

> *(Screenshot of the profile menu, and of the API Keys panel, go here.)*

**Three things people get wrong here:**

- **Do not skip Download Key.** Generating shows the key; downloading saves the file.
- **The Application ID is not the key.** `ReportTracker` is only a label so you can
  recognise this key later.
- **Make a key just for this app.** Keys are listed by Application ID with their own
  delete button, so this one can be switched off later without breaking anything else.

**Treat the key like your password.** It can read patient information. Do not email it,
do not share it, do not put it on a network drive. One key per person. Revoke it from the
same Settings page whenever you want.

### 2. Make a folder for the app

Copy the `ReportTracker` folder to your Desktop, or make one anywhere local.

**Not** a network drive: the app refuses to run from one, because everything it saves,
including the key, goes into the folder it sits in.

### 3. Put two things in that folder

- The key file you downloaded, named `mvshlf-api-key.json`
- `MoveshelfReportTracker.exe` (it is not in this GitHub repository; see
  [Getting the app](#getting-the-app))

### 4. Double-click the app

A black window opens and your browser follows a few seconds later. The first load takes
about 15 seconds while it fetches your sessions.

If your key covers more than one site, it asks which one you work at, then remembers. Pick
the **Gait** site for your location, such as `shriners/CHI-Gait`. A Gait site is
preselected for you.

**Windows may warn you that it does not recognize the app.** It is not signed by a
certificate authority, which is normal for an internally built tool. Click **More info**,
then **Run anyway**. If your computer refuses outright, your IT department has blocked
unsigned programs and will need to approve it.

Most people see no warning at all. If it just starts, nothing has been skipped.

---

## Using it

### The tiles across the top

Click any tile to see only those sessions. Hover over one to read what it means.

| Tile | What it means |
|---|---|
| **Overdue** | Past the 7 business day deadline. |
| **Due today** | The 7 business days run out today. |
| **Due soon** | Two business days or fewer left. |
| **On track** | Three or more business days left. |
| **Awaiting processing** | Data processing is not finished, so your clock has not started. Nothing for you to do yet. |
| **No report** | This referral type does not produce a report, so no deadline applies. |
| **Backlog** | More than 30 business days past due. Usually finished work whose date was never entered. |

**Two things the app genuinely cannot know.** Moveshelf does not record whether a report
has been *started*, only when milestones are *completed*. So "On track" means time
remains, not that anyone has begun writing. And a report is only counted as finished when
its **PT evaluation date** is filled in. If you finished the work but never entered the
date, the app still shows it as outstanding.

### Getting back to the full list

When you filter, an amber bar appears saying **Showing a filtered list**. Click
**Clear filters** on the right of that bar, press **Esc**, or click the highlighted tile
again. (The Refresh button does not clear filters. It fetches newer data.)

### Everything else

- **Click any row** to open that session in Moveshelf. This is the fastest way to go fix
  something.
- **Click the patient's name** to open that patient instead, showing all of their
  sessions rather than just this one. Useful when you need history the 90 day window does
  not cover.
- **The copy button** next to an MRN puts it on your clipboard for pasting into the EMR.
- **My sessions only** narrows to your own work. The first time you run it, the app asks
  which therapist you are.
- **Search** matches subject, MRN or therapist. Press `/` to jump to the box.
- **Sort** by clicking any column heading.
- **Refresh** re-fetches from Moveshelf. Press `r`.
- **Export to Excel** saves exactly the rows currently shown, so filter first and you
  export just that. Approved for clinical use.

### Deadlines and holidays

Business days are Monday to Friday, minus nine holidays the app already knows and works
out every year on its own:

New Year's Day, Martin Luther King Day, Memorial Day, Independence Day, Labor Day,
Thanksgiving, the Friday after Thanksgiving, Christmas Eve and Christmas Day.

A holiday falling at a weekend counts on the weekday it is observed: Saturday moves to
the Friday before, Sunday to the Monday after. Christmas Eve and Christmas Day would
otherwise collide in about one year in three, so the app keeps them on two consecutive
weekdays. In 2027 that is Thursday 23 and Friday 24 December; in 2028, Monday 25 and
Tuesday 26.

Good Friday is not observed. You only need a `holidays.txt` file if your site differs
from any of the above. See `holidays.txt.example`.

---

## Troubleshooting

**Windows blocked it.** See step 4 above. If **Run anyway** is not offered, IT has blocked
unsigned programs.

**Nothing happened when I double-clicked.** Look at the black window for a message. If it
closed instantly, run it once from a Command Prompt so you can read the error.

**"No API key found".** The app tells you which folder it looked in. Make sure the key
file sits in that same folder, and that it is named `mvshlf-api-key.json`.

**"Does not contain a valid API key".** You either saved the Application ID instead of the
key, or missed the **Download Key** button. Redo step 1 all the way through.

**My name is not in the therapist list.** The list is not a staff roster. It is built from
the therapist recorded on the sessions themselves, for the selected site and the current
date range, so a therapist with no sessions in that window does not appear. Just type your
name into the box, or widen the date range.

If your name is spelled more than one way in Moveshelf, open `therapists.csv` in the app
folder: it lists every spelling found, and giving two rows the same `canonical_name`
merges them. Measured across four Gait sites, every one of them had at least one person
entered two ways.

**Some sessions belong to nobody.** Where Moveshelf has no therapist recorded, the box
offers **(no therapist recorded)** so those sessions can still be found. At one site this
was 45% of recent sessions, so it is worth checking.

**A session is missing.** Check the lookback dropdown, which defaults to 90 days, and
check whether its referral type is switched off under **Which referral types need a PT
report?**

**The numbers look wrong around a holiday.** Check `holidays.txt.example` and add a
`holidays.txt` if your site's holidays differ from the nine built in.

**It says the counts are out of date.** You left the tab open past midnight. It refreshes
itself; the warning is there so you never read yesterday's countdown as today's.

**I want to turn my key off.** Go back to Moveshelf, click your profile picture, then
SETTINGS, then API Keys, and click the delete button next to `ReportTracker`. It takes effect immediately, no IT ticket needed.
Do this if you think anyone else has seen your key, or when you change roles.

---

## What the app does with your data

- It **only reads** from Moveshelf. It never writes anything back.
- It runs **entirely on your computer**. The page is served from your own machine and is
  not reachable from the network.
- **Patient data is never saved to disk.** It lives in memory while the app is running.
- **Export to Excel** writes the rows you can see to a `.csv` file you can open in Excel.
  It contains patient names and MRNs, so save it somewhere appropriate. Every export is
  recorded in the app's own log (how many rows, not which).
- Your key is **encrypted after the first successful run** so that only your Windows
  account on that computer can read it, and the plain copy is deleted.
- `logs\access.jsonl` records that a fetch happened, with no patient details in it.
- The page **hides itself after a few minutes** of inactivity, and the app shuts down on
  its own once you close the browser tab.

---

## Running or building from source

For developers, maintainers, and anyone evaluating the app from GitHub. You need
Python 3 (developed on 3.12), and a Moveshelf API key file from step 1 above.

**Run from source.** No executable is needed.

```bash
git clone https://github.com/angraf12/Moveshelf-Report-Tracker.git
cd Moveshelf-Report-Tracker
pip install -r requirements.txt
# put your mvshlf-api-key.json in this folder (it is gitignored), then:
python main.py
```

The key, `settings.json`, `therapists.csv` and `logs/` are all written into the
repository folder, and all are gitignored. To keep them somewhere else, use
`python main.py --folder <path>`. This works on macOS and Linux as well, but the key is
only encrypted at rest on Windows. Anywhere else it stays in plain text.

**Build the executable.** This needs Windows, because the build produces a Windows
`.exe`.

```bash
python build.py        # produces dist\MoveshelfReportTracker.exe
```

Copy that file into a new local folder next to your key file and double-click it, as
in [Setting it up](#setting-it-up-about-five-minutes-once). `dist\` is gitignored,
which is why no `.exe` appears in the repository.
