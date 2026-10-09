"""Project-wide paths and constants. All pipeline scripts import from here."""

from datetime import date
from pathlib import Path

# --- Paths -------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent

DATA_DIR = PROJECT_ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
RAW_MENUS_DIR = RAW_DIR / "menus"
RAW_PDFS_DIR = RAW_DIR / "pdfs"
INTERIM_DIR = DATA_DIR / "interim"
PROCESSED_DIR = DATA_DIR / "processed"

OUTPUTS_DIR = PROJECT_ROOT / "outputs"
FIGURES_DIR = OUTPUTS_DIR / "figures"
TABLES_DIR = OUTPUTS_DIR / "tables"

NOTEBOOKS_DIR = PROJECT_ROOT / "notebooks"
DOCS_DIR = PROJECT_ROOT / "docs"
REGULATIONS_DIR = DOCS_DIR / "regulations"
LOGS_DIR = PROJECT_ROOT / "logs"

# --- Source ------------------------------------------------------------------
BASE_URL = "https://www.wrpc.gov.in"
MENU_API = BASE_URL + "/api/TopMenu/{menu_id}"
SEED_MENU_ID = 1107

# Menu responses are saved as timestamped snapshots in RAW_MENUS_DIR and never
# overwritten. False: reuse the latest saved snapshot per menu (fetch only if
# none exists). True: fetch and save a new snapshot.
REFRESH_MENUS = False

# --- Step outputs ------------------------------------------------------------
FILE_LIST_CSV = INTERIM_DIR / "01_file_list.csv"
MANIFEST_CSV = INTERIM_DIR / "02_manifest.csv"
WEEK_COVERAGE_CSV = INTERIM_DIR / "02_week_coverage.csv"
DOWNLOAD_LIST_CSV = INTERIM_DIR / "02_download_list.csv"

# --- Manifest checks ---------------------------------------------------------
# Flag a date mismatch only when the filename upload date and the title date
# differ by more than this many days (uploads often land a day or two later).
DATE_MISMATCH_TOLERANCE_DAYS = 2

# --- Scope -------------------------------------------------------------------
YEARS_NEEDED = [2024, 2025, 2026]

# Describe the analysis period only; not used for filtering.
ANALYSIS_START = date(2025, 1, 1)
ANALYSIS_END = date(2026, 8, 31)

# Weeks run Monday to Sunday and belong to the year containing their Thursday
# (ISO 8601 week-year). The same rule is applied to months (our extension).
# See A01 in docs/assumptions_log.md.
FIRST_WEEK_START = date(2024, 12, 30)  # Monday of ISO week 2025-W01
LAST_WEEK_START = date(2026, 8, 24)  # Monday of the last week in Aug 2026
assert FIRST_WEEK_START.weekday() == 0, "FIRST_WEEK_START must be a Monday"
assert LAST_WEEK_START.weekday() == 0, "LAST_WEEK_START must be a Monday"
EXPECTED_WEEKS = (LAST_WEEK_START - FIRST_WEEK_START).days // 7 + 1

# --- HTTP --------------------------------------------------------------------
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
HTTP_HEADERS = {"User-Agent": USER_AGENT}
HTTP_TIMEOUT = 30  # seconds
HTTP_RETRIES = 2
