"""
Audit X2 TermPoint LTE to 5G — TermPointToGNB ipAddress (X2 from each eNB to each gNB).

For every TermPointToGNB:
  target gNB  = gNB ID inside ExternalGNodeBFunctionId (PLMN-gNBId, leading zeros ignored)
              -> GNBCUCPFunction.gNBId of a node in this ENM, or in another ENM of the database
  target IP   = X2 address of that gNB: GNBCUCPFunction LocalSctpEndpoint(interfaceUsed=X2)
              -> SctpEndpoint.localIpAddress -> Transport Router/InterfaceIPv4/AddressIPv4,
              Router=NR preferred (the address the working TermPoints already use).
This audit owns ExternalGNodeBFunction and TermPointToGNB; the relation audit (lne_part2) owns
GUtranCellRelation and ExternalGUtranCell and decides, with the Audit Rules of the sidebar, which gNBs
need a new TermPoint (GUtranCellRelation pending within the ADD tier) or have none left (DELETE).
Actions (MOs in execution order)
  NO_CHANGE                                       ipAddress already equals the target IP
  SET TermPointToGNB ipAddress                    ipAddress is 0.0.0.0/empty and one target IP is known
  CREATE ExternalGNodeBFunction; TermPointToGNB   GUtranCellRelation pending to a gNB the eNB does not define
  CREATE TermPointToGNB                           ... the ExternalGNodeBFunction exists without TermPoint
  DELETE TermPointToGNB; ExternalGNodeBFunction   no GUtranCellRelation left and the DELETE tier / overlap rule
  REVIEW                                          ipAddress differs from the target IP, the target IP is
                                                  unknown/ambiguous, or the rules cannot decide
  OUT_OF_SCOPE                                    the gNB is in none of the loaded ENMs (no IP source)
Nothing is executed: the cmedit MOP / rollback scripts are written by lne_mop (STEP 1 TermPoint CREATE
before the relation ADD of 02, STEP 4 TermPoint DELETE after the relation DELETE of 02).
"""

SET_IP = "SET TermPointToGNB ipAddress"
CREATE_BOTH = "CREATE ExternalGNodeBFunction; TermPointToGNB"
CREATE_TP = "CREATE TermPointToGNB"
DELETE_BOTH = "DELETE TermPointToGNB; ExternalGNodeBFunction"

import pandas as pd

from lne_ids import host_ip, is_zero_ip, missing, usable_ip


COLUMNS = ["Source node", "Source node status", "ExternalGNodeBFunction", "TermPointToGNB", "PLMN",
           "Target gNB ID", "Target ENM", "Target node", "Target node status", "Tier", "Target IP Configured",
           "Target IP Used", "Target IP X2 NR", "Target IP X2 status", "Target IP X2 candidates", "Action",
           "Reason", "administrativeState", "operationalState", "availabilityStatus", "createdBy"]


def audit(inv, settings, log=print, plan=None, tier=None):
    """plan: TermPoint decisions of the relation audit (see termpoint_plan); None = IP audit only.
    tier: tier(eNB node, gNB node) of the relation audit (Delaunay hops between sites), else no Tier."""
    log("Audit X2 TermPoint LTE to 5G")
    g = inv.gnbs.set_index("node")
    scope = settings.scope_set()
    scoped = settings.scope == "nodes" and bool(scope)
    rows = []
    for t in inv.termpoints.to_dict("records"):
        if scoped and t["node"].upper() not in scope:
            continue
        cur = host_ip(t["ip_address"])
        r = {"Source node": t["node"], "Source node status": inv.status_of(t["node"]),
             "ExternalGNodeBFunction": t["ext_gnb_ref"], "TermPointToGNB": t["termpoint_id"],
             "PLMN": t["plmn"], "Target gNB ID": t["gnb_id"], "Target ENM": "", "Target node": "",
             "Target node status": "",
             "Target IP Configured": t["ip_address"], "Target IP Used": t["used_ip_address"],
             "Target IP X2 NR": "", "Target IP X2 status": "", "Target IP X2 candidates": "",
             "Action": "REVIEW", "Reason": "",
             "administrativeState": t["admin"], "operationalState": t["oper"],
             "availabilityStatus": t["availability"], "createdBy": t["created_by"]}
        if missing(t["gnb_id"]):
            r["Reason"] = "ipAddress unknown (ExternalGNodeBFunctionId is not PLMN-gNBId)"
            rows.append(r)
            continue
        node = inv.gnb_node(int(t["gnb_id"]))
        if node is not None:
            tg = g.loc[node]
            r.update({"Target ENM": settings.enm, "Target node": node, "Target node status": inv.status_of(node)})
        else:
            tg = inv.other_gnb(int(t["gnb_id"]))
            if tg is None:
                r["Action"] = "OUT_OF_SCOPE"
                r["Reason"] = "ipAddress unknown (gNB not in any loaded ENM)"
                rows.append(r)
                continue
            r.update({"Target ENM": tg["enm"], "Target node": tg["node"],
                      "Target node status": f"in ENM {tg['enm']}"})
        r.update({"Target IP X2 NR": tg["x2_ip"], "Target IP X2 status": tg["x2_status"],
                  "Target IP X2 candidates": tg["x2_candidates"]})
        if tg["plmn"] and t["plmn"] and tg["plmn"] != t["plmn"]:
            r["Reason"] = f"ipAddress unknown (PLMN {t['plmn']} differs from the gNB PLMN {tg['plmn']})"
        elif not tg["x2_ip"]:
            r["Reason"] = f"ipAddress unknown (Target IP X2 NR {tg['x2_status']})"
        elif is_zero_ip(t["ip_address"]):
            r.update({"Action": SET_IP, "Reason": "ipAddress 0.0.0.0"})
            used = host_ip(t["used_ip_address"])
            if usable_ip(used) and used != tg["x2_ip"]:
                r["Reason"] += f" (Target IP Used {used} differs)"
        elif cur == tg["x2_ip"]:
            r.update({"Action": "NO_CHANGE", "Reason": "ipAddress OK"})
        else:
            r["Reason"] = f"ipAddress {cur} differs from {tg['x2_ip']}"
        rows.append(r)
    if plan:
        for r in rows:
            key = (r["Source node"], str(r["ExternalGNodeBFunction"]), str(r["TermPointToGNB"]))
            if key in plan["delete"]:
                r["Action"] = DELETE_BOTH
            elif key in plan["review"] and r["Action"] in ("NO_CHANGE", SET_IP):
                r["Action"] = "REVIEW"
            if key in plan["relations"]:               # '<tier vs rule>, <relations>, <ipAddress>'
                _, tier_part, rel = plan["relations"][key]
                r["Reason"] = f"{tier_part}, {rel}, {r['Reason']}"
        for a in plan["add"]:
            defined = a["ExternalGNodeBFunction defined"] == "YES"
            rows.append({"Source node": a["LTE node"], "Source node status": inv.status_of(a["LTE node"]),
                         "ExternalGNodeBFunction": a["ExternalGNodeBFunction"],
                         "TermPointToGNB": a["TermPointToGNB"], "PLMN": f"{a['MCC']}-{int(a['MNC'])}",
                         "Target gNB ID": a["gNB ID"],
                         "Target ENM": settings.enm if a["NR ENM"] == "this ENM" else a["NR ENM"],
                         "Target node": a["NR node"], "Target node status": inv.status_of(a["NR node"]),
                         "Target IP Configured": "", "Target IP Used": "",
                         "Target IP X2 NR": a["X2 IP"], "Target IP X2 status": a["X2 IP status"],
                         "Target IP X2 candidates": "", "Action": CREATE_TP if defined else CREATE_BOTH,
                         "Reason": f"{a['Reason']}, ipAddress new {a['X2 IP']}",
                         "administrativeState": "", "operationalState": "", "availabilityStatus": "",
                         "createdBy": ""})
    df = pd.DataFrame(rows, columns=COLUMNS)    # same columns when the scope leaves no TermPoint
    if tier is not None and len(df):
        df["Tier"] = [tier(s, t) if t else None for s, t in zip(df["Source node"], df["Target node"])]
    if plan and len(df):          # gNBs outside the loaded ENMs: tier from the EPT position (relation audit)
        known = {k: v[0] for k, v in plan["relations"].items()}             # (tier, tier text, relations)
        keys = zip(df["Source node"], df["ExternalGNodeBFunction"].astype(str), df["TermPointToGNB"].astype(str))
        df["Tier"] = [t if t is not None and t == t else known.get(k) for t, k in zip(df["Tier"], keys)]
    if len(df):
        log("  " + ", ".join(f"{k} {v:,}" for k, v in df["Action"].value_counts().items()))
    else:
        log("  No TermPointToGNB in the scope")
    return df


def summary(df):
    if not len(df):
        return []
    out = [{"Item": "TermPoints audited", "Value": len(df)}]
    for k, v in df["Action"].value_counts().items():
        out.append({"Item": f"Action {k}", "Value": int(v)})
    zero = df["Target IP Configured"].map(is_zero_ip).sum()
    out.append({"Item": "TermPoints with ipAddress 0.0.0.0", "Value": int(zero)})
    return out


def source_summary(df):
    """One row per eNB node (the TermPoints hang from the node): node status, TermPoints that exist
    (Current) and the count of each action, like the Source summary of 02 per LTE cell."""
    if not len(df):
        return pd.DataFrame()
    create = df["Action"].isin([CREATE_TP, CREATE_BOTH])
    rows = []
    for node, g in df.groupby("Source node", sort=True):
        a, c = g["Action"], create[g.index]
        rows.append({"LTE node": node, "Node status": g["Source node status"].iloc[0],
                     "Current": int((~c).sum()), "NO_CHANGE": int((a == "NO_CHANGE").sum()),
                     "SET IP": int((a == SET_IP).sum()), "DELETE": int((a == DELETE_BOTH).sum()),
                     "REVIEW": int((a == "REVIEW").sum()), "OUT_OF_SCOPE": int((a == "OUT_OF_SCOPE").sum()),
                     "CREATE": int(c.sum())})
    return pd.DataFrame(rows)


def target(df):
    """TermPoints after applying every change: all except DELETE (CREATE included), with the ipAddress
    they will have (SET IP and CREATE take the gNB X2 address)."""
    cols = ["Source node", "ExternalGNodeBFunction", "TermPointToGNB", "PLMN", "Target gNB ID", "Target ENM",
            "Target node", "Tier", "ipAddress", "Change"]
    if not len(df):
        return pd.DataFrame(columns=cols)
    t = df[df["Action"] != DELETE_BOTH].copy()
    new_ip = t["Action"].isin([SET_IP, CREATE_TP, CREATE_BOTH])
    t["ipAddress"] = t["Target IP Configured"].where(~new_ip, t["Target IP X2 NR"])
    t["Change"] = t["Action"].map({SET_IP: "ipAddress set", CREATE_TP: "new TermPoint",
                                   CREATE_BOTH: "new ExternalGNodeBFunction + TermPoint",
                                   "NO_CHANGE": "no change", "REVIEW": "no change (REVIEW)",
                                   "OUT_OF_SCOPE": "no change (OUT_OF_SCOPE)"}).fillna(t["Action"])
    t = t.reindex(columns=cols)
    return t.sort_values(["Source node", "Target gNB ID"], kind="stable", key=lambda c: c.astype(str)
                         ).reset_index(drop=True)


def termpoint_plan(tps, add):
    """TermPoint decisions of the relation audit: tps = every TermPoint with its action, add = new ones."""
    plan = {"delete": {}, "review": {}, "relations": {}, "add": []}
    if tps is not None and len(tps):
        for r in tps.to_dict("records"):
            key = (r["LTE node"], str(r["ExternalGNodeBFunction"]), str(r["TermPointToGNB"]))
            if r["Action"] in ("DELETE", "REVIEW"):
                plan[r["Action"].lower()][key] = r["Reason"]
            tr = None if pd.isna(r["Tier"]) else int(r["Tier"])
            plan["relations"][key] = (tr, r["Tier text"], r["GUtranCellRelation"])
    if add is not None and len(add):
        plan["add"] = add.to_dict("records")
    return plan
