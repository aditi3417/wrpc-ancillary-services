"""Cell-based table reader shared by steps 04, 05 and 06.

Uses pdfplumber's find_tables with the ruled-line setting. Each table comes back
with its page, position (bbox), and every row as a list of cells, with provenance
(file, page, table index, row index). Nothing is dropped: empty cells stay as "",
and the raw cell text (with its line breaks) is kept next to the joined text.

Reading happens in two passes because joining needs the whole corpus:
  1. read_page_tables(...) for every page, then classify_rows(table) for each table;
  2. known = unwrapped_names(all_tables); join_table(table, known) for each table.

Cell joining (A17): a cell that wraps over several lines is joined
  - in the station-name column: without a space if the joined name appears
    unwrapped elsewhere in the corpus; with a space if that spaced form does;
  - in text cells (every fragment contains a letter): with a space;
  - anything else: with a space, flagged ambiguous_join.
"""

import re

TABLE_SETTINGS = {"vertical_strategy": "lines", "horizontal_strategy": "lines"}

SERIAL_RE = re.compile(r"^\d{1,3}$")
NUMERIC_RE = re.compile(r"^\(?-?[\d,]+(?:\.\d+)?\)?$")
LETTER_RE = re.compile(r"[A-Za-z]")
DATE_RANGE_RE = re.compile(r"\d{1,2}[./-]\d{1,2}[./-]\d{2,4}")
TOTAL_RE = re.compile(r"^total$", re.I)
LINE_BREAK = "⏎"  # how line breaks inside a cell are shown in printed output


def read_page_tables(page, filename, page_no):
    """Return the ruled tables on one pdfplumber page, with raw cells and provenance."""
    tables = []
    for ti, tb in enumerate(page.find_tables(TABLE_SETTINGS)):
        rows = [{"row_index": ri, "raw_cells": ["" if c is None else c for c in r]}
                for ri, r in enumerate(tb.extract())]
        tables.append({"filename": filename, "page_no": page_no, "table_index": ti,
                       "bbox": [round(v, 1) for v in tb.bbox], "rows": rows})
    return tables


def _frags(cell):
    return [f.strip() for f in cell.split("\n") if f.strip()]


def _is_numeric(cell):
    return bool(NUMERIC_RE.match(cell.replace("\n", "").strip()))


def classify_rows(table):
    """Set row["kind"] (header / data / total / other) and row["name_col"] for each row.

    data: a numbered station row (serial number, then a station name), or an
          un-numbered station row followed only by numeric cells (SRAS performance).
    header: rows before the first data row. total: a TOTAL row. other: anything else.
    """
    seen_data = False
    for row in table["rows"]:
        cells = [c.strip() for c in row["raw_cells"]]
        row["name_col"] = None
        kind = "other"
        if any(TOTAL_RE.match(c.replace("\n", " ").strip()) for c in cells if c):
            kind = "total"
        elif cells and SERIAL_RE.match(cells[0]):
            name_col = next((i for i, c in enumerate(cells[1:], start=1)
                             if LETTER_RE.search(c) and not DATE_RANGE_RE.search(c)), None)
            if name_col is not None:
                kind, row["name_col"] = "data", name_col
        elif cells and LETTER_RE.search(cells[0]):
            rest = [c for c in cells[1:] if c]
            if len(rest) >= 5 and all(_is_numeric(c) for c in rest):
                kind, row["name_col"] = "data", 0
        if kind == "data":
            seen_data = True
        elif not seen_data and kind == "other":
            kind = "header"
        row["kind"] = kind
    return table


def unwrapped_names(tables):
    """Upper-cased station names that appear without a line break in any data row."""
    names = set()
    for t in tables:
        for row in t["rows"]:
            if row.get("kind") == "data" and row["name_col"] is not None:
                cell = row["raw_cells"][row["name_col"]].strip()
                if cell and "\n" not in cell:
                    names.add(cell.upper())
    return names


def join_cell(cell, is_name, known_names):
    """Return (joined text, rule). rule is "", name_nospace, name_space, text_space or ambiguous_join."""
    frags = _frags(cell)
    if len(frags) <= 1:
        return (frags[0] if frags else ""), ""
    spaced, nospace = " ".join(frags), "".join(frags)
    if is_name:
        if nospace.upper() in known_names:
            return nospace, "name_nospace"
        if spaced.upper() in known_names:
            return spaced, "name_space"
        return spaced, "ambiguous_join"
    if all(LETTER_RE.search(f) for f in frags):
        return spaced, "text_space"
    return spaced, "ambiguous_join"


def join_table(table, known_names):
    """Set row["cells"] (joined text) and row["join_flags"] ([(col, rule)]) for each row."""
    for row in table["rows"]:
        cells, flags = [], []
        for ci, raw in enumerate(row["raw_cells"]):
            text, rule = join_cell(raw, ci == row.get("name_col"), known_names)
            cells.append(text)
            if rule:
                flags.append((ci, rule))
        row["cells"], row["join_flags"] = cells, flags
    return table


def station_label(row):
    """Upper-cased station name of a data row, or None."""
    if row.get("kind") != "data" or row.get("name_col") is None:
        return None
    return row["cells"][row["name_col"]].upper()


def row_number(row):
    """Serial number of a numbered data row, or None."""
    if row.get("kind") == "data" and row.get("name_col", 0) >= 1:
        first = row["raw_cells"][0].strip()
        return int(first) if SERIAL_RE.match(first) else None
    return None


def assign_table_types(tables, type_tops):
    """Assign each table a page type: the type whose heading is nearest above the table.

    type_tops: {page_type: y of its heading on the page}, -1 for a type inherited from the
    previous page (it owns the top of the page). A table with no heading above it gets
    the type whose heading is highest on the page.
    """
    if not type_tops:
        for t in tables:
            t["table_type"] = ""
        return tables
    ordered = sorted(type_tops.items(), key=lambda kv: kv[1])
    for t in tables:
        top = t["bbox"][1]
        above = [ty for ty, y in ordered if y <= top + 1]
        t["table_type"] = above[-1] if above else ordered[0][0]
    return tables


def show(cells):
    """Cells as printable text: line breaks shown as ⏎, cells separated by |."""
    return " | ".join(c.replace("\n", LINE_BREAK) for c in cells)
