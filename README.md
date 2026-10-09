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
