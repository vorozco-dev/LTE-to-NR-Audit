"""
MOP and rollback cmedit scripts of the LTE to NR audit. Nothing is executed: review before running in ENM.

Execution order (rollback in the reverse order, 4 -> 1):
  STEP 1  01 TermPoint CREATE   ExternalGNodeBFunction / TermPointToGNB CREATE and TermPointToGNB SET ipAddress
  STEP 2  02 Relation ADD       GUtranFreqRelation / ExternalGUtranCell (when missing) and GUtranCellRelation
  STEP 3  02 Relation DELETE    GUtranCellRelation, then the ExternalGUtranCells of the gNBs left without relations
  STEP 4  01 TermPoint DELETE   TermPointToGNB, then ExternalGNodeBFunction

Each function returns (mop lines, rollback lines). The rollbacks use the attributes read from ENM: a
deleted MO is recreated only with those (other attributes take their default value).
"""

from lne_ids import missing
from lne_inventory import fdn
from lne_part1 import CREATE_BOTH, CREATE_TP, DELETE_BOTH, SET_IP

STEPS = {1: "01 TermPoint CREATE", 2: "02 Relation ADD", 3: "02 Relation DELETE", 4: "01 TermPoint DELETE"}
GUTRA = "ENodeBFunction=1,GUtraNetwork=1"


def _v(x):
    """Attribute value as text: '' for empty / NaN, integers without '.0'."""
    if missing(x) or str(x).strip().lower() in ("", "nan", "none", "null"):
        return ""
    if isinstance(x, float) and x.is_integer():
        return str(int(x))
    s = str(x).strip()
    return s[:-2] if s.endswith(".0") and s[:-2].isdigit() else s


def _attrs(pairs):
    """'a=1;b=2' leaving out the empty values."""
    return ";".join(f"{k}={v}" for k, v in pairs if _v(v) != "")


def _header(step, rollback, what, enm):
    kind = "ROLLBACK" if rollback else "MOP"
    order = (f"run AFTER the rollback of STEP {step + 1} ({STEPS[step + 1]})" if rollback and step < 4 else
             "run FIRST" if rollback else
             f"run AFTER STEP {step - 1} ({STEPS[step - 1]})" if step > 1 else "run FIRST")
    return [f"# LTE to NR Audit (Ericsson) - {kind} STEP {step} of 4: {STEPS[step]} - ENM {enm}",
            f"# {what}",
            f"# Order: {order}. Generated proposals: review before running in ENM.",
            "# Verify the attribute names in your ENM release before running.",
            ""]


def _ext_gnb_create(inv, node, ext_id, gnb, gnb_len, plmn):
    """cmedit create ExternalGNodeBFunction (plmn '334-50' or '33450')."""
    p = str(plmn).replace("-", "")
    mcc, mnc = p[:3], p[3:]
    return (f"cmedit create {fdn(inv, node, f'{GUTRA},ExternalGNodeBFunction={ext_id}')} "
            f"ExternalGNodeBFunctionId=\"{ext_id}\";gNodeBId={gnb};gNodeBIdLength={gnb_len};"
            f"gNodeBPlmnId=(mcc={mcc},mnc={mnc},mncLength={len(mnc)})")


def _gnb_len(inv, gnb):
    node = inv.gnb_node(gnb)
    if node is not None and len(inv.gnbs):
        r = inv.gnbs[inv.gnbs["node"] == node]
        if len(r) and _v(r["gnb_len"].iloc[0]):
            return _v(r["gnb_len"].iloc[0])
    o = inv.other_gnb(gnb)
    return _v(o.get("gnb_len", 22)) if o else "22"


# --- STEP 1 / 4: TermPoints (01) ---------------------------------------------------------

def termpoint_create(inv, tp, plan, enm):
    """STEP 1: CREATE ExternalGNodeBFunction / TermPointToGNB and SET ipAddress."""
    add = {(a["LTE node"], str(a["ExternalGNodeBFunction"]), str(a["TermPointToGNB"])): a
           for a in (plan or {}).get("add", [])}
    what = "ExternalGNodeBFunction / TermPointToGNB CREATE and TermPointToGNB SET ipAddress"
    mop, rb = _header(1, False, what, enm), _header(1, True, what, enm)
    for r in tp.to_dict("records") if len(tp) else []:
        node, ext_id, tp_id = r["Source node"], r["ExternalGNodeBFunction"], r["TermPointToGNB"]
        ext_mo = f"{GUTRA},ExternalGNodeBFunction={ext_id}"
        tp_fdn = fdn(inv, node, f"{ext_mo},TermPointToGNB={tp_id}")
        note = f"# {node} -> gNB {r['Target gNB ID']} ({r['Target node']}): {r['Reason']}"
        if r["Action"] == SET_IP:
            mop += [note, f"cmedit set {tp_fdn} ipAddress={r['Target IP X2 NR']}"]
            rb += [note, f"cmedit set {tp_fdn} ipAddress={_v(r['Target IP Configured']) or '0.0.0.0'}"]
        elif r["Action"] in (CREATE_BOTH, CREATE_TP):
            a = add[(node, str(ext_id), str(tp_id))]
            mop.append(note)
            rb.append(note)
            if r["Action"] == CREATE_BOTH:
                mop.append(_ext_gnb_create(inv, node, ext_id, r["Target gNB ID"], a["gNBIdLength"],
                                           f"{a['MCC']}{a['MNC']}"))
            mop.append(f"cmedit create {tp_fdn} TermPointToGNBId=\"{tp_id}\";ipAddress={r['Target IP X2 NR']}")
            rb.append(f"cmedit delete {tp_fdn}")
            if r["Action"] == CREATE_BOTH:
                rb.append(f"cmedit delete {fdn(inv, node, ext_mo)}")
    return mop, rb


def termpoint_delete(inv, tp, enm):
    """STEP 4: DELETE TermPointToGNB, then ExternalGNodeBFunction."""
    what = "TermPointToGNB and ExternalGNodeBFunction DELETE (gNBs left without GUtranCellRelation)"
    mop, rb = _header(4, False, what, enm), _header(4, True, what, enm)
    for r in tp[tp["Action"] == DELETE_BOTH].to_dict("records") if len(tp) else []:
        node, ext_id, tp_id, gnb = r["Source node"], r["ExternalGNodeBFunction"], r["TermPointToGNB"], r["Target gNB ID"]
        ext_mo = f"{GUTRA},ExternalGNodeBFunction={ext_id}"
        tp_fdn = fdn(inv, node, f"{ext_mo},TermPointToGNB={tp_id}")
        note = f"# {node} -> gNB {gnb} ({r['Target node']}): {r['Reason']}"
        mop += [note, f"cmedit delete {tp_fdn}", f"cmedit delete {fdn(inv, node, ext_mo)}"]
        rb += [note, _ext_gnb_create(inv, node, ext_id, gnb, _gnb_len(inv, gnb), r["PLMN"] or str(ext_id).split("-")[0]),
               f"cmedit create {tp_fdn} TermPointToGNBId=\"{tp_id}\"" +
               (f";ipAddress={_v(r['Target IP Configured'])}" if _v(r["Target IP Configured"]) else "")]
    return mop, rb


# --- STEP 2 / 3: relations (02) ----------------------------------------------------------

def relation_add(inv, adds, enm):
    """STEP 2: GUtranFreqRelation and ExternalGUtranCell when missing, then GUtranCellRelation."""
    what = "GUtranFreqRelation / ExternalGUtranCell (when missing) and GUtranCellRelation CREATE"
    mop, rb = _header(2, False, what, enm), _header(2, True, what, enm)
    done_freq, done_ext = set(), set()
    for r in adds.to_dict("records") if len(adds) else []:
        if not _v(r.get("ExternalGUtranCell")):
            mop.append(f"# SKIPPED {r['LTE cell']} -> {r['NR cell']}: {r.get('ExternalGUtranCell defined', '')}")
            continue
        node, cell = r["LTE node"], r["LTE cell"]
        ssb_fdn = fdn(inv, node, f"{GUTRA},GUtranSyncSignalFrequency={r['NR SSB']}")
        freq_mo = f"ENodeBFunction=1,EUtranCellFDD={cell},GUtranFreqRelation={r['GUtranFreqRelation']}"
        ext_mo = f"{GUTRA},ExternalGNodeBFunction={r['ExternalGNodeBFunction']},ExternalGUtranCell={r['ExternalGUtranCell']}"
        rel_fdn = fdn(inv, node, f"{freq_mo},GUtranCellRelation={r['GUtranCellRelation']}")
        undo = [f"cmedit delete {rel_fdn}"]
        mop.append(f"# {cell} -> {r['NR cell']} ({r['NR node']}): {r['Reason']}")
        rb.append(f"# {cell} -> {r['NR cell']} ({r['NR node']})")
        if str(r.get("GUtranFreqRelation defined", "")).startswith("NO") and (node, cell, freq_mo) not in done_freq:
            done_freq.add((node, cell, freq_mo))
            mop.append(f"# GUtranSyncSignalFrequency={r['NR SSB']} must exist on {node}")
            mop.append(f"cmedit create {fdn(inv, node, freq_mo)} GUtranFreqRelationId=\"{r['GUtranFreqRelation']}\";"
                       f"gUtranSyncSignalFrequencyRef=\"{ssb_fdn}\"")
            undo.append(f"cmedit delete {fdn(inv, node, freq_mo)}")
        if str(r.get("ExternalGUtranCell defined", "")).startswith("NO") and (node, ext_mo) not in done_ext:
            done_ext.add((node, ext_mo))
            mop.append(f"cmedit create {fdn(inv, node, ext_mo)} " + _attrs([
                ("ExternalGUtranCellId", f"\"{r['ExternalGUtranCell']}\""), ("localCellId", r["NR cellLocalId"]),
                ("nRPCI", r["NR PCI"]), ("nRTAC", r["NR TAC"]),
                ("gUtranSyncSignalFrequencyRef", f"\"{ssb_fdn}\"")]))
            undo.append(f"cmedit delete {fdn(inv, node, ext_mo)}")
        mop.append(f"cmedit create {rel_fdn} GUtranCellRelationId=\"{r['GUtranCellRelation']}\";"
                   f"neighborCellRef=\"{fdn(inv, node, ext_mo)}\"")
        rb += undo                              # relation first, then what was created for it
    return mop, rb


def relation_delete(inv, deletes, tp_audit, enm):
    """STEP 3: GUtranCellRelation, then the ExternalGUtranCells of the gNBs whose TermPoint is deleted."""
    what = "GUtranCellRelation DELETE, then ExternalGUtranCell DELETE of the gNBs left without relations"
    mop, rb = _header(3, False, what, enm), _header(3, True, what, enm)
    mop.append("# Reserved (ESS) relations are never listed here; see the PROTECTED relation sheet.")
    rel_rb = []
    for r in deletes.to_dict("records") if len(deletes) else []:
        mop.append(f"cmedit delete {r['Relation FDN']}   # {r['Reason']}")
        rel_rb.append(f"cmedit create {r['Relation FDN']} " + _attrs([
            ("GUtranCellRelationId", f"\"{r['GUtranCellRelation']}\""),
            ("neighborCellRef", f"\"{r['neighborCellRef']}\"" if _v(r.get("neighborCellRef")) else ""),
            ("isEndcAllowed", r.get("isEndcAllowed")), ("isHoAllowed", r.get("isHoAllowed")),
            ("isRemoveAllowed", r.get("isRemoveAllowed"))]))
    ext_rb = []
    tp = tp_audit[tp_audit["Action"] == "DELETE"] if tp_audit is not None and len(tp_audit) else None
    if tp is not None and len(tp):
        ec = inv.ext_cells
        by_key = {(n, str(g).strip(), str(c).strip()): row for n, g, c, row in
                  zip(ec["node"], ec["ext_gnb_ref"], ec["ext_cell_ref"], ec.to_dict("records"))} if len(ec) else {}
        mop += ["", "# ExternalGUtranCell DELETE: after the relation deletions above"]
        for r in tp.to_dict("records"):
            cells = [c for c in str(r.get("ExternalGUtranCells") or "").split("; ") if c and c != "nan"]
            if not cells:
                continue
            mop.append(f"# {r['LTE node']} -> gNB {r['gNB ID']} ({r['NR node']}): {r['Reason']}")
            for c in cells:
                ext_fdn = f"{r['ExternalGNodeBFunction FDN']},ExternalGUtranCell={c}"
                mop.append(f"cmedit delete {ext_fdn}")
                e = by_key.get((r["LTE node"], str(r["ExternalGNodeBFunction"]).strip(), c), {})
                ssb = _v(e.get("ssb_ref"))
                ext_rb.append(f"cmedit create {ext_fdn} " + _attrs([
                    ("ExternalGUtranCellId", f"\"{c}\""), ("localCellId", e.get("local_id")), ("nRPCI", e.get("pci")),
                    ("nRTAC", e.get("nr_tac")),
                    ("gUtranSyncSignalFrequencyRef",
                     f"\"{fdn(inv, r['LTE node'], f'{GUTRA},GUtranSyncSignalFrequency={ssb}')}\"" if ssb else "")]))
    # rollback: the ExternalGUtranCells first (the relations point to them), then the relations
    if ext_rb:
        rb += ["# ExternalGUtranCell (recreated first: the relations refer to them)"] + ext_rb + [""]
    rb += ["# GUtranCellRelation"] + rel_rb
    return mop, rb
