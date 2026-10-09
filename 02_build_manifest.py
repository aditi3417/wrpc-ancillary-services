"""Step 02: build a manifest of weekly statements from the step 01 file list.

Classifies each file from its title, expands it to one row per week covered,
records version information, and makes a provisional base-plus-overlay choice
per week x section (TRAS, SRAS, SCUC): the newest file covering a whole section
is the base; narrower revisions (TRAS emergency, station-specific) are overlays
on top of it (A07). Reads titles and filenames only; no PDFs are downloaded or
opened. Scope is confirmed from PDF contents in step 04.
"""

# %% Imports
import re
from datetime import date, datetime, timedelta

import pandas as pd

import config

SECTIONS = ["TRAS", "SRAS", "SCUC"]
warnings = []


def warn(msg):
    warnings.append(msg)


def join(items):
    return ";".join(str(i) for i in items)


# %% Load the step 01 file list and collapse duplicates across tabs
file_list = pd.read_csv(config.FILE_LIST_CSV, keep_default_na=False)
file_list = file_list.rename(columns={"url": "url_clean"})
n_listed = len(file_list)

tabs_listed = file_list.groupby("url_clean")["year_tab"].agg(lambda s: join(sorted(set(s))))
files = file_list.drop_duplicates("url_clean", keep="first").copy()
files["tabs_listed"] = files["url_clean"].map(tabs_listed)
files["title"] = files["title"].str.replace(r"\s+", " ", regex=True).str.strip()
n_cross_tab_dups = n_listed - len(files)

# %% Classification rules (title only)
SCED_RE = re.compile(r"^(Revised )?SCED Statement for the month", re.I)
SECTION_WORD_RE = re.compile(r"\b(TRAS|SRAS|SCUC)\b")

# Base scopes cover a whole section; overlay scopes are narrower revisions.
BASE_SCOPES = {
    "FULL": ["TRAS", "SRAS", "SCUC"],
    "TRAS_SCUC": ["TRAS", "SCUC"],
    "SRAS_ONLY": ["SRAS"],
}
OVERLAY_SCOPES = {"TRAS_EMERGENCY_IPP", "STATION_SPECIFIC"}
BASE_RULES = [
    ("FULL", re.compile(r"^(Revised )?TRAS SRAS & SCUC (for|from) \d", re.I)),
    ("SRAS_ONLY", re.compile(r"^(Revised )?SRAS (for|from) \d", re.I)),
    ("TRAS_SCUC", re.compile(r"^(Revised )?TRAS & SCUC (for|from) \d", re.I)),
]
IPP_RE = re.compile(r"\bIPPs \(TRAS Emergency\)", re.I)
# "<sections/tables> of <stations> for [the weeks] <date>"
STATION_RE = re.compile(r"^(?:Revised )?(?P<what>.+?) of (?P<stations>.+?)\s*for (?:the weeks )?\d", re.I)
TOKEN = r"(?:TRAS(?: Shortfall| Emergency)?|SRAS|SCUC)"
WHAT_RE = re.compile(rf"^{TOKEN}(?:(?:\s*&\s*|\s+and\s+|\s*,\s*|\s+){TOKEN})*$", re.I)
TABLE_RE = re.compile(r"\bTRAS (?:Shortfall|Emergency)\b", re.I)
STATION_SPLIT_RE = re.compile(r"\s*,\s*|\s+and\s+")

# dd.mm.yy or dd.mm.yyyy, a range "D to D"
D = r"(\d{1,2})\.(\d{1,2})\.(\d{4}|\d{2})(?!\d)"
RANGE_RE = re.compile(D + r"\s*to\s*" + D, re.I)
TOTAL_WEEKS_RE = re.compile(r"Total (\d+) weeks", re.I)
TITLE_DATE_RE = re.compile(
    r"\b(Updated on|Revised on|revised)\s*(\d{1,2})[./](\d{1,2})[./](\d{4}|\d{2})(?!\d)", re.I
)
REV_TITLE_RE = re.compile(r"\((rev-\d+|Revision-\d+)\)", re.I)
REV_FILE_LETTER_RE = re.compile(r"\.\d+([a-z])(?=$|[_\-\s.])", re.I)
REV_FILE_REV_RE = re.compile(r"_rev\d+", re.I)


def make_date(d, m, y):
    """Return a date, or None if invalid. Two-digit years are 20yy (A02)."""
    y = int(y)
    y = 2000 + y if y < 100 else y
    try:
        return date(y, int(m), int(d))
    except ValueError:
        return None


def classify(title):
    """Return dict with doc_type, scope, sections, stations, tables, partial_note."""
    out = {"doc_type": "other", "scope": "UNKNOWN", "sections": [], "stations": "", "tables": "", "partial_note": ""}
    if SCED_RE.search(title):
        return out | {"doc_type": "sced_monthly", "scope": "NOT_APPLICABLE"}
    if SECTION_WORD_RE.search(title) and RANGE_RE.search(title):
        out["doc_type"] = "weekly_statement"
    # Order matters: the IPP emergency title also starts with "Revised TRAS SRAS & SCUC".
    if IPP_RE.search(title):
        return out | {"scope": "TRAS_EMERGENCY_IPP", "sections": ["TRAS"], "stations": "IPPs",
                      "tables": "TRAS Emergency", "partial_note": "TRAS emergency only"}
    for scope, rx in BASE_RULES:
        if rx.search(title):
            return out | {"scope": scope, "sections": BASE_SCOPES[scope]}
    m = STATION_RE.search(title)
    if m and WHAT_RE.match(m["what"]) and not SECTION_WORD_RE.search(m["stations"]):
        stations = [s.strip() for s in STATION_SPLIT_RE.split(m["stations"]) if s.strip()]
        return out | {
            "scope": "STATION_SPECIFIC",
            "sections": [s for s in SECTIONS if re.search(rf"\b{s}\b", m["what"])],
            "stations": join(stations),
            "tables": join(t.title().replace("Tras", "TRAS") for t in TABLE_RE.findall(m["what"])),
        }
    return out


def parse_periods(title):
    """Return (list of (start, end) date pairs, list of problem strings)."""
    periods, problems = [], []
    for m in RANGE_RE.finditer(title):
        start, end = make_date(*m.group(1, 2, 3)), make_date(*m.group(4, 5, 6))
        if start is None or end is None:
            problems.append(f"invalid date in '{m.group(0)}'")
            continue
        if start.weekday() != 0:
            problems.append(f"start {start} is a {start:%A}, not Monday")
        if end.weekday() != 6:
            problems.append(f"end {end} is a {end:%A}, not Sunday")
        span = (end - start).days + 1
        if span <= 0 or span % 7:
            problems.append(f"span {start}..{end} is {span} days, not whole weeks")
        periods.append((start, end))
    if not periods and not problems:
        problems.append("no date range found")
    return periods, problems


def parse_upload_ts(filename):
    m = re.match(r"(\d{14})", filename)
    if not m:
        return None, "upload_ts_missing"
    try:
        ts = datetime.strptime(m.group(1), "%d%m%Y%H%M%S")
    except ValueError:
        return None, "upload_ts_invalid"
    if not (datetime(2020, 1, 1) <= ts <= datetime.now()):
        return None, "upload_ts_invalid"
    return ts, ""


def parse_title_date(title):
    found = [(m.group(1), make_date(*m.group(2, 3, 4))) for m in TITLE_DATE_RE.finditer(title)]
    dates = {d for _, d in found if d is not None}
    labels = join(dict.fromkeys(label for label, _ in found))
    if len(dates) > 1:
        return None, labels, "title_date_ambiguous"
    if not dates:
        return None, labels, "title_date_missing"
    return dates.pop(), labels, ""


def rev_labels(title, filename):
    title_labels = (["Revised"] if title.lower().startswith("revised") else []) + REV_TITLE_RE.findall(title)
    stem = re.sub(r"^\d+", "", filename.rsplit(".pdf", 1)[0]).strip()
    file_labels = REV_FILE_LETTER_RE.findall(stem) + REV_FILE_REV_RE.findall(stem)
    return join(title_labels), join(file_labels)


# %% Build file-level attributes
file_rows = []
for f in files.itertuples(index=False):
    c = classify(f.title)
    flags = []
    upload_ts, ts_flag = parse_upload_ts(f.filename)
    title_date, title_date_label, td_flag = parse_title_date(f.title)
    flags += [x for x in (ts_flag, td_flag) if x]
    mismatch_days = (upload_ts.date() - title_date).days if upload_ts and title_date else None
    date_mismatch = mismatch_days is not None and abs(mismatch_days) > config.DATE_MISMATCH_TOLERANCE_DAYS
    if date_mismatch:
        flags.append("date_mismatch")
    rev_t, rev_f = rev_labels(f.title, f.filename)

    periods, date_problems = ([], [])
    if c["doc_type"] == "weekly_statement":
        periods, date_problems = parse_periods(f.title)
        if date_problems:
            flags.append("date_parse_error")
    n_weeks = 0 if date_problems else sum(((e - s).days + 1) // 7 for s, e in periods)
    stated = TOTAL_WEEKS_RE.search(f.title)
    if stated and int(stated.group(1)) != n_weeks:
        flags.append("total_weeks_mismatch")
        date_problems.append(f"title says {stated.group(1)} weeks, parsed {n_weeks}")
    if n_weeks > 1:
        flags.append("multi_week")

    scope = c["scope"]
    file_rows.append(
        {
            "filename": f.filename,
            "title": f.title,
            "url_clean": f.url_clean,
            "doc_type": c["doc_type"],
            "scope": scope,
            "file_role": "base" if scope in BASE_SCOPES else "overlay" if scope in OVERLAY_SCOPES else "none",
            "sections": join(c["sections"]),
            "stations": c["stations"],
            "tables": c["tables"],
            "partial_note": c["partial_note"],
            "period_text": join(f"{s:%d.%m.%Y}-{e:%d.%m.%Y}" for s, e in periods),
            "date_problems": join(date_problems),
            "n_weeks_in_file": n_weeks,
            "upload_ts": upload_ts,
            "title_date": title_date,
            "title_date_label": title_date_label,
            "date_mismatch_days": mismatch_days,
            "date_mismatch": date_mismatch,
            "rev_label_title": rev_t,
            "rev_label_filename": rev_f,
            "tabs_listed": f.tabs_listed,
            "year_tab": f.year_tab,
            "list_position": f.list_position,
            "source_file": f.source_file,
            "_periods": [] if date_problems else periods,
            "_flags": flags,
        }
    )

# %% Expand to one row per file x week
rows = []
for fr in file_rows:
    weeks = [s + timedelta(weeks=k) for s, e in fr["_periods"] for k in range(((e - s).days + 1) // 7)]
    for ws in weeks or [None]:
        row = {k: v for k, v in fr.items() if not k.startswith("_")}
        flags = list(fr["_flags"])
        if ws is not None:
            iso = ws.isocalendar()
            row.update(
                week_start=ws,
                week_end=ws + timedelta(days=6),
                iso_year=iso.year,
                iso_week=iso.week,
                in_window=config.FIRST_WEEK_START <= ws <= config.LAST_WEEK_START,
            )
            if str(iso.year) not in fr["tabs_listed"].split(";"):
                flags.append("tab_year_mismatch")
        else:
            row.update(week_start=None, week_end=None, iso_year=None, iso_week=None, in_window=False)
        row["_flags"] = flags
        rows.append(row)
manifest = pd.DataFrame(rows)
manifest["iso_year"] = manifest["iso_year"].astype("Int64")
manifest["iso_week"] = manifest["iso_week"].astype("Int64")
manifest["date_mismatch_days"] = manifest["date_mismatch_days"].astype("Int64")

# %% Provisional selection: base plus overlays per in-window week x section
eligible = manifest[(manifest["doc_type"] == "weekly_statement") & manifest["in_window"]]
ROLE_COLS = ["base_for", "superseded_for", "overlay_active_for", "overlay_older_for", "unresolved_for"]
row_roles = {}  # (filename, week_start) -> {role column: [sections]}


def assign(fn, ws, col, value):
    row_roles.setdefault((fn, ws), {}).setdefault(col, []).append(value)


decisions = []  # one per week x section with any candidate
for sec in SECTIONS:
    covers = eligible["sections"].str.split(";").apply(lambda s: sec in s)
    cands = eligible[covers & (eligible["file_role"] != "none")]
    for ws, g in cands.groupby("week_start"):
        bases = g[g["file_role"] == "base"].sort_values("upload_ts", ascending=False, na_position="last")
        overlays = g[g["file_role"] == "overlay"].sort_values("upload_ts", na_position="last")
        d = {"week_start": ws, "section": sec, "n_base": len(bases), "base": "", "superseded": "",
             "overlay_active": "", "overlay_older": "", "overlay_unresolved": "", "issue": ""}
        base_ts = None
        if bases.empty:
            d["issue"] = "no base"
        elif bases["upload_ts"].isna().any():
            d["issue"] = "unranked: base candidate without upload_ts"
            for fn in bases["filename"]:
                assign(fn, ws, "unresolved_for", f"{sec}(unranked)")
        elif len(bases) > 1 and bases["upload_ts"].iloc[0] == bases["upload_ts"].iloc[1]:
            top = bases.loc[bases["upload_ts"] == bases["upload_ts"].iloc[0], "filename"].tolist()
            d["issue"] = f"tie at {bases['upload_ts'].iloc[0]}: {join(top)}"
            for fn in bases["filename"]:
                assign(fn, ws, "unresolved_for" if fn in top else "superseded_for", f"{sec}(tie)" if fn in top else sec)
        else:
            d["base"], base_ts = bases["filename"].iloc[0], bases["upload_ts"].iloc[0]
            d["superseded"] = join(bases["filename"].iloc[1:])
            assign(d["base"], ws, "base_for", sec)
            for fn in bases["filename"].iloc[1:]:
                assign(fn, ws, "superseded_for", sec)

        active, older, unresolved = [], [], []
        for o in overlays.itertuples(index=False):
            if base_ts is None:
                unresolved.append(o.filename)
                assign(o.filename, ws, "unresolved_for", f"{sec}(no base)")
            elif pd.isna(o.upload_ts) or o.upload_ts == base_ts:
                unresolved.append(o.filename)
                assign(o.filename, ws, "unresolved_for", f"{sec}(overlay unranked or tied with base)")
            elif o.upload_ts > base_ts:
                active.append(o.filename)
                assign(o.filename, ws, "overlay_active_for", sec)
            else:
                older.append(o.filename)
                assign(o.filename, ws, "overlay_older_for", sec)
        d.update(overlay_active=join(active), overlay_older=join(older), overlay_unresolved=join(unresolved))
        decisions.append(d)
decisions = pd.DataFrame(decisions)

for col in ROLE_COLS:
    manifest[col] = [join(row_roles.get((fn, ws), {}).get(col, []))
                     for fn, ws in zip(manifest["filename"], manifest["week_start"])]
manifest["selection_status"] = "provisional"

# %% Flags and manual inspection (in-window files only)
file_in_window = manifest.groupby("filename")["in_window"].any()


def inspect_reasons(r):
    if not file_in_window[r["filename"]]:
        return ""
    reasons = []
    if r["scope"] == "UNKNOWN":
        reasons.append("scope_unknown")
    if r["file_role"] == "overlay":
        reasons.append("overlay")
    if r["n_weeks_in_file"] > 1:
        reasons.append("multi_week")
    if "cover" in r["filename"].lower():
        reasons.append("cover_in_filename")
    if r["date_mismatch"]:
        reasons.append("date_mismatch")
    return join(reasons)


manifest["inspect_reason"] = manifest.apply(inspect_reasons, axis=1)
manifest["manual_inspection"] = manifest["inspect_reason"] != ""
manifest["flags"] = [
    join(dict.fromkeys(fl + (["check_base_includes_overlay"] if older else []) + (["unresolved"] if unres else [])))
    for fl, older, unres in zip(manifest["_flags"], manifest["overlay_older_for"], manifest["unresolved_for"])
]
manifest = manifest.drop(columns="_flags")

# %% Week coverage grid
grid = [config.FIRST_WEEK_START + timedelta(weeks=k) for k in range(config.EXPECTED_WEEKS)]
assert grid[-1] == config.LAST_WEEK_START, "week grid does not end on LAST_WEEK_START"
off_grid = set(manifest.loc[manifest["in_window"], "week_start"]) - set(grid)
if off_grid:
    warn(f"in-window week starts not on the Monday grid: {sorted(off_grid)}")

weekly = manifest[manifest["doc_type"] == "weekly_statement"]
dec_by_key = {(d.week_start, d.section): d for d in decisions.itertuples(index=False)}
cov_rows = []
for ws in grid:
    w = weekly[weekly["week_start"] == ws]
    iso = ws.isocalendar()
    row = {"week_start": ws, "week_end": ws + timedelta(days=6), "iso_year": iso.year, "iso_week": iso.week,
           "n_files": w["filename"].nunique()}
    flags = []
    for sec in SECTIONS:
        d = dec_by_key.get((ws, sec))
        row[f"base_{sec}"] = d.base if d else ""
        row[f"overlays_active_{sec}"] = d.overlay_active if d else ""
        row[f"n_base_versions_{sec}"] = d.n_base if d else 0
        if d is None or d.issue == "no base":
            flags.append(f"no_base_{sec}")
        elif d.issue:
            flags.append(f"{d.issue.split(':')[0]}_{sec}")
        if d is not None and d.overlay_older:
            flags.append(f"overlay_older_{sec}")
        if d is not None and d.overlay_unresolved:
            flags.append(f"overlay_unresolved_{sec}")
    if (w["scope"] == "UNKNOWN").any():
        flags.append("unknown_scope_file_present")
    if w["manual_inspection"].any():
        flags.append("manual_inspection_file_present")
    row["flags"] = join(flags)
    cov_rows.append(row)
coverage = pd.DataFrame(cov_rows)
assert len(coverage) == config.EXPECTED_WEEKS

# %% Download list: every distinct in-window weekly statement file
inwin = weekly[weekly["in_window"]]
dl_rows = []
for fn, g in inwin.groupby("filename", sort=False):
    reasons = [col.removesuffix("_for") for col in ROLE_COLS if g[col].ne("").any()]
    if (g["scope"] == "UNKNOWN").any():
        reasons.append("scope_unknown")
    first = g.iloc[0]
    dl_rows.append(
        {
            "filename": fn,
            "url_clean": first["url_clean"],
            "title": first["title"],
            "scope": first["scope"],
            "file_role": first["file_role"],
            "sections": first["sections"],
            "stations": first["stations"],
            "tables": first["tables"],
            "upload_ts": first["upload_ts"],
            "in_window_weeks": join(f"{ws:%Y-%m-%d}" for ws in g["week_start"]),
            "reason": join(reasons),
            "base_for": join(f"{ws:%Y-%m-%d}:{s}" for ws, s in zip(g["week_start"], g["base_for"]) if s),
            "overlay_active_for": join(
                f"{ws:%Y-%m-%d}:{s}" for ws, s in zip(g["week_start"], g["overlay_active_for"]) if s
            ),
            "manual_inspection": bool(g["manual_inspection"].any()),
            "inspect_reason": first["inspect_reason"],
        }
    )
download = pd.DataFrame(dl_rows).sort_values("upload_ts", na_position="first")

# %% Save
manifest.to_csv(config.MANIFEST_CSV, index=False)
coverage.to_csv(config.WEEK_COVERAGE_CSV, index=False)
download.to_csv(config.DOWNLOAD_LIST_CSV, index=False)

# %% Summary
pd.set_option("display.width", 250)
pd.set_option("display.max_colwidth", 140)
files_df = manifest.drop_duplicates("filename")
rel = lambda p: p.relative_to(config.PROJECT_ROOT)


def section(title):
    print(f"\n--- {title} " + "-" * max(0, 66 - len(title)))


def show_files(df, cols=("tabs_listed", "filename", "title")):
    if df.empty:
        print("  none")
    else:
        print(df[list(cols)].to_string(index=False))


def fmt_list(s):
    return ", ".join(s.split(";")) if s else "-"


print("\n" + "=" * 70)
print("STEP 02 SUMMARY: build manifest")
print("=" * 70)
print(f"Input rows (step 01): {n_listed}; duplicates across tabs collapsed: {n_cross_tab_dups}; files: {len(files_df)}")
print(f"Manifest rows (file x week): {len(manifest)}")
for p in (config.MANIFEST_CSV, config.WEEK_COVERAGE_CSV, config.DOWNLOAD_LIST_CSV):
    print(f"  -> {rel(p)}")

section("Files by doc_type and scope")
print(pd.crosstab(files_df["scope"], files_df["doc_type"], margins=True, margins_name="total").to_string())

section("UNKNOWN scope titles (all)")
show_files(files_df[files_df["scope"] == "UNKNOWN"].sort_values(["year_tab", "list_position"]))

section("Titles whose dates failed to parse or validate")
show_files(files_df[files_df["date_problems"] != ""], ("tabs_listed", "title", "date_problems"))

section(f"Week coverage ({config.EXPECTED_WEEKS} weeks, {config.FIRST_WEEK_START} to {config.LAST_WEEK_START})")
for sec in SECTIONS:
    have = coverage[f"base_{sec}"] != ""
    missing = coverage.loc[~have, "week_start"].tolist()
    n_ov = int((coverage[f"overlays_active_{sec}"] != "").sum())
    print(f"  {sec}: base in {int(have.sum())}/{config.EXPECTED_WEEKS} weeks; active overlays in {n_ov} weeks"
          + (f"; no base: {', '.join(f'{d:%Y-%m-%d}' for d in missing)}" if missing else ""))

section("In-window STATION_SPECIFIC, TRAS_EMERGENCY_IPP and UNKNOWN files")
narrow = manifest[manifest["in_window"] & manifest["scope"].isin(["STATION_SPECIFIC", "TRAS_EMERGENCY_IPP", "UNKNOWN"])]
narrow = narrow.sort_values(["week_start", "upload_ts"])
if narrow.empty:
    print("  none")
for r in narrow.itertuples(index=False):
    roles = "; ".join(f"{c.removesuffix('_for')}: {getattr(r, c)}" for c in ROLE_COLS if getattr(r, c)) or "no role"
    print(f"  {r.week_start:%Y-%m-%d}  {r.scope:<18} uploaded {r.upload_ts}  [{roles}]")
    print(f"      title:    {r.title}")
    print(f"      stations: {r.stations or '-'}   tables: {r.tables or '-'}   sections: {r.sections or '-'}")
print(f"  ({len(narrow)} file-week rows, {narrow['filename'].nunique()} files)")

section("Multi-version weeks: base, active overlays and older overlays per section")
multi_weeks = sorted(
    decisions.loc[(decisions["n_base"] > 1) | (decisions["overlay_active"] != "") | (decisions["overlay_older"] != "")
                  | (decisions["overlay_unresolved"] != ""), "week_start"].unique()
)
if not multi_weeks:
    print("  none")
for ws in multi_weeks:
    print(f"  {ws:%Y-%m-%d}")
    for sec in SECTIONS:
        d = dec_by_key.get((ws, sec))
        if d is None:
            print(f"    {sec}: no candidates")
            continue
        print(f"    {sec}: base {d.base or '(none: ' + d.issue + ')'}")
        if d.superseded:
            print(f"          superseded: {fmt_list(d.superseded)}")
        if d.overlay_active:
            print(f"          active overlays: {fmt_list(d.overlay_active)}")
        if d.overlay_older:
            print(f"          older overlays (check whether base already includes them): {fmt_list(d.overlay_older)}")
        if d.overlay_unresolved:
            print(f"          unresolved overlays: {fmt_list(d.overlay_unresolved)}")
print(f"  ({len(multi_weeks)} weeks)")

section(f"Date mismatches beyond {config.DATE_MISMATCH_TOLERANCE_DAYS} days (days = upload - title)")
mm = files_df[files_df["date_mismatch"]].copy()
mm["upload_date"] = mm["upload_ts"].apply(lambda t: t.date())
mm["any_in_window"] = mm["filename"].map(file_in_window)
mm["abs_days"] = mm["date_mismatch_days"].abs()
mm = mm.sort_values(["any_in_window", "abs_days"], ascending=[False, False])
show_files(mm, ("any_in_window", "date_mismatch_days", "upload_date", "title_date", "title_date_label", "filename"))
within = files_df["date_mismatch_days"].notna() & (files_df["date_mismatch_days"] != 0) & ~files_df["date_mismatch"]
print(f"  ({len(mm)} files beyond tolerance, {int(mm['any_in_window'].sum())} in-window; "
      f"{int(within.sum())} more differ by 1-{config.DATE_MISMATCH_TOLERANCE_DAYS} days and are not flagged)")

section("Manual inspection (in-window files only)")
mi = files_df[files_df["manual_inspection"]]
print(f"  {len(mi)} files flagged. By reason (a file can have several):")
print(mi["inspect_reason"].str.split(";").explode().value_counts().to_string())
print()
show_files(mi.sort_values(["inspect_reason", "upload_ts"]), ("inspect_reason", "filename"))

section("Ties and unranked")
issues = decisions[decisions["issue"].str.startswith(("tie", "unranked"))]
unres = decisions[decisions["overlay_unresolved"] != ""]
print(issues[["week_start", "section", "issue"]].to_string(index=False) if len(issues) else "  base ties/unranked: none")
print(unres[["week_start", "section", "overlay_unresolved", "issue"]].to_string(index=False)
      if len(unres) else "  unresolved overlays: none")

section("Files whose week falls in a different ISO year than the tab(s) listing them")
tym = manifest[manifest["flags"].str.contains("tab_year_mismatch")]
tym = tym.groupby(["filename", "tabs_listed"], sort=False).agg(
    iso_years=("iso_year", lambda s: join(sorted(set(s)))), weeks=("week_start", "count"), title=("title", "first")
).reset_index()
show_files(tym, ("tabs_listed", "iso_years", "weeks", "title"))

section("Other flags")
other_ts = files_df[files_df["flags"].str.contains("upload_ts_")]
print(f"  files with upload_ts missing/invalid: {len(other_ts)} "
      f"(in-window: {int(other_ts['filename'].isin(inwin['filename']).sum())})")
print(f"  files with no title date: {int(files_df['flags'].str.contains('title_date_missing').sum())}")
print(f"  files with ambiguous title date: {int(files_df['flags'].str.contains('title_date_ambiguous').sum())}")
labels = files_df.loc[files_df["title_date_label"] != "", "title_date_label"].value_counts()
print(f"  title date labels used: {labels.to_dict()}")

section("Download list")
print(f"  {len(download)} distinct in-window weekly statement files to download")
print("  by reason: " + str(download["reason"].value_counts().to_dict()))

print(f"\nWarnings: {len(warnings)}")
for w in warnings:
    print(f"  - {w}")
