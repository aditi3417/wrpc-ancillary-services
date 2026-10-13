"""Step 08: apply the step 05 selection to the step 06 data and build the analysis-ready time series.

For each week x section the selected base file's tables are taken, then the active overlays
are applied in upload_ts order, replacing only the overlay's stations in the table types it
contains (A18 label-conflict tables count as Shortfall). Every fact keeps its source file,
page and whether it came from an overlay. Money follows the project convention (+ = the
generator receives from the pool); down energies are stored as positive quantities with the
printed value kept in value_as_published (A19). Weekly totals are reconciled with the base
file's consolidated table; a failed reconciliation stops the run.
"""

# %% Imports
import re
from collections import defaultdict
from datetime import date, timedelta
from difflib import SequenceMatcher

import pandas as pd

import config

warnings = []


def warn(msg):
    warnings.append(msg)


SECTION_TABLES = {"TRAS": ["TRAS_SHORTFALL", "TRAS_EMERGENCY", "TRAS_SHORTFALL_EMERGENCY", "TRAS_MARKET"],
                  "SRAS": ["SRAS_PAYMENT"], "SCUC": ["SCUC"]}
MECHANISMS = ["SRAS", "TRAS_SHORTFALL", "TRAS_EMERGENCY", "TRAS_III_COMBINED", "TRAS_III_TOTAL",
              "TRAS_MARKET_DAM", "TRAS_MARKET_RTM", "SCUC"]
POOL_FLOW_MECHANISMS = ["SRAS", "TRAS_III_TOTAL", "TRAS_MARKET_DAM", "TRAS_MARKET_RTM", "SCUC"]  # no double count
CRORE = 1e7

# %% Load inputs
data = {t: pd.read_csv(p, dtype={"week_start": str, "station_raw": str, "status_text": str})
        for t, p in config.EXTRACT_CSV.items()}
selection = pd.read_csv(config.SELECTION_CSV, keep_default_na=False)
manifest = pd.read_csv(config.MANIFEST_CSV, keep_default_na=False)
upload_ts = manifest.drop_duplicates("filename").set_index("filename")["upload_ts"]
xlog = pd.read_csv(config.EXTRACTION_LOG_CSV, keep_default_na=False)
weeks = sorted(selection["week_start"].unique())
assert len(weeks) == config.EXPECTED_WEEKS, f"selection has {len(weeks)} weeks, expected {config.EXPECTED_WEEKS}"

# %% Station names: proposals, then the approved master
all_raw = sorted({s for d in data.values() for s in d["station_raw"].dropna().unique()})


def exact_id(name):
    """Proposed station_id: upper case, runs of spaces/underscores -> one underscore."""
    return re.sub(r"[\s_]+", "_", name.strip().upper())


def exact_key(name):
    return re.sub(r"[\s_]+", "", name.upper())


def loose_key(name):
    return re.sub(r"[^A-Z0-9]", "", name.upper())


tables_of = defaultdict(set)
for t, d in data.items():
    for s in d["station_raw"].dropna().unique():
        tables_of[s].add(t)
by_exact = defaultdict(list)
for s in all_raw:
    by_exact[exact_key(s)].append(s)
group_id = {k: exact_id(max(v, key=lambda s: sum(len(data[t]) for t in tables_of[s]))) for k, v in by_exact.items()}
by_loose = defaultdict(set)
for k in by_exact:
    by_loose[loose_key(k)].add(k)

proposals = []
for s in all_raw:
    k = exact_key(s)
    pid, review, reason = group_id[k], False, ""
    looser = by_loose[loose_key(s)] - {k}
    if looser:
        review, reason = True, "groups with " + ", ".join(sorted(group_id[x] for x in looser)) + " only by punctuation"
    # Names that appear only in the SRAS performance table but are a prefix of other stations' names.
    if tables_of[s] == {"SRAS_PERFORMANCE"}:
        cands = sorted({group_id[exact_key(o)] for o in all_raw
                        if o != s and exact_key(o).startswith(exact_key(s)) and tables_of[o] != {"SRAS_PERFORMANCE"}})
        review = True
        reason = (f"only in SRAS_PERFORMANCE; prefix of: {', '.join(cands)}" if cands
                  else "only in SRAS_PERFORMANCE; no name in other tables matches")
    variants = [v for v in by_exact[k] if v != s]
    proposals.append({"station_raw": s, "proposed_station_id": pid, "exact_variants": "; ".join(variants),
                      "tables": ";".join(sorted(tables_of[s])), "needs_review": review, "review_reason": reason})
proposals = pd.DataFrame(proposals)
proposals.to_csv(config.STATION_PROPOSALS_CSV, index=False)

# The master is created once from the confident matches and never overwritten; new names are appended.
config.REFERENCE_DIR.mkdir(parents=True, exist_ok=True)
if config.STATION_MASTER_CSV.exists():
    master = pd.read_csv(config.STATION_MASTER_CSV, keep_default_na=False, dtype=str)
else:
    master = pd.DataFrame(columns=["station_raw", "station_id", "plant_id", "reviewed", "notes"])
if "plant_id" not in master.columns:
    raise SystemExit(f"{config.STATION_MASTER_CSV} has no plant_id column")
new = proposals[~proposals["station_raw"].isin(master["station_raw"])]
if len(new):
    add = pd.DataFrame({
        "station_raw": new["station_raw"],
        # Confident (exact normalisation) -> proposed id, reviewed. Needs review -> its own exact id, not merged.
        "station_id": [exact_id(s) if r else pid for s, pid, r in zip(new["station_raw"], new["proposed_station_id"],
                                                                     new["needs_review"])],
        "reviewed": ["False" if r else "True" for r in new["needs_review"]],
        "notes": [f"needs review: {why}" if r else "exact normalisation (case, spaces, underscores)"
                 for r, why in zip(new["needs_review"], new["review_reason"])],
    })
    add["plant_id"] = add["station_id"]  # plant grouping only when approved later
    master = pd.concat([master, add[master.columns]], ignore_index=True).sort_values("station_raw")
    master.to_csv(config.STATION_MASTER_CSV, index=False)
station_id_of = dict(zip(master["station_raw"], master["station_id"]))
plant_of = dict(zip(master["station_id"], master["plant_id"]))
missing_ids = [s for s in all_raw if s not in station_id_of]
if missing_ids:
    raise SystemExit(f"Station names missing from the master: {missing_ids}")
for d in data.values():
    d["station_id"] = d["station_raw"].map(station_id_of)

# %% Near-duplicate station_ids (report only; nothing is merged)
# Pairs with high string similarity after normalisation, or one name contained in the other. A pair that
# appears in the same table (same file, table type and week) is two distinct stations.
occurs = defaultdict(set)  # station_id -> {(filename, table_type, week_start)}
for t, d in data.items():
    for fn, w, s in zip(d["filename"], d["week_start"], d["station_id"]):
        occurs[s].add((fn, t, w))
norm_id = lambda s: re.sub(r"[^A-Z0-9]", "", s.upper())
ids = sorted(set(station_id_of.values()))
sim_rows = []
for i, a in enumerate(ids):
    for b in ids[i + 1:]:
        na, nb = norm_id(a), norm_id(b)
        ratio = SequenceMatcher(None, na, nb).ratio()
        contained = min(len(na), len(nb)) >= 3 and (na in nb or nb in na)
        if ratio < config.STATION_SIMILARITY_THRESHOLD and not contained:
            continue
        co = occurs[a] & occurs[b]
        wa, wb = {x[2] for x in occurs[a]}, {x[2] for x in occurs[b]}
        sim_rows.append({
            "station_id_a": a, "station_id_b": b, "similarity": round(ratio, 3), "contained": contained,
            "plant_id_a": plant_of[a], "plant_id_b": plant_of[b],
            "co_occurring_tables": len(co), "example_co_occurrence": " | ".join(sorted(co)[0]) if co else "",
            "weeks_a": len(wa), "weeks_b": len(wb), "weeks_overlap": len(wa & wb),
            "tables_a": ";".join(sorted({x[1] for x in occurs[a]})), "tables_b": ";".join(sorted({x[1] for x in occurs[b]})),
            "assessment": "distinct (appear together in the same table)" if co
            else "possible duplicate: never in the same table (review)"})
similarity = pd.DataFrame(sim_rows).sort_values(["co_occurring_tables", "similarity"], ascending=[True, False])
similarity.to_csv(config.STATION_SIMILARITY_CSV, index=False)

# %% Apply the selection: base tables, then active overlays in upload_ts order
selected_rows = {}  # (week, table_type) -> {station_id: row dict with source info}
overlay_changes = defaultdict(float)  # (week, table_type) -> sum of (overlay net - base net)
overlay_applied = defaultdict(list)
NET_FIELD = {"TRAS_SHORTFALL": "net_charges_rs", "TRAS_EMERGENCY": "net_charges_rs",
             "TRAS_SHORTFALL_EMERGENCY": "net_charges_rs", "TRAS_MARKET": "net_charges_rs",
             "SRAS_PAYMENT": "total_charges_rs", "SCUC": "net_charges_rs"}


def rows_of(t, fn, w):
    d = data[t]
    return d[(d["filename"] == fn) & (d["week_start"] == w)]


def num(x):
    return 0.0 if pd.isna(x) else float(x)


for r in selection.itertuples(index=False):
    w, sec, base = r.week_start, r.section, r.base
    for t in SECTION_TABLES[sec]:
        rows = rows_of(t, base, w)
        if rows.empty:
            continue
        dup = rows["station_id"][rows["station_id"].duplicated()].tolist()
        if dup:
            raise SystemExit(f"Duplicate stations in base table {t} {base} {w}: {dup}")
        selected_rows[(w, t)] = {s: {**row, "from_overlay": False} for s, row in
                                 zip(rows["station_id"], rows.to_dict("records"))}
    overlays = sorted([f for f in r.overlays_active.split(";") if f], key=lambda f: upload_ts.get(f, ""))
    for ov in overlays:
        for t in SECTION_TABLES[sec]:
            orows = rows_of(t, ov, w)
            if orows.empty:
                continue
            target = selected_rows.setdefault((w, t), {})
            for row in orows.to_dict("records"):
                s = row["station_id"]
                old = num(target[s][NET_FIELD[t]]) if s in target else 0.0
                if s not in target:
                    warn(f"overlay {ov} adds station {s} not in base {t} {w}")
                overlay_changes[(w, t)] += num(row[NET_FIELD[t]]) - old
                target[s] = {**row, "from_overlay": True}
                overlay_applied[w].append(f"{t}:{s}:{ov}")

# %% Facts (long format)
def thursday_month(w):
    return (date.fromisoformat(w) + timedelta(days=3)).strftime("%Y-%m")


TRAS3_MECH = {"TRAS_SHORTFALL": "TRAS_SHORTFALL", "TRAS_EMERGENCY": "TRAS_EMERGENCY",
              "TRAS_SHORTFALL_EMERGENCY": "TRAS_III_COMBINED"}


def facts_for(t, row):
    """[(mechanism, direction, measure, value, value_as_published, unit, derived)] for one table row."""
    g = lambda c: row.get(c)
    out = []

    def add(mech, direction, measure, published, sign=1.0, absolute=False, derived=False):
        if published is None or pd.isna(published):
            return
        value = abs(published) if absolute else sign * published
        out.append((mech, direction, measure, value, published, "MWh" if measure.endswith("mwh") else "INR", derived))

    if t in TRAS3_MECH:
        m = TRAS3_MECH[t]
        add(m, "up", "energy_mwh", g("up_energy_mwh"))
        add(m, "up", "energy_charges", g("up_charges_rs"))
        add(m, "down", "energy_mwh", g("down_energy_mwh"), absolute=True)
        add(m, "down", "energy_charges", g("down_charges_rs"), sign=-1.0)  # paid back to the pool
        add(m, "", "net_amount", g("net_charges_rs"))
    elif t == "TRAS_MARKET":
        for m, p, (cl, sc, ec, cc, dsc, dch) in (
                ("TRAS_MARKET_DAM", "da", ("da_up_cleared_mwh", "da_up_scheduled_mwh", "da_up_energy_charges_rs",
                                           "da_up_commitment_charges_rs", "da_down_scheduled_mwh", "da_down_charges_rs")),
                ("TRAS_MARKET_RTM", "rt", ("rt_up_cleared_mwh", "rt_up_scheduled_mwh", "rt_up_energy_charges_rs",
                                           "rt_up_commitment_charges_rs", "rt_down_scheduled_mwh", "rt_down_charges_rs"))):
            add(m, "up", "cleared_mwh", g(cl))
            add(m, "up", "energy_mwh", g(sc))
            add(m, "up", "energy_charges", g(ec))
            add(m, "up", "commitment_charges", g(cc))
            add(m, "down", "energy_mwh", g(dsc), absolute=True)
            add(m, "down", "energy_charges", g(dch), sign=-1.0)  # paid back to the pool
            net = num(g(ec)) + num(g(cc)) - num(g(dch))
            out.append((m, "", "net_amount", net, None, "INR", True))  # derived: N is printed for DAM+RTM only
    elif t == "SRAS_PAYMENT":
        add("SRAS", "up", "energy_mwh", g("sras_up_mwh"))
        add("SRAS", "down", "energy_mwh", g("sras_down_mwh"), absolute=True)  # printed negative
        add("SRAS", "net", "energy_mwh", g("net_sras_mwh"))  # 15-min schedule basis, as printed
        add("SRAS", "", "incentive", g("incentive_rs"))
        add("SRAS", "", "energy_charges", g("sras_energy_charges_rs"))
        add("SRAS", "", "net_amount", g("total_charges_rs"))
    elif t == "SCUC":
        add("SCUC", "up", "energy_mwh", g("increment_mwh"))
        add("SCUC", "up", "energy_charges", g("charges_paid_to_generator_rs"))
        add("SCUC", "down", "energy_mwh", g("decrement_mwh"), absolute=True)
        add("SCUC", "down", "energy_charges", g("charges_refunded_by_generator_rs"), sign=-1.0)
        add("SCUC", "", "net_amount", g("net_charges_rs"))
    return out


facts = []
for (w, t), rows in selected_rows.items():
    iso = date.fromisoformat(w).isocalendar()
    for s, row in rows.items():
        for mech, direction, measure, value, published, unit, derived in facts_for(t, row):
            facts.append({"week_start": w, "iso_year": iso.year, "iso_week": iso.week, "month": thursday_month(w),
                          "station_id": s, "plant_id": plant_of[s], "station_raw": row["station_raw"], "mechanism": mech,
                          "direction": direction, "measure": measure, "value": value,
                          "value_as_published": published, "unit": unit, "derived": derived,
                          "source_file": row["filename"], "page": row["page_no"],
                          "from_overlay": row["from_overlay"],
                          "flags": row["flags"] if isinstance(row.get("flags"), str) else ""})
facts = pd.DataFrame(facts)

# Derived TRAS_III_TOTAL for every week: the combined table, or Shortfall + Emergency.
t3 = facts[facts["mechanism"].isin(["TRAS_SHORTFALL", "TRAS_EMERGENCY", "TRAS_III_COMBINED"])]
keys = ["week_start", "iso_year", "iso_week", "month", "station_id", "plant_id", "direction", "measure", "unit"]
agg = t3.groupby(keys, dropna=False).agg(
    value=("value", "sum"), station_raw=("station_raw", "first"),
    source_file=("source_file", lambda s: ";".join(sorted(set(s)))),
    page=("page", lambda s: ";".join(str(x) for x in sorted(set(s)))),
    from_overlay=("from_overlay", "any"), flags=("flags", lambda s: ";".join(sorted({x for x in s if x})))).reset_index()
agg["mechanism"], agg["derived"], agg["value_as_published"] = "TRAS_III_TOTAL", True, None
facts = pd.concat([facts, agg[facts.columns]], ignore_index=True)
facts = facts.sort_values(["week_start", "mechanism", "station_id", "direction", "measure"]).reset_index(drop=True)

# %% Checks: 87 weeks, no duplicates
key_cols = ["week_start", "station_id", "mechanism", "direction", "measure"]
dups = facts[facts.duplicated(key_cols, keep=False)]
if len(dups):
    raise SystemExit(f"Duplicate facts for week x station x mechanism x direction x measure:\n{dups[key_cols].head(20)}")
missing_weeks = sorted(set(weeks) - set(facts["week_start"]))
if missing_weeks:
    raise SystemExit(f"Weeks missing from the facts: {missing_weeks}")

# %% Reconciliation with the base file's consolidated table
cons = data["CONSOLIDATED"]
base_of = {(r.week_start, r.section): r.base for r in selection.itertuples()}
RECON = [("SRAS", "SRAS", ["SRAS_PAYMENT"], "sras_charges_rs"),
         ("TRAS_SHORTFALL", "TRAS", ["TRAS_SHORTFALL"], "shortfall_charges_rs"),
         ("TRAS_EMERGENCY", "TRAS", ["TRAS_EMERGENCY"], "emergency_charges_rs"),
         ("TRAS_III_COMBINED", "TRAS", ["TRAS_SHORTFALL_EMERGENCY"], "shortfall_emergency_charges_rs"),
         ("TRAS_MARKET", "TRAS", ["TRAS_MARKET"], "tras_market_charges_rs"),
         ("SCUC", "SCUC", ["SCUC"], "scuc_charges_rs")]
recon, recon_fail = [], []
for w in weeks:
    for name, sec, tables, col in RECON:
        base = base_of[(w, sec)]
        crow = cons[(cons["filename"] == base) & (cons["week_start"] == w)]
        ours = sum(num(row[NET_FIELD[t]]) for t in tables for row in selected_rows.get((w, t), {}).values())
        base_total = sum(num(x) for t in tables for x in rows_of(t, base, w)[NET_FIELD[t]])
        n_rows = sum(len(selected_rows.get((w, t), {})) for t in tables)
        ov = sum(overlay_changes.get((w, t), 0.0) for t in tables)
        rec = {"week_start": w, "mechanism": name, "base": base, "ours_total": ours, "base_tables_total": base_total,
               "overlay_change": ov, "consolidated_total": None, "difference": None,
               "tolerance": n_rows * config.ROUNDING_TOL_RS_PER_ROW, "status": ""}
        # Overlays must account exactly for the change from the base file's own tables.
        if abs((ours - base_total) - ov) > 1e-6:
            rec["status"] = "fail: overlay change does not account for difference from base tables"
        elif crow.empty:
            rec["status"] = "skipped: base file has no consolidated table"
        else:
            ct = crow[col].sum() if col in crow.columns else 0.0
            ct = 0.0 if pd.isna(ct) else float(ct)
            rec["consolidated_total"] = ct
            # Remove the overlay change before comparing with the base's consolidated table.
            rec["difference"] = (ours - ov) - ct
            ok = abs(rec["difference"]) <= rec["tolerance"] + 1e-9
            rec["status"] = ("pass" if abs(rec["difference"]) < 1e-9 else "rounding") if ok else "fail"
        recon.append(rec)
        if rec["status"].startswith("fail"):
            recon_fail.append(rec)
recon = pd.DataFrame(recon)
if recon_fail:
    print(pd.DataFrame(recon_fail).to_string(index=False))
    raise SystemExit(f"Reconciliation failed for {len(recon_fail)} week x mechanism checks")

# %% Weekly summary
present_empty = {r.week_start for r in xlog[(xlog["status"] == "present_but_empty")].itertuples()
                 if r.filename == base_of.get((r.week_start, "TRAS"))}
explicit_nd = {r.week_start for r in xlog[(xlog["status"] == "explicit_no_data")].itertuples()
               if r.filename == base_of.get((r.week_start, "TRAS"))}
TRAS3_COMPONENTS = ["TRAS_SHORTFALL", "TRAS_EMERGENCY", "TRAS_III_COMBINED"]


def provider_counts(fm):
    """(any, paid): distinct stations with any non-zero value / with a non-zero ₹ net amount."""
    nz = fm[fm["value"].fillna(0) != 0]
    return nz["station_id"].nunique(), nz[nz["measure"] == "net_amount"]["station_id"].nunique()


summ, both_sf_em = [], []
for w in weeks:
    f = facts[facts["week_start"] == w]
    iso = date.fromisoformat(w).isocalendar()
    row = {"week_start": w, "week_end": (date.fromisoformat(w) + timedelta(days=6)).isoformat(),
           "iso_year": iso.year, "iso_week": iso.week, "month": thursday_month(w)}
    for m in MECHANISMS:
        fm = f[f["mechanism"] == m]
        row[f"{m}_up_mwh"] = fm[(fm["direction"] == "up") & (fm["measure"] == "energy_mwh")]["value"].sum()
        row[f"{m}_down_mwh"] = fm[(fm["direction"] == "down") & (fm["measure"] == "energy_mwh")]["value"].sum()
        net = fm[fm["measure"] == "net_amount"]["value"].sum()
        row[f"{m}_net_rs"], row[f"{m}_net_crore"] = net, net / CRORE
        # TRAS_III_TOTAL: distinct stations across its component tables (not a sum, and not the derived total,
        # where a station's Shortfall and Emergency amounts could cancel to zero).
        src = f[f["mechanism"].isin(TRAS3_COMPONENTS)] if m == "TRAS_III_TOTAL" else fm
        row[f"{m}_providers_any"], row[f"{m}_providers_paid"] = provider_counts(src)
        row[f"{m}_available"] = not fm.empty
    total = sum(row[f"{m}_net_rs"] for m in POOL_FLOW_MECHANISMS)
    # + = net amount payable from the pool to AS providers (generators receive); - = net receivable by the pool.
    sf = set(f.loc[f["mechanism"] == "TRAS_SHORTFALL", "station_id"])
    em = set(f.loc[f["mechanism"] == "TRAS_EMERGENCY", "station_id"])
    if sf & em:
        both_sf_em.append({"week_start": w, "stations_in_both": len(sf & em),
                           "stations": ", ".join(sorted(sf & em))})
    row["total_net_payable_from_pool_to_providers_rs"] = total
    row["total_net_payable_from_pool_to_providers_crore"] = total / CRORE
    row["flag_overlay_applied"] = bool(overlay_applied.get(w))
    row["flag_combined_layout"] = bool((f["mechanism"] == "TRAS_III_COMBINED").any())
    row["flag_empty_market_table"] = w in present_empty
    row["flag_explicit_no_data"] = w in explicit_nd
    summ.append(row)
weekly = pd.DataFrame(summ)
assert len(weekly) == config.EXPECTED_WEEKS and weekly["week_start"].is_unique

# %% SRAS performance daily (selected SRAS base, as published)
perf = data["SRAS_PERFORMANCE"]
prows = []
for w in weeks:
    b = base_of[(w, "SRAS")]
    p = perf[(perf["filename"] == b) & (perf["week_start"] == w)]
    sid = p["station_raw"].map(station_id_of)
    prows.append(p.assign(station_id=sid, plant_id=sid.map(plant_of)))
perf_daily = pd.concat(prows, ignore_index=True)[["date", "week_start", "station_id", "plant_id", "station_raw",
                                                    "performance_pct",
                                                    "filename", "page_no"]].rename(columns={"filename": "source_file",
                                                                                          "page_no": "page"})
perf_daily["above_100"] = perf_daily["performance_pct"] > 100
dupp = perf_daily[perf_daily.duplicated(["date", "station_id"], keep=False)]
if len(dupp):
    raise SystemExit(f"Duplicate SRAS performance station x date rows:\n{dupp.head()}")

# %% Save
facts.to_csv(config.FACTS_CSV, index=False)
weekly.to_csv(config.WEEKLY_SUMMARY_CSV, index=False)
perf_daily.to_csv(config.SRAS_PERFORMANCE_DAILY_CSV, index=False)

# %% Summary
pd.set_option("display.width", 250)
pd.set_option("display.max_colwidth", 70)
rel = lambda p: p.relative_to(config.PROJECT_ROOT)


def section(title):
    print(f"\n--- {title} " + "-" * max(0, 66 - len(title)))


print("\n" + "=" * 70)
print("STEP 08 SUMMARY: time series")
print("=" * 70)
for p in (config.FACTS_CSV, config.WEEKLY_SUMMARY_CSV, config.SRAS_PERFORMANCE_DAILY_CSV,
          config.STATION_PROPOSALS_CSV, config.STATION_SIMILARITY_CSV, config.STATION_MASTER_CSV):
    print(f"  -> {rel(p)}")

section("Reconciliation with the base file's consolidated table")
print(pd.crosstab(recon["mechanism"], recon["status"]).to_string())
ow = recon[recon["overlay_change"] != 0]
print(f"\n  week x mechanism checks with overlays: {len(ow)} (overlay change accounted for exactly in all)")
if len(ow):
    print(ow[["week_start", "mechanism", "base_tables_total", "overlay_change", "ours_total", "consolidated_total",
              "difference"]].to_string(index=False))
rd = recon.dropna(subset=["difference"])
print(f"  largest |difference| vs consolidated (rounding): {rd['difference'].abs().max():.2f}")
print(f"  weeks: {facts['week_start'].nunique()} of {config.EXPECTED_WEEKS}; duplicate keys: 0")
sk = recon[recon["status"].str.startswith("skipped")]
if len(sk):
    print("  skipped:")
    print(sk[["week_start", "mechanism", "base", "status"]].to_string(index=False))

section("Station names for your review")
rv = proposals[proposals["needs_review"]]
reviewed_raw = set(master.loc[master["reviewed"] == "True", "station_raw"])
unresolved = master[master["reviewed"] != "True"]
print(f"  distinct station_raw: {len(proposals)}; proposed station_ids: {proposals['proposed_station_id'].nunique()}")
print(f"  flagged by automatic proposals: {len(rv)}; resolved in station master: "
      f"{int(rv['station_raw'].isin(reviewed_raw).sum())}; unresolved: {len(unresolved)}")
rv = rv.assign(master_reviewed=rv["station_raw"].isin(reviewed_raw),
               master_plant_id=rv["station_raw"].map(station_id_of).map(plant_of))
print(rv[["station_raw", "proposed_station_id", "master_plant_id", "master_reviewed", "review_reason"]]
      .to_string(index=False) if len(rv) else "  none")
if len(unresolved):
    print("  unresolved in the master (reviewed=False):")
    print(unresolved[["station_raw", "station_id", "plant_id", "notes"]].to_string(index=False))
print(f"  Master: {rel(config.STATION_MASTER_CSV)} ({len(master)} rows; reviewed=True {int((master['reviewed'] == 'True').sum())})."
      " Edit station_id and set reviewed=True to approve a merge; the master is never overwritten.")

section("Near-duplicate station_ids (report only; nothing merged)")
print(f"  pairs with similarity >= {config.STATION_SIMILARITY_THRESHOLD} or one name contained in the other: {len(similarity)}"
      f"; never in the same table: {int((similarity['co_occurring_tables'] == 0).sum())}")
print(similarity[["station_id_a", "station_id_b", "similarity", "contained", "co_occurring_tables", "weeks_a", "weeks_b",
                  "weeks_overlap", "plant_id_a", "plant_id_b", "assessment"]].to_string(index=False))

section("Stations per mechanism (with a non-zero value in at least one week)")
nz = facts[facts["value"].fillna(0) != 0]
print(nz.groupby("mechanism")["station_id"].nunique().reindex(MECHANISMS).to_string())

section("Totals per year (₹ crore, net amount; + = generators receive)")
yr = facts[facts["measure"] == "net_amount"].assign(
    period=lambda d: d["iso_year"].map({2025: "CY2025", 2026: "CY2026 to Aug"}))
print((yr.pivot_table(index="mechanism", columns="period", values="value", aggfunc="sum") / CRORE)
      .reindex(MECHANISMS).round(2).to_string())
pool = weekly.groupby(weekly["iso_year"].map({2025: "CY2025", 2026: "CY2026 to Aug"}))[
    "total_net_payable_from_pool_to_providers_crore"].sum()
print("  total net payable from pool to AS providers (SRAS + TRAS-III total + market + SCUC): "
      + ", ".join(f"{k} {v:,.2f}" for k, v in pool.items()))

section("5 largest weeks by total net payable from pool to AS providers (₹ crore)")
top = weekly.reindex(weekly["total_net_payable_from_pool_to_providers_rs"].abs().sort_values(ascending=False).index).head(5)
print(top[["week_start", "total_net_payable_from_pool_to_providers_crore"] + [f"{m}_net_crore" for m in POOL_FLOW_MECHANISMS]]
      .round(2).to_string(index=False))

section("Providers per week: any non-zero value vs non-zero ₹ net amount")
both = pd.DataFrame(both_sf_em)
print(f"  weeks where a station appears in both the Shortfall and Emergency tables: {len(both)} of "
      f"{int(weekly['TRAS_SHORTFALL_available'].sum())} separate-layout weeks")
if len(both):
    print(f"  stations in both per week: min {both['stations_in_both'].min()}, median {both['stations_in_both'].median():.0f},"
          f" max {both['stations_in_both'].max()}")
    print(both[["week_start", "stations_in_both"]].to_string(index=False))
w_ = weekly[weekly["week_start"] == "2025-12-29"]
if len(w_):
    print("\n  2025-12-29:")
    print(pd.DataFrame({m: [int(w_[f"{m}_providers_any"].iloc[0]), int(w_[f"{m}_providers_paid"].iloc[0])]
                        for m in MECHANISMS}, index=["providers_any", "providers_paid"]).T.to_string())

section("Flags")
print(f"  overlay weeks: {int(weekly['flag_overlay_applied'].sum())}; combined-layout weeks: "
      f"{int(weekly['flag_combined_layout'].sum())}; empty market table: {sorted(present_empty)}; "
      f"explicit no data: {sorted(explicit_nd)}")
print(f"  SRAS performance values above 100%: {int(perf_daily['above_100'].sum())}")

section("3 sample rows from each output")
for name, df in (("08_facts", facts), ("08_weekly_summary", weekly), ("08_sras_performance_daily", perf_daily),
                 ("08_station_name_proposals", proposals)):
    print(f"\n  {name} ({len(df)} rows, {df.shape[1]} columns)")
    sample = df.sample(3, random_state=1)
    if name == "08_weekly_summary":
        sample = sample[["week_start", "month", "SRAS_net_crore", "TRAS_III_TOTAL_net_crore", "TRAS_MARKET_DAM_net_crore",
                         "SCUC_net_crore", "total_net_payable_from_pool_to_providers_crore", "TRAS_III_TOTAL_providers_any",
                         "TRAS_III_TOTAL_providers_paid",
                         "flag_overlay_applied", "flag_combined_layout"]]
    print(sample.to_string(index=False))

print(f"\nWarnings: {len(warnings)}")
for w_ in warnings:
    print(f"  - {w_}")
