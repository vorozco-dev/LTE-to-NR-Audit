"""
Identifier helpers: PLMN / gNB ID / NR cell ID normalization and MO reference parsing.

Seen in the tul exports:
  ExternalGNodeBFunction=33450-0000000000153614   (variable leading zeros)
  ExternalGNodeBFunction=33450-153772             (no zeros, same gNB style)
  GUtranCellRelationId=334050-153273-11           (PLMN written with 3-digit MNC)
  GUtranCellRelationId=EMRDIFCHT0327_1_T          (cell name instead of numeric ID)
The relation ID is only a name; the target is always taken from neighborCellRef.
"""

import ipaddress
import re

_GNB_RE = re.compile(r"^(\d{5,6})-(\d+)$")
_CELL_RE = re.compile(r"^(\d{5,6})-(\d+)-(\d+)$")


def plmn_key(text):
    """'33450' / '334050' -> '334-50' (MCC-MNC without leading zeros)."""
    s = str(text).strip()
    if not s.isdigit() or len(s) not in (5, 6):
        return None
    return f"{s[:3]}-{int(s[3:])}"


def parse_gnb(text):
    """'33450-0000000000153614' -> ('334-50', 153614) or None."""
    m = _GNB_RE.match(str(text).strip())
    if not m:
        return None
    return plmn_key(m.group(1)), int(m.group(2))


def parse_ext_cell(text):
    """'33450-000000000093616-12' -> ('334-50', 93616, 12) or None."""
    m = _CELL_RE.match(str(text).strip())
    if not m:
        return None
    return plmn_key(m.group(1)), int(m.group(2)), int(m.group(3))


def ref_value(ref, mo):
    """Value of MO=<value> inside an FDN/relative reference, or ''."""
    m = re.search(rf"(?:^|,){mo}=([^,\]]+)", str(ref))
    return m.group(1).strip() if m else ""


def node_prefix(ref):
    """FDN up to ManagedElement=<node> ('' when the reference is relative)."""
    m = re.match(r"^(.*?ManagedElement=[^,\]]+)", str(ref).strip("[] "))
    return m.group(1) if m else ""


def split_nci(nci, gnb_len=22):
    """nCI (36 bits) -> (gNB ID, cell local ID)."""
    n = int(nci)
    shift = 36 - int(gnb_len)
    return n >> shift, n & ((1 << shift) - 1)


def to_int(text):
    try:
        return int(str(text).strip())
    except (TypeError, ValueError):
        return None


def host_ip(text):
    """'10.236.21.230/30' -> '10.236.21.230'; '' when not an IPv4 address."""
    s = str(text or "").split("/", 1)[0].strip()
    try:
        return str(ipaddress.IPv4Address(s))
    except ipaddress.AddressValueError:
        return ""


def is_zero_ip(text):
    return host_ip(text) in ("", "0.0.0.0")


def usable_ip(text):
    ip = host_ip(text)
    if not ip:
        return False
    a = ipaddress.IPv4Address(ip)
    return not (a.is_unspecified or a.is_loopback or a.is_link_local or a.is_multicast)


def is_active(admin, oper):
    return str(admin).upper() in ("UNLOCKED", "1") and str(oper).upper() in ("ENABLED", "1")


def suffix_of(cell):
    m = re.search(r"(_[A-Za-z0-9]+)$", str(cell))
    return m.group(1).upper() if m else ""


SITE_RE = re.compile(r"([A-Z]{6}\d{4})")


def site_code(name):
    """'UDIFMGH0796' / 'EMLDIFMGH0796_2_J' -> 'DIFMGH0796' ('' when absent)."""
    m = SITE_RE.search(str(name).upper())
    return m.group(1) if m else ""


def sector_of(cell):
    """'EMLDIFMGH0796_2_J' -> '2'."""
    m = re.search(r"_(\d+)_[A-Za-z0-9]+$", str(cell))
    return m.group(1) if m else ""


def missing(v):
    """None, NaN or empty (pandas turns None into NaN in numeric columns)."""
    if v is None:
        return True
    try:
        return v != v or str(v).strip() == ""
    except Exception:
        return False
