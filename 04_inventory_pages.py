"""Step 04: inventory every page of the downloaded PDFs.

Maps what is on each page (text layer, images, table types, week printed) without
extracting any table values. Every file's SHA-256 is checked against the step 03
log first; any mismatch stops the run. Page text is saved to 04_page_text.jsonl
so later steps can inspect it without reopening the PDFs.

Page types come from keyword rules on the page text. A page without its own
heading inherits the type of the last table on the previous page only when there
is evidence of continuation: the "WRPC <X> Account ... Page N" running footer
continues, an email printout repeats the same print header, or mailing-list
numbering continues. Anything else is UNKNOWN.
"""

# %% Imports
import hashlib
import json
import re
from collections import defaultdict
from datetime import date, timedelta

import pandas as pd
import pdfplumber

import config
from src.table_reader import (assign_table_types, classify_rows, join_table, read_page_tables,
                                row_number, show, station_label, unwrapped_names)

warnings = []


def warn(msg):
    warnings.append(msg)


def join(items):
    return ";".join(str(i) for i in items)


# %% Load inputs and verify hashes before reading any file
log = pd.read_csv(config.DOWNLOAD_LOG_CSV, keep_default_na=False)
log = log[log["status"].isin(["ok", "skipped_existing"])]
download = pd.read_csv(config.DOWNLOAD_LIST_CSV, keep_default_na=False).set_index("filename")
manifest = pd.read_csv(config.MANIFEST_CSV, keep_default_na=False)


def sha256_of(path):
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


mismatches = []
for r in log.itertuples(index=False):
    path = config.RAW_PDFS_DIR / r.filename
    actual = sha256_of(path) if path.exists() else "MISSING"
    if actual != r.sha256:
        mismatches.append(f"{r.filename}: log {r.sha256[:16]}…, file {actual[:16]}…")
if mismatches:
    raise SystemExit("SHA-256 mismatch against step 03 log; stopping.\n  " + "\n  ".join(mismatches))
print(f"SHA-256 verified for {len(log)} files")

manifest_weeks = (
    manifest[manifest["week_start"] != ""].groupby("filename")["week_start"].agg(lambda s: sorted(set(s)))
)

# %% Page classification rules
# TRAS_SHORTFALL_EMERGENCY: the earlier single combined table "Account in Shortfall/Emergency Condition".
STATEMENT_TYPES = ["TRAS_SHORTFALL_EMERGENCY", "TRAS_SHORTFALL", "TRAS_EMERGENCY", "TRAS_MARKET", "SRAS_PAYMENT", "SRAS_PERFORMANCE",
                   "SCUC", "CONSOLIDATED"]
OTHER_TYPES = ["COVER_LETTER", "MAILING_LIST", "WRLDC_EMAIL"]
ALL_TYPES = OTHER_TYPES[:2] + STATEMENT_TYPES + OTHER_TYPES[2:]

# Browser print header of an email printout, e.g. "1/2/26, 1:22 PM" or "23/07/2026, 22:12".
EMAIL_PRINT_RE = re.compile(r"^\d{1,2}/\d{1,2}/\d{2,4},\s*\d{1,2}:\d{2}(?:\s*[AP]M)?(?!\d)")
FOOTER_RE = re.compile(r"WRPC\s+(TRAS|SRAS|SCUC)\s+Account\s*(\(Consolidated\))?\s*:.*?Page\s*(\d+)", re.I)
LIST_ITEM_RE = re.compile(r"^\s*(\d{1,3})\.\s*\S")
# A statement table row: serial number, station name, then a numeric column.
TABLE_ROW_RE = re.compile(r"^\s*(\d{1,3})\s+[A-Za-z][^\n]*?\s+-?[\d,]+(?:\.\d+)?(?:\s|$)")
NUMBERED_LINE_RE = re.compile(r"^\s*(\d{1,3})\s+\S")


def rx(p, flags=0):
    return re.compile(p, flags)


R = {
    "sub_or_sir": rx(r"\bSub\s*:|\bSir\b"),
    "mailing": rx(r"Mailing\s*list", re.I),
    "tras3": rx(r"Format-TRAS-III\b"),
    "tras2": rx(r"Format-TRAS-II\b"),
    "shortfall": rx(r"Shortfall"),
    "emergency": rx(r"Emergency"),
    "acct_combined": rx(r"Account in Shortfall\s*/\s*Emergency Condition"),
    "acct_shortfall": rx(r"Account in Shortfall Condition"),
    "acct_emergency": rx(r"Account in Emergency Condition"),
    "day_ahead": rx(r"Day Ahead"),
    "sras_pay": rx(r"Payments to the SRAS"),
    "actual_perf": rx(r"Actual Performance"),
    "scuc_acct": rx(r"SCUC Account For Week"),
    "scuc_gen": rx(r"by the SCUC Generator\(s\)"),
    "consolidated": rx(r"Consolidated|Net Regional Shortfall"),
    "wrldc": rx(r"WRLDC"),
    "wrldc_mail": rx(r"FINAL SCHEDULING|grid-india", re.I),
}
# A page with its own statement heading never inherits a type from the previous page.
OWN_HEADING_RE = re.compile(r"Format-TRAS-II|Account For Week|For Week\s*:")
NO_DATA_RE = re.compile(r"No data (?:found|available)|No records? found|Data not available", re.I)


def first_line(lines, regex):
    return next((i for i, l in enumerate(lines) if regex.search(l)), None)


def classify_page(text, lines):
    """Return {page_type: line index of its anchor} from keyword rules (own heading only)."""
    has = {k: bool(r.search(text)) for k, r in R.items()}
    is_email_print = bool(lines) and bool(EMAIL_PRINT_RE.match(lines[0]))
    found = {}

    def add(name, anchor):
        pos = first_line(lines, R[anchor])
        found[name] = pos if pos is not None else 0

    if has["sub_or_sir"] and not is_email_print and not has["wrldc_mail"]:
        add("COVER_LETTER", "sub_or_sir")
    if has["mailing"]:
        add("MAILING_LIST", "mailing")
    # "Account in <X> Condition" states the condition outright; when present it alone decides,
    # because some Shortfall tables have "Emergency" in their column headers (and vice versa).
    if has["acct_combined"] or has["acct_shortfall"] or has["acct_emergency"]:
        if has["acct_combined"]:
            add("TRAS_SHORTFALL_EMERGENCY", "acct_combined")
        if has["acct_shortfall"]:
            add("TRAS_SHORTFALL", "acct_shortfall")
        if has["acct_emergency"]:
            add("TRAS_EMERGENCY", "acct_emergency")
    else:
        if has["tras3"] and has["shortfall"]:
            add("TRAS_SHORTFALL", "shortfall")
        if has["tras3"] and has["emergency"]:
            add("TRAS_EMERGENCY", "emergency")
    if has["consolidated"]:
        add("CONSOLIDATED", "consolidated")
    # "Day Ahead" also appears in the consolidated table's heading, so on its own it
    # counts as TRAS_MARKET only on pages that are not consolidated.
    if has["tras2"] or (has["day_ahead"] and "CONSOLIDATED" not in found):
        add("TRAS_MARKET", "tras2" if has["tras2"] else "day_ahead")
    if has["sras_pay"]:
        add("SRAS_PAYMENT", "sras_pay")
    if has["actual_perf"]:
        add("SRAS_PERFORMANCE", "actual_perf")
    if has["scuc_acct"] or has["scuc_gen"]:
        add("SCUC", "scuc_acct" if has["scuc_acct"] else "scuc_gen")
    # The mailing list includes WRLDC's address and grid-india emails; not an email page.
    if has["wrldc"] and has["wrldc_mail"] and "MAILING_LIST" not in found:
        add("WRLDC_EMAIL", "wrldc")
    return found


# %% Week periods printed on a page
MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct",
                                      "nov", "dec"], start=1)}
PD = r"(\d{1,2})[-./ ](\d{1,2}|[A-Za-z]{3})[-./ ](\d{4}|\d{2})(?!\d)"
PERIOD_RE = re.compile(PD + r"\s*to\s*" + PD, re.I)


def to_date(d, m, y):
    y = int(y)
    y = 2000 + y if y < 100 else y
    month = int(m) if m.isdigit() else MONTHS.get(m.lower())
    try:
        return date(y, month, int(d)) if month else None
    except ValueError:
        return None


# Split form used in a station table's Week column, with the table row in between:
#   "13.04.2026 to" / "1 TPCL_Mundra 87.47 ..." / "19.04.2026"
SPLIT_START_RE = re.compile(r"^" + PD + r"\s*to$", re.I)
SPLIT_END_RE = re.compile(r"^" + PD + r"$")


def weeks_printed(text):
    """Return (sorted week-start dates, list of non-week period strings)."""
    pairs = [(m.group(1, 2, 3), m.group(4, 5, 6), m.group(0)) for m in PERIOD_RE.finditer(text)]
    lines = [l.strip() for l in text.splitlines()]
    for i, line in enumerate(lines):
        ms = SPLIT_START_RE.match(line)
        if not ms:
            continue
        for nxt in lines[i + 1 : i + 4]:  # end date within the next three lines
            me = SPLIT_END_RE.match(nxt)
            if me:
                pairs.append((ms.group(1, 2, 3), me.group(1, 2, 3), f"{line} … {nxt}"))
                break
    weeks, other = set(), []
    for start, end, raw in pairs:
        s, e = to_date(*start), to_date(*end)
        if s and e and s.weekday() == 0 and (e - s).days == 6:
            weeks.add(s)
        else:
            other.append(re.sub(r"\s+", " ", raw))
    return sorted(weeks), other


# %% Read every page
config.INTERIM_DIR.mkdir(parents=True, exist_ok=True)
page_rows = []
page_texts = {}  # (filename, page_no) -> text, for the line-based row-number check below
all_tables = []  # cell-based tables (src/table_reader) on statement pages
with open(config.PAGE_TEXT_JSONL, "w", encoding="utf-8") as jf:
    for n_file, r in enumerate(log.itertuples(index=False), start=1):
        path = config.RAW_PDFS_DIR / r.filename
        prev = None  # state of the previous page in this file
        with pdfplumber.open(path) as pdf:
            for page_no, page in enumerate(pdf.pages, start=1):
                text = page.extract_text() or ""
                jf.write(json.dumps({"filename": r.filename, "page_no": page_no, "text": text},
                                    ensure_ascii=False) + "\n")
                lines = [l.strip() for l in text.splitlines() if l.strip()]
                area = float(page.width * page.height) or 1.0
                img_cov = max([abs((im["x1"] - im["x0"]) * (im["bottom"] - im["top"])) / area
                               for im in page.images] or [0.0])
                n_text = len(re.sub(r"\s", "", text))
                has_text = n_text > 0
                likely_scanned = n_text < 50 and img_cov >= 0.5

                own = classify_page(text, lines)
                footer = FOOTER_RE.search(text)
                footer_key = (footer.group(1).upper(), bool(footer.group(2))) if footer else None
                footer_page = int(footer.group(3)) if footer else None
                list_items = [int(m.group(1)) for m in map(LIST_ITEM_RE.match, lines) if m]

                # A mailing-list page whose numbering continues the previous page is a
                # continuation, even if its addresses mention WRLDC or "Sir".
                if (prev and prev["last_type"] == "MAILING_LIST" and list_items and prev["list_items"]
                        and list_items[0] == prev["list_items"][-1] + 1
                        and not set(own) - {"WRLDC_EMAIL", "COVER_LETTER"}):
                    own = {}
                types, classified_by = dict(own), "keyword" if own else ""
                own_heading = bool(OWN_HEADING_RE.search(text))
                first_numbered = next((l for l in lines if NUMBERED_LINE_RE.match(l)), None)
                row_nos = [int(m.group(1)) for m in map(TABLE_ROW_RE.match, lines) if m]
                note = "own statement heading but no recognised table type" if own_heading and not own else ""

                if not own and not own_heading and has_text and prev and prev["last_type"]:
                    if footer and prev["footer_key"] == footer_key and prev["footer_page"] == footer_page - 1:
                        classified_by = "inherited:footer"
                    elif lines and EMAIL_PRINT_RE.match(lines[0]) and prev["first_line"] == lines[0]:
                        classified_by = "inherited:email_print_header"
                    elif (prev["last_type"] == "MAILING_LIST" and list_items and prev["list_items"]
                          and list_items[0] == prev["list_items"][-1] + 1):
                        classified_by = "inherited:list_numbering"
                    elif (not footer and prev["last_type"] in STATEMENT_TYPES and first_numbered
                          and TABLE_ROW_RE.match(first_numbered) and prev["last_row_no"] is not None
                          and int(NUMBERED_LINE_RE.match(first_numbered).group(1)) == prev["last_row_no"] + 1):
                        # No heading or footer, but the table's row numbering continues (A16).
                        classified_by = "inherited:row_number"
                    if classified_by:
                        types = {prev["last_type"]: 0}

                ordered = sorted(types, key=lambda t: types[t])
                page_texts[(r.filename, page_no)] = text
                stmt = [t for t in ordered if t in STATEMENT_TYPES]
                if stmt:
                    # Heading position of each type on the page: tables are assigned to the nearest
                    # heading above them; an inherited type owns the top of the page.
                    text_lines = page.extract_text_lines(strip=True)
                    tops = {}
                    for t in ordered:
                        if classified_by.startswith("inherited"):
                            tops[t] = -1
                            continue
                        anchor_text = lines[types[t]] if types[t] < len(lines) else None
                        tops[t] = next((tl["top"] for tl in text_lines if tl["text"].strip() == anchor_text), -1)
                    page_tables = read_page_tables(page, r.filename, page_no)
                    for t in page_tables:
                        classify_rows(t)
                    all_tables.extend(assign_table_types(page_tables, tops))
                weeks, other_periods = weeks_printed(text)
                page_rows.append(
                    {
                        "filename": r.filename,
                        "page_no": page_no,
                        "n_chars": len(text),
                        "n_images": len(page.images),
                        "max_image_cover": round(img_cov, 3),
                        "has_text_layer": has_text,
                        "likely_scanned": likely_scanned,
                        "blank_page": not has_text and not page.images,
                        "page_types": join(ordered) if ordered else "UNKNOWN",
                        "classified_by": classified_by,
                        "type_anchor_lines": join(f"{t}:{types[t]}" for t in ordered),
                        "explicit_no_data": bool(NO_DATA_RE.search(text)),
                        "note": note,
                        "footer": footer.group(0) if footer else "",
                        "weeks_printed": join(f"{w:%Y-%m-%d}" for w in weeks),
                        "other_periods": join(other_periods),
                        "header_text": " | ".join(lines[:8]),
                    }
                )
                prev = {
                    "last_type": ordered[-1] if ordered else None,
                    "footer_key": footer_key,
                    "footer_page": footer_page,
                    "first_line": lines[0] if lines else None,
                    "list_items": list_items,
                    "last_row_no": row_nos[-1] if row_nos else None,
                }
        if n_file % 10 == 0 or n_file == len(log):
            print(f"  read {n_file}/{len(log)} files")

pages = pd.DataFrame(page_rows)

# Join wrapped cells now that every file has been read (A17), and save the tables.
stmt_tables = [t for t in all_tables if t["table_type"] in STATEMENT_TYPES]
known_names = unwrapped_names(stmt_tables)
for t in stmt_tables:
    join_table(t, known_names)
with open(config.TABLES_JSONL, "w", encoding="utf-8") as tf:
    for t in stmt_tables:
        tf.write(json.dumps(t, ensure_ascii=False) + "\n")

# Scanned pages cannot be keyword-classified. Record where they sit as a hint only.
first_mailing = pages[pages["page_types"].str.contains("MAILING_LIST")].groupby("filename")["page_no"].min()
pages["scan_position_hint"] = [
    ("before_mailing_list" if fn in first_mailing and pn < first_mailing[fn] else "elsewhere") if sc else ""
    for fn, pn, sc in zip(pages["filename"], pages["page_no"], pages["likely_scanned"])
]

# Table type of each explicit "no data" page. If the page's own type is known, use it.
# A TRAS-III heading without a stated condition is reported as such; which condition is
# missing is given only as a hint (the one not already present earlier in the file).
def no_data_type(row):
    if not row["explicit_no_data"]:
        return "", ""
    if row["page_types"] != "UNKNOWN":
        return row["page_types"], ""
    text = page_texts[(row["filename"], row["page_no"])]
    if R["tras3"].search(text):
        earlier = pages[(pages["filename"] == row["filename"]) & (pages["page_no"] < row["page_no"])]
        seen = {t for ts in earlier["page_types"] for t in ts.split(";")}
        missing = [t for t in ("TRAS_SHORTFALL", "TRAS_EMERGENCY") if t not in seen]
        hint = f"probably {missing[0]} (other TRAS-III condition already earlier in file)" if len(missing) == 1 else ""
        return "TRAS_III_CONDITION_NOT_STATED", hint
    return "UNDETERMINED", ""


pages[["no_data_table_type", "no_data_type_hint"]] = pages.apply(no_data_type, axis=1, result_type="expand")

# %% Station rows per table type (for telling complete restatements from narrow ones)
segment_labels = {}  # (filename, table_type) -> set of station labels, from the cell-based reader
reader_rows = defaultdict(list)  # (filename, page_no, table_type) -> row numbers found by the reader
reader_count = defaultdict(int)  # (filename, page_no, table_type) -> station rows found by the reader
for t in stmt_tables:
    for row in t["rows"]:
        label = station_label(row)
        if label:
            segment_labels.setdefault((t["filename"], t["table_type"]), set()).add(label)
            reader_count[(t["filename"], t["page_no"], t["table_type"])] += 1
        n = row_number(row)
        if n is not None:
            reader_rows[(t["filename"], t["page_no"], t["table_type"])].append(n)

def page_ranges(nums):
    nums, out = sorted(nums), []
    for n in nums:
        if out and n == out[-1][1] + 1:
            out[-1][1] = n
        else:
            out.append([n, n])
    return ",".join(f"{a}" if a == b else f"{a}-{b}" for a, b in out)


# %% Row-number check per table instance
# A table instance is a run of same-type segments in a file, following continuation pages. A new
# instance starts when the type changes, the printed week changes, or numbering restarts at 1.
# Row numbers come from the page text (line-based, independent of the cell-based reader).
# Pass: row numbers are exactly 1..N and the reader's station rows equal N. Separately, the
# line-based and reader row numbers are compared per page and table type. SRAS_PERFORMANCE rows
# carry no row numbers and are not checkable.
ROW_NO_RE = re.compile(r"^\s*(\d{1,3})\s+(?=[A-Za-z]|-?\d)")
instances = []
method_disagreements = []  # (filename, page_no, table_type, line-based numbers, reader numbers)
for fn, fp in pages.groupby("filename", sort=False):
    cur = None
    for row in fp.sort_values("page_no").itertuples(index=False):
        if not row.type_anchor_lines:
            cur = None
            continue
        lines = [l.strip() for l in page_texts[(fn, row.page_no)].splitlines() if l.strip()]
        anchors = [(t, int(pos)) for t, pos in (a.split(":") for a in row.type_anchor_lines.split(";"))]
        for i, (t, start) in enumerate(anchors):
            start = 0 if i == 0 else start
            end = anchors[i + 1][1] if i + 1 < len(anchors) else len(lines)
            if t not in STATEMENT_TYPES or t == "SRAS_PERFORMANCE":
                cur = None
                continue
            seg = lines[start:end]
            nums = [int(m.group(1)) for m in map(ROW_NO_RE.match, seg) if m]
            parsed = reader_count[(fn, row.page_no, t)]
            if sorted(nums) != sorted(reader_rows[(fn, row.page_no, t)]):
                method_disagreements.append((fn, row.page_no, t, nums, reader_rows[(fn, row.page_no, t)]))
            if (cur is None or cur["table_type"] != t or (nums and nums[0] == 1 and cur["row_numbers"])
                    or (row.weeks_printed and cur["week"] and row.weeks_printed != cur["week"])):
                cur = {"filename": fn, "table_type": t, "week": row.weeks_printed, "pages": [], "row_numbers": [],
                       "parsed_rows": 0}
                instances.append(cur)
            cur["pages"].append(row.page_no)
            cur["row_numbers"] += nums
            cur["parsed_rows"] += parsed
            cur["week"] = cur["week"] or row.weeks_printed


def check_instance(inst):
    nums = inst["row_numbers"]
    if not nums:
        return "no_rows", ""
    n = max(nums)
    problems = []
    missing = sorted(set(range(1, n + 1)) - set(nums))
    dupes = sorted({x for x in nums if nums.count(x) > 1})
    if missing:
        problems.append(f"missing row numbers {missing}")
    if dupes:
        problems.append(f"duplicate row numbers {dupes}")
    if inst["parsed_rows"] != n:
        problems.append(f"reader station rows {inst['parsed_rows']} != N {n}")
    return ("pass" if not problems else "fail"), "; ".join(problems)


table_check = pd.DataFrame(instances)
if len(table_check):
    table_check[["result", "problems"]] = table_check.apply(check_instance, axis=1, result_type="expand")
    table_check["n_rows"] = table_check["row_numbers"].apply(lambda x: max(x) if x else 0)
    reasons = download["reason"].reindex(table_check["filename"]).fillna("").str.split(";")
    table_check["step02_role"] = [("base" if "base" in r else "overlay" if any(x.startswith("overlay") for x in r)
                                   else "superseded" if "superseded" in r else "other") for r in reasons]
    table_check["pages"] = table_check["pages"].apply(lambda x: page_ranges(x))

# %% File inventory
exploded = pages.assign(page_type=pages["page_types"].str.split(";")).explode("page_type")
file_rows = []
for r in log.itertuples(index=False):
    fp = pages[pages["filename"] == r.filename]
    fe = exploded[exploded["filename"] == r.filename]
    dl = download.loc[r.filename]
    types_found = [t for t in ALL_TYPES if (fe["page_type"] == t).any()]
    stmt_pages = fe[fe["page_type"].isin(STATEMENT_TYPES)]
    printed = sorted({w for ws in fp.loc[fp["page_no"].isin(stmt_pages["page_no"]), "weeks_printed"]
                      for w in ws.split(";") if w})
    expected = manifest_weeks.get(r.filename, [])
    if not printed:
        match = "no_week_printed"
    elif printed == expected:
        match = "match"
    else:
        parts = []
        if set(printed) - set(expected):
            parts.append(f"printed_not_in_manifest: {join(sorted(set(printed) - set(expected)))}")
        if set(expected) - set(printed):
            parts.append(f"manifest_not_printed: {join(sorted(set(expected) - set(printed)))}")
        match = " | ".join(parts)
    row = {
        "filename": r.filename,
        "scope": dl["scope"],
        "file_role": dl["file_role"],
        "role": r.role,
        "stations_in_title": dl["stations"],
        "n_pages": len(fp),
        "table_types": join(types_found),
        "manifest_weeks": join(expected),
        "weeks_printed": join(printed),
        "week_match": match,
        "n_scanned_pages": int(fp["likely_scanned"].sum()),
        "n_unknown_pages": int((fp["page_types"] == "UNKNOWN").sum()),
        "n_inherited_pages": int(fp["classified_by"].str.startswith("inherited").sum()),
    }
    for t in ALL_TYPES:
        row[f"pages_{t}"] = page_ranges(fe.loc[fe["page_type"] == t, "page_no"])
    for t in STATEMENT_TYPES:
        row[f"stations_{t}"] = len(segment_labels.get((r.filename, t), ())) if t in types_found else ""
    row["n_explicit_no_data_pages"] = int(fp["explicit_no_data"].sum())
    file_rows.append(row)
files = pd.DataFrame(file_rows)

# %% Format timeline (base files, per week)
coverage = pd.read_csv(config.WEEK_COVERAGE_CSV, keep_default_na=False)
types_by_file = files.set_index("filename")["table_types"].str.split(";")
tl_rows = []
for c in coverage.itertuples(index=False):
    base_files = list(dict.fromkeys(f for f in (c.base_TRAS, c.base_SRAS, c.base_SCUC) if f))
    present = set().union(*(set(types_by_file.get(f, [])) for f in base_files)) if base_files else set()
    tl_rows.append({"week_start": c.week_start, "iso_year": c.iso_year, "iso_week": c.iso_week,
                    "base_files": join(base_files), **{t: t in present for t in STATEMENT_TYPES}})
timeline = pd.DataFrame(tl_rows)

# %% Save
pages.to_csv(config.PAGE_INVENTORY_CSV, index=False)
table_check.assign(row_numbers=table_check["row_numbers"].apply(lambda x: ";".join(map(str, x)))).to_csv(
    config.TABLE_INSTANCES_CSV, index=False)
files.to_csv(config.FILE_INVENTORY_CSV, index=False)
timeline.to_csv(config.FORMAT_TIMELINE_CSV, index=False)

# %% Summary
pd.set_option("display.width", 250)
pd.set_option("display.max_colwidth", 160)
rel = lambda p: p.relative_to(config.PROJECT_ROOT)


def section(title):
    print(f"\n--- {title} " + "-" * max(0, 66 - len(title)))


print("\n" + "=" * 70)
print("STEP 04 SUMMARY: page inventory")
print("=" * 70)
for p in (config.PAGE_INVENTORY_CSV, config.FILE_INVENTORY_CSV, config.FORMAT_TIMELINE_CSV, config.TABLE_INSTANCES_CSV,
          config.PAGE_TEXT_JSONL, config.TABLES_JSONL):
    print(f"  -> {rel(p)}")

section("Pages")
print(f"  files: {len(files)}  pages: {len(pages)}  scanned: {int(pages['likely_scanned'].sum())}  "
      f"blank: {int(pages['blank_page'].sum())}  no text layer: {int((~pages['has_text_layer']).sum())}")
print("  pages by type (a page can have several):")
print(exploded["page_type"].value_counts().to_string())
print("  how classified:")
print(pages["classified_by"].replace("", "(unclassified)").value_counts().to_string())

section("UNKNOWN pages: distinct header_text (with text layer)")
unk = pages[(pages["page_types"] == "UNKNOWN") & pages["has_text_layer"]]
if unk.empty:
    print("  none")
else:
    grp = unk.groupby("header_text").agg(n=("page_no", "size"), files=("filename", "nunique"),
                                         sample=("filename", "first"), sample_page=("page_no", "first"))
    for h, g in grp.sort_values("n", ascending=False).iterrows():
        print(f"  [{g.n} pages, {g.files} files; e.g. {g['sample']} p{g.sample_page}]\n      {h[:300]}")
unk_blank = pages[(pages["page_types"] == "UNKNOWN") & ~pages["has_text_layer"]]
print(f"  UNKNOWN pages without text: {len(unk_blank)} "
      f"(scanned: {int(unk_blank['likely_scanned'].sum())}, blank: {int(unk_blank['blank_page'].sum())}, "
      f"other: {int((~unk_blank['likely_scanned'] & ~unk_blank['blank_page']).sum())})")

section("Explicit 'no data' pages, and pages with an own heading but no recognised type")
odd = pages[(pages["note"] != "") | pages["explicit_no_data"]]
if odd.empty:
    print("  none")
for o in odd.itertuples(index=False):
    print(f"  {o.filename} p{o.page_no}: explicit_no_data={o.explicit_no_data}  page_types={o.page_types}  "
          f"no_data_table_type={o.no_data_table_type or '-'}")
    if o.no_data_type_hint:
        print(f"      hint: {o.no_data_type_hint}")
    if o.note:
        print(f"      note: {o.note}")
    print(f"      header: {o.header_text[:140]}")

section("Scanned pages that are not (probably) cover letters")
print("  Scanned pages have no text, so none can be confirmed as a cover letter. Split by position:")
print(pages.loc[pages["likely_scanned"], "scan_position_hint"].value_counts().to_string())
need_ocr = pages[pages["likely_scanned"] & (pages["scan_position_hint"] != "before_mailing_list")]
print(f"\n  Scanned pages not before the mailing list ({len(need_ocr)}; would need OCR to identify):")
if need_ocr.empty:
    print("  none")
else:
    print(need_ocr.groupby("filename")["page_no"].agg(lambda s: page_ranges(s)).to_string())
other_notext = pages[~pages["has_text_layer"] & ~pages["likely_scanned"] & ~pages["blank_page"]]
if len(other_notext):
    print(f"\n  Pages with no text and only small images ({len(other_notext)}):")
    print(other_notext.groupby("filename")["page_no"].agg(lambda s: page_ranges(s)).to_string())

section("Files whose printed week does not match the manifest")
bad = files[files["week_match"] != "match"]
print(bad[["filename", "role", "manifest_weeks", "weeks_printed", "week_match"]].to_string(index=False)
      if len(bad) else "  none")
ov = files[files["file_role"] == "overlay"]
ov_printed = ov[ov["week_match"] != "no_week_printed"]
print(f"\n  Overlays: {len(ov)}; with a printed week: {len(ov_printed)}, of which match the manifest: "
      f"{int((ov_printed['week_match'] == 'match').sum())}; no week printed: {len(ov) - len(ov_printed)}")

section("Overlay files: table types found and station names visible")
texts = {}
with open(config.PAGE_TEXT_JSONL, encoding="utf-8") as jf:
    for line in jf:
        rec = json.loads(line)
        texts[(rec["filename"], rec["page_no"])] = rec["text"]
norm = lambda s: re.sub(r"[^a-z0-9]", "", s.lower())
for f in files[files["file_role"] == "overlay"].sort_values("filename").itertuples(index=False):
    stmt = exploded[(exploded["filename"] == f.filename) & exploded["page_type"].isin(STATEMENT_TYPES)]
    stmt_text = "\n".join(texts[(f.filename, p)] for p in sorted(set(stmt["page_no"])))
    labels = sorted(set().union(*(segment_labels.get((f.filename, t), set()) for t in STATEMENT_TYPES)))
    title_st = [s for s in f.stations_in_title.split(";") if s]
    found = [s for s in title_st if norm(s) in norm(stmt_text)]
    stmt_types = [t for t in f.table_types.split(";") if t in STATEMENT_TYPES]
    print(f"  {f.filename}  [{f.scope}]")
    print(f"      table types: {', '.join(stmt_types) or '(none)'}")
    counts = [f"{t}={getattr(f, 'stations_' + t)}" for t in stmt_types]
    print(f"      distinct station rows: {', '.join(counts) or '-'}")
    print(f"      title stations found in page text: {', '.join(found) or '-'}"
          + (f"; NOT found: {', '.join(set(title_st) - set(found))}" if set(title_st) - set(found) else ""))
    print(f"      station labels in its tables ({len(labels)}): {', '.join(labels[:15])}"
          + (" …" if len(labels) > 15 else ""))

section("Row-number check per table instance (rows 1..N, no gaps, reader station rows = N)")
tc = table_check
print(f"  table instances checked: {len(tc)}; pass: {int((tc['result'] == 'pass').sum())}; "
      f"fail: {int((tc['result'] == 'fail').sum())}; no numbered rows: {int((tc['result'] == 'no_rows').sum())}  "
      f"(SRAS_PERFORMANCE not checkable: no row numbers)")
print("\n  By table type:")
print(pd.crosstab(tc["table_type"], tc["result"], margins=True, margins_name="total").to_string())
print("\n  By step 02 role:")
print(pd.crosstab(tc["step02_role"], tc["result"], margins=True, margins_name="total").to_string())
fails = tc[tc["result"] == "fail"]
print(f"\n  Failing instances ({len(fails)}, in {fails['filename'].nunique()} files):")
for r in fails.sort_values(["step02_role", "filename", "table_type"]).itertuples(index=False):
    print(f"    [{r.step02_role}] {r.filename} {r.table_type} p{r.pages} (N={r.n_rows}): {r.problems}")
no_rows = tc[tc["result"] == "no_rows"]
if len(no_rows):
    print(f"\n  Instances with no numbered rows ({len(no_rows)}):")
    for r in no_rows.itertuples(index=False):
        print(f"    [{r.step02_role}] {r.filename} {r.table_type} p{r.pages}")

section("Line-based vs cell-based row numbers (per page and table type)")
print(f"  disagreements: {len(method_disagreements)}")
for fn, pn, t, a, b in method_disagreements:
    print(f"    {fn} p{pn} {t}: line-based {a} vs reader {b}")

section("Cell-based reader: tables and joins")
n_rows = sum(len(t["rows"]) for t in stmt_tables)
rules = pd.Series([rule for t in stmt_tables for row in t["rows"] for _, rule in row["join_flags"]]).value_counts()
print(f"  statement tables read: {len(stmt_tables)}; rows: {n_rows}; distinct unwrapped station names: {len(known_names)}")
print("  wrapped cells joined, by rule: " + (str(rules.to_dict()) if len(rules) else "none"))
untyped = [t for t in all_tables if not t["table_type"]]
print(f"  tables on statement pages left without a type: {len(untyped)}")
amb = defaultdict(list)  # (row kind, raw cell text) -> [(file, page, table type)]
for t in stmt_tables:
    for row in t["rows"]:
        for ci, rule in row["join_flags"]:
            if rule == "ambiguous_join":
                amb[(row["kind"], row["raw_cells"][ci])].append((t["filename"], t["page_no"], t["table_type"]))
print(f"  ambiguous_join cells: {sum(len(v) for v in amb.values())} ({len(amb)} distinct texts), grouped:")
for (kind, raw), where in sorted(amb.items(), key=lambda kv: -len(kv[1])):
    types_ = sorted({w[2] for w in where})
    print(f"    {len(where):>4} x [{kind}] {raw.replace(chr(10), '⏎')!r}")
    print(f"           types {', '.join(types_)}; e.g. {where[0][0]} p{where[0][1]}")

section("Distinct station rows per table type: base files (min / median / max) for comparison")
base_files = files[files["file_role"] == "base"]
for t in STATEMENT_TYPES:
    v = pd.to_numeric(base_files[f"stations_{t}"], errors="coerce").dropna()
    print(f"  {t:<17} files={len(v):>3}  min={int(v.min()) if len(v) else '-':>4}  "
          f"median={v.median() if len(v) else '-':>6}  max={int(v.max()) if len(v) else '-':>4}")

section("Format timeline (base files): first/last week per table type, and gaps")
for t in STATEMENT_TYPES:
    have = timeline.loc[timeline[t], "week_start"]
    missing = timeline.loc[~timeline[t], "week_start"].tolist()
    span = f"{have.min()} .. {have.max()}" if len(have) else "never"
    print(f"  {t:<17} {len(have):>2}/{len(timeline)} weeks  first..last: {span}")
    if missing:
        print(f"      missing in: {', '.join(missing)}")

section("Short base files (8-12 pages) and trasdec25.3: table types present")
short = files[(files["file_role"] == "base") & files["n_pages"].between(8, 12)
              | files["filename"].str.contains("trasdec25.3", regex=False)]
print(short.sort_values("filename")[["n_pages", "table_types", "filename"]].to_string(index=False)
      if len(short) else "  none")

section("The 4-week file: pages by week printed")
four = manifest[manifest["n_weeks_in_file"].astype(int) == 4]["filename"].unique()
for fn in four:
    fp = pages[pages["filename"] == fn]
    print(f"  {fn}")
    for p in fp.itertuples(index=False):
        print(f"    p{p.page_no:<3} weeks: {p.weeks_printed or '-':<24} types: {p.page_types:<30} {p.classified_by}")
    by_week = fp[fp["weeks_printed"] != ""].assign(w=fp["weeks_printed"].str.split(";")).explode("w")
    print("    page ranges per week: " + "; ".join(f"{w}: p{page_ranges(g['page_no'])}"
                                                  for w, g in by_week.groupby("w")))

print(f"\nWarnings: {len(warnings)}")
for w in warnings:
    print(f"  - {w}")
