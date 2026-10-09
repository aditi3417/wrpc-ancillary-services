"""Step 03: download every file on the step 02 download list into data/raw/pdfs/.

Each file is saved under its original (decoded) filename, only after it passes
the PDF check (HTTP OK, Content-Type PDF, body starts with %PDF). Existing files
are kept if their SHA-256 matches the previous download log; anything else that
already exists is reported, never silently overwritten (unless REDOWNLOAD).
Skipped files keep the download details from when they were actually fetched;
last_verified_at records when the hash was last re-checked.
PDFs are opened only to count pages; no content is extracted.
"""

# %% Imports
import hashlib
import random
import time
from datetime import datetime
from pathlib import Path

import pandas as pd
import pdfplumber
import requests

import config
from src.http_utils import download_pdf

warnings = []


def warn(msg):
    warnings.append(msg)


def sha256_of(path):
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def count_pages(path):
    """Return (n_pages, error). Opens the PDF only to count pages."""
    try:
        with pdfplumber.open(path) as pdf:
            return len(pdf.pages), ""
    except Exception as e:  # any parse failure is reported, not fatal
        return None, f"page count failed: {e.__class__.__name__}: {e}"


# %% Load the download list and the previous log
download_list = pd.read_csv(config.DOWNLOAD_LIST_CSV, keep_default_na=False)
n_expected = len(download_list)
config.RAW_PDFS_DIR.mkdir(parents=True, exist_ok=True)

dupe_names = download_list[download_list["filename"].duplicated(keep=False)]
if not dupe_names.empty:
    raise SystemExit(f"Download list has duplicate filenames; resolve before downloading:\n{dupe_names}")
unsafe = [fn for fn in download_list["filename"] if Path(fn).name != fn or fn.startswith(".")]
if unsafe:
    raise SystemExit(f"Unsafe filenames in download list: {unsafe}")

prev_log = {}
if config.DOWNLOAD_LOG_CSV.exists():
    old = pd.read_csv(config.DOWNLOAD_LOG_CSV, keep_default_na=False)
    old = old[old["status"].isin(["ok", "skipped_existing"])]
    prev_log = {r["filename"]: r for r in old.to_dict("records")}
print(f"Download list: {n_expected} files; previous log entries (ok/skipped): {len(prev_log)}; "
      f"REDOWNLOAD={config.REDOWNLOAD}")

# %% Download
session = requests.Session()
log_rows = []
made_request = False
for i, item in enumerate(download_list.itertuples(index=False), start=1):
    dest = config.RAW_PDFS_DIR / item.filename
    row = {"filename": item.filename, "url_clean": item.url_clean, "role": item.reason, "http_status": "",
           "content_type": "", "bytes": "", "sha256": "", "n_pages": "", "downloaded_at": "",
           "last_verified_at": "", "status": "", "error": ""}

    if dest.exists() and not config.REDOWNLOAD:
        sha = sha256_of(dest)
        prev = prev_log.get(item.filename)
        row["last_verified_at"] = datetime.now().isoformat(timespec="seconds")
        if prev is not None and prev["sha256"] == sha:
            # Keep the details from the original download; only last_verified_at is new.
            row.update({k: prev[k] for k in ("http_status", "content_type", "bytes", "sha256", "n_pages",
                                             "downloaded_at")})
            row.update(status="skipped_existing", error=prev["error"])
        else:
            why = "missing from download log" if prev is None else f"hash differs from log ({prev['sha256'][:12]}…)"
            row.update(bytes=dest.stat().st_size, sha256=sha, status="failed",
                       error=f"existing file not overwritten: {why}")
        log_rows.append(row)
        print(f"  [{i:>3}/{n_expected}] {row['status']:<16} {item.filename}")
        continue

    if made_request:
        time.sleep(random.uniform(*config.DOWNLOAD_DELAY_SECONDS))
    made_request = True
    res = download_pdf(item.url_clean, dest, session=session)
    now = datetime.now().isoformat(timespec="seconds")
    row.update(http_status=res["http_status"], content_type=res["content_type"], bytes=res["bytes"],
               sha256=res["sha256"], downloaded_at=now, last_verified_at=now)
    if res["ok"]:
        n_pages, page_err = count_pages(dest)
        notes = "; ".join(x for x in (res["error"], page_err) if x)  # res["error"] holds a retry note, if any
        row.update(status="ok", n_pages=n_pages if n_pages is not None else "", error=notes)
    else:
        row.update(status="failed", error=res["error"])
    log_rows.append(row)
    print(f"  [{i:>3}/{n_expected}] {row['status']:<16} {item.filename}" + (f"  ({row['error']})" if row["error"] else ""))

log = pd.DataFrame(log_rows)
log.to_csv(config.DOWNLOAD_LOG_CSV, index=False)

leftover = sorted(p.name for p in config.RAW_PDFS_DIR.glob("*.part"))
if leftover:
    warn(f"temporary .part files left in {config.RAW_PDFS_DIR.name}/: {leftover}")
extra = sorted(set(p.name for p in config.RAW_PDFS_DIR.glob("*.pdf")) - set(download_list["filename"]))
if extra:
    warn(f"{len(extra)} PDF(s) in data/raw/pdfs/ are not on the download list: {extra}")

# %% Summary
pd.set_option("display.width", 250)
pd.set_option("display.max_colwidth", 120)


def section(title):
    print(f"\n--- {title} " + "-" * max(0, 66 - len(title)))


have = log[log["status"].isin(["ok", "skipped_existing"])].copy()
have["bytes"] = have["bytes"].astype(int)
have["n_pages"] = pd.to_numeric(have["n_pages"], errors="coerce").astype("Int64")
have["primary_role"] = have["role"].str.split(";").str[0]

print("\n" + "=" * 70)
print("STEP 03 SUMMARY: download PDFs")
print("=" * 70)
print(f"Log: {config.DOWNLOAD_LOG_CSV.relative_to(config.PROJECT_ROOT)}")

section("Counts by status")
print(log["status"].value_counts().reindex(["ok", "skipped_existing", "failed"], fill_value=0).to_string())
print(f"Total size on disk (ok + skipped): {have['bytes'].sum() / 1e6:.1f} MB")

section("Failures")
failed = log[log["status"] == "failed"]
print(failed[["filename", "role", "http_status", "content_type", "error"]].to_string(index=False)
      if len(failed) else "  none")
saved_notes = log[log["status"].isin(["ok", "skipped_existing"]) & (log["error"] != "")]
if len(saved_notes):
    print("\nSaved, with notes (retries or page-count problems):")
    print(saved_notes[["filename", "status", "role", "error"]].to_string(index=False))

section("Identical files (same SHA-256 under different filenames)")
dup = have[have["sha256"].duplicated(keep=False)].sort_values("sha256")
if dup.empty:
    print("  none")
for sha, g in dup.groupby("sha256"):
    print(f"  {sha[:16]}…")
    for r in g.itertuples(index=False):
        print(f"      {r.filename}  (role: {r.role}, {r.n_pages} pages)")

section("Page-count distribution")
print(have["n_pages"].value_counts(dropna=False).sort_index().to_string())
print("\nBy role (pages): ")
print(have.groupby("primary_role")["n_pages"].describe()[["count", "min", "50%", "max"]].to_string())

section("10 smallest files by pages")
print(have.sort_values(["n_pages", "bytes"]).head(10)[["n_pages", "bytes", "role", "filename"]].to_string(index=False))
section("10 smallest files by size")
print(have.sort_values("bytes").head(10)[["bytes", "n_pages", "role", "filename"]].to_string(index=False))

section("Unusual page counts for role (< 50% or > 200% of the role median)")
medians = have.groupby("primary_role")["n_pages"].median()
have["role_median"] = have["primary_role"].map(medians)
unusual = have[(have["n_pages"] < 0.5 * have["role_median"]) | (have["n_pages"] > 2 * have["role_median"])]
print(unusual.sort_values(["primary_role", "n_pages"])[["primary_role", "n_pages", "role_median", "role", "filename"]]
      .to_string(index=False) if len(unusual) else "  none")

section("Completeness check")
n_have = len(have)
verdict = "PASS" if n_have == n_expected else "FAIL"
print(f"  ok ({int((log['status'] == 'ok').sum())}) + skipped_existing ({int((log['status'] == 'skipped_existing').sum())})"
      f" = {n_have}; download list = {n_expected}  ->  {verdict}")

print(f"\nWarnings: {len(warnings)}")
for w in warnings:
    print(f"  - {w}")
