"""
Reader for ENM `cmedit get * <MO>.(attrs) -t` exports.

File layout (tab separated):
    <blank lines>
    SubNetwork,SubNetwork,...,MeContext,ManagedElement,<Parent>,<MO>      <- MO path line
    NodeId  <Parent>Id ... <MO>Id  attr1  attr2 ...                        <- header
    rows...
    <blank>
    12345 instance(s)                                                     <- footer (complete)
A file can hold several sections (same MO class under different parents, e.g.
AddressIPv4 under IpSystem and under Transport). A read that failed for some nodes
ends with "Error NNNN : ..." instead of the instance count; the rows before it are
kept and the file is flagged as incomplete.

Files are found as <MO>_<enm>.txt (plus <MO>_<enm>_<anything>.txt, e.g. a re-extraction
TermPointToGNB_tul_fix.txt, merged with the main file) either loose in the input folder
or inside any .zip / .rar / .7z archive of that folder (e.g. result_tul.zip). Archives
are read in memory: zip with Python, the others with the Windows tar (bsdtar/libarchive)
or 7-Zip when installed. A loose file wins over an archive member with the same name.
"""

import io
import os
import re
import shutil
import subprocess
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

INSTANCES_RE = re.compile(r"^\s*(\d+)\s+instance\(s\)\s*$")
ERROR_RE = re.compile(r"^\s*Error\s+(\d+)\s*:\s*(.*)$")
SKIPPED_RE = re.compile(r"Model has not been deployed", re.I)

# MO classes used by the audit; required=False means the audit runs without it.
MO_FILES = {
    "EUtranCellFDD": True,
    "GUtranCellRelation": True,
    "GUtranFreqRelation": True,
    "GUtranSyncSignalFrequency": False,
    "ExternalGNodeBFunction": True,
    "ExternalGUtranCell": True,
    "TermPointToGNB": True,
    "NRCellDU": True,
    "GNBCUCPFunction": True,
    "LocalSctpEndpoint": True,
    "SctpEndpoint": True,
    "AddressIPv4": True,
}


@dataclass
class MoTable:
    mo: str
    df: pd.DataFrame
    files: list = field(default_factory=list)
    rows: int = 0
    instances: int = None        # sum of the "N instance(s)" footers (None if no footer)
    errors: list = field(default_factory=list)   # "file: Error NNNN : text"

    @property
    def complete(self):
        return not self.errors and self.instances is not None

    @property
    def error_nodes(self):
        """Nodes named in the ENM errors (Error 9999 ... MeContext=<node> ... has failed)."""
        return {m for e in self.errors if not SKIPPED_RE.search(e)
                for m in re.findall(r"MeContext=([^,\]\s]+)", e)}

    @property
    def skipped(self):
        """Errors of non-radio nodes whose model is not in ENM (e.g. C6610 routers in AddressIPv4)."""
        return [e for e in self.errors if SKIPPED_RE.search(e)]

    @property
    def failed(self):
        """True when ENM could not read some radio node: a node READ error, or an Error 5010
        with no explanation (the failed nodes are then inferred by lne_inventory.read_gaps)."""
        real = [e for e in self.errors if not SKIPPED_RE.search(e)]
        return bool(self.error_nodes) or (bool(real) and not self.skipped)

    def status(self):
        if not self.files:
            return "MISSING FILE"
        if self.failed:
            return "PARTIAL (ENM error)"
        if self.skipped:
            return f"OK ({len(self.skipped)} non-radio nodes skipped)"
        if self.instances is None:
            return "NO FOOTER"
        return "OK" if self.instances == self.rows else f"ROW COUNT {self.rows} != {self.instances}"


def partial_status(t, not_read, show=0):
    """Status of one table with the size of the problem: for a read with ENM errors
    'PARTIAL: 6 of 1,119 nodes not read (0.5%)' (+ the first `show` node names); else t.status().
    not_read: the nodes ENM did not read (named by ENM or found by lne_inventory.read_gaps)."""
    if not t.files or not t.failed:
        return t.status()
    if not not_read:
        return "PARTIAL: ENM error, no node found missing"
    read = set(t.df["NodeId"]) if "NodeId" in t.df.columns else set()
    total = len(read | set(not_read))
    text = f"PARTIAL: {len(not_read):,} of {total:,} nodes not read ({100 * len(not_read) / total:.1f}%)"
    if show:
        names = ", ".join(not_read[:show]) + (f" +{len(not_read) - show} more" if len(not_read) > show else "")
        text += f": {names}"
    return text


def parse_lines(lines, name):
    """Return (rows, instances, errors). Each row is a dict plus '_path' (MO path line)."""
    rows, errors, instances = [], [], None
    mo_path, header = None, None
    for raw in lines:
        line = raw.rstrip('\r\n')
        if not line.strip():
            continue
        m = INSTANCES_RE.match(line)
        if m:
            instances = (instances or 0) + int(m.group(1))
            continue
        m = ERROR_RE.match(line)
        if m:
            errors.append(f"{name}: Error {m.group(1)} : {m.group(2).strip()}")
            header = None
            continue
        if "\t" not in line and line.startswith("SubNetwork"):
            mo_path, header = line.strip(), None
            continue
        if line.startswith("FDN :") or line.startswith("RESPONSE ERROR"):
            continue            # detail of the next/previous Error line, not a row
        if line.startswith("NodeId\t"):
            header = line.split("\t")
            continue
        if header is None:
            continue            # text of an error message, log viewer hints, ...
        values = line.split('\t')
        if len(values) < len(header):
            values += [""] * (len(header) - len(values))
        row = dict(zip(header, (v.strip() for v in values)))
        row["_path"] = mo_path or ""
        rows.append(row)
    return rows, instances, errors


def parse_file(path):
    with open(path, encoding="utf-8", errors="replace") as f:
        return parse_lines(f, Path(path).name)


# --- locating the exports: loose files and archive members ---------------------------------

ARCHIVE_EXTS = (".zip", ".rar", ".7z", ".tar", ".tgz", ".tar.gz")


def _bsdtar():
    p = Path(os.environ.get("WINDIR", r"C:\Windows")) / "System32" / "tar.exe"
    return str(p) if p.is_file() else None


def _sevenzip():
    for p in (shutil.which("7z"), r"C:\Program Files\7-Zip\7z.exe", r"C:\Program Files (x86)\7-Zip\7z.exe"):
        if p and Path(p).is_file():
            return p
    return None


def _run(cmd):
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return subprocess.run(cmd, capture_output=True, check=True, creationflags=flags).stdout


def archive_members(path):
    """Names of the files inside an archive."""
    if path.suffix.lower() == ".zip":
        with zipfile.ZipFile(path) as z:
            return [n for n in z.namelist() if not n.endswith("/")]
    tar = _bsdtar()
    if tar:
        return [n for n in _run([tar, "-tf", str(path)]).decode("utf-8", "replace").splitlines() if n.strip()]
    sz = _sevenzip()
    if sz:
        out = _run([sz, "l", "-slt", str(path)]).decode("utf-8", "replace")
        return [l[7:].strip() for l in out.splitlines() if l.startswith("Path = ")][1:]
    raise RuntimeError(f"Cannot open {path.name}: no Windows tar.exe nor 7-Zip available")


def archive_read(path, member):
    if path.suffix.lower() == ".zip":
        with zipfile.ZipFile(path) as z:
            return z.read(member)
    tar = _bsdtar()
    if tar:
        return _run([tar, "-xOf", str(path), member])
    return _run([_sevenzip(), "e", "-so", str(path), member])


_ARCHIVE_CACHE = {}


def find_sources(folder, enm):
    """{basename: (archive Path or None, member or file Path)} for every *_<enm>*.txt."""
    folder = Path(folder)
    found = {}
    if not folder.is_dir():
        return found
    pat = re.compile(rf"_{re.escape(enm)}(_.+)?\.txt$", re.I)
    for p in sorted(folder.iterdir()):
        name = p.name.lower()
        if p.is_file() and name.endswith(ARCHIVE_EXTS):
            key = (str(p), p.stat().st_mtime_ns)
            if key not in _ARCHIVE_CACHE:
                _ARCHIVE_CACHE[key] = archive_members(p)
            for m in _ARCHIVE_CACHE[key]:
                base = Path(m).name
                if pat.search(base):
                    found.setdefault(base, (p, m))
    for p in sorted(folder.glob(f"*_{enm}*.txt")):          # loose files win
        if pat.search(p.name):
            found[p.name] = (None, p)
    return found


def read_mo(folder, mo, enm, log=print, sources=None):
    sources = find_sources(folder, enm) if sources is None else sources
    pat = re.compile(rf"^{re.escape(mo)}_{re.escape(enm)}(_.+)?\.txt$", re.I)
    picked = sorted(b for b in sources if pat.match(b))
    all_rows, errors, instances, labels = [], [], None, []
    for base in picked:
        archive, item = sources[base]
        if archive is None:
            rows, inst, errs = parse_file(item)
            labels.append(base)
        else:
            text = archive_read(archive, item).decode("utf-8", "replace")
            rows, inst, errs = parse_lines(io.StringIO(text), base)
            labels.append(f"{base} [{archive.name}]")
        all_rows += rows
        errors += errs
        if inst is not None:
            instances = (instances or 0) + inst
    t = MoTable(mo, pd.DataFrame(all_rows), labels, len(all_rows), instances, errors)
    if labels:
        log(f"  {mo}: {t.rows:,} rows from {', '.join(labels)} [{t.status()}]")
    else:
        log(f"  {mo}: no file {mo}_{enm}.txt")
    for e in errors:
        log(f"    {e}")
    return t


def read_all(folder, enm, log=print):
    log(f"Reading ENM exports ({enm}) from {folder}")
    sources = find_sources(folder, enm)
    tables = {mo: read_mo(folder, mo, enm, log, sources) for mo in MO_FILES}
    missing = [mo for mo, req in MO_FILES.items() if req and not tables[mo].files]
    if missing:
        raise FileNotFoundError("Missing required ENM exports: "
                                + ", ".join(f"{m}_{enm}.txt" for m in missing))
    return tables


def parent_class(path):
    """'SubNetwork,...,ManagedElement,GNBCUCPFunction,EndpointResource,LocalSctpEndpoint' -> 'GNBCUCPFunction'"""
    # The number of SubNetwork levels varies (SubNetwork=TIJUANA vs RADIONODE,LTE), so take the
    # class right after ManagedElement.
    parts = [p.strip() for p in path.split(",")]
    if "ManagedElement" in parts:
        i = parts.index("ManagedElement") + 1
        return parts[i] if i < len(parts) else ""
    return ""
