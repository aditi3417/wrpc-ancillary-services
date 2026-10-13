"""Step 07: validate the step 06 data for internal consistency (report only).

Runs every check on all files and reports results separately for selected data
(the step 05 base or an active overlay for that week and section) and superseded
data. Nothing is modified. Differences within ROUNDING_TOL_* per row involved are
"rounding"; larger ones are "fail". Actual differences are always recorded.
"""

# %% Imports
from collections import defaultdict
from datetime import date, timedelta

import pandas as pd

import config

TOL = {"rs": config.ROUNDING_TOL_RS_PER_ROW, "mwh": config.ROUNDING_TOL_MWH_PER_ROW}
SECTION_OF = {"TRAS_SHORTFALL": "TRAS", "TRAS_EMERGENCY": "TRAS", "TRAS_SHORTFALL_EMERGENCY": "TRAS",
              "TRAS_MARKET": "TRAS", "SRAS_PAYMENT": "SRAS", "SRAS_PERFORMANCE": "SRAS", "SCUC": "SCUC",
              "CONSOLIDATED": None}
TRAS3 = ["TRAS_SHORTFALL", "TRAS_EMERGENCY", "TRAS_SHORTFALL_EMERGENCY"]
NET = {"TRAS_SHORTFALL": "net_charges_rs", "TRAS_EMERGENCY": "net_charges_rs",
       "TRAS_SHORTFALL_EMERGENCY": "net_charges_rs", "TRAS_MARKET": "net_charges_rs",
       "SRAS_PAYMENT": "total_charges_rs", "SCUC": "net_charges_rs", "CONSOLIDATED": "net_charges_rs"}
warnings = []


def warn(msg):
    warnings.append(msg)


def unit(field):
    return "mwh" if field.endswith("_mwh") else "rs" if field.endswith("_rs") else None


# %% Load inputs
data = {t: pd.read_csv(p, dtype={"week_start": str, "station_raw": str, "status_text": str})
        for t, p in config.EXTRACT_CSV.items()}
for t, d in data.items():
    d["station"] = d["station_raw"].fillna("").str.upper()
totals = pd.read_csv(config.PRINTED_TOTALS_CSV, dtype={"week_start": str})
xlog = pd.read_csv(config.EXTRACTION_LOG_CSV, keep_default_na=False)
selection = pd.read_csv(config.SELECTION_CSV, keep_default_na=False)
manifest = pd.read_csv(config.MANIFEST_CSV, keep_default_na=False)
upload_ts = manifest.drop_duplicates("filename").set_index("filename")["upload_ts"]

base_of = {(r.week_start, r.section): r.base for r in selection.itertuples()}
overlays_of = {(r.week_start, r.section): [f for f in r.overlays_active.split(";") if f] for r in selection.itertuples()}
selected = set()  # (file, week, section)
for (w, s), f in base_of.items():
    if f:
        selected.add((f, w, s))
for (w, s), fs in overlays_of.items():
    for f in fs:
        selected.add((f, w, s))


def group(fn, week, ttype):
    sec = SECTION_OF[ttype]
    if sec is None:  # consolidated: selected if the file is a base for any section that week
        return "selected" if any((fn, week, s) in selected for s in ("TRAS", "SRAS", "SCUC")) else "superseded"
    return "selected" if (fn, week, sec) in selected else "superseded"


results = []


def record(check, fn, table, week, station, field, expected, actual, n_rows=1, status=None, note=""):
    diff = None
    if status is None:
        e, a = (0.0 if pd.isna(expected) else expected), (0.0 if pd.isna(actual) else actual)
        diff = a - e
        u = unit(field) or "rs"
        tol = TOL[u] * max(n_rows, 1)
        status = "pass" if abs(diff) < 1e-9 else "rounding" if abs(diff) <= tol + 1e-9 else "fail"
    results.append({"check": check, "group": group(fn, week, table), "filename": fn, "table_type": table, "week_start": week, "station": station, "field": field,
                    "expected": expected, "actual": actual, "difference": diff, "n_rows": n_rows,
                    "status": status, "note": note})


def v(x):
    return 0.0 if pd.isna(x) else float(x)


# %% 1. Printed totals: sum of rows = printed TOTAL row, column by column
inst_rows = {t: d.groupby("instance_id") for t, d in data.items()}
for r in totals.itertuples(index=False):
    d = data[r.table_type]
    rows = d[d["instance_id"] == r.instance_id]
    record("printed_total", r.filename, r.table_type, r.week_start, "", r.field,
           r.value, rows[r.field].sum(min_count=1), n_rows=len(rows))

# %% 2. Row arithmetic (printed formulas, by meaning)
FORMULAS = {
    "TRAS_SHORTFALL": [("net_charges_rs", ["up_charges_rs"], ["down_charges_rs"])],
    "TRAS_EMERGENCY": [("net_charges_rs", ["up_charges_rs"], ["down_charges_rs"])],
    "TRAS_SHORTFALL_EMERGENCY": [("net_charges_rs", ["up_charges_rs"], ["down_charges_rs"])],
    "TRAS_MARKET": [("up_total_charges_rs", ["da_up_energy_charges_rs", "da_up_commitment_charges_rs",
                                             "rt_up_energy_charges_rs", "rt_up_commitment_charges_rs"], []),
                    ("net_charges_rs", ["up_total_charges_rs"], ["da_down_charges_rs", "rt_down_charges_rs"])],
    "SRAS_PAYMENT": [("total_charges_rs", ["incentive_rs", "sras_energy_charges_rs"], [])],
    "SCUC": [("net_charges_rs", ["charges_paid_to_generator_rs"], ["charges_refunded_by_generator_rs"])],
    "CONSOLIDATED": [("net_charges_rs", ["sras_charges_rs", "shortfall_charges_rs", "emergency_charges_rs",
                                         "shortfall_emergency_charges_rs", "tras_market_charges_rs",
                                         "scuc_charges_rs"], [])],
}
for t, formulas in FORMULAS.items():
    d = data[t]
    for target, plus, minus in formulas:
        plus = [c for c in plus if c in d.columns]
        for r in d.itertuples(index=False):
            expected = sum(v(getattr(r, c)) for c in plus) - sum(v(getattr(r, c)) for c in minus)
            record(f"row_arithmetic:{target}", r.filename, t, r.week_start, r.station, target,
                   expected, v(getattr(r, target)))

# %% 3. Cross-table: consolidated column = component table net, per file, week and station (A15 for missing)
# Consolidated columns by its header variant (step 06), each with its component table.
COMPONENTS = {
    "consolidated_9col": [("sras_charges_rs", "SRAS_PAYMENT"), ("shortfall_charges_rs", "TRAS_SHORTFALL"),
                          ("emergency_charges_rs", "TRAS_EMERGENCY"), ("tras_market_charges_rs", "TRAS_MARKET"),
                          ("scuc_charges_rs", "SCUC")],
    "consolidated_8col_combined": [("sras_charges_rs", "SRAS_PAYMENT"),
                                   ("shortfall_emergency_charges_rs", "TRAS_SHORTFALL_EMERGENCY"),
                                   ("tras_market_charges_rs", "TRAS_MARKET"), ("scuc_charges_rs", "SCUC")],
}
variant_of = dict(zip(xlog["instance_id"], xlog["header_variant"]))
present = {t: set(zip(d["filename"], d["week_start"])) for t, d in data.items()}
for r in xlog[xlog["status"] == "present_but_empty"].itertuples():
    present[r.table_type].add((r.filename, r.week_start))
# The "No data found" TRAS-III page (A14): an Emergency table present but empty.
empty_tables = {(r.filename, r.week_start, "TRAS_EMERGENCY") for r in xlog[xlog["status"] == "explicit_no_data"].itertuples()}
cons = data["CONSOLIDATED"]
cross_skipped = defaultdict(int)
for (fn, w), cg in cons.groupby(["filename", "week_start"]):
    variants = {variant_of[i] for i in cg["instance_id"].unique()}
    if len(variants) != 1:
        warn(f"{fn} {w}: consolidated table has mixed header variants {variants}")
        continue
    for col, comp in COMPONENTS[variants.pop()]:
        if (fn, w) not in present[comp] and (fn, w, comp) not in empty_tables:
            cross_skipped[comp] += 1
            continue
        d = data[comp]
        comp_rows = d[(d["filename"] == fn) & (d["week_start"] == w)]
        comp_net = comp_rows.groupby("station")[NET[comp]].sum(min_count=1)
        cons_col = cg.groupby("station")[col].sum(min_count=1)
        for st in sorted(set(cons_col.index) | set(comp_net.index)):
            c_val = cons_col.get(st)
            if st in comp_net.index:
                record("cross_table", fn, comp, w, st, col, comp_net[st], c_val if st in cons_col.index else float("nan"),
                       note="" if st in cons_col.index else "station missing from consolidated")
                if st not in cons_col.index:
                    results[-1]["status"] = "pass" if v(comp_net[st]) == 0 else "fail"
            else:  # station absent from the component table: consolidated must show 0 (A15)
                record("cross_table_missing_zero", fn, comp, w, st, col, 0.0, c_val,
                       note="station absent from component table")
        record("cross_table_total", fn, comp, w, "", col, comp_rows[NET[comp]].sum(), cg[col].sum(),
               n_rows=max(len(comp_rows), len(cg)))

# %% 4. Sanity checks
QUANTITY_COLS = {"TRAS_SHORTFALL": ["up_energy_mwh", "down_energy_mwh"], "TRAS_EMERGENCY": ["up_energy_mwh", "down_energy_mwh"],
                 "TRAS_SHORTFALL_EMERGENCY": ["up_energy_mwh", "down_energy_mwh"],
                 "TRAS_MARKET": ["da_up_cleared_mwh", "da_up_scheduled_mwh", "rt_up_cleared_mwh", "rt_up_scheduled_mwh",
                                 "da_down_scheduled_mwh", "rt_down_scheduled_mwh"],
                 "SCUC": ["increment_mwh", "decrement_mwh"], "SRAS_PAYMENT": ["sras_up_mwh"]}
for t, cols in QUANTITY_COLS.items():
    for r in data[t].itertuples(index=False):
        for c in cols:
            x = getattr(r, c)
            if not pd.isna(x):
                record("sanity_mwh_non_negative", r.filename, t, r.week_start, r.station, c, ">= 0", x,
                       status="pass" if x >= 0 else "fail")
for r in data["SRAS_PAYMENT"].itertuples(index=False):
    x = r.sras_down_mwh
    if not pd.isna(x):
        record("sanity_sras_down_negative", r.filename, "SRAS_PAYMENT", r.week_start, r.station, "sras_down_mwh",
               "<= 0", x, status="pass" if x <= 0 else "fail")
perf = data["SRAS_PERFORMANCE"]
for r in perf.itertuples(index=False):
    x = r.performance_pct
    if not pd.isna(x):
        record("sanity_performance_0_100", r.filename, "SRAS_PERFORMANCE", r.week_start, r.station,
               "performance_pct", "0..100", x, status="pass" if 0 <= x <= 100 else "fail")
for (fn, inst, w), g in perf.groupby(["filename", "instance_id", "week_start"]):
    want = {(date.fromisoformat(w) + timedelta(days=i)).isoformat() for i in range(7)}
    got = set(g["date"])
    record("sanity_performance_dates", fn, "SRAS_PERFORMANCE", w, "", "date", "7 days of week",
           f"{min(got)}..{max(got)} ({len(got)} dates)", status="pass" if got == want else "fail")

# %% 5. Revision differences between consecutive versions (overlays excluded: see overlay preview)
roles05 = pd.read_csv(config.FILE_ROLES_CSV, keep_default_na=False)
overlay_fw_sec = {(r.filename, r.week_start, s) for r in roles05.itertuples()
                  for s in ("TRAS", "SRAS", "SCUC") if getattr(r, f"final_role_{s}").startswith("overlay")}
KEY_FIELDS = {t: [c for c in d.columns if unit(c)] for t, d in data.items()}
KEY_FIELDS["SRAS_PERFORMANCE"] = ["performance_pct"]
diff_rows, rev_summary = [], []


def versions(t, w):
    d = data[t]
    fs = d.loc[d["week_start"] == w, "filename"].unique().tolist()
    sec = SECTION_OF[t]
    fs = [f for f in fs if not (sec and (f, w, sec) in overlay_fw_sec)]
    return sorted(fs, key=lambda f: upload_ts.get(f, ""))


def keyed(t, fn, w):
    d = data[t]
    rows = d[(d["filename"] == fn) & (d["week_start"] == w)]
    k = ["station", "date"] if t == "SRAS_PERFORMANCE" else ["station"]
    return rows.groupby(k)[KEY_FIELDS[t]].sum(min_count=1)


def compare(t, w, old, new, label=None):
    a, b = keyed(t, old, w), keyed(t, new, w)
    added, removed, changed = sorted(set(b.index) - set(a.index)), sorted(set(a.index) - set(b.index)), 0
    for k in added:
        diff_rows.append({"week_start": w, "table_type": label or t, "from_file": old, "to_file": new,
                          "station": k if isinstance(k, str) else " ".join(k), "change": "added", "field": "",
                          "old": None, "new": None, "difference": None})
    for k in removed:
        diff_rows.append({"week_start": w, "table_type": label or t, "from_file": old, "to_file": new,
                          "station": k if isinstance(k, str) else " ".join(k), "change": "removed", "field": "",
                          "old": None, "new": None, "difference": None})
    for k in sorted(set(a.index) & set(b.index)):
        for f in a.columns:
            x, y = a.at[k, f], b.at[k, f]
            if (pd.isna(x) and pd.isna(y)) or (not pd.isna(x) and not pd.isna(y) and abs(x - y) < 1e-9):
                continue
            changed += 1
            diff_rows.append({"week_start": w, "table_type": label or t, "from_file": old, "to_file": new,
                              "station": k if isinstance(k, str) else " ".join(k), "change": "changed", "field": f,
                              "old": x, "new": y, "difference": v(y) - v(x)})
    net = NET.get(t)
    old_total = a[net].sum() if net and net in a.columns else None
    new_total = b[net].sum() if net and net in b.columns else None
    return {"week_start": w, "table_type": label or t, "from_file": old, "to_file": new, "added": len(added),
            "removed": len(removed), "changed_values": changed,
            "net_total_old": old_total, "net_total_new": new_total,
            "net_change_rs": (new_total - old_total) if old_total is not None and new_total is not None else None,
            "identical": not added and not removed and changed == 0}


weeks = sorted(selection["week_start"].unique())
for t in data:
    if t in TRAS3:
        continue  # compared below as one TRAS-III section, so layout changes between versions are handled
    for w in weeks:
        vs = versions(t, w)
        for old, new in zip(vs, vs[1:]):
            rev_summary.append(compare(t, w, old, new))
# TRAS-III as one section: per station, the sum of its TRAS-III nets across the file's TRAS-III tables
tras3 = pd.concat([data[t].assign(table_type=t) for t in TRAS3], ignore_index=True)
data["TRAS_III_TOTAL"] = tras3
KEY_FIELDS["TRAS_III_TOTAL"] = ["net_charges_rs"]
NET["TRAS_III_TOTAL"] = "net_charges_rs"
SECTION_OF["TRAS_III_TOTAL"] = "TRAS"
for w in weeks:
    vs = versions("TRAS_III_TOTAL", w)
    for old, new in zip(vs, vs[1:]):
        rev_summary.append(compare("TRAS_III_TOTAL", w, old, new))
rev = pd.DataFrame(rev_summary)
diffs = pd.DataFrame(diff_rows)

# IPP files (June 2025): SRAS and SCUC identical to the previous version? (A12)
IPP = ["0608202619451321001 trasjun25.3b.pdf", "0608202619453824352 trasjun25.4b.pdf", "0608202612255208753 trasjun25.5b.pdf"]
ipp_check = rev[rev["to_file"].isin(IPP) & rev["table_type"].isin(["SRAS_PAYMENT", "SCUC", "SRAS_PERFORMANCE"])]

# %% 6. Overlay preview: each overlay station's value in the base and in the overlay
preview = []
for (w, sec), ovs in overlays_of.items():
    base = base_of[(w, sec)]
    for ov in ovs:
        for t in [t for t, s in SECTION_OF.items() if s == sec and t in config.EXTRACT_CSV and t != "SRAS_PERFORMANCE"]:
            d = data[t]
            orow = d[(d["filename"] == ov) & (d["week_start"] == w)]
            if orow.empty:
                continue
            brow = d[(d["filename"] == base) & (d["week_start"] == w)].set_index("station")
            for r in orow.itertuples(index=False):
                in_base = r.station in brow.index
                bv = brow.at[r.station, NET[t]] if in_base else None
                preview.append({"week_start": w, "section": sec, "table_type": t, "base": base, "overlay": ov,
                                "station": r.station_raw, "base_value": bv, "overlay_value": getattr(r, NET[t]),
                                "change": (v(getattr(r, NET[t])) - v(bv)) if in_base else None,
                                "in_base_table": in_base, "flags": r.flags if isinstance(r.flags, str) else ""})
                record("overlay_station_in_base", ov, t, w, r.station, NET[t], "station in base table",
                       "present" if in_base else "absent", status="pass" if in_base else "fail")
preview = pd.DataFrame(preview)

# %% 7. Coverage of selected data: every expected table in each week's selected base
cov_rows = []
status_of = {(r.filename, r.table_type): r.status for r in xlog.itertuples()}
title_scope = dict(zip(zip(roles05["filename"], roles05["week_start"]), roles05["title_scope"]))
for w in weeks:
    for sec in ("TRAS", "SRAS", "SCUC"):
        f = base_of[(w, sec)]
        has = {t for t in config.EXTRACT_CSV if (f, w) in present[t]}
        if sec == "TRAS":
            layout = "combined" if "TRAS_SHORTFALL_EMERGENCY" in has else "separate"
            expected = (["TRAS_SHORTFALL_EMERGENCY"] if layout == "combined" else ["TRAS_SHORTFALL", "TRAS_EMERGENCY"]) \
                + ["TRAS_MARKET"]
            if layout == "combined" and has & {"TRAS_SHORTFALL", "TRAS_EMERGENCY"}:
                layout = "mixed"
        elif sec == "SRAS":
            layout, expected = "", ["SRAS_PAYMENT", "SRAS_PERFORMANCE"]
        else:
            layout, expected = "", ["SCUC"]
        expected = expected + ["CONSOLIDATED"]
        for t in expected:
            st = status_of.get((f, t), "")
            if t == "CONSOLIDATED" and (f, w) not in present[t] and title_scope.get((f, w)) == "SRAS_ONLY":
                # An SRAS-only re-issue carries no consolidated table; not a gap in the selected data.
                note, status = "not_in_file_scope (SRAS_ONLY re-issue)", "info"
            elif (f, w) in present[t]:
                note = "present_but_empty" if st == "present_but_empty" else ""
                status = "pass"
            elif (f, w, t) in empty_tables:
                note, status = "explicit_no_data", "pass"
            else:
                note, status = "missing", "fail"
            cov_rows.append({"week_start": w, "section": sec, "base": f, "tras3_layout": layout, "table_type": t,
                             "status": status, "note": note})
            record("coverage_selected", f, t, w, "", "", "table present", note or "present", status=status, note=note)
coverage = pd.DataFrame(cov_rows)

# %% Save
res = pd.DataFrame(results)
res.to_csv(config.VALIDATION_RESULTS_CSV, index=False)
diffs.to_csv(config.REVISION_DIFFS_CSV, index=False)
rev.assign(section=rev["table_type"].map(lambda t: SECTION_OF.get(t) or "CONSOLIDATED")).to_csv(
    config.REVISION_SUMMARY_CSV, index=False)

# %% Summary
pd.set_option("display.width", 250)
pd.set_option("display.max_colwidth", 60)
rel = lambda p: p.relative_to(config.PROJECT_ROOT)


def section(title):
    print(f"\n--- {title} " + "-" * max(0, 66 - len(title)))


print("\n" + "=" * 70)
print("STEP 07 SUMMARY: validation (report only)")
print("=" * 70)
for p in (config.VALIDATION_RESULTS_CSV, config.REVISION_DIFFS_CSV, config.REVISION_SUMMARY_CSV):
    print(f"  -> {rel(p)}")
print(f"Tolerances: ₹{config.ROUNDING_TOL_RS_PER_ROW} and {config.ROUNDING_TOL_MWH_PER_ROW} MWh per row involved")

section("Results per check (selected | superseded)")
res["check_group"] = res["check"].str.split(":").str[0]
tab = pd.crosstab([res["check"]], [res["group"], res["status"]])
print(tab.to_string())

section("Failures beyond tolerance")
fails = res[res["status"] == "fail"]
if fails.empty:
    print("  none")
for (chk, grp), g in fails.groupby(["check", "group"]):
    print(f"\n  {chk} [{grp}]: {len(g)} failures in {g['filename'].nunique()} files")
    show = g.assign(adiff=g["difference"].abs()).sort_values("adiff", ascending=False, na_position="last")
    print(show.head(12)[["filename", "table_type", "week_start", "station", "field", "expected", "actual",
                         "difference", "note"]].to_string(index=False))
    if len(g) > 12:
        print(f"    … {len(g) - 12} more (see {rel(config.VALIDATION_RESULTS_CSV)})")

section("Largest rounding differences")
rd = res[res["status"] == "rounding"].assign(adiff=lambda d: d["difference"].abs())
print(f"  rounding results: {len(rd)}")
if len(rd):
    print(rd.sort_values("adiff", ascending=False).head(10)[["check", "group", "filename", "table_type", "week_start",
                                                               "station", "field", "difference", "n_rows"]]
          .to_string(index=False))

section("Cross-table checks skipped (component table absent from the file for that week)")
print("  " + (", ".join(f"{k}: {v} file-weeks" for k, v in sorted(cross_skipped.items())) or "none"))

section("Revision differences between consecutive versions")
rev["section"] = rev["table_type"].map(lambda t: SECTION_OF.get(t) or "CONSOLIDATED")
revised = rev[~rev["identical"]]
print(f"  version pairs compared: {len(rev)}; identical: {int(rev['identical'].sum())}; with differences: {len(revised)}")
print(f"  weeks with at least one revised table: {revised['week_start'].nunique()}")
print("  by table type (pairs with differences / compared):")
print(rev.groupby("table_type").agg(compared=("identical", "size"), revised=("identical", lambda s: int((~s).sum())),
                                     stations_added=("added", "sum"), stations_removed=("removed", "sum"),
                                     values_changed=("changed_values", "sum")).to_string())
big = revised.dropna(subset=["net_change_rs"]).assign(a=lambda d: d["net_change_rs"].abs()).sort_values("a", ascending=False)
print("\n  Biggest net changes (₹, new total − old total of the table's net column):")
print(big.head(12)[["week_start", "table_type", "from_file", "to_file", "net_total_old", "net_total_new",
                    "net_change_rs", "added", "removed", "changed_values"]].to_string(index=False))
by_ws = revised.dropna(subset=["net_change_rs"]).groupby(["week_start", "section"])["net_change_rs"].sum()
print("\n  Net change per week and section (₹, summed over consecutive versions), largest 15:")
print(by_ws.reindex(by_ws.abs().sort_values(ascending=False).index).head(15).to_string())
print("\n  June 2025 IPP files: SRAS and SCUC vs the previous version (A12):")
print(ipp_check[["week_start", "table_type", "from_file", "to_file", "identical", "added", "removed",
                 "changed_values", "net_change_rs"]].to_string(index=False) if len(ipp_check) else "  no pairs found")

section("Overlay preview (active overlays: base value vs overlay value, net)")
if preview.empty:
    print("  none")
for (w, sec, ov), g in preview.groupby(["week_start", "section", "overlay"]):
    print(f"\n  {w} {sec}: overlay {ov} on base {g['base'].iloc[0]}")
    print(g[["table_type", "station", "base_value", "overlay_value", "change", "in_base_table", "flags"]]
          .to_string(index=False))
nb = preview[~preview["in_base_table"]] if len(preview) else preview
print(f"\n  overlay stations: {len(preview)}; not in the base table: {len(nb)}")
if len(nb):
    print(nb[["week_start", "table_type", "overlay", "station"]].to_string(index=False))

section("Coverage of selected data")
miss = coverage[coverage["status"] == "fail"]
notes = coverage[(coverage["note"] != "") & (coverage["status"] != "fail")]
print(f"  week x section x expected table checks: {len(coverage)}; missing: {len(miss)}")
print("  TRAS base layout by week: " + ", ".join(f"{k}={v}" for k, v in
      coverage[coverage["section"] == "TRAS"].drop_duplicates("week_start")["tras3_layout"].value_counts().items()))
if len(miss):
    print(miss[["week_start", "section", "base", "table_type", "note"]].to_string(index=False))
if len(notes):
    print("  noted:")
    print(notes[["week_start", "section", "base", "table_type", "note"]].to_string(index=False))

print(f"\nWarnings: {len(warnings)}")
for w_ in warnings:
    print(f"  - {w_}")
