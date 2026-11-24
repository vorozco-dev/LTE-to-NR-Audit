"""Excel / text outputs. Every sheet gets a filter and a frozen header row."""

from pathlib import Path

import pandas as pd
from openpyxl.utils import get_column_letter

EXCEL_MAX_ROWS = 1_048_000


def write_excel(path, sheets):
    with pd.ExcelWriter(path, engine="openpyxl") as xw:
        for name, df in sheets.items():
            if df is None:
                continue
            if not isinstance(df, pd.DataFrame):
                df = pd.DataFrame(df)
            if len(df) > EXCEL_MAX_ROWS:
                df = df.head(EXCEL_MAX_ROWS)
            df.to_excel(xw, sheet_name=name[:31], index=False)
            ws = xw.sheets[name[:31]]
            ws.freeze_panes = "A2"
            if len(df.columns):
                ws.auto_filter.ref = f"A1:{get_column_letter(len(df.columns))}{max(1, len(df) + 1)}"
            for i, col in enumerate(df.columns, 1):
                sample = [str(col)] + [str(v) for v in df[col].head(200).tolist()]
                ws.column_dimensions[get_column_letter(i)].width = min(60, max(10, max(map(len, sample)) + 2))
    return path


def write_lines(path, lines):
    Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path
