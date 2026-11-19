"""
Convert the EPT workbook (EPT.xlsx) into a clean ept.csv used by the LTE->NR audit.

Rules (same criteria as the propagation_vector project):
  - Only these sheets are read, in priority order (inventory before plan):
        EPT_3G_LTE_OUTDOOR, EPT_3G_LTE_INDOOR, PLAN_OUTDOOR, PLAN_INDOOR, EVENTOS_ESPECIALES
  - '-' and blanks are empty values; coordinates/azimuth accept a decimal comma.
  - cell_name = CellName (OSS name), or ATT_CELL_ID_Name when CellName is empty (planned
    cells); the AT&T name is always kept in att_cell_name and the origin in name_from.
  - One row per cell: when a CellName appears in several sheets (or twice in one sheet)
    the row of the highest-priority sheet wins, then the first row of that sheet.
  - The sheet of origin is kept in the column "source".

Usage (from ./code):
    py -3.13 ept_process.py [EPT.xlsx] [ept.csv]
Defaults from code/.env (EPT_XLSX, EPT_CSV; else <ROOT_DIRECTORY>/input/11172026/EPT.xlsx
-> <ROOT_DIRECTORY>/output/ept.csv). ept.csv is an output: it is regenerated from the EPT.
"""

import io
import os
import re
import sys
import time

import pandas as pd

EPT_SHEETS = ("EPT_3G_LTE_OUTDOOR", "EPT_3G_LTE_INDOOR", "PLAN_OUTDOOR", "PLAN_INDOOR",
              "EVENTOS_ESPECIALES")

# EPT column (normalized: stripped, lower-case, spaces -> _) -> ept.csv column
COLUMNS = {
    "cellname": "cell_name",
    "att_cell_id_name": "att_cell_name",
    "cellname_anterior": "cell_name_prev",
    "at&t_site_name": "site",
    "at&t_node_name": "att_node_name",
    "node_b_u2000": "node_u2000",
    "node_b_u2000_anterior": "node_u2000_prev",
    "ne_name": "ne_name",
    "enodeb_function": "enodeb_function",
    "at&t_name": "site_label",
    "at&t_tech": "tech",
    "vendor": "vendor",
    "node_b_id": "node_id",
    "physical_sector": "sector",
    "local_cell_id": "local_cell_id",
    "psc/pci": "pci",
    "tac": "tac",
    "band_indicator": "band_indicator",
    "dl_uarfcn/earfcn": "dl_arfcn",
    "ssb_frequency": "ssb_frequency",
    "latitud": "latitude",
    "longitud": "longitude",
    "geographic_azimuth": "azimuth",
    "beam_width": "beam",
    "antenna_height": "antenna_height",
    "cell_radius_(m)": "cell_radius_m",
    "coverage": "coverage",
    "solution": "solution",
    "traffic_of_last_month": "traffic_last_month",
    "state": "state",
    "market": "market",
}
NUMERIC = ("latitude", "longitude", "azimuth", "beam", "antenna_height", "cell_radius_m")
TECH = {"LTE": "4G", "4G": "4G", "UMTS": "3G", "WCDMA": "3G", "3G": "3G",
        "GSM": "2G", "2G": "2G", "NR": "5G", "5G": "5G"}


def norm_col(name):
    return re.sub(r"\s+", "_", str(name).strip().lower())


def norm_tech(v):
    if not isinstance(v, str):
        return None
    v = v.strip().upper()
    return next((t for p, t in TECH.items() if v.startswith(p)), v)


def read_sheets(path, log=print):
    xls = pd.ExcelFile(path)
    by_upper = {s.strip().upper(): s for s in xls.sheet_names}
    missing = [s for s in EPT_SHEETS if s not in by_upper]
    if missing:
        log(f"  Warning: sheet(s) not found: {', '.join(missing)}")
    frames = []
    for prio, sheet in enumerate(EPT_SHEETS):
        if sheet not in by_upper:
            continue
        t = time.time()
        df = xls.parse(by_upper[sheet], dtype=str, usecols=lambda c: norm_col(c) in COLUMNS)
        df.columns = [COLUMNS[norm_col(c)] for c in df.columns]
        df = df.loc[:, ~df.columns.duplicated()]
        df["source"] = sheet
        df["_prio"] = prio
        log(f"  {sheet}: {len(df):,} rows ({time.time() - t:.0f}s)")
        frames.append(df)
    if not frames:
        raise ValueError(f"None of the sheets {', '.join(EPT_SHEETS)} found in {path}")
    return pd.concat(frames, ignore_index=True)


def clean(df, log=print):
    for c in df.columns:
        if df[c].dtype == object:
            df[c] = df[c].str.strip().replace({"-": None, "": None, "nan": None})
    # CellName is the OSS name; ATT_CELL_ID_Name is the AT&T standard name. Planned cells
    # often have only the AT&T name, so it is used when CellName is empty.
    df["name_from"] = "CellName"
    only_att = df["cell_name"].isna() & df["att_cell_name"].notna()
    df.loc[only_att, "cell_name"] = df.loc[only_att, "att_cell_name"]
    df.loc[only_att, "name_from"] = "ATT_CELL_ID_Name"
    log(f"  {int(only_att.sum()):,} rows without CellName use ATT_CELL_ID_Name")
    df = df[df["cell_name"].notna()].copy()
    for c in NUMERIC:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c].str.replace(",", ".", regex=False), errors="coerce")
    df["tech"] = df["tech"].map(norm_tech)
    if "vendor" in df.columns:
        df["vendor"] = df["vendor"].str.title()

    dup_same = df.duplicated(["cell_name", "_prio"]).sum()
    total = len(df)
    df = (df.sort_values("_prio", kind="stable")
            .drop_duplicates("cell_name", keep="first")
            .drop(columns="_prio")
            .reset_index(drop=True))
    log(f"  Rows with CellName: {total:,} -> unique cells: {len(df):,} "
        f"({total - len(df):,} duplicates dropped, {dup_same:,} of them inside the same sheet)")
    return df


def to_frame(src, log=print):
    """EPT.xlsx -> DataFrame of text columns, exactly as ept.csv would read back (the ept table
    of the database is loaded from it)."""
    df = _converted(src, log)
    buf = io.StringIO()
    df.to_csv(buf, index=False)
    buf.seek(0)
    return pd.read_csv(buf, dtype=str, low_memory=False)


def _converted(src, log=print):
    log(f"Reading {src} (a large workbook takes a few minutes)")
    df = clean(read_sheets(src, log), log)
    no_xy = int((df["latitude"].isna() | df["longitude"].isna()).sum())
    log("  By technology: " + ", ".join(f"{k} {v:,}" for k, v in df["tech"].value_counts().items()))
    log("  By source: " + ", ".join(f"{k} {v:,}" for k, v in df["source"].value_counts().items()))
    if no_xy:
        log(f"  Warning: {no_xy:,} cells without latitude/longitude")
    return df


def convert(src, dst, log=print):
    """EPT.xlsx -> ept.csv (console use). Returns the number of cells written."""
    df = _converted(src, log)
    os.makedirs(os.path.dirname(os.path.abspath(dst)), exist_ok=True)
    tmp = dst + ".tmp"
    df.to_csv(tmp, index=False, encoding="utf-8")
    os.replace(tmp, dst)
    log(f"Written {dst}: {len(df):,} cells, {len(df.columns)} columns")
    return len(df)


def main(argv):
    from lne_config import EPT_CSV, EPT_XLSX     # paths from code/.env
    src = argv[1] if len(argv) > 1 else EPT_XLSX
    dst = argv[2] if len(argv) > 2 else (os.path.join(os.path.dirname(src), "ept.csv") if len(argv) > 1 else EPT_CSV)
    convert(src, dst)


if __name__ == "__main__":
    main(sys.argv)
