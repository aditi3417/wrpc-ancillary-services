# Data Dictionary

## Raw data

## Interim data

- `tables` (02_manifest.csv, 02_download_list.csv): tables is filled only when a title narrows a revision below whole-section level; blank means the whole section(s) listed in sections.

### Step 06 extracted tables (`data/interim/06_*.csv`)

Values are exactly as published: stations are not renamed, signs are not changed, and the step 05 selection and
overlays are not applied (every version of every table from all 127 files is included). Units: rupees (₹, "Rs")
and MWh as printed. A printed "-" or blank cell is stored as empty. Numbers are stored without thousands separators.

**Provenance columns (every dataset row):** `filename`, `page_no`, `table_id` (`<file>|p<page>|t<index>`),
`instance_id` (one table continuing over pages), `row_index` (row within the detected table), `row_no` (printed
serial number; empty for SRAS performance), `week_start` (Monday), `week_source`, `station_raw` (name as printed,
wrapped names joined per A17), `parse_failures` (`field='raw text'` for any value that failed to parse), `flags`.

`week_source`: `printed_in_section` (week printed on the table's page), `page_week_split` (4-week file, week from
step 04's page-to-week split), `week_column` (TPCL table's Week column), `manifest` (no week printed; the file's
single manifest week from its title, A13).

**Published sign convention.** Each row's status text is kept as published in `status_text`. In the TRAS tables,
the net is (E) = (B) − (D): positive = "Receivable by TRAS Provider", negative = "Payable by TRAS Provider". In SCUC,
positive = "Receivable by SCUC Provider". In the consolidated table, positive = "Receivable from Pool Account",
negative = "Payable to Pool Account". In SRAS payment, positive = "Receivable", negative = "Payable". Step 06
checked every row that has a status text (all types except TRAS market and SRAS performance): the sign always agrees with it. In the TRAS market table the net (N) is printed as "(+) Payable From Pool To AS Provider", i.e. + = the provider receives. This matches the project convention
(+ = generator receives from the pool, − = generator pays the pool), so no sign change is needed.

**`06_tras_shortfall.csv`, `06_tras_emergency.csv`, `06_tras_shortfall_emergency.csv`** (Format-TRAS-III; the
third is the combined Shortfall/Emergency layout used in files issued up to July 2025, A11):
`up_energy_mwh` (A, energy scheduled TRAS-Up, MWh), `up_charges_rs` (B, total charges/compensation TRAS-Up, ₹),
`down_energy_mwh` (C, energy scheduled TRAS-Down, MWh), `down_charges_rs` (D, TRAS-Down charges to be paid back to
the pool, ₹), `net_charges_rs` (E = B − D, ₹), `status_text`. `06_tras_emergency.csv` also has `week_text` (the
Week column, TPCL table only). Flag `header_label_conflict`: table under a "Shortfall Condition" heading whose
columns say Emergency (trasjun26.3a, trasjun26.2a (2)); kept as Shortfall, not reassigned.

**`06_tras_market.csv`** (Format-TRAS-II, Day Ahead and Real Time market; no status column):
`da_up_cleared_mwh` (A), `da_up_scheduled_mwh` (B), `da_up_energy_charges_rs` (C), `da_up_commitment_charges_rs` (D),
`rt_up_cleared_mwh` (E), `rt_up_scheduled_mwh` (F), `rt_up_energy_charges_rs` (G), `rt_up_commitment_charges_rs` (H),
`up_total_charges_rs` (I = C + D + G + H), `da_down_scheduled_mwh` (J), `da_down_charges_rs` (K, to be paid back to
pool), `rt_down_scheduled_mwh` (L), `rt_down_charges_rs` (M, to be paid back to pool), `net_charges_rs`
(N = I − K − M; printed: "(+) Payable From Pool To AS Provider, (−) Receivable By Pool From AS Provider").
MWh and ₹ as printed.

**`06_sras_payment.csv`** (Payments to the SRAS Service Provider from the DSM Pool): `sras_up_mwh` (C, total SRAS
up regulation, MWh, 5-min schedule), `sras_down_mwh` (D, total SRAS down regulation, MWh, 5-min schedule; printed
negative), `incentive_rs` (E, ₹), `net_sras_mwh` (F, total net SRAS, MWh, 15-min schedule),
`sras_energy_charges_rs` (G, printed "SRAS Energy Charges (Rs.) (E x Variable Cost)"), `total_charges_rs`
(H, printed "Total Charges (Rs) [(Payable) to Pool/ Receivable from Pool] D+F"), `status_text` (I). Note: the
printed formula letters are offset by one from the printed column letters (A–I): "E x Variable Cost" in column G
refers to the net SRAS energy in column F, and "D+F" in column H means incentive (E) + energy charges (G). Step 07
confirmed total_charges_rs = incentive_rs + sras_energy_charges_rs in every row (within ₹1). Recorded as printed.

**`06_sras_performance.csv`** (SRAS Actual Performance Statement, long format): one row per station and date:
`date` (from the printed column header, dd-Mon-yy), `performance_pct` (actual performance, % as printed).

**`06_scuc.csv`** (SCUC Account): `increment_mwh` (A, increment due to SCUC schedule [Western], MWh),
`decrement_mwh` (B, MWh), `charges_paid_to_generator_rs` (C = A × variable cost, ₹),
`charges_refunded_by_generator_rs` (D = B × variable cost, ₹), `net_charges_rs` (E = C − D, ₹), `status_text`.

**`06_consolidated.csv`** (Net Regional Shortfall/Emergency, TRAS and SCUC weekly statement): `sras_charges_rs` (A),
`shortfall_charges_rs` (B), `emergency_charges_rs` (B; both printed as "(B)"), `tras_market_charges_rs` (C),
`scuc_charges_rs` (D), `net_charges_rs` (A + B + C + D), `status_text`. Files with the 8-column layout (up to July
2025) have a single `shortfall_emergency_charges_rs` column instead of the two.

**`06_printed_totals.csv`** (printed TOTAL rows, long format for step 07): `filename`, `page_no`, `table_id`,
`instance_id`, `row_index`, `table_type`, `week_start`, `field` (dataset field name), `value`, `raw_text`,
`parse_failed`.

**`06_extraction_log.csv`** (one row per table instance, plus explicit "No data found" pages): `instance_id`,
`filename`, `table_type`, `pages`, `table_ids`, `header_variant`, `week_start` (`(per row)` for the Week-column
table), `week_source`, `rows_extracted`, `row_numbers_max`, `row_numbers_1_to_n`, `printed_total_rows`,
`other_rows`, `status` (`extracted`, `present_but_empty`, `explicit_no_data`), `flags`.

## Processed data

Built by step 08 from the step 06 data by applying the step 05 selection: for each week x section, the base file's
tables, then the active overlays in upload order (only the overlay's stations, only in the table types it contains;
A18 tables count as Shortfall). Weekly totals reconcile with the base file's consolidated table (step 08 stops if
they do not). Money: ₹ (INR), + = the generator receives from the pool, − = it pays the pool. Energy: MWh.

**`data/processed/08_facts.csv`** — one row per week x station x mechanism x direction x measure.
- `week_start` (Monday), `iso_year`, `iso_week`, `month` (month of the week's Thursday, A01).
- `station_id` (from `docs/reference/station_master.csv`), `plant_id` (plant grouping from the master; equals
  `station_id` unless a grouping is approved: GANDHAR-* units -> GANDHAR, KAWAS-* units -> KAWAS), `station_raw`
  (as printed).
- `mechanism`: `SRAS`; `TRAS_SHORTFALL`, `TRAS_EMERGENCY` (separate-layout weeks only); `TRAS_III_COMBINED`
  (combined-layout weeks only, Shortfall and Emergency not separable, A11); `TRAS_III_TOTAL` (derived, all 87 weeks:
  the combined table, or Shortfall + Emergency); `TRAS_MARKET_DAM`, `TRAS_MARKET_RTM`; `SCUC`.
- `direction`: `up`, `down`, `net` (SRAS net energy only) or blank (incentive, SRAS energy charges, net amount).
- `measure` and sign:
  - `energy_mwh`: energy scheduled (TRAS-III A/C; market scheduled B/F up, J/L down; SCUC increment/decrement;
    SRAS up/down regulation on the 5-min schedule, net on the 15-min schedule). Down energy is positive (A19).
  - `cleared_mwh`: market TRAS-Up cleared (A/E).
  - `energy_charges`: TRAS-III up (B, +) and down (D paid back to the pool, −); market up energy charges (C/G, +)
    and down charges paid back (K/M, −); SCUC paid to generator (C, +) and refunded by generator (D, −); SRAS energy
    charges (G, as printed, already in the project convention).
  - `commitment_charges`: market TRAS-Up commitment charges (D/H, +).
  - `incentive`: SRAS incentive (E, +).
  - `net_amount`: TRAS-III net (E = B − D); SRAS total (H = E + G); SCUC net (E = C − D); market DAM and RTM nets are
    derived (DAM = C + D − K, RTM = G + H − M; the printed N is for DAM + RTM together).
- `value` (project convention), `value_as_published` (printed value; blank for derived facts), `unit` (`MWh`/`INR`),
  `derived` (true for TRAS_III_TOTAL and market nets).
- Provenance: `source_file`, `page`, `from_overlay` (true if the row came from an active overlay), `flags` (from step
  06, e.g. `header_label_conflict`).

**`data/processed/08_weekly_summary.csv`** — one row per week (87).
- `week_start`, `week_end`, `iso_year`, `iso_week`, `month`.
- For each mechanism M: `M_up_mwh`, `M_down_mwh`, `M_net_rs`, `M_net_crore` (₹ crore = ₹1e7), `M_available`
  (the mechanism exists that week), and two provider counts:
  - `M_providers_any`: distinct stations with any non-zero value in that mechanism that week (energy or money,
    including rows with only MWh and no ₹ amount).
  - `M_providers_paid`: distinct stations with a non-zero ₹ net amount (`net_amount`) in that mechanism that week.
  - For `TRAS_III_TOTAL`, both counts are distinct stations across the Shortfall, Emergency and Combined tables
    (a station in both Shortfall and Emergency counts once). They are not taken from the derived total, where a
    station's Shortfall and Emergency amounts could cancel to zero.
- `total_net_payable_from_pool_to_providers_rs` / `_crore`: SRAS + TRAS_III_TOTAL + TRAS_MARKET_DAM +
  TRAS_MARKET_RTM + SCUC net amounts. Sign: **+** = net amount payable from the pool (the Regional Deviation and
  Ancillary Service Pool) to the AS providers, i.e. the generators receive; **−** = net amount receivable by the pool
  from the providers. Shortfall/Emergency/combined are not added separately (they are inside TRAS_III_TOTAL).
- Flags: `flag_overlay_applied`, `flag_combined_layout`, `flag_empty_market_table`, `flag_explicit_no_data`.

**`data/processed/08_sras_performance_daily.csv`** — one row per station x date from the selected SRAS base, as
published: `date`, `week_start`, `station_id`, `plant_id`, `station_raw`, `performance_pct`, `source_file`, `page`,
`above_100`
(published values above 100%, kept as published).

**`data/interim/08_station_name_proposals.csv`** — every `station_raw` with `proposed_station_id` (upper case, runs of
spaces/underscores -> one underscore), `exact_variants` (other raw forms with the same normalisation), `tables`,
`needs_review` (grouping beyond exact normalisation, or a name found only in SRAS performance), `review_reason`.

**`data/interim/08_station_similarity.csv`** — near-duplicate report (nothing is merged): pairs of `station_id`s with
string similarity ≥ `STATION_SIMILARITY_THRESHOLD` (0.85) after removing non-alphanumerics, or one name contained in
the other: `station_id_a`, `station_id_b`, `similarity`, `contained`, `plant_id_a`, `plant_id_b`,
`co_occurring_tables` (number of tables — same file, table type and week — listing both; > 0 means two distinct
stations), `example_co_occurrence`, `weeks_a`, `weeks_b`, `weeks_overlap`, `tables_a`, `tables_b`, `assessment`.

**`docs/reference/station_master.csv`** — approved mappings used by step 08: `station_raw`, `station_id`, `plant_id`, `reviewed`
(True for exact-normalisation matches; False for names needing review, which keep their own id and are not merged),
`notes` (reasoning and evidence for each mapping). Created once from the confident matches; never overwritten (new names are appended). To approve a merge,
edit `station_id` and set `reviewed` to True.

