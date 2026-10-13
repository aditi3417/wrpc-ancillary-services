# WRPC Ancillary Services Time Series

## Project goal

Collate the Western Regional Power Committee (WRPC) **weekly** TRAS, SRAS and SCUC statements for calendar year 2025 and calendar year 2026 (up to August), using the **latest revision** of each statement, into a single time series for analysis. SCED monthly statements are excluded.

Source: <https://www.wrpc.gov.in>

## Setup

Requires Python 3.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

All paths and constants live in `config.py`.

## Project layout

```
config.py            paths and constants
src/                 shared helpers
data/raw/            source files as downloaded (never modified)
data/interim/        intermediate outputs
data/processed/      final datasets
notebooks/           exploration
outputs/             figures and tables
docs/                assumptions, data dictionary, validation notes, regulations
logs/                run logs
```

## Pipeline steps

| Step | Script | Status |
|------|--------|--------|
| 1 | `01_fetch_lists.py` | Built: fetches menu snapshots to `data/raw/menus/`, writes `data/interim/01_file_list.csv` |
| 2 | `02_build_manifest.py` | Built: classifies titles, expands to weeks, provisional base plus overlays per week x section; writes `data/interim/02_manifest.csv`, `02_week_coverage.csv`, `02_download_list.csv` |
| 3 | `03_download_pdfs.py` | Built: downloads every file on the step 02 download list to `data/raw/pdfs/` (validated, hash-checked on re-runs); writes `data/interim/03_download_log.csv` |
| 4 | `04_inventory_pages.py` | Built: page inventory (text layer, scans, page types, printed weeks) of all downloaded PDFs; writes `data/interim/04_page_inventory.csv`, `04_file_inventory.csv`, `04_format_timeline.csv`, `04_page_text.jsonl` |
| 5 | `05_finalise_selection.py` | Built: content-based base/overlay selection per week x section (station counts vs the same week's step 02 base); writes `data/interim/05_file_roles.csv`, `05_selection.csv` |
| 6 | `06_extract_tables.py` | Built: extracts every statement table (all versions) into tidy datasets, values as published; writes `data/interim/06_*.csv` |
| 7 | `07_validate.py` | Built: internal-consistency checks (totals, row arithmetic, cross-table, sanity, revisions, overlays, coverage), report only; writes `data/interim/07_validation_results.csv`, `07_revision_diffs.csv` |
| 8 | `08_build_timeseries.py` | Built: applies the step 05 selection and overlays, reconciles with the consolidated tables; writes `data/processed/08_facts.csv`, `08_weekly_summary.csv`, `08_sras_performance_daily.csv`, station name proposals and `docs/reference/station_master.csv` |
| 9 | `notebooks/09_analysis.ipynb` | Built: analysis notebook (Q1 cost over time, Q2 mechanisms incl. gross flows, Q3 who gets paid, Q4 reliability, Q5 SRAS performance, Q6 seasonality); reads step 08 outputs (and step 02/05/07 for Q4); writes `outputs/figures/09_*.png`, `outputs/tables/09_*.csv` incl. `09_key_numbers.csv`. Run with Restart & Run All |
