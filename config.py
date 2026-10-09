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

# --- Scope -------------------------------------------------------------------
YEARS_NEEDED = [2024, 2025, 2026]
ANALYSIS_START = date(2025, 1, 1)
ANALYSIS_END = date(2026, 8, 31)

# --- HTTP --------------------------------------------------------------------
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
HTTP_HEADERS = {"User-Agent": USER_AGENT}
HTTP_TIMEOUT = 30  # seconds
HTTP_RETRIES = 2
