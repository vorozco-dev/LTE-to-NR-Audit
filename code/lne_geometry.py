"""
Fans and site topology.

Fans are built in a local azimuthal-equidistant projection (metres) centred on the
data, so areas, overlaps and distances are plain planar operations. For an ENM area
(~100 km) the AEQD distortion is far below the propagation uncertainty.

Tier = number of hops between two sites in the Delaunay triangulation of the NR
sites (sites = nodes with NR target cells and a known position). Nodes at the same
position (6 decimals) are one site, i.e. tier 0 = co-located.
"""

import collections
import math

import numpy as np
from pyproj import Transformer
from scipy.spatial import Delaunay
from shapely.geometry import Point, Polygon
from shapely.ops import transform as shp_transform


class Projector:
    def __init__(self, lats, lons):
        lat0, lon0 = float(np.median(lats)), float(np.median(lons))
        crs = f"+proj=aeqd +lat_0={lat0} +lon_0={lon0} +datum=WGS84 +units=m +no_defs"
        self.fwd = Transformer.from_crs("EPSG:4326", crs, always_xy=True)
        self.inv = Transformer.from_crs(crs, "EPSG:4326", always_xy=True)

    def xy(self, lat, lon):
        return self.fwd.transform(lon, lat)

    def to_wgs84(self, geom):
        return shp_transform(lambda x, y, z=None: self.inv.transform(x, y), geom)


def fan(x, y, azimuth, beam, radius, step=2.0):
    """Sector polygon (planar metres). beam >= 360 -> circle."""
    if beam >= 360:
        return Point(x, y).buffer(radius, quad_segs=max(8, int(90 / step)))
    n = max(2, int(math.ceil(beam / step)))
    start = azimuth - beam / 2
    pts = [(x, y)]
    for i in range(n + 1):
        a = math.radians(start + beam * i / n)
        pts.append((x + radius * math.sin(a), y + radius * math.cos(a)))
    pts.append((x, y))
    return Polygon(pts)


class SiteTopology:
    """Delaunay graph over site positions; tiers by breadth-first search (cached per origin)."""

    def __init__(self, node_xy, log=print):
        # node_xy: node -> (x, y). Same rounded position -> same site.
        self.site_of = {}
        pos = {}
        for node, (x, y) in node_xy.items():
            key = (round(x, 1), round(y, 1))
            self.site_of[node] = pos.setdefault(key, len(pos))
        self.graph = collections.defaultdict(set)
        pts = np.array(sorted(pos, key=pos.get), dtype=float)
        if len(pts) >= 3:
            try:
                tri = Delaunay(pts)
                for s in tri.simplices:
                    for i in range(3):
                        a, b = int(s[i]), int(s[(i + 1) % 3])
                        self.graph[a].add(b)
                        self.graph[b].add(a)
            except Exception:       # collinear points: chain in x order
                order = np.lexsort((pts[:, 1], pts[:, 0]))
                for a, b in zip(order, order[1:]):
                    self.graph[int(a)].add(int(b))
                    self.graph[int(b)].add(int(a))
        elif len(pts) == 2:
            self.graph[0].add(1)
            self.graph[1].add(0)
        self._cache = {}
        log(f"Topology: {len(pts):,} NR sites, {sum(len(v) for v in self.graph.values()) // 2:,} Delaunay edges")

    def tier(self, node_a, node_b):
        a, b = self.site_of.get(node_a), self.site_of.get(node_b)
        if a is None or b is None:
            return None
        if a not in self._cache:
            dist = {a: 0}
            q = collections.deque([a])
            while q:
                u = q.popleft()
                for v in self.graph[u]:
                    if v not in dist:
                        dist[v] = dist[u] + 1
                        q.append(v)
            self._cache[a] = dist
        return self._cache[a].get(b)
