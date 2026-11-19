"""Excel / text / KMZ outputs. Every sheet gets a filter and a frozen header row."""

import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

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


# KMZ layers of the neighbor audit: folder name, KML color aabbggrr (line, fill), line width, visible
KMZ_LAYERS = {
    "lte_fans": ("LTE fans", "ffff7f00", "40ff7f00", 1, True),
    "nr_fans": ("NR fans", "ff00a5ff", "4000a5ff", 1, True),
    "current_relations": ("Current relations", "b3808080", None, 1, False),
    "missing_relations": ("Missing relations (ADD)", "ff00c800", None, 2, True),
    "delete_relations": ("Delete relations", "ff0000ff", None, 2, True),
}


def _kml_coords(ring):
    return " ".join(f"{x:.6f},{y:.6f},0" for x, y, *_ in ring)


def _kml_geometry(g):
    t, c = g["type"], g["coordinates"]
    if t == "Point":
        return f"<Point><coordinates>{_kml_coords([c])}</coordinates></Point>"
    if t == "LineString":
        return f"<LineString><tessellate>1</tessellate><coordinates>{_kml_coords(c)}</coordinates></LineString>"
    if t == "Polygon":
        rings = [f"<outerBoundaryIs><LinearRing><coordinates>{_kml_coords(c[0])}</coordinates></LinearRing></outerBoundaryIs>"]
        rings += [f"<innerBoundaryIs><LinearRing><coordinates>{_kml_coords(r)}</coordinates></LinearRing></innerBoundaryIs>"
                  for r in c[1:]]
        return f"<Polygon>{''.join(rings)}</Polygon>"
    if t.startswith("Multi"):
        part = t[5:]
        return "<MultiGeometry>" + "".join(_kml_geometry({"type": part, "coordinates": x}) for x in c) + "</MultiGeometry>"
    return ""


def _kml_placemark(f, style):
    props = f.get("properties") or {}
    name = props.get("cell") or (f"{props.get('source', '')} -> {props.get('target', '')}" if "source" in props else "")
    data = "".join(f'<Data name="{escape(str(k))}"><value>{escape("" if v is None else str(v))}</value></Data>'
                   for k, v in props.items())
    return (f"<Placemark><name>{escape(str(name))}</name><styleUrl>#{style}</styleUrl>"
            f"<ExtendedData>{data}</ExtendedData>{_kml_geometry(f['geometry'])}</Placemark>")


def write_kmz(path, layers, title):
    """One KMZ (Google Earth) with a folder per layer; layers = {key: [GeoJSON features]}."""
    parts = [f'<?xml version="1.0" encoding="UTF-8"?><kml xmlns="http://www.opengis.net/kml/2.2"><Document>'
             f"<name>{escape(title)}</name>"]
    for key in layers:
        _, line, fill, width, _ = KMZ_LAYERS.get(key, (key, "ff808080", None, 1, True))
        poly = f"<PolyStyle><color>{fill}</color></PolyStyle>" if fill else ""
        parts.append(f'<Style id="{key}"><LineStyle><color>{line}</color><width>{width}</width></LineStyle>{poly}</Style>')
    for key, features in layers.items():
        name, *_, visible = KMZ_LAYERS.get(key, (key, None, None, 1, True))
        parts.append(f"<Folder><name>{escape(name)} ({len(features):,})</name><visibility>{int(visible)}</visibility>"
                     + "".join(_kml_placemark(f, key) for f in features if f.get("geometry")) + "</Folder>")
    parts.append("</Document></kml>")
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as z:
        z.writestr("doc.kml", "".join(parts))
    return path
