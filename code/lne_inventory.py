"""
Normalized network inventory built from the ENM exports.

Everything downstream works on these tables, never on the raw cmedit columns:
  lte_cells   one row per LTE cell (EUtranCellFDD)
  nr_cells    one row per NR cell (NRCellDU) with gNB ID / local ID
  gnbs        one row per gNB node: gNB ID, length, PLMN, X2 signalling IP
  relations   one row per GUtranCellRelation with the parsed target (PLMN, gNB, local ID)
  ext_cells   ExternalGUtranCell (NR cells known by each eNB)
  termpoints  TermPointToGNB rows
  node_status one row per LTE node: data completeness

Nodes that did not answer when ENM was queried (missing from a
file that ended with an ENM error) are flagged in node_status so the audit never
reads "absent from the export" as "absent from the network".
"""

import re
from dataclasses import dataclass, field

import pandas as pd

from lne_bands import arfcn_map, band_by_number, lte_band, nr_band
from lne_enm import parent_class, partial_status
from lne_ids import (host_ip, is_active, missing, node_prefix, parse_ext_cell, parse_gnb, plmn_key,
                    ref_value, split_nci, suffix_of, to_int)


@dataclass
class Inventory:
    lte_cells: pd.DataFrame
    nr_cells: pd.DataFrame
    gnbs: pd.DataFrame
    relations: pd.DataFrame
    ext_cells: pd.DataFrame
    termpoints: pd.DataFrame
    freq_relations: pd.DataFrame
    node_status: pd.DataFrame
    node_prefix: dict = field(default_factory=dict)
    file_status: list = field(default_factory=list)

    def other_gnb(self, gnb_id):
        """Record (node, enm, plmn, x2_ip, x2_status, x2_candidates) of a gNB of another ENM, or None."""
        return getattr(self, "other_gnbs", {}).get(gnb_id)

    def gnb_node(self, gnb_id):
        return self._gnb_node.get(gnb_id)

    def status_of(self, node):
        return self._status.get(node, "NOT IN EUtranCellFDD")


def _col(df, name, default=""):
    return df[name] if name in df.columns else pd.Series(default, index=df.index, dtype=object)


# --- cells ----------------------------------------------------------------------

def _lte_cells(t):
    df = t.df
    out = pd.DataFrame({
        "node": df["NodeId"], "cell": df["EUtranCellFDDId"],
        "freq_band": _col(df, "freqBand"), "earfcn_dl": _col(df, "earfcndl"),
        "cell_id": _col(df, "cellId"), "pci": _col(df, "physicalLayerCellId"), "tac": _col(df, "tac"),
        "admin": _col(df, "administrativeState"), "oper": _col(df, "operationalState"),
    })
    out["suffix"] = out["cell"].map(suffix_of)
    bands = arfcn_map("4g")
    out["band"] = out["earfcn_dl"].map(lambda e: lte_band(e, bands))
    out["active"] = [is_active(a, o) for a, o in zip(out["admin"], out["oper"])]
    return out.drop_duplicates(["node", "cell"]).reset_index(drop=True)


def _gnbs(t):
    df = t.df
    out = pd.DataFrame({
        "node": df["NodeId"],
        "gnb_id": df["gNBId"].map(to_int),
        "gnb_len": df["gNBIdLength"].map(to_int).fillna(22).astype(int),
        "plmn": df["pLMNId"].map(_plmn_struct),
    })
    return out.drop_duplicates("node").reset_index(drop=True)


def _plmn_struct(text):
    """'{mcc=334, mnc=050}' / '{mncLength=3, mcc=334, mnc=50}' -> '334-50'"""
    mcc = re.search(r"mcc=(\d+)", str(text))
    mnc = re.search(r"mnc=(\d+)", str(text))
    return f"{int(mcc.group(1))}-{int(mnc.group(1))}" if mcc and mnc else ""


def _nr_cells(t, gnbs):
    df = t.df
    length = dict(zip(gnbs["node"], gnbs["gnb_len"]))
    gnb_of = dict(zip(gnbs["node"], gnbs["gnb_id"]))
    bands, by_number = arfcn_map("5g"), band_by_number("5g")
    rows = []
    for r in df.to_dict("records"):
        node, nci = r["NodeId"], to_int(r.get("nCI"))
        gnb, local = (split_nci(nci, length.get(node, 22)) if nci is not None else (None, None))
        local_cfg = to_int(r.get("cellLocalId"))
        rows.append({
            "node": node, "cell": r["NRCellDUId"],
            "gnb_id": gnb if gnb is not None else gnb_of.get(node),
            "local_id": local_cfg if local_cfg is not None else local,
            "nci": nci if nci is not None else "",
            "nci_status": "OK" if nci is not None else "nCI not set",
            "pci": r.get("nRPCI", ""), "tac": r.get("configuredEpsTAC", ""),
            "ssb_key": f"{r.get('ssbFrequency', '')}-{r.get('ssbSubCarrierSpacing', '')}",
            "band_list": re.sub(r"[^\d,]", "", str(r.get("bandList", ""))),
            "band": nr_band(r.get("ssbFrequency", ""), r.get("bandList", ""), bands, by_number),
            "admin": r.get("administrativeState", ""), "oper": r.get("operationalState", ""),
            "cell_state": r.get("cellState", ""), "service_state": r.get("serviceState", ""),
        })
    out = pd.DataFrame(rows)
    out["suffix"] = out["cell"].map(suffix_of)
    out["active"] = [is_active(a, o) for a, o in zip(out["admin"], out["oper"])]
    return out.drop_duplicates(["node", "cell"]).reset_index(drop=True)


# --- X2 signalling address of each gNB -------------------------------------------

def _x2_addresses(lse, sctp, addr, routers_pref):
    """node -> list of (router, ip) used by the GNBCUCPFunction X2 SCTP endpoints."""
    addresses = {}
    for r in addr.df.to_dict("records"):
        if ",Transport," not in "," + r["_path"] + ",":
            continue
        key = (r["NodeId"], r.get("RouterId", ""), r.get("InterfaceIPv4Id", ""), r.get("AddressIPv4Id", ""))
        addresses[key] = host_ip(r.get("address", ""))
    local_ip = {(r["NodeId"], r["SctpEndpointId"]): r.get("localIpAddress", "") for r in sctp.df.to_dict("records")}
    result = {}
    for r in lse.df.to_dict("records"):
        if parent_class(r["_path"]) != "GNBCUCPFunction" or "X2" not in str(r.get("interfaceUsed", "")).upper():
            continue
        node = r["NodeId"]
        ep = ref_value(r.get("sctpEndpointRef", ""), "SctpEndpoint")
        for router, intf, a in re.findall(r"Router=([^,\]]+),InterfaceIPv4=([^,\]]+),AddressIPv4=([^,\]]+)",
                                          local_ip.get((node, ep), "")):
            ip = addresses.get((node, router, intf, a), "")
            if ip:
                result.setdefault(node, [])
                if (router, ip) not in result[node]:
                    result[node].append((router, ip))
    return result


def _choose_x2(cands, routers_pref):
    """(ip, status, candidates text). Preferred router first; otherwise only a unique address."""
    if not cands:
        return "", "NO X2 ADDRESS", ""
    text = "; ".join(f"{r}={ip}" for r, ip in cands)
    for pref in routers_pref:
        ips = sorted({ip for r, ip in cands if r.upper() == pref.upper()})
        if len(ips) == 1:
            return ips[0], f"OK (Router={pref})", text
        if len(ips) > 1:
            return "", f"AMBIGUOUS (Router={pref})", text
    ips = sorted({ip for _, ip in cands})
    return (ips[0], "OK (unique address)", text) if len(ips) == 1 else ("", "AMBIGUOUS", text)


# --- relations / external definitions ------------------------------------------------

def _relations(t):
    rows = []
    for r in t.df.to_dict("records"):
        ref = r.get("neighborCellRef", "")
        ext_cell = ref_value(ref, "ExternalGUtranCell")
        target = parse_ext_cell(ext_cell)
        mob = r.get("mobilityStatusNR", "")
        rows.append({
            "node": r["NodeId"], "cell": r["EUtranCellFDDId"],
            "freq_rel": r.get("GUtranFreqRelationId", ""), "relation_id": r.get("GUtranCellRelationId", ""),
            "ext_gnb_ref": ref_value(ref, "ExternalGNodeBFunction"), "ext_cell_ref": ext_cell,
            "t_plmn": target[0] if target else "", "t_gnb": target[1] if target else None,
            "t_local": target[2] if target else None,
            "ref_ok": target is not None,
            "is_remove_allowed": r.get("isRemoveAllowed", ""), "is_endc_allowed": r.get("isEndcAllowed", ""),
            "is_ho_allowed": r.get("isHoAllowed", ""), "created_by": r.get("createdBy", ""),
            "ess_enabled": r.get("essEnabled", ""), "reserved_by": _clean_list(r.get("reservedBy", "")),
            "nr_mobility_available": _struct_field(mob, "available"),
            "nr_mobility_reason": _struct_field(mob, "reason"),
            "time_of_creation": r.get("timeOfCreation", ""),
            "neighbor_cell_ref": ref,
        })
    return pd.DataFrame(rows)


def _clean_list(text):
    s = str(text or "").strip()
    return "" if s in ("", "[]", "null") else s.strip("[]")


def _struct_field(text, name):
    m = re.search(rf"{name}=([^,}}]+)", str(text))
    return m.group(1).strip() if m else ""


def _ext_cells(t):
    df = t.df
    rows = []
    for r in df.to_dict("records"):
        p = parse_ext_cell(r.get("ExternalGUtranCellId", ""))
        g = parse_gnb(r.get("ExternalGNodeBFunctionId", ""))
        rows.append({
            "node": r["NodeId"], "ext_gnb_ref": r.get("ExternalGNodeBFunctionId", ""),
            "ext_cell_ref": r.get("ExternalGUtranCellId", ""),
            "gnb_id": p[1] if p else (g[1] if g else None), "local_id": to_int(r.get("localCellId")),
            "nr_tac": r.get("nRTAC", ""), "configured_tac": r.get("configuredTAC", ""),
            "pci": r.get("nRPCI", ""), "ssb_ref": ref_value(r.get("gUtranSyncSignalFrequencyRef", ""),
                                                           "GUtranSyncSignalFrequency"),
            "cell_state": r.get("cellState", ""), "service_status": r.get("serviceStatus", ""),
            "created_by": r.get("createdBy", ""),
        })
    return pd.DataFrame(rows)


def _termpoints(t):
    df = t.df
    rows = []
    for r in df.to_dict("records"):
        g = parse_gnb(r.get("ExternalGNodeBFunctionId", ""))
        rows.append({
            "node": r["NodeId"], "ext_gnb_ref": r.get("ExternalGNodeBFunctionId", ""),
            "termpoint_id": r.get("TermPointToGNBId", ""),
            "plmn": g[0] if g else "", "gnb_id": g[1] if g else None,
            "ip_address": r.get("ipAddress", ""), "used_ip_address": r.get("usedIpAddress", ""),
            "domain_name": r.get("domainName", ""),
            "admin": r.get("administrativeState", ""), "oper": r.get("operationalState", ""),
            "availability": _clean_list(r.get("availabilityStatus", "")),
            "created_by": r.get("createdBy", ""),
        })
    return pd.DataFrame(rows)


def _freq_relations(t, ssb):
    df = t.df
    out = pd.DataFrame({
        "node": df["NodeId"], "cell": df["EUtranCellFDDId"], "freq_rel": df["GUtranFreqRelationId"],
        "ssb_key": _col(df, "gUtranSyncSignalFrequencyRef").map(
            lambda s: ref_value(s, "GUtranSyncSignalFrequency")),
    })
    return out


# --- node completeness ------------------------------------------------------------------

NEIGHBOR_MOS = ("GUtranCellRelation", "ExternalGUtranCell", "ExternalGNodeBFunction", "TermPointToGNB")


def _nodes(tables, mo):
    t = tables.get(mo)
    return set(t.df["NodeId"]) if t is not None and len(t.df) and "NodeId" in t.df.columns else set()


def read_gaps(tables):
    """{node: [MO, ...]} LTE nodes whose neighbor data ENM did not read completely.

    ENM names some failed nodes ("READ command for Node [...MeContext=X...] has failed"), but a
    file can also end with a bare "Error 5010 : The read command has errors" and lose nodes
    silently. Those are found by cross-checking the 4 neighbor MOs of each node (only against
    a file that had ENM errors):
      relations without ExternalGUtranCell, ExternalGUtranCell without relations,
      external cells / TermPoints without ExternalGNodeBFunction, ExternalGNodeBFunction /
      external cells without TermPointToGNB.
    A node that failed in all 4 files at once leaves no trace and cannot be detected."""
    rel, ext, egnb, tp = (_nodes(tables, mo) for mo in NEIGHBOR_MOS)
    gaps = {}

    def add(nodes, mo):
        t = tables.get(mo)
        if t is not None and t.failed:
            for n in nodes:
                if mo not in gaps.setdefault(n, []):
                    gaps[n].append(mo)

    add(rel - ext, "ExternalGUtranCell")
    add(ext - rel, "GUtranCellRelation")
    add((ext | tp) - egnb, "ExternalGNodeBFunction")
    add((ext | egnb) - tp, "TermPointToGNB")
    for mo, t in tables.items():                       # named by ENM
        for n in t.error_nodes:
            if mo not in gaps.setdefault(n, []):
                gaps[n].append(mo)
    return gaps


def _node_status(tables, lte_cells, ext_cells, termpoints, relations):
    nodes = sorted(set(lte_cells["node"]))
    ext_gnb_nodes = _nodes(tables, "ExternalGNodeBFunction")
    tp_nodes = set(termpoints["node"]) if len(termpoints) else set()
    rel_nodes = set(relations["node"]) if len(relations) else set()
    extcell_nodes = set(ext_cells["node"]) if len(ext_cells) else set()
    all_gaps = read_gaps(tables)

    rows = []
    for n in nodes:
        gaps = all_gaps.get(n, [])
        if gaps:
            status = "INCOMPLETE: " + ", ".join(gaps)
        else:
            status = "OK"
        rows.append({"node": n, "status": status,
                     "missing_in": ", ".join(gaps),
                     "relations": n in rel_nodes, "termpoints": n in tp_nodes,
                     "ext_gnb": n in ext_gnb_nodes, "ext_cells": n in extcell_nodes})
    return pd.DataFrame(rows)


# --- build --------------------------------------------------------------------------------

GNB_MOS = ("GNBCUCPFunction", "LocalSctpEndpoint", "SctpEndpoint", "AddressIPv4")


def gnb_directory(tables, settings):
    """gNB node, gNB ID, PLMN and X2 signalling IP from the 4 GNB_MOS tables of one ENM."""
    gnbs = _gnbs(tables["GNBCUCPFunction"])
    x2 = _x2_addresses(tables["LocalSctpEndpoint"], tables["SctpEndpoint"], tables["AddressIPv4"],
                       settings.x2_router_preference)
    chosen = [_choose_x2(x2.get(n, []), settings.x2_router_preference) for n in gnbs["node"]]
    gnbs["x2_ip"] = [c[0] for c in chosen]
    gnbs["x2_status"] = [c[1] for c in chosen]
    gnbs["x2_candidates"] = [c[2] for c in chosen]
    return gnbs


def build(tables, settings, log=print, other_gnbs=None):
    """other_gnbs: gnb_directory() of the other ENMs (column enm), used to resolve gNBs outside this ENM."""
    log("Building inventory")
    lte = _lte_cells(tables["EUtranCellFDD"])
    gnbs = gnb_directory(tables, settings)
    nr = _nr_cells(tables["NRCellDU"], gnbs)

    relations = _relations(tables["GUtranCellRelation"])
    ext_cells = _ext_cells(tables["ExternalGUtranCell"])
    termpoints = _termpoints(tables["TermPointToGNB"])
    freq = _freq_relations(tables["GUtranFreqRelation"], tables.get("GUtranSyncSignalFrequency"))
    status = _node_status(tables, lte, ext_cells, termpoints, relations)

    # FDN prefix per node, taken from any full reference in the exports
    prefix = {}
    for df, col in ((tables["GUtranCellRelation"].df, "neighborCellRef"),
                    (tables["GUtranFreqRelation"].df, "gUtranSyncSignalFrequencyRef"),
                    (tables["SctpEndpoint"].df, "localIpAddress")):
        if col in df.columns:
            for n, ref in zip(df["NodeId"], df[col]):
                if n not in prefix:
                    p = node_prefix(ref)
                    if p:
                        prefix[n] = p

    gaps = {n: g for n, g in read_gaps(tables).items() if n in set(lte["node"])}

    def not_read(mo):
        return sorted(n for n, g in gaps.items() if mo in g)

    inv = Inventory(lte, nr, gnbs, relations, ext_cells, termpoints, freq, status, prefix,
                    [{"MO": t.mo, "Files": ", ".join(t.files), "Rows": t.rows,
                      "Instances (footer)": t.instances, "Status": partial_status(t, not_read(t.mo)),
                      "Nodes not read": ", ".join(not_read(t.mo))} for t in tables.values()])
    inv._gnb_node = {}
    for n, g in zip(gnbs["node"], gnbs["gnb_id"]):
        if not missing(g):
            inv._gnb_node.setdefault(int(g), n)
    inv._status = dict(zip(status["node"], status["status"]))
    inv.other_gnbs = {}
    if other_gnbs is not None and len(other_gnbs):
        for rec in other_gnbs.to_dict("records"):
            if not missing(rec["gnb_id"]) and int(rec["gnb_id"]) not in inv._gnb_node:
                inv.other_gnbs.setdefault(int(rec["gnb_id"]), rec)
        log(f"  gNBs of the other ENMs: {len(inv.other_gnbs):,} "
            f"({sum(1 for r in inv.other_gnbs.values() if r['x2_ip']):,} with X2 IP)")

    log(f"  LTE cells {len(lte):,} on {lte['node'].nunique():,} nodes · NR cells {len(nr):,} on "
        f"{nr['node'].nunique():,} nodes · gNBs {len(gnbs):,} ({(gnbs['x2_ip'] != '').sum():,} with X2 IP)")
    log(f"  Relations {len(relations):,} · external NR cells {len(ext_cells):,} · TermPoints {len(termpoints):,}")
    bad = status[status["status"] != "OK"]
    if len(bad):
        log(f"  Nodes with incomplete data: {len(bad)} -> "
            + ", ".join(f"{n} ({s})" for n, s in zip(bad["node"], bad["status"]))[:600])
    return inv


def fdn(inv, node, rest):
    """Full FDN for a node-relative MO path (falls back to MeContext/ManagedElement)."""
    p = inv.node_prefix.get(node) or f"MeContext={node},ManagedElement={node}"
    return f"{p},{rest}"
