"""Step 05: finalise the base file per week x section from file contents (A12).

Uses step 04's page inventory (table types, station rows per table) instead of
titles. For each in-window file x week x section, the file's station count per
table is compared with the same week's step 02 provisional base. Files with at
least COMPLETENESS_THRESHOLD of the base's stations in every table of the section
are complete restatements (base candidates); files below REVIEW_BAND are narrow
(overlays); anything in between is needs_review. A needs_review file blocks the
slot only if it is at least as new as the newest complete candidate; older ones
are flagged older_needs_review. Labels and row text are read, never parsed into
values. Steps 02 and 04 are not modified.
"""

# %% Imports
import json
import re
from collections import defaultdict

import pandas as pd

import config
from src.table_reader import show, station_label

SECTIONS = ["TRAS", "SRAS", "SCUC"]
TRAS3 = ["TRAS_SHORTFALL_EMERGENCY", "TRAS_SHORTFALL", "TRAS_EMERGENCY"]
STATEMENT_TYPES = TRAS3 + ["TRAS_MARKET", "SRAS_PAYMENT", "SRAS_PERFORMANCE", "SCUC", "CONSOLIDATED"]
THR = config.COMPLETENESS_THRESHOLD
BAND_LOW, BAND_HIGH = config.REVIEW_BAND
assert BAND_HIGH == THR, "REVIEW_BAND upper bound must equal COMPLETENESS_THRESHOLD"

warnings = []


def warn(msg):
    warnings.append(msg)


def join(items):
    return ";".join(str(i) for i in items)


# %% Load inputs
manifest = pd.read_csv(config.MANIFEST_CSV, keep_default_na=False)
inwin = manifest[(manifest["doc_type"] == "weekly_statement") & (manifest["in_window"].astype(str) == "True")].copy()
inwin["upload_ts"] = pd.to_datetime(inwin["upload_ts"])
download = pd.read_csv(config.DOWNLOAD_LIST_CSV, keep_default_na=False)
if set(inwin["filename"]) != set(download["filename"]):
    warn("in-window manifest files differ from the step 02 download list")
coverage02 = pd.read_csv(config.WEEK_COVERAGE_CSV, keep_default_na=False)
base02 = {(r.week_start, s): getattr(r, f"base_{s}") for r in coverage02.itertuples(index=False) for s in SECTIONS}
pages = pd.read_csv(config.PAGE_INVENTORY_CSV, keep_default_na=False)
files04 = pd.read_csv(config.FILE_INVENTORY_CSV, keep_default_na=False).set_index("filename")

# Cell-based tables from step 04 (src/table_reader), indexed by page.
tables_by_page = defaultdict(list)
with open(config.TABLES_JSONL, encoding="utf-8") as tf:
    for line in tf:
        t = json.loads(line)
        tables_by_page[(t["filename"], t["page_no"])].append(t)

# %% Station labels and table types per file x week
file_weeks = inwin.groupby("filename")["week_start"].agg(lambda s: sorted(set(s)))
labels = defaultdict(set)  # (file, week, table) -> station labels
row_text = defaultdict(lambda: defaultdict(list))  # (file, week, table) -> label -> [row text, cells shown]
row_cells = defaultdict(dict)  # (file, week, table) -> label -> (cells, name column) of its first row
present = defaultdict(set)  # (file, week) -> table types present
empty_tras3 = set()  # (file, week) with an explicit "no data" TRAS-III page
has_consolidated = defaultdict(bool)
carried = defaultdict(int)  # statement pages of multi-week files assigned by carrying the previous week

for fn, weeks in file_weeks.items():
    current = []
    for pg in pages[pages["filename"] == fn].sort_values("page_no").itertuples(index=False):
        types = [t for t in pg.page_types.split(";") if t in STATEMENT_TYPES]
        printed = [w for w in pg.weeks_printed.split(";") if w in weeks]
        if len(weeks) == 1:
            assign = weeks
        elif printed:
            assign = current = printed
        else:
            assign = current
            if types:
                carried[fn] += 1
        if (types or pg.explicit_no_data) and not assign:
            warn(f"{fn} p{pg.page_no}: statement page not assigned to any in-window week")
        for w in assign:
            present[(fn, w)].update(types)
            if "CONSOLIDATED" in types:
                has_consolidated[(fn, w)] = True
            if pg.explicit_no_data and pg.no_data_table_type == "TRAS_III_CONDITION_NOT_STATED":
                empty_tras3.add((fn, w))
        for tb in tables_by_page.get((fn, pg.page_no), []):
            t = tb["table_type"]
            if t not in STATEMENT_TYPES:
                continue
            for row in tb["rows"]:
                label = station_label(row)
                if not label:
                    continue
                for w in assign:
                    labels[(fn, w, t)].add(label)
                    row_text[(fn, w, t)][label].append(f"p{pg.page_no}: {show(row['cells'])}")
                    row_cells[(fn, w, t)].setdefault(label, (row["cells"], row["name_col"]))

# Consistency with step 04: per file, the union over weeks must equal step 04's count.
n_checked, n_diff = 0, 0
for fn, weeks in file_weeks.items():
    for t in STATEMENT_TYPES:
        v04 = files04.at[fn, f"stations_{t}"]
        if v04 == "":
            continue
        n_checked += 1
        mine = len(set().union(*(labels[(fn, w, t)] for w in weeks)))
        if mine != int(v04):
            n_diff += 1
            warn(f"station count differs from step 04: {fn} {t}: step 05 {mine}, step 04 {v04}")


# %% Evaluate each file x week x section against the same week's step 02 base
def n_st(fn, w, t):
    return len(labels.get((fn, w, t), ()))


def tras3_union(fn, w):
    return set().union(*(labels.get((fn, w, t), set()) for t in TRAS3))


def layout(fn, w):
    p = present[(fn, w)]
    comb = "TRAS_SHORTFALL_EMERGENCY" in p
    sep = bool(p & {"TRAS_SHORTFALL", "TRAS_EMERGENCY"})
    return {(True, False): "combined", (False, True): "separate", (True, True): "mixed"}.get((comb, sep), "none")


def ratio(cand, base):
    if base == 0:
        return (1.0 if cand == 0 else float("inf")), "base_zero_stations"
    return cand / base, ""


def section_tables(fn, w, sec):
    """Return (required tables, present tables, flags) for a section in this file-week."""
    p, flags = present[(fn, w)], []
    if sec == "SRAS":
        required = ["SRAS_PAYMENT"]
    elif sec == "SCUC":
        required = ["SCUC"]
    else:
        tras3 = ["TRAS_SHORTFALL_EMERGENCY"] if layout(fn, w) == "combined" else ["TRAS_SHORTFALL", "TRAS_EMERGENCY"]
        required = tras3 + ["TRAS_MARKET"]
    have = [t for t in required if t in p]
    # A TRAS-III page with its own heading and "No data found" is the other TRAS-III table,
    # present but empty (A14). It fills the gap only when exactly one separate table is missing.
    sep_missing = [t for t in ("TRAS_SHORTFALL", "TRAS_EMERGENCY") if t in required and t not in p]
    if sec == "TRAS" and (fn, w) in empty_tras3 and len(sep_missing) == 1:
        have.append(sep_missing[0])
        flags.append(f"explicit_no_data:{sep_missing[0]}")
    return required, have, flags


# Consolidated table columns (amounts after the station name), by layout. Early files have one
# combined Shortfall/Emergency column (5 amounts); later files have separate columns (6 amounts).
CONS_COLS = {
    5: {"SRAS": [0], "SHORTFALL": [1], "EMERGENCY": [1], "MARKET": [2], "SCUC": [3]},
    6: {"SRAS": [0], "SHORTFALL": [1], "EMERGENCY": [2], "MARKET": [3], "SCUC": [4]},
}
TABLE_COLUMN = {"SRAS_PAYMENT": ["SRAS"], "TRAS_SHORTFALL": ["SHORTFALL"], "TRAS_EMERGENCY": ["EMERGENCY"],
                "TRAS_SHORTFALL_EMERGENCY": ["SHORTFALL", "EMERGENCY"], "TRAS_MARKET": ["MARKET"], "SCUC": ["SCUC"]}
NUM_RE = re.compile(r"-?\d[\d,]*(?:\.\d+)?")


def consolidated_amounts(fn, w, label, table):
    """Amounts in the file's consolidated row for this station, in the column(s) of `table`.

    Returns (amounts, problem). problem is "" when readable.
    """
    found = row_cells[(fn, w, "CONSOLIDATED")].get(label)
    if not found:
        return None, "not_in_consolidated"
    cells, name_col = found
    # Amount cells after the name; the trailing status cell ("Payable to Pool Account", "-") is text.
    vals = [float(c.replace(",", "")) for c in cells[name_col + 1:] if NUM_RE.fullmatch(c.strip())]
    cols = CONS_COLS.get(len(vals))
    if cols is None:
        return None, f"consolidated_row_unreadable({len(vals)} amounts)"
    return [vals[i] for c in TABLE_COLUMN[table] for i in cols[c]], ""


def dropped_rows(fn, base, w, required):
    """[(table, station)] for stations in the comparison file's section tables but not the candidate's."""
    out = []
    lay_c, lay_b = layout(fn, w), layout(base, w)
    tras3_req = [t for t in required if t in TRAS3]
    if tras3_req and lay_c != lay_b and "none" not in (lay_c, lay_b):
        # Different TRAS-III layouts: compare all TRAS-III stations; a missing one must be 0 in both columns.
        for st in sorted(tras3_union(base, w) - tras3_union(fn, w)):
            out.append(("TRAS_SHORTFALL_EMERGENCY", st))
        required = [t for t in required if t not in TRAS3]
    for t in required:
        for st in sorted(labels.get((base, w, t), set()) - labels.get((fn, w, t), set())):
            out.append((t, st))
    return out


def evaluate(fn, w, sec):
    base = base02.get((w, sec), "")
    required, have, flags = section_tables(fn, w, sec)
    out = {"content": "absent" if not have else "full" if len(have) == len(required) else "partial",
           "ratios": {}, "flags": flags, "decision": "", "basis": "", "dropped_as_zero": []}
    if not have:
        return out
    if not base:
        out["flags"].append("no_step02_base")
        out["decision"] = "needs_review"
        return out
    # Per-table ratios: secondary evidence (primary only when a consolidated table is missing).
    ratios = {}
    tras3_have = [t for t in have if t in TRAS3]
    lay_c, lay_b = layout(fn, w), layout(base, w)
    if tras3_have and lay_c != lay_b and "none" not in (lay_c, lay_b):
        r, f = ratio(len(tras3_union(fn, w)), len(tras3_union(base, w)))
        ratios["TRAS3_UNION"] = r
        out["flags"] += ["layout_differs"] + ([f] if f else [])
    else:
        for t in tras3_have:
            r, f = ratio(n_st(fn, w, t), n_st(base, w, t))
            ratios[t] = r
            out["flags"] += [f"{f}:{t}"] if f else []
    for t in have:
        if t not in TRAS3:
            r, f = ratio(n_st(fn, w, t), n_st(base, w, t))
            ratios[t] = r
            out["flags"] += [f"{f}:{t}"] if f else []
    out["ratios"] = ratios

    if "CONSOLIDATED" in present[(fn, w)] and "CONSOLIDATED" in present[(base, w)]:
        # Primary: the consolidated table lists every station, including zeros (A12).
        out["basis"] = "consolidated"
        r, f = ratio(n_st(fn, w, "CONSOLIDATED"), n_st(base, w, "CONSOLIDATED"))
        out["ratios"][f"CONSOLIDATED_{sec}"] = r
        if r < BAND_LOW:
            out["decision"] = "narrow"
        elif r < THR:
            out["decision"] = "needs_review"
        else:
            # Dropped-row check (A15): every station missing from a section table must show 0
            # in that section's column of the candidate's own consolidated table.
            zero, problems = [], []
            for t, st in dropped_rows(fn, base, w, required):
                amounts, problem = consolidated_amounts(fn, w, st, t)
                if problem:
                    problems.append(f"{st}:{problem}")
                elif any(a != 0 for a in amounts):
                    problems.append(f"{st}:{t}={amounts}")
                else:
                    zero.append(f"{t}:{st}")
            if problems:
                out["decision"] = "needs_review"
                out["flags"].append("rows_dropped_nonzero_or_unreadable:" + ",".join(problems))
            else:
                out["decision"] = "complete"
                if zero:
                    out["flags"].append("rows_dropped_as_zero")
                    out["dropped_as_zero"] = zero
        return out

    out["basis"] = "per_table"
    vals = list(ratios.values())
    if out["content"] == "full" and min(vals) >= THR:
        out["decision"] = "complete"
    elif max(vals) < BAND_LOW:
        out["decision"] = "narrow"
    else:
        out["decision"] = "needs_review"
    return out


fw = inwin.drop_duplicates(["filename", "week_start"]).set_index(["filename", "week_start"])
evals = {(fn, w, s): evaluate(fn, w, s) for fn, w in fw.index for s in SECTIONS}

# %% Final selection per week x section
grid = sorted(coverage02["week_start"])
assert len(grid) == config.EXPECTED_WEEKS
final_role = {}  # (file, week, section) -> role
sel_rows = []
for w in grid:
    for sec in SECTIONS:
        cands = [(fn, evals[(fn, w, sec)]) for fn, ww in fw.index if ww == w and evals[(fn, w, sec)]["decision"]]
        complete = sorted([fn for fn, e in cands if e["decision"] == "complete"],
                          key=lambda fn: fw.at[(fn, w), "upload_ts"], reverse=True)
        narrow = [fn for fn, e in cands if e["decision"] == "narrow"]
        review = [fn for fn, e in cands if e["decision"] == "needs_review"]
        flags, base, active, older = [], "", [], []
        newest_complete_ts = fw.at[(complete[0], w), "upload_ts"] if complete else None
        # A needs_review file blocks the slot only if it is at least as new as the newest
        # complete candidate (a tie also blocks). Older ones cannot become the base anyway.
        blocking = [fn for fn in review
                    if newest_complete_ts is None or fw.at[(fn, w), "upload_ts"] >= newest_complete_ts]
        older_review = [fn for fn in review if fn not in blocking]
        if older_review:
            flags.append("older_needs_review")
            for fn in older_review:
                final_role[(fn, w, sec)] = "older_needs_review"
        if blocking:
            flags.append("needs_review")
            for fn, _ in cands:
                if fn not in older_review:
                    final_role[(fn, w, sec)] = "needs_review" if fn in blocking else "unresolved"
        elif not complete:
            flags.append("no_complete_candidate")
            for fn in narrow:
                final_role[(fn, w, sec)] = "unresolved"
        elif len(complete) > 1 and fw.at[(complete[0], w), "upload_ts"] == fw.at[(complete[1], w), "upload_ts"]:
            flags.append("tie")
            for fn, _ in cands:
                final_role[(fn, w, sec)] = "unresolved"
        else:
            base = complete[0]
            base_ts = fw.at[(base, w), "upload_ts"]
            final_role[(base, w, sec)] = "base"
            for fn in complete[1:]:
                final_role[(fn, w, sec)] = "superseded"
            for fn in sorted(narrow, key=lambda fn: fw.at[(fn, w), "upload_ts"]):
                ts = fw.at[(fn, w), "upload_ts"]
                if ts > base_ts:
                    active.append(fn)
                    final_role[(fn, w, sec)] = "overlay_active"
                elif ts < base_ts:
                    older.append(fn)
                    final_role[(fn, w, sec)] = "overlay_older"
                else:
                    flags.append("overlay_tied_with_base")
                    final_role[(fn, w, sec)] = "unresolved"
            if older:
                flags.append("overlay_older")
        layout_differs = any("layout_differs" in e["flags"] for _, e in cands)
        if any(any(f.startswith("explicit_no_data") for f in e["flags"]) for _, e in cands):
            flags.append("explicit_no_data_table")
        old = base02.get((w, sec), "")
        sel_rows.append({
            "week_start": w, "section": sec, "base": base, "base_upload_ts": fw.at[(base, w), "upload_ts"] if base else "",
            "base_tras3_layout": layout(base, w) if base and sec == "TRAS" else "",
            "overlays_active": join(active), "overlays_older": join(older),
            "n_complete_candidates": len(complete), "newest_complete": complete[0] if complete else "",
            "n_overlays": len(narrow), "needs_review_files": join(blocking),
            "older_needs_review_files": join(older_review),
            "base_step02": old, "changed": base != old, "layout_differs": layout_differs,
            "needs_review": bool(blocking), "flags": join(dict.fromkeys(flags)),
        })
selection = pd.DataFrame(sel_rows)
assert len(selection) == config.EXPECTED_WEEKS * 3

# %% File roles (one row per file x in-window week)
ROLE_COLS02 = {"base_for": "base", "superseded_for": "superseded", "overlay_active_for": "overlay_active",
               "overlay_older_for": "overlay_older", "unresolved_for": "unresolved"}


def roles02(fn, w):
    row = fw.loc[(fn, w)]
    out = {}
    for col, role in ROLE_COLS02.items():
        for s in filter(None, row[col].split(";")):
            out[s.split("(")[0]] = role
    return out


role_rows = []
for fn, w in fw.index:
    r02 = roles02(fn, w)
    row = {"filename": fn, "week_start": w, "upload_ts": fw.at[(fn, w), "upload_ts"],
           "title_scope": fw.at[(fn, w), "scope"], "step02_file_role": fw.at[(fn, w), "file_role"],
           "step02_roles": join(f"{s}:{r02[s]}" for s in SECTIONS if s in r02),
           "tras3_layout": layout(fn, w), "has_consolidated": has_consolidated[(fn, w)]}
    for s in SECTIONS:
        row[f"content_{s}"] = evals[(fn, w, s)]["content"]
    for t in STATEMENT_TYPES:
        row[f"stations_{t}"] = n_st(fn, w, t) if t in present[(fn, w)] else ""
    row["stations_TRAS3_UNION"] = len(tras3_union(fn, w)) if present[(fn, w)] & set(TRAS3) else ""
    for s in SECTIONS:
        for t, r in evals[(fn, w, s)]["ratios"].items():
            row[f"ratio_{t}"] = round(r, 3) if r != float("inf") else "inf"
    final = {s: final_role.get((fn, w, s), "") for s in SECTIONS}
    for s in SECTIONS:
        row[f"basis_{s}"] = evals[(fn, w, s)]["basis"]
        row[f"dropped_as_zero_{s}"] = join(evals[(fn, w, s)]["dropped_as_zero"])
        row[f"decision_{s}"] = evals[(fn, w, s)]["decision"]
        row[f"final_role_{s}"] = final[s]
    row["final_roles"] = join(f"{s}:{final[s]}" for s in SECTIONS if final[s])
    row["role_changed"] = any(final[s] != r02.get(s, "") for s in SECTIONS)
    row["flags"] = join(dict.fromkeys(f for s in SECTIONS for f in evals[(fn, w, s)]["flags"]))
    role_rows.append(row)
roles = pd.DataFrame(role_rows)

# %% Save (keep the previous selection for the change report)
prev_selection = (pd.read_csv(config.SELECTION_CSV, keep_default_na=False)
                  if config.SELECTION_CSV.exists() else None)
roles.to_csv(config.FILE_ROLES_CSV, index=False)
selection.to_csv(config.SELECTION_CSV, index=False)

# %% Summary
pd.set_option("display.width", 250)
pd.set_option("display.max_colwidth", 80)
rel = lambda p: p.relative_to(config.PROJECT_ROOT)


def section(title):
    print(f"\n--- {title} " + "-" * max(0, 66 - len(title)))


def counts_str(r, sections=SECTIONS):
    parts = []
    for t in STATEMENT_TYPES:
        if r[f"stations_{t}"] != "":
            rc = f"ratio_{t}"
            parts.append(f"{t}={r[f'stations_{t}']}" + (f" ({r[rc]})" if rc in r and r[rc] == r[rc] and r[rc] != "" else ""))
    if "ratio_TRAS3_UNION" in r and r["ratio_TRAS3_UNION"] == r["ratio_TRAS3_UNION"] and r["ratio_TRAS3_UNION"] != "":
        parts.append(f"TRAS3_UNION={r['stations_TRAS3_UNION']} ({r['ratio_TRAS3_UNION']})")
    cons = [f"{s}={r[f'ratio_CONSOLIDATED_{s}']}" for s in SECTIONS
            if f"ratio_CONSOLIDATED_{s}" in r and r[f"ratio_CONSOLIDATED_{s}"] == r[f"ratio_CONSOLIDATED_{s}"]
            and r[f"ratio_CONSOLIDATED_{s}"] != ""]
    if cons:
        parts.append("consolidated ratio " + ", ".join(cons))
    return ", ".join(parts)


print("\n" + "=" * 70)
print("STEP 05 SUMMARY: content-based selection")
print("=" * 70)
for p in (config.FILE_ROLES_CSV, config.SELECTION_CSV):
    print(f"  -> {rel(p)}")
print(f"In-window files: {roles['filename'].nunique()}; file-week rows: {len(roles)}; "
      f"thresholds: complete >= {THR}, needs_review in [{BAND_LOW}, {BAND_HIGH}), narrow < {BAND_LOW}")
print(f"Station counts re-derived and checked against step 04: {n_checked} file-table counts, {n_diff} differ")
if carried:
    print(f"Multi-week files with statement pages assigned by carrying the previous week: {dict(carried)}")
print("\nDecisions (file-week x section):")
dec = pd.concat([roles[f"decision_{s}"].replace("", "(absent)").rename("decision").to_frame().assign(section=s)
                 for s in SECTIONS], ignore_index=True)
print(pd.crosstab(dec["decision"], dec["section"]).to_string())

section("Files whose role changed from step 02")
chg = roles[roles["role_changed"]]
if chg.empty:
    print("  none")
for r in chg.sort_values(["week_start", "filename"]).to_dict("records"):
    print(f"  {r['week_start']}  {r['filename']}  [title scope {r['title_scope']}, layout {r['tras3_layout']}]")
    print(f"      step 02: {r['step02_roles'] or '-'}")
    print(f"      step 05: {r['final_roles'] or '-'}   (decisions: "
          + ", ".join(f"{s}={r[f'decision_{s}'] or 'absent'}" for s in SECTIONS) + ")")
    print(f"      stations (ratio to week's step 02 base): {counts_str(r)}")
print(f"  ({len(chg)} file-week rows, {chg['filename'].nunique()} files)")

SECTION_TABLES = {"TRAS": ["TRAS_SHORTFALL_EMERGENCY", "TRAS_SHORTFALL", "TRAS_EMERGENCY", "TRAS_MARKET"],
                  "SRAS": ["SRAS_PAYMENT"], "SCUC": ["SCUC"]}


def print_station_diff(fn_a, fn_b, w, tables):
    """Stations in one file's table but not the other's, with each row's text from both files."""
    for t in tables:
        a, b = labels.get((fn_a, w, t), set()), labels.get((fn_b, w, t), set())
        if not a and not b:
            continue
        only_a, only_b = sorted(a - b), sorted(b - a)
        print(f"    {t}: {len(a)} vs {len(b)} stations; only in review file: {len(only_a)}, only in base: {len(only_b)}")
        for label in only_a + only_b:
            where = "only in review file" if label in only_a else "only in base"
            print(f"      {label}  ({where})")
            for name, fn in (("review", fn_a), ("base  ", fn_b)):
                rows = row_text[(fn, w, t)].get(label) or ["(not present)"]
                for line in rows:
                    print(f"        {name}: {line}")


section("needs_review cases that block a slot (newer than the newest complete candidate)")
nr = selection[selection["needs_review"]]
if nr.empty:
    print("  none")
for s in nr.itertuples(index=False):
    print(f"  {s.week_start} {s.section}: blocking {s.needs_review_files}; newest complete candidate: "
          f"{s.newest_complete or '(none)'}")
    for fn in s.needs_review_files.split(";"):
        r = roles[(roles["filename"] == fn) & (roles["week_start"] == s.week_start)].iloc[0].to_dict()
        ts = fw.at[(fn, s.week_start), "upload_ts"]
        print(f"      {fn} (uploaded {ts}): {counts_str(r)}")
        if s.newest_complete:
            cb = fw.at[(s.newest_complete, s.week_start), "upload_ts"]
            print(f"    Station-set difference vs {s.newest_complete} (uploaded {cb}):")
            tables = ["TRAS_EMERGENCY", "TRAS_SHORTFALL"] if s.section == "TRAS" else SECTION_TABLES[s.section]
            print_station_diff(fn, s.newest_complete, s.week_start, tables)

section("older_needs_review (older than the newest complete candidate; not blocking)")
onr = selection[selection["older_needs_review_files"] != ""]
if onr.empty:
    print("  none")
for s in onr.itertuples(index=False):
    for fn in s.older_needs_review_files.split(";"):
        r = roles[(roles["filename"] == fn) & (roles["week_start"] == s.week_start)].iloc[0].to_dict()
        ts = fw.at[(fn, s.week_start), "upload_ts"]
        print(f"  {s.week_start} {s.section}: {fn} (uploaded {ts}); base {s.base or '(none)'}")
        print(f"      {counts_str(r)}")
print(f"  ({int(onr['older_needs_review_files'].str.split(';').str.len().sum()) if len(onr) else 0} file-slot cases)")

section("layout_differs cases")
ld = roles[roles["flags"].str.contains("layout_differs")]
if ld.empty:
    print("  none")
for r in ld.to_dict("records"):
    b = base02[(r["week_start"], "TRAS")]
    print(f"  {r['week_start']}  candidate {r['filename']}: layout {r['tras3_layout']}, TRAS-III stations "
          f"{r['stations_TRAS3_UNION']} (ratio {r.get('ratio_TRAS3_UNION')})")
    print(f"      step 02 base {b}: layout {layout(b, r['week_start'])}, TRAS-III stations {len(tras3_union(b, r['week_start']))}")

section("Slots whose base changed from step 02 (old -> new)")
bc = selection[selection["changed"]]
print(bc[["week_start", "section", "base_step02", "base"]].rename(columns={"base_step02": "old", "base": "new"})
      .to_string(index=False) if len(bc) else "  none")

section("Remaining overlays")
ov = roles[roles["final_roles"].str.contains("overlay")]
for fn, g in ov.groupby("filename"):
    r = g.iloc[0].to_dict()
    print(f"  {fn}  [{r['title_scope']}]  weeks: {join(g['week_start'])}")
    print(f"      roles: {join(g['final_roles'].unique())}   stations: {counts_str(r)}")
print(f"  ({ov['filename'].nunique()} files; expected 9)")

section("overlay_older cases")
oo = selection[selection["overlays_older"] != ""]
print(oo[["week_start", "section", "base", "overlays_older"]].to_string(index=False) if len(oo) else "  none")

section("Completeness basis (file-week x section, where the section is present)")
basis = pd.concat([roles[f"basis_{s}"].rename("basis").to_frame().assign(section=s) for s in SECTIONS],
                  ignore_index=True)
basis = basis[basis["basis"] != ""]
print(pd.crosstab(basis["basis"], basis["section"]).to_string())

section("rows_dropped_as_zero cases (missing from a section table, 0 in own consolidated)")
dz = roles[[any(r[f"dropped_as_zero_{s}"] for s in SECTIONS) for r in roles.to_dict("records")]]
if dz.empty:
    print("  none")
for r in dz.to_dict("records"):
    for s in SECTIONS:
        if r[f"dropped_as_zero_{s}"]:
            items = r[f"dropped_as_zero_{s}"].split(";")
            print(f"  {r['week_start']} {s}: {r['filename']} (final role {r[f'final_role_{s}'] or '-'}); "
                  f"{len(items)} stations:")
            print("      " + ", ".join(items))

section("Changes from the previous run")
if prev_selection is None:
    print("  no previous 05_selection.csv")
else:
    prev = prev_selection.set_index(["week_start", "section"])
    cur = selection.set_index(["week_start", "section"])
    moved = [(k, prev.at[k, "base"], cur.at[k, "base"]) for k in cur.index if prev.at[k, "base"] != cur.at[k, "base"]]
    print(f"  slots whose base changed: {len(moved)}")
    for (w, sec), a, b in moved:
        print(f"    {w} {sec}: {a or '(none)'} -> {b or '(none)'}")

    def review_set(df, col):
        return {(w, sec, f) for (w, sec), v in df[col].items() for f in str(v).split(";") if f}

    for col, name in (("needs_review_files", "blocking needs_review"), ("older_needs_review_files", "older_needs_review")):
        if col not in prev.columns:
            print(f"  {name}: no previous column to compare")
            continue
        new, gone = review_set(cur, col) - review_set(prev, col), review_set(prev, col) - review_set(cur, col)
        print(f"  {name}: new {len(new)}, no longer {len(gone)}")
        for w, sec, f in sorted(new):
            print(f"    new:  {w} {sec}: {f}")
        for w, sec, f in sorted(gone):
            print(f"    gone: {w} {sec}: {f}")

section("Final coverage")
have = selection["base"] != ""
print(f"  slots with a base: {int(have.sum())} / {len(selection)}")
for sec in SECTIONS:
    gaps = selection[(selection["section"] == sec) & ~have]
    print(f"  {sec}: {config.EXPECTED_WEEKS - len(gaps)}/{config.EXPECTED_WEEKS}"
          + (f"; gaps: " + ", ".join(f"{g.week_start} ({g.flags})" for g in gaps.itertuples()) if len(gaps) else ""))
other_flags = selection[selection["flags"] != ""]
if len(other_flags):
    print("  slots with flags:")
    print(other_flags[["week_start", "section", "flags"]].to_string(index=False))

section("TRAS-III layout of each week's TRAS base")
tb = selection[selection["section"] == "TRAS"][["week_start", "base", "base_tras3_layout"]]
runs, prev = [], None
for r in tb.itertuples(index=False):
    if runs and r.base_tras3_layout == prev:
        runs[-1][1], runs[-1][2] = r.week_start, runs[-1][2] + 1
    else:
        runs.append([r.week_start, r.week_start, 1, r.base_tras3_layout])
    prev = r.base_tras3_layout
for a, b, n, lay in runs:
    print(f"  {a} .. {b}  ({n:>2} weeks)  {lay or '(no base)'}")
print("  per week: " + ", ".join(f"{r.week_start}={(r.base_tras3_layout or '-')[0]}" for r in tb.itertuples()))

print(f"\nWarnings: {len(warnings)}")
for w in warnings:
    print(f"  - {w}")
