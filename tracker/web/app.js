/* Report Tracker page logic.

   Rules this file must keep, from PLAN.md section 9:
   - No patient identifier ever goes into a URL, so nothing lands in browser
     history. All data arrives in JSON response bodies.
   - Export writes patient names and MRNs to a file. Approved for clinical
     use 2026-07-30. Every export is recorded in the access log.
   - Status is conveyed by text and symbol as well as color. */
"use strict";

/* Each bucket carries a plain-language description, shown when hovering its
   tile. Two of these names were genuinely ambiguous until someone asked, and the
   answer belongs in the app rather than in a conversation.

   The important caveat, repeated in the on_track and awaiting-processing text:
   Moveshelf has no field recording that a report is in progress. Every workflow
   field is a completed-milestone date. A report that is nearly finished and one
   nobody has opened look identical here. */
const STATUS_META = {
  overdue:     { icon: "⚠", label: "Overdue", tile: "Overdue",
                 desc: "Past the 7 business day deadline that started when processing completed." },
  due_today:   { icon: "⏰", label: "Due today", tile: "Due today",
                 desc: "The 7 business days run out today." },
  due_soon:    { icon: "••", label: "Due soon", tile: "Due soon",
                 desc: "Two business days or fewer left." },
  on_track:    { icon: "✓", label: "On track", tile: "On track",
                 desc: "Three or more business days left. This means time remains, "
                     + "not that the report has been started: Moveshelf does not record that." },
  not_started: { icon: "⋯", label: "Awaiting processing", tile: "Awaiting processing",
                 desc: "Data processing is not finished, so the 7 day clock has not started "
                     + "and nothing is owed by the therapist yet. Waiting on the person processing." },
  no_report:   { icon: "–", label: "No report needed", tile: "No report",
                 desc: "This referral type does not produce a PT report, so no deadline applies." },
  backlog:     { icon: "▫", label: "Backlog", tile: "Backlog",
                 desc: "More than 30 business days past due. Usually finished work whose "
                     + "completion date was never entered." },
  done:        { icon: "✓", label: "Done", tile: "Done",
                 desc: "The PT evaluation date is filled in, which stops the clock." },
};
const TILE_ORDER = ["overdue", "due_today", "due_soon", "on_track", "not_started",
                    "no_report", "backlog"];
const WEEKDAYS = ["Sun","Mon","Tue","Wed","Thu","Fri","Sat"];
const IDLE_BLANK_MS = 5 * 60 * 1000;

/* Sessions where Moveshelf records no therapist at all. At some sites that is a
   large share (40 of 88 in 90 days at LEX-Gait), and without an entry for them
   they belong to nobody and nobody ever sees them. */
const UNASSIGNED = "(no therapist recorded)";

/* Sentinel for the "Other name..." entry. Not a real therapist, so it is never
   stored; picking it just reveals the text box. Deliberately plain and readable:
   a NUL byte here does not survive round-tripping through source files and the
   DOM, and it broke test collection outright when tried. */
const OTHER_NAME = "__other_name__";

let STATE = null;
let filterStatus = null;
let sortKey = "sort_rank";
let sortDir = 1;
let idleTimer = null;
let pollTimer = null;
let identitySkipped = false;

/* The local calendar date, in the same YYYY-MM-DD form the server reports. */
function localToday() {
  return new Date().toLocaleDateString("en-CA");
}

/* Every countdown is computed against the date the data was fetched. Leave the
   tab open past midnight and those numbers are quietly a day stale, which in a
   deadline tool means under-reporting urgency. Re-fetch as soon as the date
   turns over. */
function checkDateRollover() {
  if (!STATE || !STATE.today || STATE.loading) return;
  if (STATE.today !== localToday()) load("/api/refresh", "POST");
}

const $ = (id) => document.getElementById(id);

/* Therapist and site names come from Moveshelf, so never trust them as markup. */
function escapeHtml(text) {
  const div = document.createElement("div");
  div.textContent = String(text == null ? "" : text);
  return div.innerHTML;
}

/* ---------- server calls ---------- */

async function call(path, method = "GET", body = null) {
  const opts = { method, headers: { "Accept": "application/json" } };
  if (body) {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body);
  }
  const res = await fetch(path, opts);
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  return res.json();
}

async function load(path = "/api/state", method = "GET", body = null) {
  setBusy(true);
  try {
    STATE = await call(path, method, body);
    render();
  } catch (err) {
    banner(`Could not reach the Report Tracker service: ${err.message}. ` +
           `Close this tab and run the app again.`, true);
  } finally {
    setBusy(false);
  }
}

function setBusy(busy) {
  $("refresh").disabled = busy;
  $("refresh").textContent = busy ? "↻ Loading…" : "↻ Refresh";
}

/* ---------- rendering ---------- */

function banner(message, isError) {
  const div = document.createElement("div");
  div.className = "banner" + (isError ? " error" : "");
  div.textContent = message;
  $("banners").appendChild(div);
}

function render() {
  if (!STATE) return;
  $("banners").innerHTML = "";
  $("loading").classList.add("hidden");
  $("setup-location").classList.add("hidden");
  $("setup-key").classList.add("hidden");
  $("setup-site").classList.add("hidden");
  $("main").classList.add("hidden");
  $("site").classList.add("hidden");

  // The opening fetch runs in the background so the browser can appear at once.
  // Until it has finished there is genuinely nothing to show, and rendering an
  // empty table would read as "you have no reports" rather than "still loading".
  clearTimeout(pollTimer);
  if (STATE.loading && !STATE.ever_loaded) {
    $("loading").classList.remove("hidden");
    setBusy(true);
    pollTimer = setTimeout(() => load(), 1000);
    return;
  }

  if (STATE.status === "bad_location") {
    $("location-error").textContent = STATE.location_error || "";
    $("setup-location").classList.remove("hidden");
    return;
  }
  if (STATE.status === "needs_key") {
    $("folder").textContent = STATE.folder;
    $("key-error").textContent = STATE.key_error || "";
    $("setup-key").classList.remove("hidden");
    return;
  }
  if (STATE.status === "needs_project") {
    const picker = $("site-picker");
    picker.innerHTML = "";
    (STATE.projects || []).forEach((p) => {
      const opt = document.createElement("option");
      opt.value = p.id; opt.textContent = p.name;
      picker.appendChild(opt);
    });
    // Almost everyone using this works at a Gait site, and picking the wrong one
    // gives an empty list with no obvious cause. Preselect the first Gait
    // project so the common case needs no thought.
    const gait = (STATE.projects || []).find(
      (p) => /gait/i.test(p.name) && !/sandbox/i.test(p.name)
    );
    if (gait) picker.value = gait.id;
    if (STATE.error) banner(STATE.error, true);
    $("setup-site").classList.remove("hidden");
    return;
  }

  if (STATE.error) banner(STATE.error, true);

  renderSitePicker();
  renderControls();
  renderTiles();
  renderTable();
  $("main").classList.remove("hidden");
  // Only now does the table have a real width to measure. Call it directly
  // rather than waiting for an animation frame: reading scrollWidth forces
  // layout synchronously, so this is deterministic, whereas a rAF callback can
  // be delayed or throttled and leave the strip unsized. The second call covers
  // fonts or scrollbars settling a frame later.
  syncScrollbars();
  requestAnimationFrame(syncScrollbars);

  // Show the date too whenever the data is not from today, so "8:48 AM" can
  // never be mistaken for this morning.
  const stale = STATE.today && STATE.today !== localToday();
  let when = "never";
  if (STATE.last_refresh) {
    const at = new Date(STATE.last_refresh * 1000);
    const clock = at.toLocaleTimeString([], {hour: "numeric", minute: "2-digit"});
    when = stale ? `${at.toLocaleDateString()} ${clock}` : clock;
  }
  $("refreshed").textContent = `Last refreshed ${when}`;
  // Which build this is. Nothing updates itself, so someone can sit on an old
  // copy indefinitely; knowing which one has to be a glance, not a hunt.
  $("version").textContent = `v${STATE.version || "?"}`;
  if (stale) {
    banner(`These counts were worked out on ${STATE.today}, not today, so every ` +
           `countdown is out of date. Refreshing now\u2026`, true);
    checkDateRollover();
  }
}

function renderSitePicker() {
  const site = $("site");
  if (!STATE.projects || STATE.projects.length < 2) {
    if (STATE.project_name) {
      site.innerHTML = "";
      const opt = document.createElement("option");
      opt.textContent = STATE.project_name;
      site.appendChild(opt);
      site.classList.remove("hidden");
      site.disabled = true;
    }
    return;
  }
  site.disabled = false;
  site.innerHTML = "";
  STATE.projects.forEach((p) => {
    const opt = document.createElement("option");
    opt.value = p.id; opt.textContent = p.name;
    opt.selected = p.id === STATE.project_id;
    site.appendChild(opt);
  });
  site.classList.remove("hidden");
}

function renderControls() {
  $("mine").checked = !!STATE.mine_only;
  $("backlog").checked = !!STATE.backlog_enabled;
  $("backlog-days").textContent = String(STATE.backlog_days);
  $("lookback").value = String(STATE.lookback_days);

  const me = $("me");
  const known = STATE.therapists || [];
  const saved = STATE.my_therapist;
  const unassigned = (STATE.rows || []).filter((r) => !r.therapist).length;

  const add = (value, label) => {
    const opt = document.createElement("option");
    opt.value = value;
    opt.textContent = label;
    opt.selected = value === saved;
    me.appendChild(opt);
  };

  me.innerHTML = "";
  add("", "(who are you?)");
  known.forEach((name) => add(name, name));

  // A saved name can be absent from the window: back from leave, part time, or
  // newly arrived. Keep it selectable and say why it shows nothing, rather than
  // letting the box read "(who are you?)" while the table is filtered to them.
  if (saved && saved !== UNASSIGNED && !known.includes(saved)) {
    add(saved, `${saved} (no sessions in this period)`);
  }

  // Sessions with nobody recorded belong to no one and would otherwise be
  // invisible to everybody.
  if (unassigned) {
    add(UNASSIGNED,
        `${UNASSIGNED} - ${unassigned} session${unassigned === 1 ? "" : "s"}`);
  }

  add(OTHER_NAME, "Other name...");

  const note = $("me-note");
  let message = "";
  if (saved && saved !== UNASSIGNED && !known.includes(saved)) {
    message = `No sessions for ${saved} in this period. Try a longer date range.`;
  } else if (!saved && unassigned) {
    message = `${unassigned} session${unassigned === 1 ? " has" : "s have"} ` +
              `no therapist recorded.`;
  }
  note.textContent = message;

  const showing = $("mine").checked;
  me.classList.toggle("hidden", !showing);
  note.classList.toggle("hidden", !showing || !message);
  if (!showing) $("me-other").classList.add("hidden");
  renderReferralControls();
}

/* The referral filter and the "needs a report" panel share one source of truth:
   the distinct referral types seen in the fetched rows. */
function renderReferralControls() {
  const types = STATE.referral_types || [];
  const excluded = new Set(
    (STATE.no_report_referral_types || []).map((s) => s.toLowerCase())
  );

  const filter = $("referral");
  const previous = filter.value;
  filter.innerHTML = '<option value="">All referral types</option>';
  types.forEach((name) => {
    const opt = document.createElement("option");
    opt.value = name; opt.textContent = name;
    filter.appendChild(opt);
  });
  filter.value = types.includes(previous) ? previous : "";

  const counts = {};
  (STATE.rows || []).forEach((r) => {
    if (r.referral_type) counts[r.referral_type] = (counts[r.referral_type] || 0) + 1;
  });

  const box = $("reporttypes");
  box.innerHTML = "";
  types.forEach((name) => {
    const label = document.createElement("label");
    const cb = document.createElement("input");
    cb.type = "checkbox";
    cb.checked = !excluded.has(name.toLowerCase());
    cb.onchange = () => {
      const next = [];
      box.querySelectorAll("input[type=checkbox]").forEach((other) => {
        if (!other.checked) next.push(other.dataset.name);
      });
      load("/api/settings", "POST", { no_report_referral_types: next });
    };
    cb.dataset.name = name;
    label.appendChild(cb);
    label.appendChild(document.createTextNode(name + " "));
    const n = document.createElement("span");
    n.className = "n"; n.textContent = `(${counts[name] || 0})`;
    label.appendChild(n);
    box.appendChild(label);
  });
}

/* Rows in scope for the tile counts: everything except the tile filter itself.

   Reported 2026-07-28: with "My sessions only" ticked, the tiles still counted
   the whole department, so a therapist saw someone else's overdue number sitting
   above their own filtered list. Counts must describe the list underneath them.

   The tile filter is deliberately excluded, otherwise clicking "Overdue" would
   zero every other tile and there would be no way back. */
/* Does this row belong to the selected person? */
function matchesTherapist(row, me) {
  if (!me) return true;
  if (me === UNASSIGNED) return !row.therapist;
  return row.therapist === me;
}

function scopedRows() {
  const query = $("q").value.trim().toLowerCase();
  const mine = $("mine").checked;
  const me = STATE.my_therapist;
  const referral = $("referral").value;

  return (STATE.rows || []).filter((r) => {
    if (mine && !matchesTherapist(r, me)) return false;
    if (referral && r.referral_type !== referral) return false;
    if (query) {
      const hay = `${r.subject_id} ${r.mrn} ${r.therapist}`.toLowerCase();
      if (!hay.includes(query)) return false;
    }
    return true;
  });
}

function visibleRows() {
  const query = $("q").value.trim().toLowerCase();
  const mine = $("mine").checked;
  const me = STATE.my_therapist;
  const showDone = $("showdone").checked;
  const referral = $("referral").value;

  let rows = (STATE.rows || []).filter((r) => {
    if (r.status === "done" && !showDone) return false;
    if (r.status === "backlog" && filterStatus !== "backlog") return false;
    if (r.status === "no_report" && filterStatus !== "no_report") return false;
    if (referral && r.referral_type !== referral) return false;
    if (filterStatus && r.status !== filterStatus) return false;
    if (mine && !matchesTherapist(r, me)) return false;
    if (query) {
      const hay = `${r.subject_id} ${r.mrn} ${r.therapist}`.toLowerCase();
      if (!hay.includes(query)) return false;
    }
    return true;
  });

  const dir = sortDir;
  rows = rows.slice().sort((a, b) => {
    if (sortKey === "sort_rank") {
      return (
        (a.sort_rank - b.sort_rank) ||
        ((a.days_left ?? 9999) - (b.days_left ?? 9999)) ||
        String(a.session_date || "").localeCompare(String(b.session_date || ""))
      ) * dir;
    }
    const x = a[sortKey], y = b[sortKey];
    if (x === null || x === undefined) return 1;
    if (y === null || y === undefined) return -1;
    if (typeof x === "number" && typeof y === "number") return (x - y) * dir;
    return String(x).localeCompare(String(y)) * dir;
  });
  return rows;
}

/* Show what is currently narrowing the table, with a way out of each one.

   "Clear filters" rather than "Show all": it removes the filters the user
   applied, and does not claim to also reveal completed rows, which are governed
   by their own checkbox. */
/* Ask once who is looking, so "My sessions only" is usable on day one instead of
   waiting to be discovered in a dropdown. */
function renderIdentityPrompt() {
  const bar = $("identity");
  const names = STATE.therapists || [];
  const needed = !STATE.my_therapist && names.length > 1 && !identitySkipped;
  bar.classList.toggle("hidden", !needed);
  if (!needed) return;

  const pick = $("identity-pick");
  pick.innerHTML = "";
  names.forEach((name) => {
    const opt = document.createElement("option");
    opt.value = name;
    opt.textContent = name;
    pick.appendChild(opt);
  });
}

function renderActiveFilters() {
  const bar = $("activefilters");
  const active = [];

  if (filterStatus) {
    active.push({
      label: (STATUS_META[filterStatus] || {}).tile || filterStatus,
      clear: () => { filterStatus = null; },
    });
  }
  const query = $("q").value.trim();
  if (query) {
    active.push({ label: `Search: "${query}"`, clear: () => { $("q").value = ""; } });
  }
  const referral = $("referral").value;
  if (referral) {
    active.push({ label: referral, clear: () => { $("referral").value = ""; } });
  }
  if ($("mine").checked) {
    active.push({
      label: STATE.my_therapist
        ? (STATE.my_therapist === UNASSIGNED
            ? "No therapist recorded" : `Only ${STATE.my_therapist}`)
        : "My sessions only",
      clear: () => {
        $("mine").checked = false;
        $("me").classList.add("hidden");
        $("me-other").classList.add("hidden");
        $("me-note").classList.add("hidden");
        if (STATE) STATE.mine_only = false;
        call("/api/settings", "POST", { mine_only: false }).catch(() => {});
      },
    });
  }

  bar.classList.toggle("hidden", active.length === 0);
  if (!active.length) return;

  bar.innerHTML = "";
  const lead = document.createElement("span");
  lead.textContent = "Showing a filtered list:";
  bar.appendChild(lead);

  active.forEach((item) => {
    const chip = document.createElement("span");
    chip.className = "chip";
    chip.appendChild(document.createTextNode(item.label));
    const x = document.createElement("button");
    x.type = "button";
    x.textContent = "\u00d7";
    x.title = `Remove this filter`;
    x.setAttribute("aria-label", `Remove filter ${item.label}`);
    x.onclick = () => { item.clear(); renderTiles(); renderTable(); };
    chip.appendChild(x);
    bar.appendChild(chip);
  });

  const clear = document.createElement("button");
  clear.className = "clearall";
  clear.type = "button";
  clear.textContent = "Clear filters";
  clear.title = "Show the full list again (Esc)";
  clear.onclick = () => {
    active.forEach((item) => item.clear());
    renderTiles();
    renderTable();
  };
  bar.appendChild(clear);
}

function renderTiles() {
  const tiles = $("tiles");
  const rows = scopedRows();

  // Count from the rows actually in scope, not from everything fetched.
  const counts = {};
  rows.forEach((r) => { counts[r.status] = (counts[r.status] || 0) + 1; });

  // Say plainly whose numbers these are. A count with no owner is how the
  // original confusion started.
  const mine = $("mine").checked && STATE.my_therapist;
  const narrowed = rows.length !== (STATE.rows || []).length;
  $("scope").innerHTML = mine
    ? (STATE.my_therapist === UNASSIGNED
        ? "Counts below are for sessions with <b>no therapist recorded</b>."
        : `Counts below are for <b>${escapeHtml(STATE.my_therapist)}</b> only.`)
    : (narrowed
        ? "Counts below are for the sessions matching your filters."
        : `Counts below are for <b>every therapist</b> at ` +
          `${escapeHtml(STATE.project_name || "this site")}.`);

  tiles.innerHTML = "";
  TILE_ORDER.forEach((status) => {
    const meta = STATUS_META[status];
    const btn = document.createElement("button");
    btn.className = `tile t-${status}`;
    btn.dataset.status = status;
    btn.title = `${meta.desc}\n\nClick to show only these. Click again to show all.`;
    btn.setAttribute("aria-pressed", String(filterStatus === status));
    btn.innerHTML =
      `<div class="n">${counts[status] || 0}</div>` +
      `<div class="l">${meta.icon} ${meta.tile}</div>`;
    btn.onclick = () => {
      filterStatus = filterStatus === status ? null : status;
      renderTiles(); renderTable();
    };
    tiles.appendChild(btn);
  });
}

/* Phrase the countdown the way a person would say it. */
function duePhrase(row) {
  const meta = STATUS_META[row.status];
  if (row.status === "done") {
    return `${meta.icon} Done${row.pt_evaluation ? " " + row.pt_evaluation : ""}`;
  }
  if (row.status === "not_started") return `${meta.icon} Awaiting processing`;
  if (row.status === "no_report") return `${meta.icon} No report needed`;
  if (row.status === "backlog") {
    const n = Math.abs(row.days_left);
    return `${meta.icon} Backlog, ${n} days overdue`;
  }
  if (row.status === "overdue") {
    const n = Math.abs(row.days_left);
    return `${meta.icon} ${n} day${n === 1 ? "" : "s"} overdue`;
  }
  if (row.status === "due_today") return `${meta.icon} Due today`;
  let suffix = "";
  if (row.due_date) {
    const d = new Date(row.due_date + "T12:00:00");
    if (!isNaN(d)) suffix = `, due ${WEEKDAYS[d.getDay()]}`;
  }
  return `${meta.icon} ${row.days_left} left${suffix}`;
}

function td(text, cls) {
  const cell = document.createElement("td");
  if (cls) cell.className = cls;
  if (text === null || text === undefined || text === "") {
    const dash = document.createElement("span");
    dash.className = "dash"; dash.textContent = "—";
    cell.appendChild(dash);
  } else {
    cell.textContent = String(text);
  }
  return cell;
}

function renderTable() {
  const rows = visibleRows();
  const body = $("tbody");
  body.innerHTML = "";

  rows.forEach((row) => {
    const tr = document.createElement("tr");
    tr.className = `status s-${row.status}`;
    tr.tabIndex = 0;
    if (row.url) {
      tr.title = "Open this session in Moveshelf. Click the name for the patient.";
      const open = () => window.open(row.url, "_blank", "noopener");
      tr.onclick = (e) => { if (!e.target.closest("button")) open(); };
      tr.onkeydown = (e) => { if (e.key === "Enter") open(); };
    }

    const due = document.createElement("td");
    const pill = document.createElement("span");
    pill.className = `pill p-${row.status}`;
    pill.textContent = duePhrase(row);
    due.appendChild(pill);
    tr.appendChild(due);

    // The name opens the patient; anywhere else in the row opens this session.
    const subject = document.createElement("td");
    subject.className = "namecell";
    if (row.subject_url && row.subject_id) {
      const link = document.createElement("a");
      link.href = row.subject_url;
      link.target = "_blank";
      link.rel = "noopener";
      link.className = "subject";
      link.textContent = row.subject_id;
      link.title = `Open ${row.subject_id} in Moveshelf (all their sessions)`;
      link.onclick = (e) => e.stopPropagation();  // not the session link
      subject.appendChild(link);
    } else {
      const strong = document.createElement("b");
      strong.textContent = row.subject_id || "—";
      subject.appendChild(strong);
    }
    tr.appendChild(subject);

    const mrn = document.createElement("td");
    mrn.className = "mono";
    mrn.textContent = row.mrn || "—";
    if (row.mrn && navigator.clipboard) {
      const copy = document.createElement("button");
      copy.className = "copy"; copy.title = "Copy MRN"; copy.textContent = "⧉";
      copy.onclick = (e) => {
        e.stopPropagation();
        navigator.clipboard.writeText(row.mrn).then(() => {
          copy.textContent = "✓";
          setTimeout(() => { copy.textContent = "⧉"; }, 1200);
        }, () => {});
      };
      mrn.appendChild(document.createTextNode(" "));
      mrn.appendChild(copy);
    }
    tr.appendChild(mrn);

    const when = document.createElement("td");
    when.className = "mono";
    when.textContent = row.session_date || "—";
    if (row.days_since_session === 0) when.title = "Seen today";
    tr.appendChild(when);

    tr.appendChild(td(row.days_since_session, "num"));
    tr.appendChild(td(row.therapist));
    tr.appendChild(td(row.referring_physician, "ellipsis"));
    const referral = td(row.referral_type, "ellipsis");
    if (row.referral_type) referral.title = row.referral_type;
    tr.appendChild(referral);
    tr.appendChild(td(row.processing_completed, "mono"));
    tr.appendChild(td(row.days_since_processing, "num"));
    tr.appendChild(td(row.pt_evaluation, "mono"));
    tr.appendChild(td(row.interpretation, "mono"));

    // Foot model last. Marked with a tick and a word in the tooltip, not colour
    // alone, so it survives grayscale and colour vision deficiency.
    const foot = document.createElement("td");
    foot.className = "num";
    if (row.foot_model) {
      const mark = document.createElement("span");
      mark.className = "foot";
      mark.textContent = "\u2713";
      mark.title = "Foot model collected: processing waits on x-ray measurements. "
                 + "The deadline is unchanged.";
      foot.appendChild(mark);
      tr.classList.add("hasfoot");
    } else {
      const dash = document.createElement("span");
      dash.className = "dash";
      dash.textContent = "\u2014";
      foot.appendChild(dash);
    }
    tr.appendChild(foot);
    body.appendChild(tr);
  });

  const total = (STATE.rows || []).length;
  $("count").textContent = `${rows.length} of ${total} session${total === 1 ? "" : "s"} shown`;

  // Say which holidays are in force. An invisible rule that shifts deadlines is
  // exactly the kind of thing a user should be able to check rather than trust.
  const hol = STATE.holiday_names || [];
  const note = $("holidaynote");
  if (hol.length) {
    const names = hol.map((h) => `${h.name} ${h.date.slice(5)}`).join(" · ");
    note.textContent =
      `Deadlines skip weekends and ${hol.length} holidays this year: ${names}.` +
      (STATE.holidays_file ? " (adjusted by your holidays.txt)" : "");
  } else {
    note.textContent = "Deadlines skip weekends only.";
  }

  syncScrollbars();
  renderIdentityPrompt();
  renderActiveFilters();

  const empty = $("empty");
  empty.classList.toggle("hidden", rows.length > 0);
  if (rows.length === 0) {
    const summary = STATE.summary || {};
    const pressing = (summary.overdue || 0) + (summary.due_today || 0) + (summary.due_soon || 0);
    if (total === 0) {
      $("empty-big").textContent = "No sessions in this period";
      $("empty-sub").textContent = "Try a longer lookback from the dropdown above.";
    } else if (!filterStatus && pressing === 0) {
      $("empty-big").textContent = "🎉 You’re all caught up";
      $("empty-sub").textContent = "No reports are due right now. Nice work.";
    } else {
      $("empty-big").textContent = "Nothing matches these filters";
      $("empty-sub").textContent =
        "Use Clear filters above, press Esc, or click the highlighted tile again.";
    }
  }
}

/* ---------- proxy scrollbar above the table ---------- */

/* Keep the strip above the table the same scrollable width as the table, and
   keep the two scroll positions in step. The guard stops each one's scroll
   handler from re-triggering the other. */
let syncingScroll = false;

function syncScrollbars() {
  const bar = $("hbar"), inner = $("hbar-inner"), wrap = $("tablewrap");
  if (!bar || !wrap) return;
  // A display:none container reports zero width for everything. Deciding
  // "does this need a scrollbar?" from that answers no, forever. This is
  // exactly what hid the strip on every render the first time round, so the
  // guard stays even though the call sites are now ordered correctly.
  if (!wrap.clientWidth) return;
  const width = wrap.scrollWidth;
  inner.style.width = width + "px";
  bar.classList.toggle("hidden", width <= wrap.clientWidth + 1);
  bar.scrollLeft = wrap.scrollLeft;
}

function wireScrollSync() {
  const bar = $("hbar"), wrap = $("tablewrap");
  if (!bar || !wrap) return;
  bar.addEventListener("scroll", () => {
    if (syncingScroll) return;
    syncingScroll = true;
    wrap.scrollLeft = bar.scrollLeft;
    syncingScroll = false;
  }, { passive: true });
  wrap.addEventListener("scroll", () => {
    if (syncingScroll) return;
    syncingScroll = true;
    bar.scrollLeft = wrap.scrollLeft;
    syncingScroll = false;
  }, { passive: true });
  window.addEventListener("resize", syncScrollbars, { passive: true });
}

/* ---------- export ---------- */

/* Columns written to the file, in the order they appear on screen. Kept
   explicit rather than derived from the row object, so an internal field added
   later is never exported by accident. */
const EXPORT_COLUMNS = [
  ["status", "Status"],
  ["days_left", "Business days left"],
  ["due_date", "Due date"],
  ["subject_id", "Subject ID"],
  ["mrn", "MRN"],
  ["session_date", "Session date"],
  ["days_since_session", "Days since seen"],
  ["therapist", "Therapist"],
  ["referring_physician", "Referring MD"],
  ["referral_type", "Referral type"],
  ["processing_completed", "Processed on"],
  ["days_since_processing", "Days since processed"],
  ["pt_evaluation", "PT evaluation in EMR"],
  ["interpretation", "Interpretation"],
  ["foot_model", "Foot model"],
  ["url", "Session link"],
];

/* Excel treats a leading =, +, - or @ as the start of a formula, so a value
   beginning with one can execute when the file is opened. Prefix with an
   apostrophe, which Excel strips on display. */
function csvCell(value) {
  let text = value === null || value === undefined ? "" : String(value);
  if (/^[=+\-@\t\r]/.test(text)) text = "'" + text;
  return '"' + text.replace(/"/g, '""') + '"';
}

function buildCsv(rows) {
  const lines = [EXPORT_COLUMNS.map(([, label]) => csvCell(label)).join(",")];
  rows.forEach((row) => {
    lines.push(EXPORT_COLUMNS.map(([key]) => {
      if (key === "status") return csvCell((STATUS_META[row.status] || {}).label);
      if (key === "foot_model") return csvCell(row.foot_model ? "Yes" : "");
      return csvCell(row[key]);
    }).join(","));
  });
  return lines.join("\r\n") + "\r\n";
}

function exportRows() {
  const rows = visibleRows();
  if (!rows.length) {
    banner("Nothing to export: no sessions are currently shown.", true);
    return;
  }
  // A BOM, or Excel reads accented names as mojibake.
  const blob = new Blob(["\ufeff" + buildCsv(rows)],
                        { type: "text/csv;charset=utf-8;" });
  const site = (STATE.project_name || "moveshelf").replace(/[^A-Za-z0-9]+/g, "-");
  const name = `report-tracker-${site}-${STATE.today}.csv`;

  const link = document.createElement("a");
  link.href = URL.createObjectURL(blob);
  link.download = name;
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
  setTimeout(() => URL.revokeObjectURL(link.href), 10000);

  // Recorded server-side: the audit trail has to cover PHI leaving the app.
  // Row count only, never the rows themselves.
  call("/api/exported", "POST", { n_rows: rows.length, filename: name })
    .catch(() => {});
}

/* ---------- privacy screen ---------- */

function resetIdle() {
  clearTimeout(idleTimer);
  idleTimer = setTimeout(() => $("screen").classList.remove("hidden"), IDLE_BLANK_MS);
}

/* ---------- wiring ---------- */

function wire() {
  $("refresh").onclick = () => load("/api/refresh", "POST");
  $("export").onclick = exportRows;
  $("quit").onclick = async () => {
    try { await call("/api/quit", "POST"); } catch (e) { /* server is going away */ }
    document.body.innerHTML =
      '<div class="wrap"><div class="setup"><h2>Report Tracker has closed</h2>' +
      "<p>You can close this tab. Run the app again whenever you need it.</p></div></div>";
  };

  $("site").onchange = (e) => {
    const opt = e.target.selectedOptions[0];
    load("/api/settings", "POST",
         { project_id: e.target.value, project_name: opt ? opt.textContent : "" });
  };
  $("site-save").onclick = () => {
    const picker = $("site-picker");
    const opt = picker.selectedOptions[0];
    load("/api/settings", "POST",
         { project_id: picker.value, project_name: opt ? opt.textContent : "" });
  };
  $("lookback").onchange = (e) =>
    load("/api/settings", "POST", { lookback_days: Number(e.target.value) });

  $("mine").onchange = (e) => {
    call("/api/settings", "POST", { mine_only: e.target.checked }).catch(() => {});
    if (STATE) STATE.mine_only = e.target.checked;
    renderControls();
    renderTiles();
    renderTable();
  };
  function applyTherapist(name) {
    if (STATE) STATE.my_therapist = name;
    call("/api/settings", "POST", { my_therapist: name }).catch(() => {});
    renderControls();
    renderTiles();
    renderTable();
  }

  $("me").onchange = (e) => {
    if (e.target.value === OTHER_NAME) {
      // Not a name: reveal the box instead of storing the sentinel.
      const box = $("me-other");
      box.value = "";
      box.classList.remove("hidden");
      box.focus();
      return;
    }
    $("me-other").classList.add("hidden");
    applyTherapist(e.target.value);
  };

  $("me-other").onchange = (e) => {
    const name = e.target.value.trim();
    e.target.classList.add("hidden");
    if (name) applyTherapist(name);
    else renderControls();
  };
  $("me-other").onkeydown = (e) => {
    if (e.key === "Escape") { e.target.classList.add("hidden"); renderControls(); }
  };
  $("backlog").onchange = (e) =>
    load("/api/settings", "POST", { backlog_enabled: e.target.checked });
  $("referral").onchange = () => { renderTiles(); renderTable(); };
  $("q").oninput = () => { renderTiles(); renderTable(); };
  $("showdone").onchange = renderTable;

  document.querySelectorAll("#tbl th").forEach((th) => {
    th.onclick = () => {
      const key = th.dataset.k;
      sortDir = key === sortKey ? -sortDir : 1;
      sortKey = key;
      document.querySelectorAll("#tbl th").forEach((o) => {
        o.textContent = o.textContent.replace(/ [▲▼]$/, "");
      });
      th.textContent += sortDir > 0 ? " ▲" : " ▼";
      renderTable();
    };
  });

  document.addEventListener("keydown", (e) => {
    if (e.key === "/" && document.activeElement.id !== "q") {
      e.preventDefault(); $("q").focus();
    } else if (e.key === "r" && !/^(INPUT|SELECT|TEXTAREA)$/.test(document.activeElement.tagName)) {
      load("/api/refresh", "POST");
    } else if (e.key === "Escape") {
      filterStatus = null; $("q").value = ""; $("referral").value = "";
      if ($("mine").checked) {
        $("mine").checked = false;
        $("me").classList.add("hidden");
        $("me-other").classList.add("hidden");
        $("me-note").classList.add("hidden");
        if (STATE) STATE.mine_only = false;
        call("/api/settings", "POST", { mine_only: false }).catch(() => {});
      }
      renderTiles(); renderTable();
    }
  });

  $("identity-set").onclick = () => {
    const name = $("identity-pick").value;
    if (STATE) { STATE.my_therapist = name; STATE.mine_only = true; }
    $("mine").checked = true;
    $("me").classList.remove("hidden");
    load("/api/settings", "POST", { my_therapist: name, mine_only: true });
  };
  $("identity-skip").onclick = () => {
    identitySkipped = true;
    $("identity").classList.add("hidden");
  };

  $("reveal").onclick = () => { $("screen").classList.add("hidden"); resetIdle(); };
  ["mousemove", "keydown", "click", "scroll"].forEach((evt) =>
    document.addEventListener(evt, resetIdle, { passive: true })
  );

  // Keep the server alive while this tab is open. It shuts itself down when
  // these stop arriving, so a forgotten process does not sit holding data.
  setInterval(() => {
    call("/api/ping").catch(() => {});
    checkDateRollover();
  }, 60 * 1000);
}

wire();
wireScrollSync();
resetIdle();
load();
