"""
Graphical Audit view (GUI tab 3): one site, its LTE fans, the NR sites around it by tier and the
relations of its audit (lne_siteaudit: Current — Complete / Missing TermPoint / To Be Deleted,
Missing — To Be Added; target sites outside the drawn tiers in black).

Data of one workdir of the database (loaded once, FanData):
  EPT (cells on air, not PLAN; plus the PLAN NR cells of sites whose gNB is in ENM NRCellDU): position, azimuth, beam, band_indicator ("Band 4_AWS" -> band_4_aws)
  propagation_e4g / e5g: max85 radius of each LTE / NR cell (fan length)
Tier = hops in the Delaunay triangulation of the NR sites (same topology as the audit, see
lne_geometry.SiteTopology); the selected site is added to it, so a site without NR also gets tiers.

FanData.build(site, ...) -> FanView: LTE fans of the site, NR sites with tier/distance (and the fans
of their NR cells, colored by tier), Delaunay links, relations, tables.
draw(fig, view, basemap) draws one large map; write_kmz / table() export it.
"""

import math
import zipfile
from xml.sax.saxutils import escape

import numpy as np
import pandas as pd
from pyproj import Geod

import lne_db
from lne_colors import BAND_COLORS, OTHER_COLOR, OUT_OF_TIER_COLOR, RELATION_LINES, TIER_COLORS, TIERS
from lne_colors import kml_color as _kml_color
from lne_geo_inputs import Propagation
from lne_geometry import Projector, SiteTopology, fan
from lne_ids import site_code

GEOD = Geod(ellps="WGS84")
NO_RADIUS_M = 500.0                       # fan length drawn (dashed) when the cell has no max85
NR_FAN_SHARE = 0.08                       # neighbor NR fans drawn at 8% of the map radius (240 m at 3 km)


def norm_band(text):
    """'Band 4_AWS' -> 'band_4_aws' (the naming of conf/freq_band.csv)."""
    t = str(text or "").strip().lower().replace(" ", "_")
    return t if t and t != "nan" else "no band"


def _km(lat1, lon1, lat2, lon2):
    az, _baz, dist = GEOD.inv(lon1, lat1, lon2, lat2)
    return dist / 1000.0, az % 360


class FanData:
    """EPT + propagation of one workdir, ready to build site views quickly."""

    def __init__(self, db, workdir, log=print):
        df = lne_db.read_frame(db, "ept", workdir)
        if df is None or not len(df):
            raise ValueError(f"No EPT rows for workdir {workdir}: run 1. Load Data -> Process")
        for c in ("latitude", "longitude", "azimuth", "beam"):
            df[c] = pd.to_numeric(df[c], errors="coerce")
        df = df[df["latitude"].between(-90, 90) & df["longitude"].between(-180, 180)].copy()
        df["site_u"] = df["site"].fillna("").str.upper()
        # site positions of the audit (lne_geo_inputs.Ept.by_site: every EPT row), for Max Site distance
        allpos = df.groupby("site_u")[["latitude", "longitude"]].median()
        self.audit_pos = {s: (float(r.latitude), float(r.longitude)) for s, r in allpos.iterrows()}
        # on air only (not PLAN), as the audit; but a site whose nodes are in ENM is live even if the EPT
        # still has it as PLAN: its PLAN 4G rows count when its eNB is in ENM (EUtranCellFDD; the audit
        # reads their position) and its PLAN 5G rows when its gNB is (NRCellDU; the audit topology adds it)
        self.enm_sites = {site_code(n) for n in lne_db.node_enms(db, workdir)} - {None, ""}
        enm_nr = {site_code(n) for n in lne_db.nr_nodes(db, workdir)} - {None, ""}
        plan = df["source"].fillna("").str.startswith("PLAN")
        lte_ok = (df["tech"] == "4G") & df["site_u"].isin(self.enm_sites)
        nr_ok = (df["tech"] == "5G") & df["site_u"].isin(enm_nr)
        self.plan_lte_sites = set(df[plan & lte_ok]["site_u"])
        self.plan_nr_sites = set(df[plan & nr_ok]["site_u"])
        df = df[~plan | lte_ok | nr_ok].copy()
        df["band"] = df["band_indicator"].map(norm_band)
        df = df[df["site_u"] != ""]
        self.workdir = workdir
        pos = df.groupby("site_u")[["latitude", "longitude"]].median()
        self.site_pos = {s: (float(r.latitude), float(r.longitude)) for s, r in pos.iterrows()}
        self.lte = df[df["tech"] == "4G"]
        nr = df[df["tech"] == "5G"]
        self.nr_bands = nr.groupby("site_u")["band"].agg(lambda b: set(b)).to_dict()
        self.nr_cells = nr.groupby("site_u").size().to_dict()
        self.nr = {s: g.to_dict("records") for s, g in nr[nr["azimuth"].notna()].groupby("site_u")}
        # att_site list: EPT LTE sites with an LTE node in ENM (EUtranCellFDD of any ENM of the workdir);
        # a site of another vendor has nothing to audit. Without ENM tables: every EPT LTE site.
        ept_sites = set(self.lte["site_u"])
        self.sites = sorted(ept_sites & self.enm_sites if self.enm_sites else ept_sites)
        prop = Propagation.from_frames({k: lne_db.read_frame(db, f"propagation_{k}", workdir)
                                        for k in Propagation.KEYS.values()}, workdir, lambda _m: None)
        self.radius = prop.radius["LTE"]
        self.nr_radius = prop.radius["NR"]
        log(f"Graphical Audit: workdir {workdir}: {len(self.sites):,} LTE sites with nodes in ENM "
            f"(of {len(ept_sites):,} in the EPT; {len(self.plan_lte_sites):,} PLAN in the EPT), "
            f"{len(self.nr_bands):,} NR sites ({len(self.plan_nr_sites):,} PLAN in the EPT but with NR in ENM), "
            f"{len(self.radius):,} LTE / {len(self.nr_radius):,} NR cells with max85")

    @staticmethod
    def _fan(proj, r, radius, default_beam, max85=None):
        """Drawn fan (radius, NO_RADIUS_M when missing) + 'geom': the max85 fan in metres (same as the
        audit, None without max85) used for the overlap counter."""
        beam = r["beam"] if not pd.isna(r["beam"]) and 0 < r["beam"] <= 360 else default_beam
        x, y = proj.xy(r["latitude"], r["longitude"])
        az = float(r["azimuth"]) % 360
        poly = proj.to_wgs84(fan(x, y, az, float(beam), radius or NO_RADIUS_M))
        return {"cell": r["cell_name"], "band": r["band"], "azimuth": az,
                "beam": float(beam), "radius_m": round(radius) if radius else None,
                "lat": r["latitude"], "lon": r["longitude"], "coords": list(poly.exterior.coords),
                "geom": fan(x, y, az, float(beam), max85) if max85 else None, "overlaps": None}

    @staticmethod
    def _count_overlaps(lte_fans, nr_fans):
        """Fan overlap counter: for each LTE fan, how many NR fans overlap it (max85 polygons, area > 0,
        as the audit); for each NR fan, how many LTE fans. None (unknown) when the cell has no max85."""
        for f in lte_fans + nr_fans:
            f["overlaps"] = 0 if f["geom"] is not None else None
        for lf in lte_fans:
            if lf["geom"] is None:
                continue
            for nf in nr_fans:
                g = nf["geom"]
                if g is not None and lf["geom"].intersects(g) and lf["geom"].intersection(g).area > 0:
                    lf["overlaps"] += 1
                    nf["overlaps"] += 1

    def build(self, site, lte_bands, nr_bands, tiers, radius_km, default_beam=65.0,
              nr_max85=False, relations=None, audit=None, lte_max85=True, rel_kinds=None, max_site_km=None):
        """relations: lne_siteaudit relations of the site (None = not drawn); audit: what was audited
        (enm, nodes, note, rules) for the Settings sheet. lte_max85: real max85 length, else short (NR_FAN_SHARE of the map
        radius). rel_kinds: relation kinds kept (keys of RELATION_LINES; None = all). max_site_km: NR
        sites farther than this from the site are not tier sites (None = no limit)."""
        site = site.strip().upper()
        if site not in self.site_pos:
            raise ValueError(f"Site {site} is not in the EPT (on air) of workdir {self.workdir}")
        if self.enm_sites and site not in self.enm_sites:
            raise ValueError(f"Site {site} has no LTE node in the ENM of workdir {self.workdir} "
                             "(another vendor or not loaded): nothing to audit")
        lat0, lon0 = self.site_pos[site]
        proj = Projector([lat0], [lon0])

        # NR sites of the chosen 5G bands + the selected site -> Delaunay tiers
        nr_sites = [s for s, b in self.nr_bands.items() if b & set(nr_bands) and s in self.site_pos]
        xy = {s: proj.xy(*self.site_pos[s]) for s in nr_sites}
        xy[site] = proj.xy(lat0, lon0)
        topo = SiteTopology(xy, log=lambda _m: None)
        tier_of = {s: topo.tier(site, s) for s in xy}
        # Max Site distance (Audit Rules), applied after the tiers as in the audit: a farther site is not
        # one of the tier sites (if the audit has a relation to it, it is drawn as a target out of tiers)
        p0 = self.audit_pos.get(site, (lat0, lon0))

        def site_km(s):
            return _km(*p0, *self.audit_pos.get(s, self.site_pos[s]))[0]

        keep = {s for s, t in tier_of.items() if t is not None and t in tiers and s != site
                and (max_site_km is None or site_km(s) <= max_site_km)}

        sites = []
        for s in sorted(keep, key=lambda s: (tier_of[s], s)):
            lat, lon = self.site_pos[s]
            dist, bearing = _km(lat0, lon0, lat, lon)
            sites.append({"site": s, "tier": tier_of[s], "lat": lat, "lon": lon, "distance_km": round(dist, 3),
                          "bearing": round(bearing, 1), "nr_cells": self.nr_cells.get(s, 0),
                          "nr_bands": ", ".join(sorted(self.nr_bands.get(s, ())))})

        # Delaunay links between the drawn sites (and the selected one)
        index_sites = {}
        for s in keep | {site}:
            index_sites.setdefault(topo.site_of[s], s)
        links = []
        for a, s_a in index_sites.items():
            for b in topo.graph[a]:
                if b > a and b in index_sites:
                    links.append((self.site_pos[s_a], self.site_pos[index_sites[b]]))

        fans = []
        short_m = radius_km * 1000 * NR_FAN_SHARE
        cells = self.lte[(self.lte["site_u"] == site) & self.lte["band"].isin(lte_bands)]
        for r in cells.sort_values(["band", "cell_name"]).to_dict("records"):
            if not pd.isna(r["azimuth"]):
                max85 = self.radius.get(str(r["cell_name"]).upper())
                f = self._fan(proj, r, max85 if lte_max85 else short_m, default_beam, max85)
                f["radius_m"] = round(max85) if max85 else None
                fans.append(f)

        # fans of the NR cells (chosen 5G bands) of every drawn neighbor site, with its tier.
        # nr_max85=False: drawn short (NR_FAN_SHARE of the map radius) as azimuth / beam markers, because
        # with the real max85 (several km) hundreds of fans cover the map. nr_max85=True: real max85
        # length (NO_RADIUS_M when missing). radius_m always keeps the real max85.
        nr_fan_list = []
        for s in sites:
            for r in sorted(self.nr.get(s["site"], ()), key=lambda r: str(r["cell_name"])):
                if r["band"] in nr_bands:
                    max85 = self.nr_radius.get(str(r["cell_name"]).upper())
                    f = self._fan(proj, r, max85 if nr_max85 else short_m, default_beam, max85)
                    f.update(site=s["site"], tier=s["tier"], radius_m=round(max85) if max85 else None)
                    nr_fan_list.append(f)
        self._count_overlaps(fans, nr_fan_list)

        # relations of the audit (LTE cells of the drawn bands); their target sites that are not drawn
        # (tier not selected, beyond the triangulation or not an NR site of the 5G bands) -> black points
        rels = [r for r in relations or () if r["LTE band"] in lte_bands
                and (rel_kinds is None or r["kind"] in rel_kinds)]
        out_sites = []
        for s in sorted({r["NR site"] for r in rels} - keep - {site, ""}):
            r = next(r for r in rels if r["NR site"] == s)
            lat, lon = self.site_pos.get(s, (r["nr_lat"], r["nr_lon"]))
            if lat is None:
                continue
            dist, bearing = _km(lat0, lon0, lat, lon)
            tr = tier_of.get(s)
            out_sites.append({"site": s, "tier": "?" if tr is None else tr, "lat": lat, "lon": lon,
                              "distance_km": round(dist, 3), "bearing": round(bearing, 1)})
        return FanView(site, (lat0, lon0), fans, sites, links, radius_km,
                       {"lte_bands": lte_bands, "nr_bands": nr_bands, "tiers": tiers, "workdir": self.workdir,
                        "max_site_km": max_site_km},
                       nr_fan_list, nr_max85, rels if relations is not None else None, out_sites, audit,
                       lte_max85, list(rel_kinds or RELATION_LINES))


class FanView:
    def __init__(self, site, center, fans, sites, links, radius_km, filters, nr_fans=(), nr_max85=False,
                 relations=None, out_sites=(), audit=None, lte_max85=True, rel_kinds=None):
        self.site, self.center, self.fans, self.sites, self.links = site, center, fans, sites, links
        self.nr_fans = list(nr_fans)
        self.nr_max85 = nr_max85      # neighbor NR fans drawn with the real max85 (else short markers)
        self.lte_max85 = lte_max85    # site LTE fans drawn with the real max85 (else short)
        self.rel_kinds = list(rel_kinds or RELATION_LINES)    # relation kinds drawn / listed
        self.radius_km, self.filters = radius_km, filters
        self.relations = relations    # None: relations not drawn; else lne_siteaudit relation dicts
        self.out_sites = list(out_sites)
        self.audit = audit or {}

    def extent(self):
        lat0, lon0 = self.center
        dlat = self.radius_km / 111.32
        dlon = self.radius_km / (111.32 * max(math.cos(math.radians(lat0)), 0.01))
        return lon0 - dlon, lon0 + dlon, lat0 - dlat, lat0 + dlat

    NODE_COLUMNS = ["Type", "Site", "Tier", "Distance (km)", "Bearing", "LTE cells", "NR cells", "Bands",
                    "Fan overlaps", "Lat", "Lon"]
    CELL_COLUMNS = ["Type", "Site", "Cell", "Tier", "Band", "Azimuth", "Beam", "Max85 radius (m)",
                    "Fan overlaps", "Distance (km)", "Bearing", "Lat", "Lon"]

    @staticmethod
    def _overlap_sum(fans):
        """Sum of the cell counters of a site ('' when none of its cells has a known counter)."""
        known = [f["overlaps"] for f in fans if f["overlaps"] is not None]
        return sum(known) if known else ""

    def nodes_table(self):
        """Results -> Nodes: the selected site, then one row per NR neighbor site (tier, distance, bearing).
        Fan overlaps = sum of the Fan overlaps of its cells (Cells tab)."""
        lat0, lon0 = self.center
        rows = [{"Type": "Selected site", "Site": self.site, "Tier": 0, "Distance (km)": 0.0, "Bearing": "",
                 "LTE cells": len(self.fans), "NR cells": "", "Bands": ", ".join(sorted({f["band"] for f in self.fans})),
                 "Fan overlaps": self._overlap_sum(self.fans), "Lat": round(lat0, 6), "Lon": round(lon0, 6)}]
        by_site = {}
        for f in self.nr_fans:
            by_site.setdefault(f["site"], []).append(f)
        rows += [{"Type": "NR site", "Site": s["site"], "Tier": s["tier"], "Distance (km)": s["distance_km"],
                  "Bearing": s["bearing"], "LTE cells": "", "NR cells": s["nr_cells"], "Bands": s["nr_bands"],
                  "Fan overlaps": self._overlap_sum(by_site.get(s["site"], [])),
                  "Lat": round(s["lat"], 6), "Lon": round(s["lon"], 6)} for s in self.sites]
        rows += [{"Type": "Target out of tiers", "Site": s["site"], "Tier": s["tier"],
                  "Distance (km)": s["distance_km"], "Bearing": s["bearing"], "LTE cells": "", "NR cells": "",
                  "Bands": "", "Fan overlaps": "", "Lat": round(s["lat"], 6), "Lon": round(s["lon"], 6)}
                 for s in self.out_sites]
        return pd.DataFrame(rows, columns=self.NODE_COLUMNS)

    def cells_table(self):
        """Results -> Cells: LTE cells of the selected site, then the NR cells of the neighbor sites."""
        def max85(f):
            return f["radius_m"] if f["radius_m"] else "none"

        def ov(f):
            return f["overlaps"] if f["overlaps"] is not None else "unknown (no max85)"

        rows = [{"Type": "LTE cell", "Site": self.site, "Cell": f["cell"], "Tier": 0, "Band": f["band"],
                 "Azimuth": round(f["azimuth"], 1), "Beam": f["beam"], "Max85 radius (m)": max85(f),
                 "Fan overlaps": ov(f), "Distance (km)": 0.0, "Bearing": "", "Lat": round(f["lat"], 6), "Lon": round(f["lon"], 6)}
                for f in self.fans]
        site_of = {s["site"]: s for s in self.sites}
        rows += [{"Type": "NR cell", "Site": f["site"], "Cell": f["cell"], "Tier": f["tier"], "Band": f["band"],
                  "Azimuth": round(f["azimuth"], 1), "Beam": f["beam"], "Max85 radius (m)": max85(f),
                  "Fan overlaps": ov(f), "Distance (km)": site_of[f["site"]]["distance_km"], "Bearing": site_of[f["site"]]["bearing"],
                  "Lat": round(f["lat"], 6), "Lon": round(f["lon"], 6)} for f in self.nr_fans]
        return pd.DataFrame(rows, columns=self.CELL_COLUMNS)

    RELATION_COLUMNS = ["Relation", "LTE node", "LTE cell", "LTE band", "NR node", "NR cell", "NR site", "Tier",
                        "Site distance km", "Action", "TermPointToGNB", "Overlap m2", "Reason"]

    def relations_table(self):
        """Results -> Relations: every relation of the audit of the site (drawn LTE bands), by kind."""
        order = list(RELATION_LINES)
        rows = [dict(r, Relation=RELATION_LINES[r["kind"]][0])
                for r in sorted(self.relations or (), key=lambda r: (order.index(r["kind"]), r["LTE cell"],
                                                                     r["Tier"] if r["Tier"] is not None else 99))]
        return pd.DataFrame(rows, columns=self.RELATION_COLUMNS)

    def settings_rows(self):
        f = self.filters
        a = self.audit
        audit_rows = [{"Setting": "relations (audit)", "Value": "not drawn" if self.relations is None else
                       f"ENM {a.get('enm') or '-'}, nodes {', '.join(a.get('nodes', [])) or '-'}"
                       + (f" · {a['note']}" if a.get("note") else "")}]
        if self.relations is not None:
            audit_rows += [{"Setting": "relations drawn",
                            "Value": ", ".join(RELATION_LINES[k][0] for k in self.rel_kinds)}]
        audit_rows += [{"Setting": f"audit rule: {k}", "Value": v} for k, v in a.get("rules", [])]
        return pd.DataFrame(audit_rows + [
            {"Setting": "workdir", "Value": f["workdir"]}, {"Setting": "site", "Value": self.site},
            {"Setting": "LTE bands", "Value": ", ".join(f["lte_bands"])},
            {"Setting": "5G bands", "Value": ", ".join(f["nr_bands"])},
            {"Setting": "tiers", "Value": ", ".join(map(str, f["tiers"]))},
            {"Setting": "max site distance (km)",
             "Value": "no limit" if f.get("max_site_km") is None else f"{f['max_site_km']:g}"},
            {"Setting": "map radius (km)", "Value": self.radius_km},
            {"Setting": "site LTE fan length",
             "Value": "LTE max85" if self.lte_max85 else f"{NR_FAN_SHARE:.0%} of the map radius"},
            {"Setting": "neighbor NR fans", "Value": len(self.nr_fans)},
            {"Setting": "fan overlaps",
             "Value": "LTE cell: NR fans (drawn tiers / 5G bands) overlapping it; NR cell: LTE fans of the site "
                      "overlapping it; node: sum of its cells. Max85 fans, area > 0 (as the audit)"},
            {"Setting": "neighbor NR fan length",
             "Value": "NR max85" if self.nr_max85 else f"{NR_FAN_SHARE:.0%} of the map radius"},
            {"Setting": "tier", "Value": "hops in the Delaunay triangulation of the NR sites (EPT on air)"}])


# --- drawing -----------------------------------------------------------------------------------

def draw(fig, view, basemap=None):
    from matplotlib.lines import Line2D
    from matplotlib.patches import Patch, Polygon as MplPolygon

    fig.clear()
    ax = fig.add_subplot(111)
    lon_min, lon_max, lat_min, lat_max = view.extent()
    if basemap is not None:
        img, (a, b, c, d) = basemap
        ax.imshow(img, extent=(a, b, c, d), origin="upper", zorder=0, alpha=0.85)
    for (la1, lo1), (la2, lo2) in view.links:
        ax.plot([lo1, lo2], [la1, la2], color="#808080", lw=0.7, alpha=0.7, zorder=1)
    for f in view.nr_fans:            # neighbor NR cells, colored by tier, under the LTE fans
        color = TIER_COLORS.get(f["tier"], "#7f7f7f")
        if view.nr_max85:             # long, overlapping fans: light fill, dashed when no max85
            ax.add_patch(MplPolygon(f["coords"], closed=True, facecolor=color, edgecolor=color, alpha=0.18,
                                    lw=0.8, ls="-" if f["radius_m"] else "--", zorder=2))
        else:
            ax.add_patch(MplPolygon(f["coords"], closed=True, facecolor=color, edgecolor=color, alpha=0.55,
                                    lw=0.6, zorder=2))
    for f in view.fans:
        color = BAND_COLORS.get(f["band"], OTHER_COLOR)
        ax.add_patch(MplPolygon(f["coords"], closed=True, facecolor=color, edgecolor=color,
                                alpha=0.35 if view.lte_max85 else 0.6, lw=1.2,
                                ls="-" if f["radius_m"] or not view.lte_max85 else "--", zorder=3))
    rel_count = {}
    for kind in view.rel_kinds:                                    # relations over the fans, under the sites
        _name, color, width = RELATION_LINES[kind]
        rs = [r for r in view.relations or () if r["kind"] == kind and r["lte_lat"] is not None
              and r["nr_lat"] is not None]
        rel_count[kind] = sum(1 for r in view.relations or () if r["kind"] == kind)
        for r in rs:
            ax.plot([r["lte_lon"], r["nr_lon"]], [r["lte_lat"], r["nr_lat"]], color=color, lw=width * 0.6,
                    alpha=0.75, zorder=3.5, solid_capstyle="round")
    for s in view.out_sites:
        ax.scatter([s["lon"]], [s["lat"]], s=28, color=OUT_OF_TIER_COLOR, edgecolor="white", linewidth=0.6, zorder=4)
        if lon_min <= s["lon"] <= lon_max and lat_min <= s["lat"] <= lat_max:
            ax.annotate(s["site"], (s["lon"], s["lat"]), xytext=(3, 3), textcoords="offset points", fontsize=7,
                        color=OUT_OF_TIER_COLOR, zorder=5)
    by_tier = {}
    for s in view.sites:
        by_tier.setdefault(s["tier"], []).append(s)
    for t, ss in sorted(by_tier.items()):
        ax.scatter([s["lon"] for s in ss], [s["lat"] for s in ss], s=28, color=TIER_COLORS.get(t, "#7f7f7f"),
                   edgecolor="white", linewidth=0.6, zorder=4)
        for s in ss:
            if lon_min <= s["lon"] <= lon_max and lat_min <= s["lat"] <= lat_max:
                ax.annotate(s["site"], (s["lon"], s["lat"]), xytext=(3, 3), textcoords="offset points",
                            fontsize=7, color=TIER_COLORS.get(t, "#333333"), zorder=5)
    lat0, lon0 = view.center
    ax.scatter([lon0], [lat0], s=90, marker="*", color="red", edgecolor="black", linewidth=0.6, zorder=6)
    ax.annotate(view.site, (lon0, lat0), xytext=(5, 5), textcoords="offset points", fontsize=10,
                fontweight="bold", color="red", zorder=6)
    ax.set_xlim(lon_min, lon_max)
    ax.set_ylim(lat_min, lat_max)
    ax.set_aspect(1 / max(math.cos(math.radians(lat0)), 0.01))
    ax.set_xlabel("Longitude", fontsize=5)          # axis titles and numbers at 50% of the default (10)
    ax.set_ylabel("Latitude", fontsize=5)
    ax.tick_params(axis="both", labelsize=5, length=2)
    ax.set_title(f"{view.site} · {len(view.fans)} LTE fans · {len(view.sites)} NR sites "
                 f"(tiers {', '.join(map(str, view.filters['tiers']))})", fontsize=11)
    handles = [Patch(facecolor=BAND_COLORS.get(b, OTHER_COLOR), alpha=0.5, label=b)
               for b in sorted({f["band"] for f in view.fans})]
    handles += [Line2D([], [], marker="o", ls="", color=TIER_COLORS.get(t, "#7f7f7f"),
                       label=f"Tier {t} ({len(by_tier[t])})") for t in sorted(by_tier)]
    if view.nr_fans:
        handles += [Patch(facecolor="#bbbbbb", edgecolor="#777777", alpha=0.5,
                          label=f"NR fans by tier ({len(view.nr_fans)}, "
                                f"{'max85' if view.nr_max85 else 'short'})")]
    if view.out_sites:
        handles += [Line2D([], [], marker="o", ls="", color=OUT_OF_TIER_COLOR,
                           label=f"Target out of tiers ({len(view.out_sites)})")]
    handles += [Line2D([], [], color="#808080", lw=0.7, label="Delaunay link")]
    if view.relations is not None:
        handles += [Line2D([], [], color=RELATION_LINES[k][1], lw=RELATION_LINES[k][2],
                           label=f"{RELATION_LINES[k][0]} ({rel_count[k]})") for k in view.rel_kinds]
    if handles:                 # outside the map, so it never hides fans or sites
        ax.legend(handles=handles, loc="upper left", bbox_to_anchor=(1.01, 1.0), fontsize=8, framealpha=0.9,
                  borderaxespad=0)
    if basemap is not None:
        ax.text(0.995, 0.005, "© OpenStreetMap contributors", transform=ax.transAxes, fontsize=7,
                ha="right", va="bottom", color="#333333")
    fig.tight_layout()


# --- KMZ ---------------------------------------------------------------------------------------

def _data(d):
    return "<ExtendedData>" + "".join(
        f'<Data name="{escape(str(k))}"><value>{escape(str(v))}</value></Data>' for k, v in d.items()) + \
        "</ExtendedData>"


def kml_text(view):
    styles = []
    for b in sorted({f["band"] for f in view.fans}):
        c = BAND_COLORS.get(b, OTHER_COLOR)
        styles.append(f'<Style id="fan_{b}"><LineStyle><color>{_kml_color(c)}</color><width>2</width></LineStyle>'
                      f'<PolyStyle><color>{_kml_color(c, "66")}</color></PolyStyle></Style>')
    icon = "http://maps.google.com/mapfiles/kml/shapes/placemark_circle.png"
    for t in TIERS:
        styles.append(f'<Style id="tier_{t}"><IconStyle><color>{_kml_color(TIER_COLORS[t])}</color><scale>0.9</scale>'
                      f'<Icon><href>{icon}</href></Icon></IconStyle><LabelStyle><scale>0.7</scale></LabelStyle></Style>')
    styles.append(f'<Style id="site"><IconStyle><color>ff0000ff</color><scale>1.3</scale><Icon><href>'
                  'http://maps.google.com/mapfiles/kml/paddle/red-stars.png</href></Icon></IconStyle></Style>')
    for t in TIERS:
        c = TIER_COLORS[t]
        styles.append(f'<Style id="nrfan_{t}"><LineStyle><color>{_kml_color(c)}</color><width>1</width></LineStyle>'
                      f'<PolyStyle><color>{_kml_color(c, "40")}</color></PolyStyle></Style>')
    styles.append('<Style id="link"><LineStyle><color>b3808080</color><width>1</width></LineStyle></Style>')
    styles.append(f'<Style id="out_of_tier"><IconStyle><color>{_kml_color(OUT_OF_TIER_COLOR)}</color><scale>0.9</scale>'
                  f'<Icon><href>{icon}</href></Icon></IconStyle><LabelStyle><scale>0.7</scale></LabelStyle></Style>')
    for kind, (_name, color, width) in RELATION_LINES.items():
        styles.append(f'<Style id="rel_{kind}"><LineStyle><color>{_kml_color(color)}</color>'
                      f'<width>{width}</width></LineStyle><IconStyle><color>{_kml_color(color)}</color>'
                      f'<scale>0.6</scale><Icon><href>{icon}</href></Icon></IconStyle>'
                      f'<LabelStyle><scale>0</scale></LabelStyle></Style>')

    def rel_geometry(r):
        """Line LTE cell -> NR cell; a point when both are at the same position (tier 0): Google
        Earth rejects a LineString without two distinct coordinates."""
        a = f'{r["lte_lon"]:.6f},{r["lte_lat"]:.6f},0'
        b = f'{r["nr_lon"]:.6f},{r["nr_lat"]:.6f},0'
        if a == b:
            return f'<Point><coordinates>{a}</coordinates></Point>'
        return f'<LineString><tessellate>1</tessellate><coordinates>{a} {b}</coordinates></LineString>'

    def coords(pts):
        return " ".join(f"{lon:.6f},{lat:.6f},0" for lon, lat in pts)

    fans_by_band = {}
    for f in view.fans:
        fans_by_band.setdefault(f["band"], []).append(
            f'<Placemark><name>{escape(f["cell"])}</name><styleUrl>#fan_{f["band"]}</styleUrl>'
            + _data({"cell": f["cell"], "band": f["band"], "azimuth": f["azimuth"], "beam": f["beam"],
                     "max85_radius_m": f["radius_m"] or "none"})
            + f'<Polygon><outerBoundaryIs><LinearRing><coordinates>{coords(f["coords"])}</coordinates>'
              '</LinearRing></outerBoundaryIs></Polygon></Placemark>')
    sites_by_tier = {}
    for s in view.sites:
        sites_by_tier.setdefault(s["tier"], []).append(
            f'<Placemark><name>{escape(s["site"])}</name><styleUrl>#tier_{s["tier"]}</styleUrl>'
            + _data({k: s[k] for k in ("site", "tier", "distance_km", "bearing", "nr_cells", "nr_bands")})
            + f'<Point><coordinates>{s["lon"]:.6f},{s["lat"]:.6f},0</coordinates></Point></Placemark>')
    nr_fans_by_tier = {}
    for f in view.nr_fans:
        nr_fans_by_tier.setdefault(f["tier"], {}).setdefault(f["site"], []).append(
            f'<Placemark><name>{escape(f["cell"])}</name><styleUrl>#nrfan_{f["tier"]}</styleUrl>'
            + _data({"cell": f["cell"], "site": f["site"], "tier": f["tier"], "band": f["band"],
                     "azimuth": f["azimuth"], "beam": f["beam"], "max85_radius_m": f["radius_m"] or "none"})
            + f'<Polygon><outerBoundaryIs><LinearRing><coordinates>{coords(f["coords"])}</coordinates>'
              '</LinearRing></outerBoundaryIs></Polygon></Placemark>')
    links = "".join(f'<Placemark><styleUrl>#link</styleUrl><LineString><coordinates>'
                    f'{lo1:.6f},{la1:.6f},0 {lo2:.6f},{la2:.6f},0</coordinates></LineString></Placemark>'
                    for (la1, lo1), (la2, lo2) in view.links)
    lat0, lon0 = view.center
    circle = [GEOD.fwd(lon0, lat0, a, view.radius_km * 1000)[:2] for a in range(0, 361, 5)]
    parts = [f'<?xml version="1.0" encoding="UTF-8"?><kml xmlns="http://www.opengis.net/kml/2.2"><Document>'
             f'<name>Graphical Audit {escape(view.site)}</name>', *styles,
             f'<Placemark><name>{escape(view.site)}</name><styleUrl>#site</styleUrl>'
             f'<Point><coordinates>{lon0:.6f},{lat0:.6f},0</coordinates></Point></Placemark>',
             f'<Folder><name>Site LTE fans {escape(view.site)}</name>']
    for b, pms in sorted(fans_by_band.items()):
        parts.append(f"<Folder><name>{escape(b)}</name>{''.join(pms)}</Folder>")
    parts.append("</Folder><Folder><name>NR sites by tier</name>")
    for t, pms in sorted(sites_by_tier.items()):
        parts.append(f"<Folder><name>Tier {t} ({len(pms)})</name>{''.join(pms)}</Folder>")
    if nr_fans_by_tier:
        parts.append("</Folder><Folder><name>NR fans by tier</name>")
        for t, by_site in sorted(nr_fans_by_tier.items()):       # Tier -> site -> its NR fans
            n = sum(len(pms) for pms in by_site.values())
            parts.append(f"<Folder><name>Tier {t} ({len(by_site)} sites, {n} fans)</name>")
            parts += [f"<Folder><name>{escape(s)} ({len(pms)})</name>{''.join(pms)}</Folder>"
                      for s, pms in sorted(by_site.items())]
            parts.append("</Folder>")
    if view.out_sites:
        parts.append(f"</Folder><Folder><name>Target sites out of tiers ({len(view.out_sites)})</name>")
        parts += [f'<Placemark><name>{escape(s["site"])}</name><styleUrl>#out_of_tier</styleUrl>'
                  + _data({k: s[k] for k in ("site", "tier", "distance_km", "bearing")})
                  + f'<Point><coordinates>{s["lon"]:.6f},{s["lat"]:.6f},0</coordinates></Point></Placemark>'
                  for s in view.out_sites]
    if view.relations is not None:
        parts.append("</Folder><Folder><name>Relations (audit)</name>")
        for kind in view.rel_kinds:
            name = RELATION_LINES[kind][0]
            rs = [r for r in view.relations if r["kind"] == kind]
            parts.append(f"<Folder><name>{escape(name)} ({len(rs)})</name>")
            parts += [f'<Placemark><name>{escape(r["LTE cell"])} -&gt; {escape(r["NR cell"])}</name>'
                      f'<styleUrl>#rel_{kind}</styleUrl>'
                      + _data({k: "" if r[k] is None else r[k] for k in
                               ("LTE cell", "NR cell", "NR site", "Tier", "Site distance km", "Action",
                                "TermPointToGNB", "Overlap m2", "Reason")})
                      + rel_geometry(r) + '</Placemark>'
                      for r in rs if r["lte_lat"] is not None and r["nr_lat"] is not None]
            parts.append("</Folder>")
    parts.append(f"</Folder><Folder><name>Delaunay links</name><visibility>0</visibility>{links}</Folder>")
    parts.append(f'<Placemark><name>Map radius {view.radius_km:g} km</name><Style><LineStyle><color>ff00ffff'
                 f'</color><width>1</width></LineStyle></Style><LineString><coordinates>{coords(circle)}'
                 f'</coordinates></LineString></Placemark>')
    parts.append("</Document></kml>")
    return "".join(parts)


def write_kmz(path, view):
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as z:
        z.writestr("doc.kml", kml_text(view))
    return path
