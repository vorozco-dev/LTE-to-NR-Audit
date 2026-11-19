"""
Cell geometry (EPT) and cell radius (propagation) for the fans.

EPT (table ept of the database, converted by ept_process.py), lookup order for a cell of node N:
  1. EXACT   cell name = CellName or ATT_CELL_ID_Name
  2. SECTOR  same site code (from the node name) and same sector number: position, azimuth
             and beam of that sector's other cells
  3. SITE    same site code: position only, no azimuth -> not usable for ADD decisions
  4. NONE
The site code is [A-Z]{6}\\d{4} inside the node name (UDIFMGH0796 -> DIFMGH0796), which
matches the EPT AT&T_Site_Name for 99.8% of the tul cells found by name.

Propagation: max85coverage_km per cell from the propagation tables (propagation_table_e4g
for LTE, propagation_table_e5g for NR) found in the input folder (zip or csv). The Load Data
step loads them into the database (propagation_e4g / propagation_e5g). A value
of 0 or a missing cell is "unknown radius", never a zero-size fan.
"""

import re
import zipfile
from pathlib import Path

import pandas as pd

from lne_ids import sector_of, site_code


class Ept:
    def __init__(self, path, log=print, df=None):
        """path: ept.csv, or a label when df (the ept table of the database) is given."""
        if df is None:
            if not Path(path).is_file():
                raise FileNotFoundError(f"EPT csv not found: {path} (GUI: 1. Load Data -> Process; "
                                        "console: ept_process.py)")
            df = pd.read_csv(path, dtype=str, low_memory=False)
        df = df.copy()
        for c in ("latitude", "longitude", "azimuth", "beam"):
            df[c] = pd.to_numeric(df[c], errors="coerce")
        df = df[df["latitude"].between(-90, 90) & df["longitude"].between(-180, 180)]
        df["site_u"] = df["site"].fillna("").str.upper()
        self.by_name = {}
        for col in ("att_cell_name", "cell_name"):        # cell_name wins
            for rec in df[df[col].notna()].to_dict("records"):
                self.by_name[rec[col].upper()] = rec
        sec = df.dropna(subset=["azimuth"]).copy()
        sec["sector_k"] = sec["sector"].fillna("").str.replace(r"\.0$", "", regex=True)
        self.by_sector = {}
        for rec in sec.to_dict("records"):
            self.by_sector.setdefault((rec["site_u"], rec["sector_k"]), rec)
        self.by_site = {}
        for site, g in df.groupby("site_u"):
            self.by_site[site] = (float(g["latitude"].median()), float(g["longitude"].median()))
        # NR cells by (gNB ID, local cell ID): EPT Node_B_ID equals the gNB ID for NR cells,
        # which resolves relation targets that belong to another ENM.
        nr = df[(df["tech"] == "5G") & df["node_id"].notna() & df["local_cell_id"].notna()]
        self.by_gnb = {}
        for rec in nr.to_dict("records"):
            try:
                self.by_gnb.setdefault((int(float(rec["node_id"])), int(float(rec["local_cell_id"]))), rec)
            except ValueError:
                pass
        # NR sites on air (inventory sheets, not plan) for the tier topology
        live = df[(df["tech"] == "5G") & ~df["source"].str.startswith("PLAN")]
        self.nr_sites = {s: self.by_site[s] for s in set(live["site_u"]) if s in self.by_site}
        log(f"EPT: {len(df):,} cells with coordinates from {Path(path).name} "
            f"({len(self.nr_sites):,} NR sites on air)")

    def geometry(self, node, cell, default_beam):
        """dict(lat, lon, azimuth, beam, quality, evidence) — quality EXACT|SECTOR|SITE|NONE."""
        rec = self.by_name.get(str(cell).upper())
        quality = "EXACT"
        if rec is None:
            site = site_code(node) or site_code(cell)
            rec = self.by_sector.get((site, sector_of(cell)))
            quality = "SECTOR"
            if rec is None:
                pos = self.by_site.get(site)
                if pos is None:
                    return {"lat": None, "lon": None, "azimuth": None, "beam": None,
                            "quality": "NONE", "evidence": "not in EPT (name, site, sector)"}
                return {"lat": pos[0], "lon": pos[1], "azimuth": None, "beam": None,
                        "quality": "SITE", "evidence": f"site {site} position; no sector match"}
        beam = rec.get("beam")
        beam_note = ""
        if beam is None or pd.isna(beam) or not 0 < beam <= 360:
            beam, beam_note = default_beam, f"; beam missing -> {default_beam:g}"
        az = rec.get("azimuth")
        return {"lat": float(rec["latitude"]), "lon": float(rec["longitude"]),
                "azimuth": None if pd.isna(az) else float(az) % 360, "beam": float(beam),
                "quality": quality if not pd.isna(az) else "SITE",
                "evidence": f"{quality}: {rec['cell_name']} ({rec.get('source', '')}){beam_note}"}


class Propagation:
    """
    max85coverage_km per cell (metres in radius_m), on-demand: whatever tables are in the folder.
    Files accepted (loose or inside a .zip):
        propagation_table_e4g*.csv / propagation_table_e5g*.csv   (raw tables, e.g. ..._w43_2026.csv)
        propagation_e4g.csv / propagation_e5g.csv                  (written by process() in the work directory)
    e4g = LTE, e5g = NR (the e5g km already include the cell range); other tables are ignored.
    `sources` is a path or a list of paths (folder, zip or csv); the first one with tables is used.
    """
    KEYS = {"LTE": "e4g", "NR": "e5g"}
    NAME_RE = re.compile(r"^propagation_(?:table_)?(?P<key>[ehn][345]g)(?:_\w*)?\.csv$", re.I)

    def __init__(self, sources, log=print):
        self.radius = {"LTE": {}, "NR": {}}
        self.source = ""
        for src in ([sources] if isinstance(sources, (str, Path)) else sources):
            members = find_propagation(src)
            if members:
                self._load(members, log)
                return
        log(f"No propagation tables found ({sources}): every fan radius will be unknown")

    def _load(self, members, log):
        names = []
        for tech, key in self.KEYS.items():
            parts = [m for m in members if m[2] == key]
            if not parts:
                log(f"Propagation {tech}: no propagation_table_{key} file")
                continue
            df = read_propagation(parts)
            self.radius[tech] = dict(zip(df["cell_name"].str.upper(), df["max85coverage_km"] * 1000))
            files = ", ".join(_label(p, m) for p, m, _ in parts)
            names.append(files)
            log(f"Propagation {tech}: {len(self.radius[tech]):,} cells with max85 radius ({files})")
        self.source = "; ".join(names)

    @classmethod
    def from_frames(cls, frames, source, log=print):
        """frames: {"e4g": df, "e5g": df} with cell_name, max85coverage_km (the database tables)."""
        self = cls([], log=lambda _m: None)
        self.source = source
        for tech, key in cls.KEYS.items():
            df = frames.get(key)
            if df is None or not len(df):
                log(f"Propagation {tech}: no propagation_{key} table")
                continue
            km = pd.to_numeric(df["max85coverage_km"], errors="coerce")
            self.radius[tech] = {c.upper(): k * 1000 for c, k in zip(df["cell_name"].astype(str), km) if k > 0}
            log(f"Propagation {tech}: {len(self.radius[tech]):,} cells with max85 radius (propagation_{key}, {source})")
        return self

    def radius_m(self, tech, cell):
        return self.radius[tech].get(str(cell).upper())


def _label(path, member):
    return f"{Path(member).name} [{Path(path).name}]" if member else Path(path).name


def find_propagation(source):
    """[(path, member or None, key)] for the e4g/e5g csv files of a folder, zip or csv."""
    if not source:
        return []
    src = Path(source)
    paths = sorted(src.iterdir()) if src.is_dir() else [src] if src.is_file() else []
    found = []
    for p in paths:
        if p.name.lower().endswith(".zip") and "propagation" in p.name.lower():
            with zipfile.ZipFile(p) as z:
                for m in z.namelist():
                    hit = Propagation.NAME_RE.match(Path(m).name)
                    if hit and hit["key"].lower() in Propagation.KEYS.values():
                        found.append((p, m, hit["key"].lower()))
        else:
            hit = Propagation.NAME_RE.match(p.name)
            if hit and hit["key"].lower() in Propagation.KEYS.values():
                found.append((p, None, hit["key"].lower()))
    return found


def read_propagation(parts):
    """cell_name, max85coverage_km (> 0, one row per cell) from several files of the same table."""
    frames = []
    for path, member, _ in parts:
        cols = {"usecols": ["cell_name", "max85coverage_km"], "dtype": {"cell_name": str}}
        if member:
            with zipfile.ZipFile(path) as z, z.open(member) as f:
                frames.append(pd.read_csv(f, **cols))
        else:
            frames.append(pd.read_csv(path, **cols))
    df = pd.concat(frames, ignore_index=True)
    df["max85coverage_km"] = pd.to_numeric(df["max85coverage_km"], errors="coerce")
    return df[df["max85coverage_km"] > 0].dropna(subset=["cell_name"]).drop_duplicates("cell_name")
