# Project rules

1. Work on one step at a time. Never build beyond the step requested.
2. Pipeline scripts are numbered at the project root (`01_…py`), use `# %%` cell markers, take all paths and constants from `config.py`, and put shared helpers in `src/`.
3. Never modify anything in `data/raw/`. All outputs must be regenerable from raw.
4. Every extracted record must carry provenance: source file, page and table type.
5. Never silently drop, fill or guess data. Flag problems and report them.
6. Units: rupees (₹) and MWh as published. Sign convention for money: **+** means the generator receives from the pool, **−** means it pays the pool.
7. Revision handling: the latest version applies per week per table, not per file.
8. Each script ends by printing a clear summary (counts, warnings, samples) that the user can check.
9. Record any new assumption in `docs/assumptions_log.md`.
