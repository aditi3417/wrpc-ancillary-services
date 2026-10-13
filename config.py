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
DOWNLOAD_LOG_CSV = INTERIM_DIR / "03_download_log.csv"
PAGE_INVENTORY_CSV = INTERIM_DIR / "04_page_inventory.csv"
FILE_INVENTORY_CSV = INTERIM_DIR / "04_file_inventory.csv"
FORMAT_TIMELINE_CSV = INTERIM_DIR / "04_format_timeline.csv"
PAGE_TEXT_JSONL = INTERIM_DIR / "04_page_text.jsonl"
TABLES_JSONL = INTERIM_DIR / "04_tables.jsonl"  # cell-based tables (src/table_reader) on statement pages
TABLE_INSTANCES_CSV = INTERIM_DIR / "04_table_instances.csv"  # row-number check per table instance
EXTRACT_CSV = {  # step 06: one tidy dataset per table type
    "TRAS_SHORTFALL": INTERIM_DIR / "06_tras_shortfall.csv",
    "TRAS_EMERGENCY": INTERIM_DIR / "06_tras_emergency.csv",
    "TRAS_SHORTFALL_EMERGENCY": INTERIM_DIR / "06_tras_shortfall_emergency.csv",
    "TRAS_MARKET": INTERIM_DIR / "06_tras_market.csv",
    "SRAS_PAYMENT": INTERIM_DIR / "06_sras_payment.csv",
    "SRAS_PERFORMANCE": INTERIM_DIR / "06_sras_performance.csv",
    "SCUC": INTERIM_DIR / "06_scuc.csv",
    "CONSOLIDATED": INTERIM_DIR / "06_consolidated.csv",
}
PRINTED_TOTALS_CSV = INTERIM_DIR / "06_printed_totals.csv"
EXTRACTION_LOG_CSV = INTERIM_DIR / "06_extraction_log.csv"
REFERENCE_DIR = DOCS_DIR / "reference"
STATION_MASTER_CSV = REFERENCE_DIR / "station_master.csv"  # approved station_raw -> station_id mappings
STATION_PROPOSALS_CSV = INTERIM_DIR / "08_station_name_proposals.csv"
STATION_SIMILARITY_CSV = INTERIM_DIR / "08_station_similarity.csv"
STATION_SIMILARITY_THRESHOLD = 0.85  # near-duplicate report: string similarity after normalisation
FACTS_CSV = PROCESSED_DIR / "08_facts.csv"
WEEKLY_SUMMARY_CSV = PROCESSED_DIR / "08_weekly_summary.csv"
SRAS_PERFORMANCE_DAILY_CSV = PROCESSED_DIR / "08_sras_performance_daily.csv"
VALIDATION_RESULTS_CSV = INTERIM_DIR / "07_validation_results.csv"
REVISION_DIFFS_CSV = INTERIM_DIR / "07_revision_diffs.csv"
REVISION_SUMMARY_CSV = INTERIM_DIR / "07_revision_summary.csv"  # one row per consecutive version pair
VALIDATION_NOTES_MD = DOCS_DIR / "validation_notes.md"

# --- Validation tolerances (step 07) -------------------------------------------
# Differences up to these amounts per row involved (summed over the rows of a total) are rounding.
ROUNDING_TOL_RS_PER_ROW = 1.0
ROUNDING_TOL_MWH_PER_ROW = 0.01
FILE_ROLES_CSV = INTERIM_DIR / "05_file_roles.csv"
SELECTION_CSV = INTERIM_DIR / "05_selection.csv"

# --- Content-based selection (step 05) ----------------------------------------
# Station-count ratio of a candidate to the same week's step 02 base, per table.
COMPLETENESS_THRESHOLD = 0.9  # ratio >= this: complete restatement of the section
REVIEW_BAND = (0.5, 0.9)  # ratio inside [low, high): needs_review; below low: narrow (overlay)

# --- PDF download ------------------------------------------------------------
# False: keep existing PDFs whose hash matches the download log; report any
# existing PDF that does not match instead of overwriting it.
# True: download every file again and replace the existing copy.
REDOWNLOAD = False
DOWNLOAD_DELAY_SECONDS = (1.0, 2.0)  # random pause between requests (min, max)

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
