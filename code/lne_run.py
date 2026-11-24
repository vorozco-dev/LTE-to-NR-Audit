"""
Run the LTE to NR Audit (Ericsson) (console). The GUI (tk_lte_nr_ericsson_app.py) calls run() too.

    py -3.13 lne_load.py --workdir 2026-11-18 # Process: input folder -> rows of the workdir in db/lte_nr_e.sqlite
    py -3.13 lne_run.py                       # both parts, every ENM of the last workdir loaded
    py -3.13 lne_run.py --workdir 2026-11-18 --part 1
    py -3.13 lne_run.py --scope nodes --names DIFVEN1271,MEXECA1067

Output: output/<workdir>/<ENM>_<YYYYMMDD>/ (one folder per ENM and day; every file has its own time)
    01_lte_nr_termpoint_audit_<ENM>_<YYYYMMDD>_<HHMMSS>.xlsx
    01_lte_nr_termpoint_create_mop_cmedit_..., 01_lte_nr_termpoint_create_rollback_cmedit_...   STEP 1
    02_lte_nr_relation_audit_<ENM>_<YYYYMMDD>_<HHMMSS>.xlsx   (the map of a site: GUI 3. Graphical Audit)
    02_lte_nr_relation_add_mop_cmedit_...,     02_lte_nr_relation_add_rollback_cmedit_...       STEP 2
    02_lte_nr_relation_delete_mop_cmedit_...,  02_lte_nr_relation_delete_rollback_cmedit_...    STEP 3
    01_lte_nr_termpoint_delete_mop_cmedit_..., 01_lte_nr_termpoint_delete_rollback_cmedit_...   STEP 4
Run log: log/run_<ENM>_<YYYYMMDD>_<HHMMSS>.log
"""

import argparse
import datetime as dt
import time
from pathlib import Path

import pandas as pd

import lne_db
import lne_inventory
import lne_mop
import lne_part1
import lne_part2
import lne_report
from lne_config import LOG_DIR, OUTPUT_ROOT, Settings
from lne_geo_inputs import Ept, Propagation

VERSION = "2.5.0"


def scope_found(inv, settings):
    """(nodes of the node list present in this ENM, nodes absent): LTE/NR nodes and TermPoint owners
    of the ENM. scope 'all' -> (None, set())."""
    names = settings.scope_set()
    if settings.scope != "nodes" or not names:
        return None, set()
    have = set(inv.lte_cells["node"].str.upper()) | set(inv.nr_cells["node"].str.upper()) \
        | set(inv.termpoints["node"].str.upper())
    return names & have, names - have


def termpoint_sheets(tp):
    """Sheets of 01 named like the relation sheets of 02: one per action, CURRENT = TermPoints that exist
    (every row except the CREATE ones, which are only in CREATE TermPoint)."""
    act = tp["Action"] if len(tp) else pd.Series(dtype=str)
    return {
        "CURRENT TermPoint": tp[~act.isin([lne_part1.CREATE_TP, lne_part1.CREATE_BOTH])],
        "CREATE TermPoint": tp[act.isin([lne_part1.CREATE_TP, lne_part1.CREATE_BOTH])],
        "DELETE TermPoint": tp[act == lne_part1.DELETE_BOTH],
        "SET IP TermPoint": tp[act == lne_part1.SET_IP],
        "REVIEW TermPoint": tp[act == "REVIEW"],
        "TARGET TermPoint": lne_part1.target(tp),
    }


class RunOutput:
    """Folder of the run (<ENM>_<YYYYMMDD>) and the files this run wrote in it."""

    def __init__(self, folder):
        self.folder, self.files = Path(folder), []

    def path(self, stem, ext):
        """<folder>/<stem>_<ENM>_<YYYYMMDD>_<HHMMSS>.<ext> with the time the file is written."""
        enm = self.folder.name.rsplit("_", 1)[0]
        p = self.folder / f"{stem}_{enm}_{dt.datetime.now():%Y%m%d_%H%M%S}.{ext}"
        self.files.append(p)
        return p


def run(settings, log=print):
    """Audit one ENM -> RunOutput (folder and files written), or None when no name of the scope is in
    this ENM (skipped, no output)."""
    t0 = time.time()
    skipped = False
    now = dt.datetime.now()
    out = Path(settings.output_dir) / f"{settings.enm}_{now:%Y%m%d}"
    out.mkdir(parents=True, exist_ok=True)
    res = RunOutput(out)
    Path(LOG_DIR).mkdir(parents=True, exist_ok=True)
    log_path = Path(LOG_DIR) / f"run_{settings.enm}_{now:%Y%m%d_%H%M%S}.log"
    log_file = log_path.open("w", encoding="utf-8")

    def say(msg):
        log_file.write(str(msg) + "\n")
        log_file.flush()
        log(msg)

    try:
        db, wd = settings.db, settings.workdir
        say(f"LTE to NR Audit (Ericsson) {VERSION} · workdir {wd} · ENM {settings.enm} · output {out}")
        say(f"Run log {log_path}")
        meta = lne_db.meta(db, wd)
        if not meta:
            raise ValueError(f"Workdir {wd} is not loaded in {db}: run 1. Load Data -> Process")
        say(f"Database {db}, workdir {wd} (input {meta.get('input_dir', '?')}, loaded {meta.get('loaded_at', '?')})")
        tables = lne_db.read_tables(db, wd, settings.enm, log=say)
        others = [e for e in lne_db.ready_enms(db, wd) if e != settings.enm]
        other_gnbs = None
        if others:
            other_gnbs = pd.concat([lne_inventory.gnb_directory(
                lne_db.read_tables(db, wd, e, mos=lne_inventory.GNB_MOS), settings).assign(enm=e) for e in others],
                ignore_index=True)
        inv = lne_inventory.build(tables, settings, say, other_gnbs)
        found, absent = scope_found(inv, settings)
        if found is not None:
            if not found:
                say(f"Node list: {', '.join(sorted(absent))} not in ENM {settings.enm} -> "
                    f"nothing to audit, ENM skipped (no output)")
                skipped = True
                return None
            say(f"Node list in ENM {settings.enm}:{', '.join(sorted(found))}"
                + (f" · not in this ENM: {', '.join(sorted(absent))}" if absent else ""))
        common = {
            "Input files": pd.DataFrame(inv.file_status),
            "Node status": inv.node_status,
            "Settings": pd.DataFrame(settings.as_rows() + [
                {"Setting": "version", "Value": VERSION},
                {"Setting": "input folder (Process)", "Value": meta.get("input_dir", "")},
                {"Setting": "database", "Value": db},
                {"Setting": "database loaded at", "Value": meta.get("loaded_at", "")},
                {"Setting": "other ENMs (gNB resolution)", "Value": ", ".join(others)}]),
        }

        part2 = None
        if settings.part2:
            ept_df = lne_db.read_frame(db, "ept", wd)
            if ept_df is None or not len(ept_df):
                raise ValueError(f"No ept rows for workdir {wd} in {db}: run 1. Load Data -> Process")
            label = f"{Path(db).name} [{wd}]"
            ept = Ept(label, say, df=ept_df)
            prop = Propagation.from_frames({k: lne_db.read_frame(db, f"propagation_{k}", wd)
                                            for k in Propagation.KEYS.values()}, label, say)
            part2 = lne_part2.audit(inv, ept, prop, settings, say)
        # TermPoint decisions of the relation audit (Audit Rules): CREATE / DELETE in the TermPoint audit
        plan = lne_part1.termpoint_plan(part2[2]["tp_audit"], part2[2]["tp_add"]) if part2 is not None else None

        def write_mop(stem, mop_rb):
            mop, rb = mop_rb
            lne_report.write_lines(res.path(f"{stem}_mop_cmedit", "txt"), mop)
            lne_report.write_lines(res.path(f"{stem}_rollback_cmedit", "txt"), rb)

        tp = None
        if settings.part1:
            tp = lne_part1.audit(inv, settings, say, plan, part2[2]["tier"] if part2 is not None else None)
            gnb = inv.gnbs.rename(columns={"node": "gNB node", "gnb_id": "gNBId", "gnb_len": "gNBIdLength",
                                           "plmn": "PLMN", "x2_ip": "X2 IP", "x2_status": "X2 IP status",
                                           "x2_candidates": "X2 candidates"})
            lne_report.write_excel(res.path("01_lte_nr_termpoint_audit", "xlsx"), {
                "Summary": pd.DataFrame(lne_part1.summary(tp)),
                "Source summary": lne_part1.source_summary(tp),
                **termpoint_sheets(tp),
                "gNB X2 IP": gnb, **common})
            write_mop("01_lte_nr_termpoint_create", lne_mop.termpoint_create(inv, tp, plan, settings.enm))

        if settings.part2 and part2 is None:
            say("  Audit Neighbor Relation LTE to 5G: no source cells in the scope (no NR on the node or no "
                "cell of the LTE source bands) -> 02_lte_nr_relation_audit not written")
        elif settings.part2:
            sheets, _lines, totals = part2
            summary = pd.DataFrame([
                {"Item": "Source cells audited", "Value": totals["sources"]},
                {"Item": "ADD proposals", "Value": totals["add"]},
                {"Item": "DELETE proposals", "Value": totals["delete"]},
                {"Item": "Propagation source", "Value": prop.source},
                {"Item": "Note", "Value": "ADD is only proposed toward NR cells of this ENM; relations to other "
                                          "ENMs are audited (resolved by EPT) but not added."},
            ])
            say("  Writing 02_lte_nr_relation_audit ...")
            lne_report.write_excel(res.path("02_lte_nr_relation_audit", "xlsx"), {"Summary": summary, **sheets, **common})
            write_mop("02_lte_nr_relation_add", lne_mop.relation_add(inv, sheets["ADD relation"], settings.enm))
            write_mop("02_lte_nr_relation_delete", lne_mop.relation_delete(
                inv, sheets["DELETE relation"], totals["tp_audit"], settings.enm))

        if tp is not None:
            write_mop("01_lte_nr_termpoint_delete", lne_mop.termpoint_delete(inv, tp, settings.enm))

        for p in res.files:
            say(f"  Written {p.name}")
        say(f"Completed in {time.time() - t0:.0f}s: {out}")
        return res
    except Exception as e:
        say(f"ERROR: {e}")
        raise
    finally:
        log_file.close()
        if skipped and not any(out.iterdir()):   # no folder for an ENM without the scope
            out.rmdir()


def main():
    p = argparse.ArgumentParser(description="LTE to NR Audit (Ericsson) (reads the database written by lne_load.py / Process)")
    p.add_argument("--part", choices=["1", "2", "both"], default="both")
    p.add_argument("--enm", help="one ENM; default every ENM loaded in the database")
    p.add_argument("--db", help="database (default db/lte_nr_e.sqlite)")
    p.add_argument("--workdir", help="workdir loaded by Process (default the last one loaded); "
                                     "output goes to output/<workdir>")
    p.add_argument("--scope", choices=["all", "nodes"])
    p.add_argument("--names", default="", help="node names, comma separated (with --scope nodes)")
    p.add_argument("--source-bands", help="LTE source bands, comma separated band_indicator of "
                                          "conf/freq_band.csv (default all: lne_config.LTE_BANDS)")
    p.add_argument("--target-bands", help="5G target bands, comma separated band_indicator (default all: lne_config.NR_BANDS)")
    p.add_argument("--zero-overlap-tier", type=int, help="zero-overlap neighbors are deleted from this tier up")
    p.add_argument("--delete-tier", type=int, help="any neighbor is deleted from this tier up")
    p.add_argument("--add-all-tier", type=int, help="every missing NR cell (overlap or not) is added up to this tier")
    p.add_argument("--add-tier", type=int, help="missing NR cells with fan overlap are added up to this tier")
    p.add_argument("--max-site-km", type=float, help="no ADD towards a site farther than this, an existing "
                                                     "relation to it is deleted (default 30)")
    a = p.parse_args()
    s = Settings()
    for attr, val in (("zero_overlap_min_tier", a.zero_overlap_tier), ("min_delete_tier", a.delete_tier),
                      ("max_add_tier", a.add_tier), ("max_add_all_tier", a.add_all_tier),
                      ("max_site_km", a.max_site_km)):
        if val is not None:
            setattr(s, attr, val)
    if s.rule_problems():
        raise SystemExit("Contradictory Audit Rules:\n  " + "\n  ".join(s.rule_problems()))
    s.part1, s.part2 = a.part in ("1", "both"), a.part in ("2", "both")
    for attr, val in (("db", a.db), ("scope", a.scope)):
        if val:
            setattr(s, attr, val)
    loaded = lne_db.workdirs(s.db)
    if not loaded:
        raise SystemExit(f"No workdir loaded in {s.db}: run lne_load.py first")
    s.workdir = a.workdir or loaded[0]
    s.output_dir = str(Path(OUTPUT_ROOT) / s.workdir)
    s.scope_names = a.names
    if a.source_bands:
        s.source_bands = tuple(b.strip() for b in a.source_bands.split(",") if b.strip())
    if a.target_bands:
        s.target_bands = tuple(b.strip() for b in a.target_bands.split(",") if b.strip())
    for enm in ([a.enm] if a.enm else lne_db.ready_enms(s.db, s.workdir)):
        s.enm = enm
        run(s)


if __name__ == "__main__":
    main()
