"""
Load Data step (GUI tab 1): check an input folder and process it into the work directory.

    scan(folder)                    -> Scan: what is in the folder, what is missing (no parsing)
    process(scan, workdir, db)      -> the rows of <workdir> in db/lte_nr.sqlite: ENM tables of every ENM,
                                       ept (EPT.xlsx converted, or reused when unchanged), propagation e4g/e5g

Expected input folder (one on-demand extraction):
    EPT.xlsx                                         (or any *EPT*.xlsx)
    propagation tables e4g + e5g                     zip (propagation_tables_*.zip) or csv
    <table>_<enm>.txt for tul, ptl, tlt              loose or inside zip / rar / 7z
"""

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

import ept_process
import lne_db
import lne_enm
import lne_inventory
from lne_geo_inputs import Propagation, find_propagation, read_propagation

ENMS = ("tul", "ptl", "tlt")


@dataclass
class Scan:
    folder: str
    files: list = field(default_factory=list)            # (name, size MB)
    ept_xlsx: str = ""
    propagation: dict = field(default_factory=dict)      # key -> [file labels]
    enm: dict = field(default_factory=dict)              # enm -> {mo: [basenames]}
    lines: list = field(default_factory=list)            # report text

    def enm_ready(self, enm):
        found = self.enm.get(enm, {})
        return all(found.get(mo) for mo, req in lne_enm.MO_FILES.items() if req)

    @property
    def ready_enms(self):
        return [e for e in ENMS if self.enm_ready(e)]

    @property
    def ready(self):
        return bool(self.ept_xlsx) and all(self.propagation.get(k) for k in Propagation.KEYS.values()) \
            and bool(self.ready_enms)


def _find_ept(folder):
    cands = sorted(p for p in Path(folder).glob("*.xlsx") if "ept" in p.name.lower() and not p.name.startswith("~$"))
    exact = [p for p in cands if p.name.lower() == "ept.xlsx"]
    return str((exact or cands)[0]) if cands else ""


def scan(folder, log=print):
    s = Scan(str(folder))
    out = s.lines

    def say(msg=""):
        out.append(msg)
        log(msg)

    folder = Path(folder)
    say(f"Input folder: {folder}")
    if not folder.is_dir():
        say("  ✗ folder not found")
        return s
    for p in sorted(folder.iterdir()):
        if p.is_file():
            s.files.append((p.name, p.stat().st_size / 1e6))
            say(f"    {p.name:<50} {p.stat().st_size / 1e6:>9,.1f} MB")
    say()

    s.ept_xlsx = _find_ept(folder)
    say("EPT workbook")
    say(f"  ✓ {Path(s.ept_xlsx).name}" if s.ept_xlsx else "  ✗ MISSING  EPT.xlsx")
    say()

    say("Propagation tables (max85coverage_km)")
    members = find_propagation(folder)
    for tech, key in Propagation.KEYS.items():
        labels = [f"{Path(m).name} [{p.name}]" if m else p.name for p, m, k in members if k == key]
        s.propagation[key] = labels
        say(f"  ✓ {key} ({tech}): {', '.join(labels)}" if labels else f"  ✗ MISSING  propagation_table_{key} ({tech})")
    say()

    for enm in ENMS:
        sources = lne_enm.find_sources(folder, enm)
        found = {}
        for mo in lne_enm.MO_FILES:
            pref = f"{mo}_{enm}".lower()
            found[mo] = sorted(b for b in sources
                               if b.lower() == pref + ".txt" or b.lower().startswith(pref + "_"))
        s.enm[enm] = found
        n_req = sum(1 for mo, r in lne_enm.MO_FILES.items() if r)
        n_ok = sum(1 for mo, r in lne_enm.MO_FILES.items() if r and found[mo])
        arch = sorted({sources[b][0].name for files in found.values() for b in files if sources[b][0]})
        where = f" (in {', '.join(arch)})" if arch else ""
        if not any(found.values()):
            say(f"ENM {enm}: no files <table>_{enm}.txt")
        else:
            say(f"ENM {enm}: {n_ok}/{n_req} required tables{where}")
            for mo, req in lne_enm.MO_FILES.items():
                if found[mo]:
                    say(f"  ✓ {mo:<26} {', '.join(found[mo])}")
                else:
                    say(f"  {'✗ MISSING ' if req else '· optional'} {mo}_{enm}.txt")
        say()

    say("Result")
    say(f"  EPT: {'OK' if s.ept_xlsx else 'MISSING'} · propagation: "
        f"{'OK' if all(s.propagation.values()) else 'INCOMPLETE'} · ENM ready: "
        f"{', '.join(s.ready_enms) or 'none'}")
    say("  ✓ Ready to Process" if s.ready else "  ✗ Not ready: complete the missing files and Load again")
    return s


def node_gap_status(t, nodes, show=6):
    """Status of one ENM table for the report and 4. DB Status: for a table with ENM errors how many
    nodes were not read out of all of them, and the first names (see lne_enm.partial_status)."""
    return lne_enm.partial_status(t, nodes, show)


def ept_source(xlsx):
    """Identity of the workbook (path, size, modification time) stored in meta as ept_source."""
    st = os.stat(xlsx)
    return json.dumps({"path": os.path.normcase(os.path.abspath(xlsx)), "size": st.st_size,
                       "mtime": int(st.st_mtime)}, sort_keys=True)


def ept_from_db(db_path, source, prefer=None):
    """(workdir, ept rows) of a loaded workdir whose EPT came from the same workbook, else (None, None).
    The workdir being processed (prefer) is tried first."""
    wds = lne_db.workdirs(db_path)
    for wd in sorted(wds, key=lambda w: w != prefer):
        if lne_db.meta(db_path, wd).get("ept_source") == source:
            df = lne_db.read_frame(db_path, "ept", wd)
            if df is not None and len(df):
                return wd, df
    return None, None


def process(s, workdir, db_path, enms=None, log=print):
    """Load the input folder into db_path under the workdir name (the last folder of workdir,
    whose previous rows are replaced) and write load_report.txt to workdir.
    EPT.xlsx is converted only when no workdir of the database was loaded from the same workbook
    (same path, size and date); otherwise its ept rows are reused. Returns the list of ENMs loaded."""
    work = Path(workdir)
    work.mkdir(parents=True, exist_ok=True)
    lines = []

    def say(msg=""):
        lines.append(msg)
        log(msg)

    say(f"Processing {s.folder}")
    say(f"  database {db_path}")
    say(f"  workdir  {work.name}  ({work})")
    say()
    say("EPT")
    if not s.ept_xlsx:
        raise FileNotFoundError("No EPT workbook in the input folder")
    source = ept_source(s.ept_xlsx)
    from_wd, ept = ept_from_db(db_path, source, prefer=work.name)
    if ept is not None:
        say(f"  ✓ {Path(s.ept_xlsx).name} unchanged: ept reused from workdir {from_wd} (not converted again)")
    else:
        ept = ept_process.to_frame(s.ept_xlsx, log)

    db = lne_db.Writer(db_path, work.name, say)
    try:
        db.meta(input_dir=s.folder, output_dir=str(work), ept_xlsx=s.ept_xlsx, ept_source=source,
                loaded_at=lne_db.stamp())
        db.frame("ept", ept)
        say(f"  ✓ table ept: {len(ept):,} cells")
        say()

        say("Propagation")
        members = find_propagation(s.folder)
        for tech, key in Propagation.KEYS.items():
            parts = [m for m in members if m[2] == key]
            if not parts:
                say(f"  ✗ propagation_table_{key} ({tech}): NOT FOUND")
                continue
            df = read_propagation(parts)
            db.frame(f"propagation_{key}", df)
            say(f"  ✓ table propagation_{key} ({tech}): {len(df):,} cells")
        say()

        loaded = []
        for enm in (enms or s.ready_enms):
            say(f"ENM {enm}")
            sources = lne_enm.find_sources(s.folder, enm)
            tables = {mo: lne_enm.read_mo(s.folder, mo, enm, lambda _m: None, sources) for mo in lne_enm.MO_FILES}
            gaps = lne_inventory.read_gaps(tables)
            lte_nodes = lne_inventory._nodes(tables, "EUtranCellFDD")
            gaps = {n: g for n, g in gaps.items() if n in lte_nodes}
            for mo, t in tables.items():
                status = node_gap_status(t, sorted(n for n, g in gaps.items() if mo in g))
                db.mo_table(enm, t, status)
                if not t.files:
                    mark = "·" if not lne_enm.MO_FILES[mo] else "✗"
                    say(f"  {mark} {mo:<26} {'':>9}  {status}")
                elif t.failed:
                    say(f"  ⚠ {mo:<26} {'':>9}  {status}")
                else:
                    say(f"  ✓ {mo:<26} {t.rows:>9,} rows  {status}")
            if gaps:
                say(f"  Nodes not fully read by ENM: {len(gaps)} (node status INCOMPLETE: ADD proposals held; "
                    "re-export these nodes to audit them fully)")
                for n in sorted(gaps):
                    say(f"        {n:<16} {', '.join(gaps[n])}")
            loaded.append(enm)
            say()
        db.meta(enms=",".join(loaded))
        db.close()
    except BaseException:
        db.abort()
        raise

    say(f"Done. Run Audit reads workdir {work.name} from {db_path}")
    (work / "load_report.txt").write_text("\n".join(s.lines + ["", "-" * 80, ""] + lines), encoding="utf-8")
    return loaded


def main():
    import argparse
    from lne_config import DB_PATH, INPUT_DIR, OUTPUT_ROOT, today
    ap = argparse.ArgumentParser(description="Load an input folder into the database (GUI 1. Load Data)")
    ap.add_argument("--input", default=INPUT_DIR)
    ap.add_argument("--workdir", default=today(), help="workdir name: output/<workdir> and its rows in the database")
    ap.add_argument("--db", default=DB_PATH)
    a = ap.parse_args()
    sc = scan(a.input)
    if not sc.ready:
        raise SystemExit("Input folder not ready (see above)")
    process(sc, Path(OUTPUT_ROOT) / a.workdir, a.db)


if __name__ == "__main__":
    main()
