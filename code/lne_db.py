"""
SQLite database of the on-demand extractions (db/lte_nr.sqlite). Every Process loads one input
folder under a workdir name; the first column of every data table is `workdir`, so several
extractions live side by side. Processing a workdir again replaces only its rows.

Tables
  loads                   workdir, table_name, enm, rows, status, executed_at   (tab 3. DB Status)
  meta                    workdir, key, value       input folder, output folder, load time, ENMs
  load_status             workdir, enm, mo, required, files, rows, instances, status, errors (JSON)
  <MO> (12 tables)        workdir, enm, _path, NodeId, <parent ids>, <MO>Id, <attributes...>   all TEXT,
                          exactly the columns of the cmedit export
  ept                     workdir, ept.csv columns (one row per cell)
  propagation_e4g / e5g   workdir, cell_name, max85coverage_km
  mo_columns              table_name, sql_name, name: schema map, shared by every workdir. SQLite
                          ignores case in column names, so InterfaceIpv4Id / InterfaceIPv4Id of
                          AddressIPv4 get distinct SQL names and this table keeps the export name.

The audit reads only this database: lne_db.read_tables(db, workdir, enm) rebuilds the same MoTable
objects that lne_enm.read_all() produces from the files, so the inventory code is shared.
"""

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

import pandas as pd

import lne_enm

BASE_TABLES = {
    "loads": "workdir TEXT, table_name TEXT, enm TEXT, rows INTEGER, status TEXT, executed_at TEXT",
    "meta": "workdir TEXT, key TEXT, value TEXT, PRIMARY KEY (workdir, key)",
    "load_status": "workdir TEXT, enm TEXT, mo TEXT, required INTEGER, files TEXT, rows INTEGER, "
                   "instances INTEGER, status TEXT, errors TEXT",
    "mo_columns": "table_name TEXT, sql_name TEXT, name TEXT",
}


def connect(path):
    conn = sqlite3.connect(str(path), timeout=60)
    conn.execute("PRAGMA synchronous=OFF")
    return conn


@contextmanager
def _open(path):
    """Read connection that is always closed (sqlite3's own `with` only commits, it does not close,
    and an open handle keeps the file locked on Windows)."""
    conn = connect(path)
    try:
        yield conn
    finally:
        conn.close()


def stamp():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _tables_in(conn):
    return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _columns(conn, table):
    return [r[1] for r in conn.execute(f'PRAGMA table_info("{table}")')]


def _workdir_tables(conn):
    """Every table that has a workdir column."""
    return [t for t in sorted(_tables_in(conn)) if "workdir" in _columns(conn, t)]


# --- write ------------------------------------------------------------------------------------

class Writer:
    """Loads one workdir in a single transaction: its previous rows are deleted first, and an
    error rolls everything back (the other workdirs are never touched)."""

    def __init__(self, path, workdir, log=print):
        self.path, self.workdir, self.log = Path(path), workdir, log
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = connect(self.path)
        self.conn.isolation_level = None
        self.conn.execute("BEGIN")
        names = _tables_in(self.conn)
        if "meta" in names and "workdir" not in _columns(self.conn, "meta"):
            for t in names:                             # single-load database of v2.1 (no workdir)
                self.conn.execute(f'DROP TABLE "{t}"')
            names = set()
            log(f"  Old database layout without workdir dropped: {self.path}")
        for t, cols in BASE_TABLES.items():
            if t not in names:
                self.conn.execute(f"CREATE TABLE {t} ({cols})")
        self._cols = {}                                  # table -> {export name: sql name}
        for t in _tables_in(self.conn) - set(BASE_TABLES):
            m = dict(self.conn.execute("SELECT name, sql_name FROM mo_columns WHERE table_name=?", (t,)).fetchall())
            self._cols[t] = {c: c for c in _columns(self.conn, t) if c not in m.values()} | m
        replaced = 0
        for t in _workdir_tables(self.conn):
            replaced += self.conn.execute(f'DELETE FROM "{t}" WHERE workdir=?', (workdir,)).rowcount
        if replaced:
            log(f"  Workdir {workdir} was already loaded: its previous rows are replaced")

    def meta(self, **kv):
        self.conn.executemany("INSERT OR REPLACE INTO meta VALUES (?, ?, ?)",
                              [(self.workdir, k, str(v)) for k, v in kv.items()])

    def _insert(self, table, df, enm=None):
        """Append df (workdir, [enm,] columns...) to table; new columns are added as TEXT."""
        df = df.copy()
        if enm is not None:
            df.insert(0, "enm", enm)
        df.insert(0, "workdir", self.workdir)
        cols = self._cols.get(table)
        if cols is None:
            cols = self._cols[table] = {}
            self.conn.execute(f'CREATE TABLE "{table}" ("workdir" TEXT)')
            cols["workdir"] = "workdir"
        for c in df.columns:
            if c not in cols:
                # SQLite column names ignore case, the exports do not (IpSystem InterfaceIpv4Id vs
                # Transport InterfaceIPv4Id): a clashing name gets a suffix, mo_columns keeps the name.
                sql_name, n = c, 1
                while sql_name.lower() in {v.lower() for v in cols.values()}:
                    n += 1
                    sql_name = f"{c}__{n}"
                self.conn.execute(f'ALTER TABLE "{table}" ADD COLUMN "{sql_name}" TEXT')
                if sql_name != c:
                    self.conn.execute("INSERT INTO mo_columns VALUES (?, ?, ?)", (table, sql_name, c))
                cols[c] = sql_name
        names = list(df.columns)
        sql = f'INSERT INTO "{table}" (' + ", ".join(f'"{cols[c]}"' for c in names) + ") VALUES (" + \
              ", ".join("?" * len(names)) + ")"
        self.conn.executemany(sql, df.astype(object).where(df.notna(), None).itertuples(index=False, name=None))

    def _loaded(self, table, enm, rows, status):
        self.conn.execute("INSERT INTO loads VALUES (?, ?, ?, ?, ?, ?)",
                          (self.workdir, table, enm, rows, status, stamp()))

    def mo_table(self, enm, t, status=None):
        """One ENM's MoTable into the table of its MO. status: text for the loads table (3. DB Status),
        e.g. "1 node not read: JALZAP1012"; default t.status()."""
        self.conn.execute("INSERT INTO load_status VALUES (?,?,?,?,?,?,?,?,?)",
                          (self.workdir, enm, t.mo, int(lne_enm.MO_FILES[t.mo]), json.dumps(t.files), t.rows,
                           t.instances, t.status(), json.dumps(t.errors)))
        self._loaded(t.mo, enm, t.rows, status or t.status())
        if len(t.df):
            self._insert(t.mo, t.df, enm)

    def frame(self, name, df, status="OK"):
        self._insert(name, df)
        self._loaded(name, "", len(df), status)

    def close(self):
        for t in self._cols:
            enm = ", enm" if "enm" in self._cols[t] else ""
            self.conn.execute(f'CREATE INDEX IF NOT EXISTS "ix_{t}_workdir" ON "{t}" (workdir{enm})')
        self.conn.execute("CREATE INDEX IF NOT EXISTS ix_load_status ON load_status (workdir, enm, mo)")
        self.conn.execute("COMMIT")
        self.conn.close()
        self.log(f"  Database written: {self.path} ({self.path.stat().st_size / 1e6:,.0f} MB, "
                 f"workdir {self.workdir})")

    def abort(self):
        try:
            self.conn.execute("ROLLBACK")
        except sqlite3.Error:
            pass
        self.conn.close()


# --- read -------------------------------------------------------------------------------------

def workdirs(path):
    """Workdirs in the database, most recent load first."""
    if not Path(path).is_file():
        return []
    with _open(path) as conn:
        if "loads" not in _tables_in(conn):
            return []
        return [r[0] for r in conn.execute(
            "SELECT workdir FROM loads GROUP BY workdir ORDER BY MAX(executed_at) DESC")]


def status(path, workdir=None):
    """The loads table (tab 3. DB Status), optionally of one workdir."""
    cols = ["table_name", "enm", "workdir", "rows", "status", "executed_at"]
    if not workdirs(path):
        return pd.DataFrame(columns=cols)
    with _open(path) as conn:
        sql = f"SELECT {', '.join(cols)} FROM loads"
        if workdir:
            return pd.read_sql(sql + " WHERE workdir=? ORDER BY rowid", conn, params=(workdir,))
        return pd.read_sql(sql + " l ORDER BY (SELECT MAX(executed_at) FROM loads m WHERE m.workdir = l.workdir) "
                                 "DESC, rowid", conn)


def meta(path, workdir):
    if not Path(path).is_file():
        return {}
    with _open(path) as conn:
        if "meta" not in _tables_in(conn) or "workdir" not in _columns(conn, "meta"):
            return {}
        return dict(conn.execute("SELECT key, value FROM meta WHERE workdir=?", (workdir,)).fetchall())


def load_status(path, workdir):
    with _open(path) as conn:
        return pd.read_sql("SELECT * FROM load_status WHERE workdir=? ORDER BY rowid", conn, params=(workdir,))


def ready_enms(path, workdir):
    """ENMs of a workdir whose required tables were all loaded, in load order."""
    if workdir not in workdirs(path):
        return []
    st = load_status(path, workdir)
    enms = list(dict.fromkeys(st["enm"]))
    return [e for e in enms if (st[(st["enm"] == e) & (st["required"] == 1)]["files"] != "[]").all()]


def _read(conn, table, workdir, enm=None):
    sql, params = f'SELECT * FROM "{table}" WHERE workdir=?', [workdir]
    if enm is not None:
        sql, params = sql + " AND enm=?", params + [enm]
    df = pd.read_sql(sql, conn, params=params)
    df = df.rename(columns=dict(conn.execute(
        "SELECT sql_name, name FROM mo_columns WHERE table_name=?", (table,)).fetchall()))
    return df.drop(columns=[c for c in ("workdir", "enm") if c in df.columns and (c == "workdir" or enm)])


def read_tables(path, workdir, enm, mos=None, log=print):
    """{mo: lne_enm.MoTable} of one ENM of one workdir, as lne_enm.read_all() builds them from the files."""
    if not Path(path).is_file():
        raise FileNotFoundError(f"Database not found: {path} (run 1. Load Data -> Process)")
    out = {}
    with _open(path) as conn:
        names = _tables_in(conn)
        st = {r["mo"]: r for r in pd.read_sql("SELECT * FROM load_status WHERE workdir=? AND enm=?", conn,
                                              params=(workdir, enm)).to_dict("records")}
        if not st:
            raise ValueError(f"ENM {enm} is not loaded in workdir {workdir} ({path})")
        for mo in (mos or lne_enm.MO_FILES):
            s = st.get(mo)
            df = pd.DataFrame()
            if mo in names:
                df = _read(conn, mo, workdir, enm).dropna(axis=1, how="all").fillna("")
            files = json.loads(s["files"]) if s else []
            inst = None if s is None or pd.isna(s["instances"]) else int(s["instances"])
            t = lne_enm.MoTable(mo, df.reset_index(drop=True), files, len(df), inst,
                               json.loads(s["errors"]) if s else [])
            out[mo] = t
            if mos is None:
                log(f"  {mo}: {t.rows:,} rows [{t.status()}]")
    missing = [mo for mo, req in lne_enm.MO_FILES.items() if req and mos is None and not out[mo].files]
    if missing:
        raise FileNotFoundError(f"ENM {enm}: required tables not loaded: {', '.join(missing)}")
    return out


def read_frame(path, name, workdir, enm=None):
    """Rows of one workdir (without the workdir column), or None when the table does not exist."""
    with _open(path) as conn:
        if name not in _tables_in(conn):
            return None
        return _read(conn, name, workdir, enm)


def delete_workdir(path, workdir, log=print):
    """Delete every row of a workdir and compact the file. Returns the rows deleted."""
    with _open(path) as conn:
        n = 0
        for t in _workdir_tables(conn):
            n += conn.execute(f'DELETE FROM "{t}" WHERE workdir=?', (workdir,)).rowcount
        conn.commit()
    log(f"Workdir {workdir}: {n:,} rows deleted; compacting the database ...")
    conn = sqlite3.connect(str(path))
    conn.execute("VACUUM")
    conn.close()
    log(f"Database {path}: {Path(path).stat().st_size / 1e6:,.0f} MB")
    return n
