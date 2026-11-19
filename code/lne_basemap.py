"""
OpenStreetMap background for the Neighbor Fan map (same approach as the Propagation Vector tool).

Tiles come from tile.openstreetmap.org only when there is internet access and are cached in
<ROOT>/db/tiles, so an area already seen is drawn again without downloading. When the tiles
cannot be downloaded (no connection, proxy), fetch_basemap returns None and the map keeps a
white background. OSM tiles are Web Mercator; the mosaic is resampled to a regular lon/lat grid
so it lines up with the longitude / latitude axes.
"""

import io
import math
import os
import time
import urllib.request

import numpy as np
from PIL import Image

from lne_config import ROOT

TILE_URL = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"
USER_AGENT = "OTLKM-LteNrAudit/2.1 (desktop application)"
ATTRIBUTION = "© OpenStreetMap contributors"
CACHE_DIR = os.path.join(str(ROOT), "db", "tiles")
TILE_SIZE = 256
TIMEOUT_S = 3
MAX_TILES = 30          # tiles per map (light download, as the OSM usage policy asks)
OFFLINE_RETRY_S = 60    # after a failed download, do not try again for this long

_offline_since = None


def _tile_xy(lon, lat, z):
    n = 2 ** z
    lat = max(min(lat, 85.0511), -85.0511)
    return (lon + 180.0) / 360.0 * n, (1.0 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2.0 * n


def _get_tile(z, x, y):
    path = os.path.join(CACHE_DIR, str(z), str(x), f"{y}.png")
    if os.path.exists(path):
        return Image.open(path).convert("RGB")
    req = urllib.request.Request(TILE_URL.format(z=z, x=x, y=y), headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
        data = resp.read()
    img = Image.open(io.BytesIO(data)).convert("RGB")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)
    return img


def fetch_basemap(lon_min, lon_max, lat_min, lat_max):
    """(RGB array, (lon_min, lon_max, lat_min, lat_max)) covering the box, or None."""
    global _offline_since
    if _offline_since and time.time() - _offline_since < OFFLINE_RETRY_S:
        return None
    for z in range(18, 1, -1):              # highest zoom whose tile count fits MAX_TILES
        x0, y0 = _tile_xy(lon_min, lat_max, z)
        x1, y1 = _tile_xy(lon_max, lat_min, z)
        tx0, tx1, ty0, ty1 = int(x0), int(x1), int(y0), int(y1)
        if (tx1 - tx0 + 1) * (ty1 - ty0 + 1) <= MAX_TILES:
            break
    try:
        mosaic = Image.new("RGB", ((tx1 - tx0 + 1) * TILE_SIZE, (ty1 - ty0 + 1) * TILE_SIZE), "white")
        for tx in range(tx0, tx1 + 1):
            for ty in range(ty0, ty1 + 1):
                mosaic.paste(_get_tile(z, tx, ty), ((tx - tx0) * TILE_SIZE, (ty - ty0) * TILE_SIZE))
    except Exception:
        _offline_since = time.time()
        return None
    _offline_since = None
    src = np.asarray(mosaic)
    c0, c1 = (x0 - tx0) * TILE_SIZE, (x1 - tx0) * TILE_SIZE
    cols = np.clip(np.arange(int(c0), int(math.ceil(c1))), 0, src.shape[1] - 1)
    height = max(int(round((y1 - y0) * TILE_SIZE)), 1)
    lats = np.linspace(lat_max, lat_min, height)
    rows = np.array([(_tile_xy(lon_min, la, z)[1] - ty0) * TILE_SIZE for la in lats])
    rows = np.clip(rows.astype(int), 0, src.shape[0] - 1)
    return src[rows][:, cols], (lon_min, lon_max, lat_min, lat_max)
