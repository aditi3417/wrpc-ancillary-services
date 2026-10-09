"""Step 01: fetch and save the raw WRPC file lists for the years in config.YEARS_NEEDED.

Saves each menu API response unchanged to data/raw/menus/ and writes one row per
listed file to config.FILE_LIST_CSV. Titles are recorded as published; this step
does not interpret them (no week dates, no SCED filtering, no revision logic).
"""

# %% Imports
import json
import re
from datetime import datetime, timezone
from urllib.parse import quote, unquote, urljoin, urlparse, urlunparse

import pandas as pd
from bs4 import BeautifulSoup

import config
from src.http_utils import fetch, probe_pdf

warnings = []


def warn(msg):
    warnings.append(msg)


# %% Snapshot helpers
def latest_snapshot(menu_id):
    snaps = sorted(config.RAW_MENUS_DIR.glob(f"menu_{menu_id}_*.json"))
    return snaps[-1] if snaps else None


def get_menu(menu_id):
    """Return (snapshot path, parsed JSON), fetching a new snapshot if needed."""
    path = latest_snapshot(menu_id)
    if path is None or config.REFRESH_MENUS:
        url = config.MENU_API.format(menu_id=menu_id)
        resp = fetch(url)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        path = config.RAW_MENUS_DIR / f"menu_{menu_id}_{stamp}.json"
        path.write_bytes(resp.content)
        print(f"  menu {menu_id}: fetched -> {path.name}")
    else:
        print(f"  menu {menu_id}: reusing {path.name}")
    data = json.loads(path.read_bytes())
    if data.get("menu_id") != menu_id:
        warn(f"menu {menu_id}: response reports menu_id={data.get('menu_id')!r}")
    return path, data


# %% Parsing helpers
def clean_text(s):
    return re.sub(r"\s+", " ", s.replace("\xa0", " ")).strip()


def parse_tabs(soup, page_menu_id):
    """Return {tab label: menu_id or None} from the year-tab nav, plus PDF links found there."""
    tabs, nav_pdfs = {}, []
    for a in soup.select("ul#tabs a"):
        label = clean_text(a.get_text())
        href = (a.get("href") or "").strip()
        if not href:
            # The current page's own tab is sometimes rendered without a link.
            tabs[label] = page_menu_id if "active" in a.get("class", []) else None
        elif is_file_link(href):
            nav_pdfs.append((label, href))
            tabs[label] = None
        else:
            m = re.search(r"_(\d+)$", unquote(href))
            tabs[label] = int(m.group(1)) if m else None
    return tabs, nav_pdfs


CANONICAL = urlparse(config.BASE_URL)
FIX_TYPES = ["whitespace", "backslash", "relative_path", "host", "encoding"]


def bare_host(netloc):
    return netloc.lower().removeprefix("www.")


def normalise_href(href):
    """Return (canonical absolute URL, list of fix types applied).

    Fix types: whitespace (stripped), backslash (\\ -> /), relative_path (resolved
    against BASE_URL), host (wrpc.gov.in / http -> BASE_URL's scheme and host, which
    avoids a 301 HTML redirect), encoding (unsafe path characters percent-encoded).
    """
    fixes = []
    url = href.strip()
    if url != href:
        fixes.append("whitespace")
    if "\\" in url:
        url = url.replace("\\", "/")
        fixes.append("backslash")
    if not urlparse(url).scheme:
        url = urljoin(config.BASE_URL + "/", url)
        fixes.append("relative_path")
    parts = urlparse(url)
    if (parts.scheme, parts.netloc) != (CANONICAL.scheme, CANONICAL.netloc) and bare_host(
        parts.netloc
    ) == bare_host(CANONICAL.netloc):
        parts = parts._replace(scheme=CANONICAL.scheme, netloc=CANONICAL.netloc)
        fixes.append("host")
    path = quote(parts.path, safe="/%-._~!$&'()*+,;=:@")
    if path != parts.path:
        parts = parts._replace(path=path)
        fixes.append("encoding")
    return urlunparse(parts), fixes


def is_file_link(href):
    return href is not None and "/allfile/" in href.replace("\\", "/")


def parse_files(soup):
    """Return one dict per /allfile/ link outside the tab nav, in page order."""
    rows = []
    for a in soup.find_all("a"):
        if a.find_parent("ul", id="tabs") or not is_file_link(a.get("href")):
            continue
        url, fixes = normalise_href(a["href"])
        rows.append(
            {
                "title": clean_text(a.get_text()),
                "url": url,
                "filename": unquote(urlparse(url).path.rsplit("/", 1)[-1]),
                "href_raw": a["href"],
                "href_fix": ";".join(fixes),
                "tr": a.find_parent("tr"),
            }
        )
    return rows


def check_page(soup, files, label):
    """Flag anything on the page that might hold a file the parser did not capture."""
    for a in soup.find_all("a"):
        if a.find_parent("ul", id="tabs"):
            continue
        href = a.get("href")
        if not is_file_link(href):
            warn(f"{label}: non-file link outside nav: text={clean_text(a.get_text())!r} href={href!r}")
    link_trs = {id(f["tr"]) for f in files}
    for tr in soup.find_all("tr"):
        if tr.find_parent("ul", id="tabs") or tr.select("ul#tabs"):
            continue
        if id(tr) not in link_trs and clean_text(tr.get_text()):
            warn(f"{label}: table row with text but no file link: {clean_text(tr.get_text())[:120]!r}")
    per_tr = pd.Series([id(f["tr"]) for f in files]).value_counts()
    if (per_tr > 1).any():
        warn(f"{label}: {int((per_tr > 1).sum())} table row(s) contain more than one file link")
    for f in files:
        if urlparse(f["url"]).netloc != CANONICAL.netloc:
            warn(f"{label}: file link on an unexpected host (left unchanged): {f['url']}")
        if not f["filename"].lower().endswith(".pdf"):
            warn(f"{label}: link is not a .pdf: {f['url']}")
        if not f["title"]:
            warn(f"{label}: empty title for {f['url']}")


# %% Fetch seed menu and discover year tabs
config.RAW_MENUS_DIR.mkdir(parents=True, exist_ok=True)
config.INTERIM_DIR.mkdir(parents=True, exist_ok=True)

print(f"Seed menu {config.SEED_MENU_ID}")
seed_path, seed = get_menu(config.SEED_MENU_ID)
seed_soup = BeautifulSoup(seed["html"], "lxml")
seed_tabs, seed_nav_pdfs = parse_tabs(seed_soup, config.SEED_MENU_ID)
print(f"  tabs on seed page: {seed_tabs}")

year_menus = {}
for year in config.YEARS_NEEDED:
    menu_id = seed_tabs.get(str(year))
    if menu_id is None:
        warn(f"no menu id found for year tab {year} on seed page")
    else:
        year_menus[year] = menu_id
print(f"  year -> menu_id: {year_menus}")

# %% Fetch each year's menu and extract the file list
records, pages = [], {}
nav_pdfs = {href for _, href in seed_nav_pdfs}
for year, menu_id in year_menus.items():
    print(f"\nYear {year} (menu {menu_id})")
    path, data = get_menu(menu_id)
    soup = BeautifulSoup(data["html"], "lxml")
    label = f"{year}/menu {menu_id}"

    # Cross-check this page's tab mapping against the seed page.
    tabs, page_nav_pdfs = parse_tabs(soup, menu_id)
    nav_pdfs |= {href for _, href in page_nav_pdfs}
    for y in config.YEARS_NEEDED:
        if tabs.get(str(y)) is not None and tabs[str(y)] != seed_tabs.get(str(y)):
            warn(f"{label}: tab {y} -> {tabs[str(y)]}, but seed page says {seed_tabs.get(str(y))}")

    files = parse_files(soup)
    check_page(soup, files, label)
    for i, f in enumerate(files, start=1):
        records.append(
            {
                "year_tab": year,
                "menu_id": menu_id,
                "menu_name": clean_text(data.get("menuname") or ""),
                "list_position": i,
                "title": f["title"],
                "url": f["url"],
                "filename": f["filename"],
                "href_raw": f["href_raw"],
                "href_fix": f["href_fix"],
                "source_file": path.name,
            }
        )
    pages[year] = (menu_id, path.name, len(files))
    print(f"  {len(files)} file links, {sum(bool(f['href_fix']) for f in files)} with href fixes")

file_list = pd.DataFrame(records)

# %% Cross-page checks
dups = file_list[file_list.duplicated("filename", keep=False)].sort_values("filename")
for fname, grp in dups.groupby("filename"):
    where = ", ".join(f"{r.year_tab}#{r.list_position}" for r in grp.itertuples())
    warn(f"filename listed more than once (all rows kept): {fname} at {where}")

listed = set(file_list["url"])
for href in sorted(nav_pdfs):
    where = "also in a file list" if normalise_href(href)[0] in listed else "NOT in any fetched file list"
    warn(f"year-tab nav links directly to a PDF ({where}): {href.strip()}")

# %% Verify a small sample of URLs serve real PDFs (first 1 KB only, nothing saved)
# One row per fix type, plus the first unfixed row per year.
fix_lists = file_list["href_fix"].str.split(";")
has_fix = {t: fix_lists.apply(lambda fx, t=t: t in fx) for t in FIX_TYPES}
sample_idx = [has_fix[t].idxmax() for t in FIX_TYPES if has_fix[t].any()]
sample_idx += file_list[file_list["href_fix"] == ""].groupby("year_tab").head(1).index.tolist()
verification = []
for idx in dict.fromkeys(sample_idx):
    row = file_list.loc[idx]
    res = probe_pdf(row["url"])
    res.update(year_tab=row["year_tab"], list_position=row["list_position"], href_fix=row["href_fix"] or "-")
    verification.append(res)
    if not res["ok"]:
        warn(f"sample URL failed PDF check: {res}")

# Negative control: a bad path the site answers with an HTML page and a 2xx status.
# The check must reject it, otherwise it cannot be trusted.
control = probe_pdf(config.BASE_URL + "/no-such-page-pdf-check")
control.update(year_tab="control", list_position="-", href_fix="bad path")
verification.append(control)
if control["ok"]:
    warn("negative control passed the PDF check; the check is not discriminating")
verification = pd.DataFrame(verification)

# %% Save
file_list.to_csv(config.FILE_LIST_CSV, index=False)

# %% Summary
print("\n" + "=" * 70)
print("STEP 01 SUMMARY: fetch file lists")
print("=" * 70)
print(f"Snapshot mode: {'refresh' if config.REFRESH_MENUS else 'reuse latest (fetch if missing)'}")
print(f"Seed: menu {config.SEED_MENU_ID} ({seed_path.name})")
print("\nFiles per year tab:")
for year, (menu_id, src, n) in pages.items():
    print(f"  {year}: menu {menu_id:>5}  {n:>4} files  [{src}]")
print(f"  total rows: {len(file_list)}  unique filenames: {file_list['filename'].nunique()}")
print(f"\nOutput: {config.FILE_LIST_CSV.relative_to(config.PROJECT_ROOT)}")
print("\nHref fixes by type (original kept in href_raw; a row can have several):")
exploded = file_list.assign(fix=fix_lists).explode("fix", ignore_index=True)
counts = pd.crosstab(exploded["fix"], exploded["year_tab"]).reindex(FIX_TYPES, fill_value=0)
counts["total"] = counts.sum(axis=1)
print(counts.to_string())
print(f"  rows with any fix: {(file_list['href_fix'] != '').sum()} of {len(file_list)}")
print(f"\nPDF check on {len(verification)} sample URLs (status 200/206 + Content-Type pdf + body starts %PDF; 1 KB read each):")
print(verification[["year_tab", "list_position", "href_fix", "status", "content_type",
                    "starts_pdf", "redirected", "ok"]].to_string(index=False))
print(f"\nWarnings: {len(warnings)}")
for w in warnings:
    print(f"  - {w}")
print("\nSample rows (first 3 per year):")
with pd.option_context("display.max_colwidth", 70, "display.width", 200):
    print(file_list.groupby("year_tab").head(3)[["year_tab", "list_position", "title", "filename"]].to_string(index=False))
