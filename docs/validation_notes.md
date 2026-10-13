# Validation Notes

Step 07 (`07_validate.py`) checks the step 06 data for internal consistency and only reports; no data is changed.
Every check runs on all files; results are split into **selected** data (the step 05 base or an active overlay for
that week and section) and **superseded** data. Rounding tolerance: ₹1 and 0.01 MWh per row involved (summed over
the rows of a total). Full results: `data/interim/07_validation_results.csv`; revision diffs:
`data/interim/07_revision_diffs.csv`.

## Coverage checks

- All 87 weeks × 3 sections have a selected base, and every selected base has its expected tables: the TRAS-III
  table(s) for its layout (combined in 20 weeks, 2024-12-30 to 2025-05-12; separate in 67), TRAS market, SRAS
  payment and performance, SCUC and consolidated. 763 checks, 0 missing.
- Noted, not gaps:
  - 2025-01-13 SRAS: the base is the SRAS-only re-issue (srasjan25.2a), which has no consolidated table by design.
  - 2025-04-07 TRAS: the market table is present but empty (header and "Total 0 0" only).
  - 2025-06-09 TRAS: the Emergency table is "No data found" (explicit no data, A14).
- SRAS performance dates are exactly the 7 days of the table's week in every table (122 tables).
- All 22 stations in the 12 active overlays exist in the corresponding base table.

## Revision checks

- 178 pairs of consecutive versions (by upload time) were compared per week and table type; 80 differ, in 22
  weeks. TRAS-III is compared as one section (sum of a station's TRAS-III nets) so that layout changes between
  versions (combined → separate) do not hide differences. Narrow overlays are excluded here and shown in the
  overlay preview instead.
- Most revisions are in early 2025 (Feb–Jul). The largest single changes: week 2025-06-02 consolidated net
  −₹35.4 crore and SCUC −₹28.9 crore (tras.pdf → tras18.06.pdf); week 2025-04-21 TRAS-III −₹28.7 crore
  (trasapr25.3 → trasapr25.3a); week 2025-02-24 SCUC +₹14.8 crore.
- The June 2025 IPP re-issues (trasjun25.3b, 4b and 5b): their SRAS payment, SRAS performance and SCUC tables are
  **identical** to the previous versions (the rev1 files) in all three weeks, as A12 assumed. Their TRAS changes
  were described in step 05 (8 IPP stations' Emergency charges removed in 2025-06-30; DHARIWAL_ISTS Shortfall
  revised in 2025-06-16 and 2025-06-23).
- Overlay effects (net, ₹): the RGPPL SCUC overlays reduce RGPPL's SCUC net by ₹91.5 crore (2026-05-11) and
  ₹33.5 crore (2026-05-18); its TRAS Shortfall by ₹6.0 crore and ₹3.4 crore. The TPCL 3-week overlay repeats the
  base value exactly in 2026-04-20 and 2026-05-04, and changes 2026-04-13 by +₹89,873.

## Totals and reconciliation

No failures beyond tolerance in any reconciliation check, for selected or superseded data:
- Printed TOTAL rows equal the sum of rows for every column of every table instance (4,663 totals; 62 within
  rounding, largest 0.21).
- Row arithmetic holds by meaning in every row: TRAS-III net = (B) − (D); market I = C + D + G + H and
  N = I − K − M; SRAS total = incentive + energy charges (printed letters offset by one, see data dictionary);
  SCUC net = paid − refunded; consolidated net = sum of its components (largest rounding difference ₹0.50).
- Cross-table: every station's consolidated column equals its net in the matching component table, and every
  station absent from a component table shows 0 in the consolidated table (A15). Column totals match within
  rounding (largest ₹7 over 115 rows). 14 file-weeks were skipped because the component table is not in that file
  (TRAS-only restatements without SRAS or SCUC tables).
- Signs: every row's net sign agrees with its published status text (step 06).

## Open issues

- **SRAS performance above 100%**: 167 published values above 100% (max 168.7%), in 17 weeks from 2024-12-30 to
  2025-04-28, mostly NSPCL (133) and MOUDA (17). Recorded as published; whether to cap or exclude them is an
  analysis decision.
- **Negative TRAS-Down MWh in the combined layout**: 12 selected rows (gas stations Gandhar/Kawas RLNG/ComGas,
  Jan–May 2025) and 121 superseded rows (mostly three June 2025 originals where nearly the whole column is
  negative). The rupee columns reconcile; the sign of these energy quantities is as published.
- **Label conflict (A18)**: two June 2026 overlay tables headed "Shortfall Condition" with "Emergency" column
  labels are treated as Shortfall; the files have no consolidated table to confirm this directly.
- **2025-06-30 TRAS**: the IPP re-issue (trasjun25.5b) is the base; it removes the 8 IPP stations' Emergency
  charges (₹1.35 crore). This is the published latest version, but it is a material revision worth noting in any
  analysis of that week.
