"""
Audit Neighbor Relation LTE to 5G (GUtranCellRelation).

Sources   LTE cells whose band (earfcndl -> band_indicator of conf/freq_band.csv) is one of
          settings.source_bands (default all 4g bands) on a node that also has an NR cell of a
          target band in NRCellDU.
Targets   NR cells whose band (ssbFrequency -> band_indicator, see lne_bands) is one of
          settings.target_bands (default all 5g bands). Existing relations are resolved from
          neighborCellRef (PLMN-gNBId-localId) to this ENM's NRCellDU; a gNB of another
          ENM is resolved through the EPT (Node_B_ID = gNB ID, Local_CELL_ID).
Fans      EPT position/azimuth/beam + max85 propagation radius (see lne_geo_inputs).
Tier      Delaunay hops between NR sites on air (EPT, all ENMs); co-located = 0.

Actions on an existing relation
  DELETE      tier >= settings.min_delete_tier, or zero fan overlap at tier >=
              settings.zero_overlap_min_tier (same site / first ring are never deleted for overlap)
  PROTECTED   would be DELETE but the relation is reserved (reservedBy, e.g. ESS sharing group)
  KEEP        tier below settings.min_delete_tier and either fan overlap > 0 or tier below
              settings.zero_overlap_min_tier (kept with or without overlap)
  REVIEW      overlap or tier unknown (missing geometry/propagation) — unknown is not zero
Actions on a missing pair (no relation)
  ADD         tier <= settings.max_add_all_tier (every NR cell of those sites, overlap or not), or
              overlap > 0 and tier <= settings.max_add_tier (geometries reliable: EXACT/SECTOR);
              source/target active (option) and source node data complete
  HOLD        would be ADD but one of those conditions is not met (reason given)
Last rule, after the tier rules: Max Site distance (settings.max_site_km, km between the EPT
site positions). A relation to a site farther than it is DELETE (PROTECTED if reserved) and a
missing one is not proposed (no ADD / HOLD).
ADD rows are sorted by tier (0 first). Nothing is executed.

X2 TermPoints (decided here, reported and written as commands by the TermPoint audit, lne_part1),
one per eNB node -> gNB
  ADD         an ADD relation goes to a gNB the eNB has no TermPointToGNB for (with the gNB X2
              address; without it the relation ADD becomes HOLD)
  DELETE      after the relation DELETEs no GUtranCellRelation of any cell of the eNB (any band,
              PROTECTED included) goes to that gNB, and tier >= settings.min_delete_tier, or no fan of
              the eNB overlaps a fan of the gNB at tier >= settings.zero_overlap_min_tier. Its
              ExternalGUtranCells are deleted here (02), the TermPoint and ExternalGNodeBFunction in 01.
              Also DELETE when no relation is left and the gNB site is beyond Max Site distance.
  KEEP        relations remain, or the fans overlap / the tier is below the thresholds
  REVIEW      no relation left but tier or overlap unknown
"""

import collections

import pandas as pd
from shapely import STRtree

from lne_geometry import Projector, SiteTopology, fan
from lne_ids import missing, parse_gnb, site_code
from lne_inventory import fdn

RELIABLE = ("EXACT", "SECTOR")


class _Cells:
    """Geometry + fan per cell (LTE or NR) with the reason when a fan cannot be built."""

    def __init__(self, ept, prop, proj, settings):
        self.ept, self.prop, self.proj, self.s = ept, prop, proj, settings
        self.cache = {}

    def get(self, tech, node, cell, ept_rec=None):
        key = (tech, cell)
        if key in self.cache:
            return self.cache[key]
        if ept_rec is not None:
            geo = self.ept.geometry("", ept_rec["cell_name"], self.s.default_beam)
        else:
            geo = self.ept.geometry(node, cell, self.s.default_beam)
        radius = self.prop.radius_m(tech, cell)
        if radius is None and ept_rec is not None:
            radius = self.prop.radius_m(tech, ept_rec["cell_name"])
        g = dict(geo, radius=radius, xy=None, fan=None, area=None, fan_issue="",
                 site=site_code(node) or site_code(cell))
        if geo["lat"] is not None:
            g["xy"] = self.proj.xy(geo["lat"], geo["lon"])
        if geo["quality"] == "NONE":
            g["fan_issue"] = "no position (not in EPT)"
        elif geo["azimuth"] is None:
            g["fan_issue"] = "no azimuth (site position only)"
        elif radius is None:
            g["fan_issue"] = "no propagation radius"
        else:
            g["fan"] = fan(g["xy"][0], g["xy"][1], geo["azimuth"], geo["beam"], radius, self.s.arc_step_deg)
            g["area"] = g["fan"].area
        self.cache[key] = g
        return g


def audit(inv, ept, prop, settings, log=print, want_lines=False):
    """(sheets, lines, totals) or None when no source cell is in the scope. lines (only with
    want_lines, for the Graphical Audit map): one dict per relation, see _add_line."""
    log("Audit Neighbor Relation LTE to 5G")
    s = settings
    nr = inv.nr_cells[inv.nr_cells["band"].isin(s.target_bands)].copy()
    nr_sites = set(nr["node"])
    log(f"  Target NR cells: {len(nr):,} ({', '.join(s.target_bands)}) on {len(nr_sites):,} gNB nodes")
    nr_unknown = inv.nr_cells[inv.nr_cells["band"].str.startswith("unknown")]
    if len(nr_unknown):
        log(f"  NR cells with a band not in conf/freq_band.csv (never targets): {len(nr_unknown):,} -> "
            + ", ".join(f"{c} {b}" for c, b in zip(nr_unknown["cell"][:10], nr_unknown["band"][:10])))
    scope = s.scope_set()

    exclusions = []
    lte = inv.lte_cells[inv.lte_cells["band"].isin(s.source_bands)]
    if s.scope == "nodes" and scope:
        lte = lte[lte["node"].str.upper().isin(scope)]
    for r in lte[~lte["node"].isin(nr_sites)].to_dict("records"):
        exclusions.append({"Technology": "LTE", "Node": r["node"], "Cell": r["cell"],
                           "Reason": f"node has no NR cell of {', '.join(s.target_bands)} in NRCellDU"})
    sources = lte[lte["node"].isin(nr_sites)].reset_index(drop=True)
    log(f"  Source cells: {len(sources):,} ({', '.join(s.source_bands)} on {sources['node'].nunique():,} "
        f"nodes with NR); {len(exclusions):,} excluded (no NR on the node)")
    unknown = inv.lte_cells[inv.lte_cells["band"].str.startswith("unknown")]
    if len(unknown):
        log(f"  LTE cells with an earfcndl not in conf/freq_band.csv (never sources): {len(unknown):,} -> "
            + ", ".join(sorted(set(unknown["band"]))[:10]))
    if not len(sources):
        return None                   # lne_run logs it and skips 02_* (not an error with a node list)

    # Projection centred on the sources
    pos = [ept.geometry(n, c, s.default_beam) for n, c in zip(sources["node"], sources["cell"])]
    lats = [p["lat"] for p in pos if p["lat"] is not None] or [19.4]
    lons = [p["lon"] for p in pos if p["lon"] is not None] or [-99.1]
    proj = Projector(lats, lons)
    cells = _Cells(ept, prop, proj, s)

    # Topology over NR sites on air (EPT) plus this ENM's NR nodes
    site_xy = {site: proj.xy(*ll) for site, ll in ept.nr_sites.items()}
    for node in nr_sites:
        sc = site_code(node)
        if sc and sc not in site_xy and sc in ept.by_site:
            site_xy[sc] = proj.xy(*ept.by_site[sc])
    topo = SiteTopology(site_xy, log)

    def tier(src_node, tgt_node):
        return topo.tier(site_code(src_node), site_code(tgt_node))

    site_xy_all = {}

    def site_km(src_node, tgt_node):
        """km between the two sites (EPT site positions), None when one is not in the EPT. The
        'Max Site distance' rule is applied with it after the tier rules."""
        out = []
        for n in (src_node, tgt_node):
            sc = site_code(n)
            if sc not in site_xy_all:
                site_xy_all[sc] = proj.xy(*ept.by_site[sc]) if sc in ept.by_site else None
            out.append(site_xy_all[sc])
        if None in out:
            return None
        (x1, y1), (x2, y2) = out
        return round(((x1 - x2) ** 2 + (y1 - y2) ** 2) ** 0.5 / 1000, 2)

    # NR target fans of this ENM (spatial index for missing-neighbor candidates)
    nr_recs = nr.to_dict("records")
    nr_by_key = {}
    for r in nr_recs:
        if not missing(r["gnb_id"]) and not missing(r["local_id"]):
            nr_by_key[(int(r["gnb_id"]), int(r["local_id"]))] = r
    nr_geo = [cells.get("NR", r["node"], r["cell"]) for r in nr_recs]
    with_fan = [i for i, g in enumerate(nr_geo) if g["fan"] is not None]
    tree = STRtree([nr_geo[i]["fan"] for i in with_fan])

    # Existing relations of the sources, resolved to target cells
    src_keys = set(zip(sources["node"], sources["cell"]))
    rel = inv.relations[[k in src_keys for k in zip(inv.relations["node"], inv.relations["cell"])]]
    ext_cell_id = {}                             # (node, gNB, localCellId) -> (ExternalGNodeBFunction, ExternalGUtranCell)
    for n, eg, ec, g, l in zip(inv.ext_cells["node"], inv.ext_cells["ext_gnb_ref"], inv.ext_cells["ext_cell_ref"],
                               inv.ext_cells["gnb_id"], inv.ext_cells["local_id"]):
        if not (missing(g) or missing(l)):
            ext_cell_id.setdefault((n, int(g), int(l)), (str(eg).strip(), str(ec).strip()))
    ids = _IdConvention(inv)
    freq_by_cell = collections.defaultdict(dict)
    for n, c, f, k in zip(inv.freq_relations["node"], inv.freq_relations["cell"],
                          inv.freq_relations["freq_rel"], inv.freq_relations["ssb_key"]):
        freq_by_cell[(n, c)][k] = f

    existing = collections.defaultdict(list)     # (node, cell) -> [(relation row, target dict)]
    unresolved = []
    for r in rel.to_dict("records"):
        target = None
        if r["ref_ok"]:
            key = (int(r["t_gnb"]), int(r["t_local"]))
            t = nr_by_key.get(key)
            if t is not None:
                target = {"node": t["node"], "cell": t["cell"], "in_enm": True, "row": t, "ept": None}
            else:
                e = ept.by_gnb.get(key)
                if e is not None:
                    target = {"node": e.get("att_node_name") or e.get("site") or "", "cell": e["cell_name"],
                              "in_enm": False, "row": None, "ept": e}
        if target is None:
            unresolved.append({"Source node": r["node"], "Source cell": r["cell"],
                               "GUtranCellRelation": r["relation_id"], "neighborCellRef": r["neighbor_cell_ref"],
                               "Reason": ("neighborCellRef is not PLMN-gNBId-localId" if not r["ref_ok"] else
                                          f"gNB {r['t_gnb']} cell {r['t_local']} not in NRCellDU nor in EPT")})
            continue
        existing[(r["node"], r["cell"])].append((r, target))

    current, adds, review = [], [], []
    lines = []                                   # Graphical Audit relations (only with want_lines)
    summary_rows = []
    tp_on = True                                 # X2 TermPoints are always audited with the relations
    tps = _TermPoints(inv) if tp_on else None
    nr_by_node = collections.defaultdict(list)   # NR node -> indexes in nr_recs
    for i, r in enumerate(nr_recs):
        nr_by_node[r["node"]].append(i)
    near_cache = {}

    def near_nr(src_node):
        """Indexes of the NR cells whose site is within 'ADD ... within tier' of the source node."""
        if src_node not in near_cache:
            out = []
            for n, idx in nr_by_node.items():
                tr = tier(src_node, n)
                if tr is not None and tr <= s.max_add_all_tier:
                    out += idx
            near_cache[src_node] = out
        return near_cache[src_node]

    rel_act = collections.defaultdict(collections.Counter)   # (eNB node, gNB ID) -> relation actions
    add_to = collections.defaultdict(list)       # (eNB node, gNB ID) -> ADD relations that need a new TermPoint
    for src in sources.to_dict("records"):
        node, cell = src["node"], src["cell"]
        sg = cells.get("LTE", node, cell)
        node_status = inv.status_of(node)
        counts = collections.Counter()
        have = set()
        for r, t in existing.get((node, cell), []):
            tg = cells.get("NR", t["node"], t["cell"], t["ept"])
            have.add(t["cell"].upper())
            rec = _pair(src, sg, t, tg, tier(node, t["node"]), s, relation=r)
            rec["Site distance km"] = site_km(node, t["node"])
            rec["Action"], rec["Reason"] = _classify_existing(rec, r, s)
            rec["Relation FDN"] = fdn(inv, node, f"ENodeBFunction=1,EUtranCellFDD={cell},"
                                                f"GUtranFreqRelation={r['freq_rel']},"
                                                f"GUtranCellRelation={r['relation_id']}")
            current.append(rec)
            counts[rec["Action"]] += 1
            gnb = _relation_gnb(r)
            rel_act[(node, gnb)][rec["Action"]] += 1
            if want_lines:
                has_tp = gnb is not None and (node, gnb) in tps.tp
                kind = "delete" if rec["Action"] == "DELETE" else "complete" if has_tp else "missing_tp"
                _add_line(lines, kind, src, sg, tg, rec, termpoint="YES" if has_tp else "NO")
        # candidates: NR fans overlapping the source fan, plus every NR cell of the sites within
        # 'ADD ... within tier' (overlap or not)
        candidates = {}
        if sg["fan"] is not None:
            for j in tree.query(sg["fan"]):
                i = with_fan[j]
                inter = sg["fan"].intersection(nr_geo[i]["fan"]).area
                if inter > 0:
                    candidates[i] = inter
        for i in near_nr(node):
            candidates.setdefault(i, None)
        for i, inter in candidates.items():
            t = nr_recs[i]
            if t["cell"].upper() in have:
                continue
            tg = nr_geo[i]
            tt = {"node": t["node"], "cell": t["cell"], "in_enm": True, "row": t, "ept": None}
            rec = _pair(src, sg, tt, tg, tier(node, t["node"]), s, overlap=inter)
            rec["Site distance km"] = site_km(node, t["node"])
            rec["Action"], rec["Reason"] = _classify_missing(rec, src, t, sg, tg, node_status, s)
            if rec["Action"] is None:
                continue
            key = (int(t["gnb_id"]), int(t["local_id"])) if not (missing(t["gnb_id"]) or missing(t["local_id"])) else None
            fr = freq_by_cell[(node, cell)].get(t["ssb_key"])
            rec.update({"NR gNB ID": t["gnb_id"], "NR cellLocalId": t["local_id"], "NR PCI": t["pci"],
                        "NR TAC": t["tac"], "NR SSB": t["ssb_key"],
                        "GUtranFreqRelation": fr or ids.freq_rel(t["ssb_key"]),
                        "GUtranFreqRelation defined": "YES" if fr else "NO (created first)"})
            if key is not None:
                ext = ext_cell_id.get((node, *key))
                eg = ext[0] if ext else (tps.ext_ref.get((node, key[0])) or
                                         tps._new_ext_ref(node, key[0], (tps.gnb_info(key[0]) or {}).get("plmn", "")))
                ec = ext[1] if ext else ids.ext_cell(eg, key[1])
                rec.update({"ExternalGNodeBFunction": eg, "ExternalGUtranCell": ec,
                            "ExternalGUtranCell defined": "YES" if ext else "NO (created first)",
                            "GUtranCellRelation": ids.relation(ec, key)})
            else:
                rec["ExternalGUtranCell defined"] = "UNKNOWN (target gNB ID or cellLocalId missing)"
            if tp_on:
                tps.check_add(rec, node, None if missing(t["gnb_id"]) else int(t["gnb_id"]), add_to)
            (adds if rec["Action"] == "ADD" else review).append(rec)
            if rec["Action"] == "ADD" and not missing(t["gnb_id"]):
                rel_act[(node, int(t["gnb_id"]))]["ADD"] += 1
            counts[rec["Action"]] += 1
            if rec["Action"] == "ADD" and want_lines:
                _add_line(lines, "add", src, sg, tg, rec, termpoint=rec.get("TermPointToGNB", ""))
        summary_rows.append({"LTE node": node, "LTE cell": cell, "LTE band": src["band"], "Node data": node_status,
                             "Geometry": sg["quality"], "Fan": "OK" if sg["fan"] is not None else sg["fan_issue"],
                             "Current": len(existing.get((node, cell), [])),
                             "KEEP": counts["KEEP"], "DELETE": counts["DELETE"], "PROTECTED": counts["PROTECTED"],
                             "REVIEW": counts["REVIEW"], "ADD": counts["ADD"], "HOLD": counts["HOLD"]})

    adds.sort(key=lambda r: (r["Tier"] if r["Tier"] is not None else 99, r["LTE node"], r["LTE cell"], r["NR cell"]))
    cur = pd.DataFrame(current)
    sheets = {
        "Source summary": pd.DataFrame(summary_rows),
        "CURRENT relation": cur,
        "ADD relation": pd.DataFrame(adds),
        "DELETE relation": cur[cur["Action"] == "DELETE"] if len(cur) else cur,
        "PROTECTED relation": cur[cur["Action"] == "PROTECTED"] if len(cur) else cur,
        "REVIEW relation": pd.concat([cur[cur["Action"] == "REVIEW"] if len(cur) else cur, pd.DataFrame(review)],
                                    ignore_index=True),
        "TARGET relation": target_relations(cur, adds),
        "NO NR MOB relation": (cur[cur["nr mobility available"].astype(str).str.lower() == "false"]
                               if len(cur) else cur),
        "UNRESOLVED relation": pd.DataFrame(unresolved),
        "Exclusions": pd.DataFrame(exclusions),
    }
    tp_audit, tp_add = pd.DataFrame(), pd.DataFrame()
    if tp_on:
        tp_audit, tp_add = tps.audit(sources, cells, ept, tier, s, rel_act, add_to, site_km)
        log(f"  TermPoints: ADD {len(tp_add):,}, " + (", ".join(
            f"{k} {v:,}" for k, v in tp_audit["Action"].value_counts().items()) if len(tp_audit) else "none audited"))
    n_add, n_del = len(adds), int((cur["Action"] == "DELETE").sum()) if len(cur) else 0
    log(f"  Existing relations of the sources: {len(cur):,} resolved, {len(unresolved):,} unresolved")
    if len(cur):
        log("  Existing: " + ", ".join(f"{k} {v:,}" for k, v in cur["Action"].value_counts().items()))
    log(f"  Missing: ADD {n_add:,}, HOLD {len(review):,}")
    return sheets, lines, {"sources": len(sources), "add": n_add, "delete": n_del, "tier": tier,
                         "tp_audit": tp_audit, "tp_add": tp_add}


TARGET_COLUMNS = ["LTE node", "LTE cell", "NR node", "NR cell", "Tier", "Overlap m2", "Distance m",
                  "GUtranFreqRelation", "GUtranCellRelation", "Change"]
TARGET_CHANGE = {"KEEP": "no change", "PROTECTED": "no change (PROTECTED)", "REVIEW": "no change (REVIEW)",
                 "ADD": "new"}


def target_relations(cur, adds):
    """Relations of the sources after applying every change: existing ones except DELETE, plus the ADDs
    (HOLD is not applied, so not included)."""
    rows = [r for r in (cur.to_dict("records") if len(cur) else []) if r["Action"] != "DELETE"] + list(adds)
    df = pd.DataFrame(rows)
    if not len(df):
        return pd.DataFrame(columns=TARGET_COLUMNS)
    df["Change"] = df["Action"].map(TARGET_CHANGE).fillna(df["Action"])
    df = df.reindex(columns=TARGET_COLUMNS)
    return df.sort_values(["LTE node", "LTE cell", "NR node", "NR cell"], kind="stable").reset_index(drop=True)


def _pair(src, sg, t, tg, tier, s, relation=None, overlap=None):
    if overlap is None and sg["fan"] is not None and tg["fan"] is not None:
        overlap = sg["fan"].intersection(tg["fan"]).area if sg["fan"].intersects(tg["fan"]) else 0.0
    dist = None
    if sg["xy"] is not None and tg["xy"] is not None:
        dist = ((sg["xy"][0] - tg["xy"][0]) ** 2 + (sg["xy"][1] - tg["xy"][1]) ** 2) ** 0.5
    rec = {
        "LTE node": src["node"], "LTE cell": src["cell"], "NR node": t["node"], "NR cell": t["cell"],
        "Tier": tier, "Overlap m2": round(overlap) if overlap is not None else None,
        "LTE overlap %": round(100 * overlap / sg["area"], 1) if overlap is not None and sg["area"] else None,
        "NR overlap %": round(100 * overlap / tg["area"], 1) if overlap is not None and tg["area"] else None,
        "Distance m": round(dist) if dist is not None else None,
        "LTE radius m": round(sg["radius"]) if sg["radius"] else None,
        "NR radius m": round(tg["radius"]) if tg["radius"] else None,
        "LTE geometry": sg["quality"] + (f" ({sg['fan_issue']})" if sg["fan_issue"] else ""),
        "NR geometry": tg["quality"] + (f" ({tg['fan_issue']})" if tg["fan_issue"] else ""),
        "NR in this ENM": "YES" if t["in_enm"] else "NO (resolved by EPT)",
        "LTE active": src["active"],
        "NR active": t["row"]["active"] if t["row"] is not None else "",
    }
    if relation is not None:
        rec.update({
            "GUtranCellRelation": relation["relation_id"], "GUtranFreqRelation": relation["freq_rel"],
            "createdBy": relation["created_by"], "isRemoveAllowed": relation["is_remove_allowed"],
            "reservedBy": relation["reserved_by"], "essEnabled": relation["ess_enabled"],
            "isEndcAllowed": relation["is_endc_allowed"], "isHoAllowed": relation["is_ho_allowed"],
            "nr mobility available": relation["nr_mobility_available"],
            "nr mobility reason": relation["nr_mobility_reason"],
            "neighborCellRef": relation["neighbor_cell_ref"],
        })
    return rec


def _classify_existing(rec, r, s):
    area, tier = rec["Overlap m2"], rec["Tier"]
    reasons = []
    if area is not None and area <= 0 and tier is not None and tier >= s.zero_overlap_min_tier:
        reasons.append(f"zero fan overlap at tier {tier}")
    if tier is not None and tier >= s.min_delete_tier:
        reasons.append(f"tier {tier} >= {s.min_delete_tier}")
    km = rec.get("Site distance km")
    if km is not None and km > s.max_site_km:           # last rule: whatever the tier says
        reasons.append(f"site distance {km:g} km > {s.max_site_km:g}")
    if reasons:
        if r["reserved_by"]:
            return "PROTECTED", "; ".join(reasons) + f" but reserved by {r['reserved_by']} (not deleted)"
        return "DELETE", "; ".join(reasons)
    if area is None or tier is None:
        unknown = [x for x, v in (("overlap", area), ("tier", tier)) if v is None]
        return "REVIEW", "unknown " + " and ".join(unknown) + " (missing geometry, propagation or topology)"
    if area > 0:
        return "KEEP", f"fan overlap; tier {tier} < {s.min_delete_tier}"
    return "KEEP", f"no fan overlap but tier {tier} < {s.zero_overlap_min_tier} (kept with or without overlap)"


def _classify_missing(rec, src, t, sg, tg, node_status, s):
    tier, area = rec["Tier"], rec["Overlap m2"]
    near = tier is not None and tier <= s.max_add_all_tier          # every cell, overlap or not
    overlap = area is not None and area > 0
    if not near and not (overlap and (tier is None or tier <= s.max_add_tier)):
        return None, ""                       # not a candidate
    km = rec.get("Site distance km")
    if km is not None and km > s.max_site_km:
        return None, ""                       # last rule: no ADD towards a site beyond 'Max Site distance'
    holds = []
    if tier is None:
        holds.append("tier unknown")
    if node_status != "OK":
        holds.append(f"source node data {node_status}")
    if s.active_only_for_add and not (src["active"] and t["active"]):
        holds.append("source or target not UNLOCKED+ENABLED")
    if not near and (sg["quality"] not in RELIABLE or tg["quality"] not in RELIABLE):
        holds.append("geometry estimated from site only")
    if holds:
        return "HOLD", "; ".join(holds)
    if near:
        return "ADD", f"missing relation; tier {tier} <= {s.max_add_all_tier}"
    return "ADD", f"missing relation; fans overlap; tier {tier} <= {s.max_add_tier}"


def _add_line(lines, kind, src, sg, tg, rec, termpoint=""):
    """One relation for the Graphical Audit map: positions of both cells (EPT), kind = key of
    lne_colors.RELATION_LINES (complete / missing_tp / delete / add)."""
    lines.append({"kind": kind, "LTE node": rec["LTE node"], "LTE cell": rec["LTE cell"], "LTE band": src["band"],
                  "NR node": rec["NR node"], "NR cell": rec["NR cell"], "NR site": tg["site"],
                  "Tier": rec["Tier"], "Action": rec["Action"], "TermPointToGNB": termpoint,
                  "Site distance km": rec.get("Site distance km"), "Overlap m2": rec["Overlap m2"],
                  "Reason": rec["Reason"],
                  "lte_lat": sg["lat"], "lte_lon": sg["lon"], "nr_lat": tg["lat"], "nr_lon": tg["lon"]})


class _IdConvention:
    """IDs of new MOs following what the network already uses (majority of the existing ones)."""

    def __init__(self, inv):
        fr = inv.freq_relations
        same = (fr["freq_rel"].astype(str) == fr["ssb_key"].astype(str)).mean() if len(fr) else 1
        arfcn = (fr["freq_rel"].astype(str) == fr["ssb_key"].astype(str).str.split("-").str[0]).mean() if len(fr) else 0
        self.freq_style = "arfcn" if arfcn > same else "ssb"
        rel = inv.relations[inv.relations["ref_ok"]] if len(inv.relations) else inv.relations
        if len(rel):
            as_ext = (rel["relation_id"].astype(str) == rel["ext_cell_ref"].astype(str)).mean()
            as_pair = (rel["relation_id"].astype(str) == [f"{g}-{l}" for g, l in zip(rel["t_gnb"], rel["t_local"])]).mean()
            self.rel_style = "pair" if as_pair > as_ext else "ext"
        else:
            self.rel_style = "ext"
        ec = inv.ext_cells
        parts = [str(x).split("-") for x in (ec["ext_cell_ref"] if len(ec) else [])]
        self.cell_width = _most_common((len(p[1]) for p in parts if len(p) == 3), 0)

    def freq_rel(self, ssb_key):
        return str(ssb_key).split("-")[0] if self.freq_style == "arfcn" else str(ssb_key)

    def ext_cell(self, ext_gnb, local):
        prefix, _, gnb = str(ext_gnb).partition("-")
        g = f"{int(gnb):0{self.cell_width}d}" if self.cell_width and gnb.isdigit() else gnb
        return f"{prefix}-{g}-{local}"

    def relation(self, ext_cell, key):
        return f"{key[0]}-{key[1]}" if self.rel_style == "pair" else ext_cell


# --- X2 TermPoints ---------------------------------------------------------------------

def _relation_gnb(r):
    """gNB ID of a relation: from neighborCellRef, else from its ExternalGNodeBFunction."""
    if not missing(r.get("t_gnb")):
        return int(r["t_gnb"])
    g = parse_gnb(r.get("ext_gnb_ref", ""))
    return g[1] if g else None


REL_ORDER = ("ADD", "KEEP", "DELETE", "PROTECTED", "REVIEW")


def relations_text(c):
    """'ADD 2, KEEP 4 GUtranCellRelation' (zero counts left out) or 'no GUtranCellRelation'."""
    parts = [f"{k} {c[k]}" for k in REL_ORDER if c.get(k)]
    return ", ".join(parts) + " GUtranCellRelation" if parts else "no GUtranCellRelation"


def _t(tier):
    return "?" if tier is None else tier


def tier_text(tr, s, overlap=None):
    """Tier against the threshold of its Audit Rule zone; overlap ('no' / 'unknown' / 'yes') when the
    decision depends on the fan overlap (zone of 'DELETE ... with no fan overlap from tier')."""
    if tr is None:
        return "tier ?"
    if overlap:
        return f"tier {tr} >= {s.zero_overlap_min_tier} " + {"no": "no fan overlap", "unknown": "fan overlap unknown",
                                                            "yes": "fan overlap"}[overlap]
    if tr <= s.max_add_all_tier:
        return f"tier {tr} <= {s.max_add_all_tier}"
    if tr <= s.max_add_tier:
        return f"tier {tr} <= {s.max_add_tier}"
    if tr < s.min_delete_tier:
        return f"tier {tr} < {s.min_delete_tier}"
    return f"tier {tr} >= {s.min_delete_tier}"


def _most_common(values, default):
    c = collections.Counter(v for v in values if v not in (None, ""))
    return c.most_common(1)[0][0] if c else default


class _TermPoints:
    """TermPointToGNB / ExternalGNodeBFunction of each eNB node, by target gNB ID."""

    def __init__(self, inv):
        self.inv = inv
        self.tp = {}                                  # (node, gNB) -> TermPointToGNB record
        for r in inv.termpoints.to_dict("records") if len(inv.termpoints) else []:
            if not missing(r["gnb_id"]):
                self.tp.setdefault((r["node"], int(r["gnb_id"])), r)
        self.ext_ref = {}                             # (node, gNB) -> ExternalGNodeBFunction ID
        for df in (inv.termpoints, inv.ext_cells, inv.relations):
            if not len(df):
                continue
            for n, ref in zip(df["node"], df["ext_gnb_ref"]):
                g = parse_gnb(ref)
                if g:
                    self.ext_ref.setdefault((n, g[1]), str(ref).strip())
        self.ext_cells_of = collections.defaultdict(list)   # (node, ExternalGNodeBFunction) -> ExternalGUtranCell IDs
        for n, ref, c in (zip(inv.ext_cells["node"], inv.ext_cells["ext_gnb_ref"], inv.ext_cells["ext_cell_ref"])
                          if len(inv.ext_cells) else []):
            self.ext_cells_of[(n, str(ref).strip())].append(str(c).strip())
        self.rel_count = collections.Counter()        # (node, gNB) -> relations of every cell of the node
        for r in inv.relations.to_dict("records") if len(inv.relations) else []:
            self.rel_count[(r["node"], _relation_gnb(r))] += 1
        refs = [r.split("-") for r in self.ext_ref.values()]
        self.prefix_of = collections.defaultdict(list)
        for (n, _), ref in self.ext_ref.items():
            self.prefix_of[n].append(ref.split("-")[0])
        self.prefix = _most_common((p for p, _ in refs), "")
        self.width = _most_common((len(d) for _, d in refs), 0)
        # TermPointToGNB ID of a new TermPoint: the network convention (same ID as its
        # ExternalGNodeBFunction in most networks, otherwise the most common fixed ID such as "1")
        tps = inv.termpoints
        same = (tps["termpoint_id"].astype(str) == tps["ext_gnb_ref"].astype(str)).mean() if len(tps) else 0
        self.tp_same_as_ext = same > 0.5
        self.tp_id = _most_common((str(r) for r in tps.get("termpoint_id", [])), "1")
        self.gnb_rows = inv.gnbs.set_index("node").to_dict("index") if len(inv.gnbs) else {}
        self.ept_gnb = None

    def gnb_info(self, gnb, ept=None):
        """node, ENM, PLMN, X2 IP and gNBIdLength of a gNB (this ENM, another ENM, or only the EPT)."""
        node = self.inv.gnb_node(gnb)
        if node is not None:
            r = self.gnb_rows.get(node, {})
            return {"node": node, "enm": "this ENM", "plmn": r.get("plmn", ""), "x2_ip": r.get("x2_ip", ""),
                    "x2_status": r.get("x2_status", ""), "gnb_len": r.get("gnb_len", 22)}
        o = self.inv.other_gnb(gnb)
        if o is not None:
            return {"node": o["node"], "enm": o["enm"], "plmn": o["plmn"], "x2_ip": o["x2_ip"],
                    "x2_status": o["x2_status"], "gnb_len": o.get("gnb_len", 22)}
        if ept is not None:
            recs = self._ept_cells(ept).get(gnb)
            if recs:
                e = recs[0]
                return {"node": e.get("att_node_name") or e.get("site") or "", "enm": "EPT only", "plmn": "",
                        "x2_ip": "", "x2_status": "gNB not in any loaded ENM", "gnb_len": 22}
        return None

    def _ept_cells(self, ept):
        if self.ept_gnb is None:
            self.ept_gnb = collections.defaultdict(list)
            for (g, _), rec in ept.by_gnb.items():
                self.ept_gnb[g].append(rec)
        return self.ept_gnb

    def check_add(self, rec, node, gnb, add_to):
        """TermPoint status of a missing relation; an ADD without a creatable TermPoint becomes HOLD."""
        if gnb is None:
            rec["TermPointToGNB"] = "UNKNOWN (target gNB ID missing)"
            hold = "target gNB ID unknown (TermPoint cannot be checked)"
        elif (node, gnb) in self.tp:
            rec["TermPointToGNB"] = "YES"
            return
        else:
            info = self.gnb_info(gnb)
            if info and info["x2_ip"]:
                rec["TermPointToGNB"] = "NO (see CREATE TermPoint in 01)"
                if rec["Action"] == "ADD":
                    add_to[(node, gnb)].append(rec)
                return
            rec["TermPointToGNB"] = "NO (gNB X2 IP unknown)"
            hold = "no TermPointToGNB and the gNB X2 IP is unknown"
        if rec["Action"] == "ADD":
            rec["Action"], rec["Reason"] = "HOLD", hold
        else:
            rec["Reason"] += f"; {hold}"

    def _new_ext_ref(self, node, gnb, plmn):
        prefix = _most_common(self.prefix_of.get(node, []), self.prefix)
        if not prefix and plmn:
            mcc, mnc = plmn.split("-")
            prefix = f"{mcc}{int(mnc):02d}"
        return f"{prefix}-{gnb:0{self.width}d}" if self.width else f"{prefix}-{gnb}"

    def _overlap(self, node, gnb, cells, ept):
        """True if a fan of the eNB overlaps a fan of the gNB, False if none does, None if unknown."""
        lte = self.inv.lte_cells
        lte_fans = [cells.get("LTE", node, c)["fan"] for c in lte[lte["node"] == node]["cell"]]
        nr = self.inv.nr_cells
        nr_rows = nr[nr["gnb_id"] == gnb] if len(nr) else nr
        if len(nr_rows):
            nr_fans = [cells.get("NR", n, c)["fan"] for n, c in zip(nr_rows["node"], nr_rows["cell"])]
        else:
            nr_fans = [cells.get("NR", "", e["cell_name"], e)["fan"] for e in self._ept_cells(ept).get(gnb, [])]
        known_l = [f for f in lte_fans if f is not None]
        known_n = [f for f in nr_fans if f is not None]
        if any(a.intersects(b) and a.intersection(b).area > 0 for a in known_l for b in known_n):
            return True
        if not known_l or not known_n or len(known_l) < len(lte_fans) or len(known_n) < len(nr_fans):
            return None
        return False

    def rel_counts(self, key, rel_act):
        """Relation actions of one eNB -> gNB; relations of cells not audited (other bands) count as KEEP."""
        c = collections.Counter(rel_act.get(key, {}))
        audited = sum(c[k] for k in ("KEEP", "DELETE", "PROTECTED", "REVIEW"))
        c["KEEP"] += max(self.rel_count[key] - audited, 0)
        return c

    def audit(self, sources, cells, ept, tier, s, rel_act, add_to, site_km=None):
        """(every TermPoint of the scope with its action, TermPoint ADD proposals).
        All eNB nodes of the scope are audited, also the ones without source cells: their relations
        are not audited (none deleted), so only a TermPoint without relations can be deleted."""
        scope = s.scope_set() if s.scope == "nodes" else set()
        rows = []
        for (node, gnb), t in sorted(self.tp.items()):
            if scope and node.upper() not in scope:
                continue
            info = self.gnb_info(gnb, ept)
            c = self.rel_counts((node, gnb), rel_act)
            total, deleted = self.rel_count[(node, gnb)], c["DELETE"]
            left = total - deleted
            rel = relations_text(c)
            tr = tier(node, info["node"]) if info and info["node"] else None
            overlap = None
            ov = None                                      # overlap word when it decides
            if left > 0 or c["ADD"]:                       # the TermPoint is still used (or will be)
                action = "KEEP"
            elif tr is None:
                action = "REVIEW"
            elif tr >= s.min_delete_tier:
                action = "DELETE"
            elif tr >= s.zero_overlap_min_tier:
                overlap = self._overlap(node, gnb, cells, ept)
                ov = {None: "unknown", True: "yes", False: "no"}[overlap]
                action = {None: "REVIEW", True: "KEEP", False: "DELETE"}[overlap]
            else:
                action = "KEEP"
            tier_part = tier_text(tr, s, ov)
            reason = f"{tier_part}, {rel}"
            # last rule: a TermPoint without relations towards a site beyond 'Max Site distance' is deleted
            km = site_km(node, info["node"]) if site_km and info and info["node"] else None
            if km is not None and km > s.max_site_km and not (left > 0 or c["ADD"]) and action != "DELETE":
                action = "DELETE"
                reason += f"; site distance {km:g} km > {s.max_site_km:g}"
            ext_mo = f"ENodeBFunction=1,GUtraNetwork=1,ExternalGNodeBFunction={t['ext_gnb_ref']}"
            mo = f"{ext_mo},TermPointToGNB={t['termpoint_id']}"
            ext_cells = self.ext_cells_of.get((node, str(t["ext_gnb_ref"]).strip()), [])
            rows.append({"LTE node": node, "ExternalGNodeBFunction": t["ext_gnb_ref"],
                         "ExternalGUtranCells": "; ".join(ext_cells),
                         "TermPointToGNB": t["termpoint_id"], "gNB ID": gnb,
                         "NR node": info["node"] if info else "", "NR ENM": info["enm"] if info else "not found",
                         "Tier": tr, "Relations": total, "Relations DELETE": deleted, "Relations left": left,
                         "Tier text": tier_part, "GUtranCellRelation": rel,
                         "Fan overlap": {True: "YES", False: "NO", None: ""}[overlap],
                         "ipAddress": t["ip_address"], "administrativeState": t["admin"],
                         "operationalState": t["oper"], "Action": action, "Reason": reason,
                         "TermPoint FDN": fdn(self.inv, node, mo),
                         "ExternalGNodeBFunction FDN": fdn(self.inv, node, ext_mo)})
        adds = []
        for (node, gnb), recs in sorted(add_to.items()):
            info = self.gnb_info(gnb)
            ext = self.ext_ref.get((node, gnb))
            ext_id = ext or self._new_ext_ref(node, gnb, info["plmn"])
            prefix = ext_id.split("-")[0]
            tp_id = ext_id if self.tp_same_as_ext else self.tp_id
            tiers = [r["Tier"] for r in recs if r["Tier"] is not None]
            tmin = min(tiers) if tiers else None
            rel = relations_text(self.rel_counts((node, gnb), rel_act))
            ext_mo = f"ENodeBFunction=1,GUtraNetwork=1,ExternalGNodeBFunction={ext_id}"
            adds.append({"LTE node": node, "ExternalGNodeBFunction": ext_id,
                         "ExternalGNodeBFunction defined": "YES" if ext else "NO (created first)",
                         "TermPointToGNB": tp_id, "MCC": prefix[:3], "MNC": prefix[3:],
                         "gNB ID": gnb, "gNBIdLength": info["gnb_len"], "NR node": info["node"],
                         "NR ENM": info["enm"], "X2 IP": info["x2_ip"], "X2 IP status": info["x2_status"],
                         "Tier": min(tiers) if tiers else None, "ADD relations": len(recs),
                         "Action": "ADD",
                         "Tier text": tier_text(tmin, s), "GUtranCellRelation": rel,
                         "Reason": f"{tier_text(tmin, s)}, {rel}",
                         "ExternalGNodeBFunction FDN": fdn(self.inv, node, ext_mo),
                         "TermPoint FDN": fdn(self.inv, node, f"{ext_mo},TermPointToGNB={tp_id}")})
        return pd.DataFrame(rows), pd.DataFrame(adds)
