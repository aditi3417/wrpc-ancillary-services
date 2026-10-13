"""Step 06: extract every statement table into tidy datasets, values exactly as published.

Reads the cell-based tables from step 04 (04_tables.jsonl, src/table_reader.py) for
all 127 downloaded files. Columns are mapped to standard field names by matching the
normalised header text (case, spaces and hyphens ignored) against explicit templates
for each known header variant; a header that matches no template stops the run.
The step 05 selection and overlays are NOT applied, stations are not renamed and
signs are not changed. Printed TOTAL rows go to a separate file for step 07.
"""

# %% Imports
import json
import re
from collections import defaultdict
from datetime import date, datetime

import pandas as pd

import config
from src.table_reader import row_number, show

warnings = []


def warn(msg):
    warnings.append(msg)


def join(items):
    return ";".join(str(i) for i in items)


# %% Header templates
def key(cell):
    """Normalised header text: no case, spaces, hyphens or line breaks."""
    return re.sub(r"[\s\-]", "", cell.replace("\n", "")).lower()


DATE_KEY = "<date>"  # an SRAS performance date column, e.g. 29-Dec-25
DATE_KEY_RE = re.compile(r"^\d{2}[a-z]{3}\d{2}$")

TRAS3_FIELDS = ["sr_no", "station_raw", "up_energy_mwh", "up_charges_rs", "down_energy_mwh", "down_charges_rs",
                "net_charges_rs", "status_text"]


def tras3_header(cond):
    return [["sr.no.", "trasprovidername",
             f"energyscheduledunder{cond}trasup(mwh)(a)",
             f"totalcharges/compensationchargesfor{cond}trasup(rs)(b)",
             f"energyscheduledunder{cond}trasdown(mwh)(c)",
             f"totalcharges/compensationchargesfor{cond}trasdowntobepaidbacktopool(rs)(d)",
             "netcharges(rs)(e)=(b)(d)",
             "payablebytrasproviders()orreceivabletotrasproviders(+)"]]


# Each variant: header rows (normalised cells, "" for an empty cell), field per column, flags.
TEMPLATES = {
    "TRAS_SHORTFALL": [
        {"variant": "shortfall", "header": tras3_header("shortfall"), "fields": TRAS3_FIELDS, "flags": []},
        # Heading says "Shortfall Condition" but the columns say Emergency (trasjun26.3a, trasjun26.2a).
        {"variant": "shortfall_heading_emergency_columns", "header": tras3_header("emergency"),
         "fields": TRAS3_FIELDS, "flags": ["header_label_conflict"]},
    ],
    "TRAS_EMERGENCY": [
        {"variant": "emergency", "header": tras3_header("emergency"), "fields": TRAS3_FIELDS, "flags": []},
        {"variant": "emergency_with_week_column",
         "header": [[c for c in tras3_header("emergency")[0][:1]] + ["week"] + tras3_header("emergency")[0][1:]],
         "fields": ["sr_no", "week_text"] + TRAS3_FIELDS[1:], "flags": []},
    ],
    "TRAS_SHORTFALL_EMERGENCY": [
        {"variant": "combined", "header": tras3_header("shortfall/emergency"), "fields": TRAS3_FIELDS, "flags": []},
    ],
    "TRAS_MARKET": [
        {"variant": "market",
         "header": [
             ["sr.", "trasprovider", "trasupindayaheadasmarket", "", "", "", "trasupinrealtimeasmarket", "", "", "",
              "totalcharges/compensationchargesfortrasup(rs.)", "trasdownindayaheadasmarket", "",
              "trasdowninrealtimeasmarket", "",
              "netcharges(rs.)(+)payablefrompooltoasprovider()receivablebypoolfromasprovider"],
             ["", "", "trasupcleared(mwh)", "trasupenergyscheduled(mwh)", "trasupenergycharges(rs.)",
              "trasupcommitmentcharges(rs.)", "trasupcleared(mwh)", "trasupenergyscheduled(mwh)",
              "trasupenergycharges(rs.)", "trasupcommitmentcharges(rs.)", "", "trasdownenergyscheduled(mwh)",
              "trasdownchargestobepaidbacktopool(rs.)", "trasdownenergyscheduled(mwh)",
              "trasdownchargestobepaidbacktopool(rs.)", ""],
             ["", "", "a", "b", "c", "d", "e", "f", "g", "h", "i=c+d+g+h", "j", "k", "l", "m", "n=ikm"]],
         "fields": ["sr_no", "station_raw",
                    "da_up_cleared_mwh", "da_up_scheduled_mwh", "da_up_energy_charges_rs", "da_up_commitment_charges_rs",
                    "rt_up_cleared_mwh", "rt_up_scheduled_mwh", "rt_up_energy_charges_rs", "rt_up_commitment_charges_rs",
                    "up_total_charges_rs", "da_down_scheduled_mwh", "da_down_charges_rs",
                    "rt_down_scheduled_mwh", "rt_down_charges_rs", "net_charges_rs"],
         "flags": []},
    ],
    "SRAS_PAYMENT": [
        {"variant": "sras_payment",
         "header": [
             ["sr.no.", "srasprovidername", "totalsrasupregulation(mwh)(basedon5minschedule)",
              "totalsrasdownregulation(mwh)(basedon5minschedule)", "incentive(rs.)",
              "totalnetsras(mwh)(basedon15minschedule)", None, None, "payabletopool/receivablefrompool"],
             ["a", "b", "c", "d", "e", "f", "g", "h", "i"]],
         "fields": ["sr_no", "station_raw", "sras_up_mwh", "sras_down_mwh", "incentive_rs", "net_sras_mwh",
                    "sras_energy_charges_rs", "total_charges_rs", "status_text"],
         "flags": []},
    ],
    "SCUC": [
        {"variant": "scuc",
         "header": [["sr.no.", "scucgenerator", "incrementduetoscucschedule[western](mwhr)(a)",
                     "decrementduetoscucschedule[western](mwhr)(b)",
                     "chargestobepaidtoscucgenerator(inrs)(c)=(a)xv.c.",
                     "chargestoberefundedbyscucgenerators(inrs)(d)=(b)xv.c.",
                     "netchargespayableforscuc(+)/receivable()(inrs)(e)*=(c)(d)", "payable/receivable"]],
         "fields": ["sr_no", "station_raw", "increment_mwh", "decrement_mwh", "charges_paid_to_generator_rs",
                    "charges_refunded_by_generator_rs", "net_charges_rs", "status_text"],
         "flags": []},
    ],
    "CONSOLIDATED": [
        {"variant": "consolidated_9col",
         "header": [["sr.no.", "scuc/shortfall/emergency/scucgenerator", "srastotalcharges(rs.)(a)",
                     "shortfallconditioncharges(rs.)(b)", "emergencyconditioncharges(rs.)(b)",
                     "netchargespayablefortras(dayaheadandrealtimemarket)(rs.)(c)",
                     "netchargespayableforscuc(rs.)(d)",
                     "netchargespayable()/receivable(+)bythegenerator(a)+(b)+(c)+(d)(rs)",
                     "payable()(payabletothepoolaccount)/receivable(+)(receivablefrompoolaccount)"]],
         "fields": ["sr_no", "station_raw", "sras_charges_rs", "shortfall_charges_rs", "emergency_charges_rs",
                    "tras_market_charges_rs", "scuc_charges_rs", "net_charges_rs", "status_text"],
         "flags": []},
        {"variant": "consolidated_8col_combined",
         "header": [["sr.no.", "scuc/shortfall/emergency/scucgenerator", "srastotalcharges(rs.)(a)",
                     "shortfall/emergencyconditioncharges(rs.)(b)",
                     "netchargespayablefortras(dayaheadandrealtimemarket)(rs.)(c)",
                     "netchargespayableforscuc(rs.)(d)",
                     "netchargespayable()/receivable(+)bythegenerator(a)+(b)+(c)+(d)(rs)",
                     "payable()(payabletothepoolaccount)/receivable(+)(receivablefrompoolaccount)"]],
         "fields": ["sr_no", "station_raw", "sras_charges_rs", "shortfall_emergency_charges_rs",
                    "tras_market_charges_rs", "scuc_charges_rs", "net_charges_rs", "status_text"],
         "flags": []},
    ],
    "SRAS_PERFORMANCE": [
        {"variant": "sras_performance", "header": [["srasprovider"] + [DATE_KEY] * 7 + [""]],
         "fields": ["station_raw"] + [f"day_{i}" for i in range(1, 8)] + ["blank_column"], "flags": []},
    ],
}
# SRAS_PAYMENT columns G and H carry long headers with printed formula references; they are matched
# by prefix (None above) and checked here, so the printed text can be reported as published.
SRAS_PAYMENT_PREFIXES = {6: "srasenergycharges(rs.)", 7: "totalcharges(rs)"}
TEXT_FIELDS = {"sr_no", "station_raw", "status_text", "week_text", "blank_column"}


def match_variant(table_type, header_rows):
    """Return the template whose header rows equal the table's (normalised), or None."""
    keys = [[key(c) for c in r["raw_cells"]] for r in header_rows]
    for tpl in TEMPLATES[table_type]:
        h = tpl["header"]
        if len(h) != len(keys) or any(len(a) != len(b) for a, b in zip(h, keys)):
            continue
        ok = True
        for trow, krow in zip(h, keys):
            for ci, (tcell, kcell) in enumerate(zip(trow, krow)):
                if tcell is None:
                    ok &= kcell.startswith(SRAS_PAYMENT_PREFIXES.get(ci, "\0"))
                elif tcell == DATE_KEY:
                    ok &= bool(DATE_KEY_RE.match(kcell))
                else:
                    ok &= tcell == kcell
        if ok:
            return tpl
    return None


# %% Value parsing
NUM_RE = re.compile(r"^-?\d+(?:\.\d+)?$")


def parse_value(text):
    """Return (value, failed). "-" or blank -> (None, False); unparseable -> (None, True)."""
    t = text.strip()
    if t in ("", "-"):
        return None, False
    t2 = t.replace(",", "")
    if NUM_RE.match(t2):
        return float(t2), False
    return None, True


def parse_week_text(text):
    """Week column of the TPCL table: 'dd.mm.yyyy to dd.mm.yyyy' -> Monday date, or None."""
    m = re.match(r"^(\d{1,2})\.(\d{1,2})\.(\d{4})\s*to\s*(\d{1,2})\.(\d{1,2})\.(\d{4})$", text.strip())
    if not m:
        return None
    s = date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
    e = date(int(m.group(6)), int(m.group(5)), int(m.group(4)))
    return s if s.weekday() == 0 and (e - s).days == 6 else None


def parse_header_date(text):
    try:
        return datetime.strptime(text.strip(), "%d-%b-%y").date()
    except ValueError:
        return None


# %% Load inputs
tables = [json.loads(l) for l in open(config.TABLES_JSONL, encoding="utf-8")]
pages = pd.read_csv(config.PAGE_INVENTORY_CSV, keep_default_na=False)
page_weeks = {(r.filename, r.page_no): [w for w in r.weeks_printed.split(";") if w] for r in pages.itertuples()}
manifest = pd.read_csv(config.MANIFEST_CSV, keep_default_na=False)
manifest_weeks = manifest[manifest["week_start"] != ""].groupby("filename")["week_start"].agg(lambda s: sorted(set(s)))
n_weeks_in_file = manifest.groupby("filename")["n_weeks_in_file"].first().astype(int)
log03 = pd.read_csv(config.DOWNLOAD_LOG_CSV, keep_default_na=False)
all_files = set(log03.loc[log03["status"].isin(["ok", "skipped_existing"]), "filename"])
instances04 = pd.read_csv(config.TABLE_INSTANCES_CSV, keep_default_na=False)

tables.sort(key=lambda t: (t["filename"], t["page_no"], t["table_index"]))
for t in tables:
    t["table_id"] = f"{t['filename']}|p{t['page_no']}|t{t['table_index']}"

# %% Group tables into instances (a table continuing over pages is one instance)
# New instance: different file or type, a page gap, a different printed week, or row numbering
# restarting at 1. SRAS_PERFORMANCE has no row numbers: a new one starts on each table with a header.
instances = []
cur = None
for t in tables:
    nums = [row_number(r) for r in t["rows"] if row_number(r) is not None]
    week = join(page_weeks.get((t["filename"], t["page_no"]), []))
    has_header = any(r["kind"] == "header" for r in t["rows"])
    new = (cur is None or cur["filename"] != t["filename"] or cur["table_type"] != t["table_type"]
           or t["page_no"] - cur["last_page"] > 1
           or (week and cur["week"] and week != cur["week"])
           or (nums and nums[0] == 1)
           or (t["table_type"] == "SRAS_PERFORMANCE" and has_header))
    if new:
        cur = {"filename": t["filename"], "table_type": t["table_type"], "tables": [], "week": week,
               "last_page": t["page_no"]}
        instances.append(cur)
    cur["tables"].append(t)
    cur["last_page"] = t["page_no"]
    cur["week"] = cur["week"] or week
for i, inst in enumerate(instances, start=1):
    inst["instance_id"] = f"I{i:04d}"

# %% Extract rows
records = defaultdict(list)  # table_type -> rows
totals, log_rows, other_rows, parse_failures = [], [], [], []
for inst in instances:
    fn, ttype = inst["filename"], inst["table_type"]
    flags, tpl = [], None
    # Header: every table with its own header must match a template; headerless continuations
    # use the instance's template if their column count matches.
    for t in inst["tables"]:
        hdr = [r for r in t["rows"] if r["kind"] == "header"]
        if hdr:
            m = match_variant(ttype, hdr)
            if m is None:
                raise SystemExit(f"Unknown header for {ttype} in {fn} page {t['page_no']}:\n  "
                                 + "\n  ".join(show(r["raw_cells"]) for r in hdr))
            if tpl is not None and m["variant"] != tpl["variant"]:
                raise SystemExit(f"Header variant changes inside one table instance: {fn} page {t['page_no']}")
            tpl = m
        t["_tpl"] = tpl
    if tpl is None:
        raise SystemExit(f"No header found for {ttype} table instance in {fn} pages "
                         f"{[t['page_no'] for t in inst['tables']]}")
    for t in inst["tables"]:
        t["_tpl"] = tpl
        ncols = {len(r["raw_cells"]) for r in t["rows"]}
        if ncols != {len(tpl["fields"])}:
            raise SystemExit(f"Column count {sorted(ncols)} does not match header variant {tpl['variant']} "
                             f"({len(tpl['fields'])} columns): {fn} page {t['page_no']}")
    flags += tpl["flags"]

    # Week assignment
    mweeks = manifest_weeks.get(fn, [])
    printed = sorted({w for t in inst["tables"] for w in page_weeks.get((fn, t["page_no"]), [])})
    if tpl["variant"] == "emergency_with_week_column":
        week_source, inst_week = "week_column", None
    elif n_weeks_in_file.get(fn, 1) > 1 and len(printed) == 1:
        week_source, inst_week = "page_week_split", printed[0]
    elif len(printed) == 1:
        week_source, inst_week = "printed_in_section", printed[0]
    elif not printed and len(mweeks) == 1:
        week_source, inst_week = "manifest", mweeks[0]
    else:
        raise SystemExit(f"Cannot assign a week to {ttype} in {fn} pages "
                         f"{[t['page_no'] for t in inst['tables']]}: printed {printed}, manifest {mweeks}")
    if inst_week and inst_week not in mweeks:
        flags.append("week_not_in_manifest")

    fields = tpl["fields"]
    n_data, nums, n_total, n_other = 0, [], 0, 0
    perf_dates = None
    if ttype == "SRAS_PERFORMANCE":
        hdr = next(r for t in inst["tables"] for r in t["rows"] if r["kind"] == "header")
        perf_dates = [parse_header_date(c) for c in hdr["cells"][1:8]]
        if any(d is None for d in perf_dates):
            raise SystemExit(f"Unreadable SRAS performance date header in {fn}: {hdr['cells']}")
        if inst_week and (perf_dates[0].isoformat() != inst_week or (perf_dates[6] - perf_dates[0]).days != 6):
            flags.append("performance_dates_not_assigned_week")

    for t in inst["tables"]:
        for r in t["rows"]:
            base = {"filename": fn, "page_no": t["page_no"], "table_id": t["table_id"],
                    "instance_id": inst["instance_id"], "row_index": r["row_index"]}
            if r["kind"] == "header":
                continue
            if r["kind"] == "total":
                n_total += 1
                for f, cell, rawc in zip(fields, r["cells"], r["raw_cells"]):
                    if f in TEXT_FIELDS:
                        continue
                    v, failed = parse_value(cell)
                    totals.append({**base, "table_type": ttype, "week_start": inst_week or "",
                                   "field": f, "value": v, "raw_text": rawc, "parse_failed": failed})
                continue
            if r["kind"] != "data":
                n_other += 1
                other_rows.append({**base, "table_type": ttype, "kind": r["kind"], "cells": show(r["raw_cells"])})
                continue
            n_data += 1
            rec = {**base, "row_no": None, "week_start": inst_week or "", "week_source": week_source}
            failures, row_flags = [], []
            for f, cell, rawc in zip(fields, r["cells"], r["raw_cells"]):
                if f == "sr_no":
                    rec["row_no"] = int(cell) if cell.isdigit() else None
                elif f in ("station_raw", "status_text", "week_text"):
                    rec[f] = cell
                elif f == "blank_column":
                    if cell.strip():
                        row_flags.append(f"unexpected_text_in_blank_column:{cell!r}")
                else:
                    v, failed = parse_value(cell)
                    rec[f] = v
                    if failed:
                        failures.append(f"{f}={rawc!r}")
                        parse_failures.append({**base, "table_type": ttype, "field": f, "raw_text": rawc})
            if rec["row_no"] is not None:
                nums.append(rec["row_no"])
            if week_source == "week_column":
                w = parse_week_text(rec.get("week_text", ""))
                rec["week_start"] = w.isoformat() if w else ""
                if not w:
                    row_flags.append("week_text_unparsed")
                elif w.isoformat() not in mweeks:
                    row_flags.append("week_not_in_manifest")
            rec["parse_failures"] = join(failures)
            rec["flags"] = join(tpl["flags"] + row_flags)
            if ttype == "SRAS_PERFORMANCE":
                for i, d in enumerate(perf_dates, start=1):
                    records[ttype].append({**{k: v for k, v in rec.items() if not k.startswith("day_")},
                                           "date": d.isoformat(), "performance_pct": rec[f"day_{i}"]})
            else:
                records[ttype].append(rec)

    n = max(nums) if nums else 0
    rows_ok = (ttype == "SRAS_PERFORMANCE") or (sorted(nums) == list(range(1, n + 1)) and n_data == n)
    status = "extracted" if n_data else "present_but_empty"
    log_rows.append({
        "instance_id": inst["instance_id"], "filename": fn, "table_type": ttype,
        "pages": join(sorted({t["page_no"] for t in inst["tables"]})), "table_ids": join(t["table_id"] for t in inst["tables"]),
        "header_variant": tpl["variant"], "week_start": inst_week or "(per row)", "week_source": week_source,
        "rows_extracted": n_data, "row_numbers_max": n if ttype != "SRAS_PERFORMANCE" else "",
        "row_numbers_1_to_n": rows_ok if ttype != "SRAS_PERFORMANCE" else "not_numbered",
        "printed_total_rows": n_total, "other_rows": n_other, "status": status,
        "flags": join(dict.fromkeys(flags + ([] if rows_ok else ["row_numbers_not_1_to_n"]))),
    })

# Explicit "No data found" pages (no table to extract)
for r in pages[pages["explicit_no_data"].astype(str) == "True"].itertuples():
    log_rows.append({
        "instance_id": f"N{r.page_no:03d}", "filename": r.filename, "table_type": r.no_data_table_type,
        "pages": str(r.page_no), "table_ids": "", "header_variant": "", "week_start": r.weeks_printed,
        "week_source": "printed_in_section", "rows_extracted": 0, "row_numbers_max": "",
        "row_numbers_1_to_n": "", "printed_total_rows": 0, "other_rows": 0, "status": "explicit_no_data",
        "flags": join(x for x in ("explicit_no_data", r.no_data_type_hint) if x),
    })
log = pd.DataFrame(log_rows)

# %% Save
datasets = {t: pd.DataFrame(records[t]) for t in config.EXTRACT_CSV}
for t, path in config.EXTRACT_CSV.items():
    datasets[t].to_csv(path, index=False)
totals_df = pd.DataFrame(totals)
totals_df.to_csv(config.PRINTED_TOTALS_CSV, index=False)
log.to_csv(config.EXTRACTION_LOG_CSV, index=False)

# %% Summary
pd.set_option("display.width", 250)
pd.set_option("display.max_colwidth", 70)
rel = lambda p: p.relative_to(config.PROJECT_ROOT)


def section(title):
    print(f"\n--- {title} " + "-" * max(0, 66 - len(title)))


print("\n" + "=" * 70)
print("STEP 06 SUMMARY: extract tables")
print("=" * 70)
for p in list(config.EXTRACT_CSV.values()) + [config.PRINTED_TOTALS_CSV, config.EXTRACTION_LOG_CSV]:
    print(f"  -> {rel(p)}")

section("Rows and table instances per type")
ext = log[log["status"] != "explicit_no_data"]
summary = ext.groupby("table_type").agg(instances=("instance_id", "size"), rows=("rows_extracted", "sum"),
                                        files=("filename", "nunique"))
summary["dataset_rows"] = [len(datasets[t]) for t in summary.index]
print(summary.to_string())
print("  (SRAS_PERFORMANCE dataset rows are station x date)")
print(f"  files with at least one table: {ext['filename'].nunique()} of {len(all_files)}; "
      f"files without: {sorted(all_files - set(ext['filename'])) or 'none'}")
print(f"  tables in 04_tables.jsonl: {len(tables)}; tables used: {sum(len(i['tables']) for i in instances)}")

section("Table instances per file vs step 04 (SRAS_PERFORMANCE excluded: not numbered in step 04)")
mine = ext[ext["table_type"] != "SRAS_PERFORMANCE"].groupby(["filename", "table_type"]).size()
theirs = instances04.groupby(["filename", "table_type"]).size()
cmp = pd.concat([mine.rename("step06"), theirs.rename("step04")], axis=1).fillna(0).astype(int)
bad = cmp[cmp["step06"] != cmp["step04"]]
print(f"  file x type pairs compared: {len(cmp)}; mismatches: {len(bad)}")
if len(bad):
    print(bad.to_string())
per_file = cmp.groupby(level=0).sum()
print(f"  files compared: {len(per_file)}; files whose instance count differs: {int((per_file['step06'] != per_file['step04']).sum())}")

section("Data rows equal row numbers 1..N")
exc = log[(log["row_numbers_1_to_n"].astype(str) == "False")]
print(f"  numbered instances: {int((log['row_numbers_1_to_n'].astype(str).isin(['True', 'False'])).sum())}; exceptions: {len(exc)}")
if len(exc):
    print(exc[["filename", "table_type", "pages", "rows_extracted", "row_numbers_max"]].to_string(index=False))
oth = pd.DataFrame(other_rows)
print(f"  rows that are neither header, data nor TOTAL: {len(oth)}")
if len(oth):
    print(oth[["filename", "page_no", "table_type", "kind", "cells"]].to_string(index=False))

section("Numeric parse failures")
pf = pd.DataFrame(parse_failures)
print(pf[["filename", "page_no", "table_type", "field", "raw_text"]].to_string(index=False) if len(pf) else "  none")
tpf = totals_df[totals_df["parse_failed"]] if len(totals_df) else totals_df
if len(tpf):
    print("  in printed TOTAL rows:")
    print(tpf[["filename", "page_no", "table_type", "field", "raw_text"]].to_string(index=False))

section("Sign check: net amount vs status text")
NET_FIELD = {"TRAS_SHORTFALL": "net_charges_rs", "TRAS_EMERGENCY": "net_charges_rs",
             "TRAS_SHORTFALL_EMERGENCY": "net_charges_rs", "SCUC": "net_charges_rs",
             "CONSOLIDATED": "net_charges_rs", "SRAS_PAYMENT": "total_charges_rs"}
sign_rows, checked = [], 0
for t, f in NET_FIELD.items():
    d = datasets[t]
    for r in d.itertuples(index=False):
        s = str(getattr(r, "status_text") or "").lower()
        v = getattr(r, f)
        rec, pay = "receivable" in s, "payable" in s
        if rec == pay:  # both or neither: "-", blank, or ambiguous text
            expect = "zero_or_empty" if s.strip() in ("", "-") else None
        else:
            expect = "positive" if rec else "negative"
        if expect is None:
            continue
        checked += 1
        ok = {"positive": v is not None and v == v and v > 0, "negative": v is not None and v == v and v < 0,
              "zero_or_empty": v is None or v != v or v == 0}[expect]
        if not ok:
            sign_rows.append({"table_type": t, "filename": r.filename, "page_no": r.page_no, "row_no": r.row_no,
                              "station_raw": r.station_raw, f: v, "status_text": r.status_text, "expected": expect})
print(f"  rows checked: {checked}; disagreements: {len(sign_rows)}")
if sign_rows:
    print(pd.DataFrame(sign_rows).to_string(index=False))
print("  rule: status containing 'Receivable' (not 'Payable') expects > 0; 'Payable' (not 'Receivable') expects < 0;"
      " '-' or blank expects 0 or empty. Net field: net_charges_rs (SRAS payment: total_charges_rs).")

section("Present-but-empty and explicit-no-data tables")
pe = log[log["status"].isin(["present_but_empty", "explicit_no_data"])]
print(pe[["status", "filename", "table_type", "pages", "week_start", "flags"]].to_string(index=False) if len(pe) else "  none")

section("Week assignment by week_source")
ws = pd.concat([datasets[t].assign(table_type=t)[["table_type", "week_source"]] for t in datasets if len(datasets[t])],
               ignore_index=True)
print("  table instances:")
print(log[log["status"] != "explicit_no_data"]["week_source"].value_counts().to_string())
print("  dataset rows:")
print(pd.crosstab(ws["table_type"], ws["week_source"]).to_string())
wf = log[log["flags"].str.contains("week_not_in_manifest|performance_dates_not_assigned_week")]
if len(wf):
    print("  instances with week flags:")
    print(wf[["filename", "table_type", "pages", "week_start", "flags"]].to_string(index=False))
row_wf = [(t, r.filename, r.week_text, r.flags) for t in ("TRAS_EMERGENCY",) for r in datasets[t].itertuples()
          if "week" in str(r.flags)]
for x in row_wf:
    print("  row week flag:", x)

section("Label conflict: Shortfall heading with Emergency columns")
conf = log[log["flags"].str.contains("header_label_conflict")]
sel = pd.read_csv(config.SELECTION_CSV, keep_default_na=False).set_index(["week_start", "section"])
for c in conf.itertuples(index=False):
    rows = datasets["TRAS_SHORTFALL"][datasets["TRAS_SHORTFALL"]["instance_id"] == c.instance_id]
    net = rows["net_charges_rs"].sum()
    print(f"  {c.filename} p{c.pages} week {c.week_start}: {len(rows)} rows, net total {net:,.0f}")
    has_cons = (ext[(ext["filename"] == c.filename) & (ext["table_type"] == "CONSOLIDATED")]).shape[0] > 0
    if not has_cons:
        print("      This file has no consolidated table, so its Shortfall/Emergency column totals cannot be compared.")
    else:
        cons = datasets["CONSOLIDATED"][datasets["CONSOLIDATED"]["filename"] == c.filename]
        for col in ("shortfall_charges_rs", "emergency_charges_rs"):
            print(f"      consolidated {col} total: {cons[col].sum():,.0f}  match: {abs(cons[col].sum() - net) < 1}")
    base = sel.at[(c.week_start, "TRAS"), "base"] if (c.week_start, "TRAS") in sel.index else ""
    print(f"      Evidence from the week's base file ({base or 'none'}), same stations:")
    for r in rows.itertuples(index=False):
        out = []
        for t in ("TRAS_SHORTFALL", "TRAS_EMERGENCY"):
            b = datasets[t][(datasets[t]["filename"] == base) & (datasets[t]["station_raw"].str.upper() == r.station_raw.upper())]
            out.append(f"{t}: " + (", ".join(f"net {v:,.0f}" for v in b["net_charges_rs"]) if len(b) else "absent"))
        bc = datasets["CONSOLIDATED"][(datasets["CONSOLIDATED"]["filename"] == base)
                                      & (datasets["CONSOLIDATED"]["station_raw"].str.upper() == r.station_raw.upper())]
        cons_txt = (f"consolidated shortfall {bc['shortfall_charges_rs'].iloc[0]:,.0f}, emergency "
                    f"{bc['emergency_charges_rs'].iloc[0]:,.0f}") if len(bc) else "consolidated: absent"
        print(f"        {r.station_raw}: this table net {r.net_charges_rs:,.0f} | base {'; '.join(out)}; {cons_txt}")
    print("      Not reassigned: kept in 06_tras_shortfall.csv with flag header_label_conflict.")

section("3 sample rows from each output")
for t, d in datasets.items():
    print(f"\n  {rel(config.EXTRACT_CSV[t])} ({len(d)} rows)")
    if len(d):
        print(d.sample(3, random_state=0).drop(columns=["table_id"]).to_string(index=False))
print(f"\n  {rel(config.PRINTED_TOTALS_CSV)} ({len(totals_df)} rows)")
print(totals_df.sample(3, random_state=0).drop(columns=["table_id"]).to_string(index=False))
print(f"\n  {rel(config.EXTRACTION_LOG_CSV)} ({len(log)} rows)")
print(log.sample(3, random_state=0).drop(columns=["table_ids"]).to_string(index=False))

print(f"\nWarnings: {len(warnings)}")
for w in warnings:
    print(f"  - {w}")
