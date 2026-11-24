"""Colors of the Graphical Audit map (GUI tab 3) and its KMZ."""

TIERS = tuple(range(7))                   # tiers drawn by color (the audit deletes from 6)
TIER_COLORS = {0: "#000000", 1: "#2ca02c", 2: "#1f77b4", 3: "#ff7f0e", 4: "#9467bd", 5: "#8c564b",
               6: "#7f7f7f"}
OUT_OF_TIER_COLOR = "#000000"             # target sites beyond the last tier (or tier unknown)
BAND_COLORS = {"band_2_pcs": "#17becf", "band_4_aws": "#e41a1c", "band_5_850": "#ff7f00",
               "band_7_2600": "#984ea3", "band_26_800": "#4daf4a"}
OTHER_COLOR = "#555555"

# relation lines of the Graphical Audit (map and KMZ): key -> (name, color, width)
RELATION_LINES = {
    "complete": ("Current — Complete", "#000000", 1.5),
    "missing_tp": ("Current — Missing TermPoint", "#0000ff", 2),
    "delete": ("Current — To Be Deleted", "#ff0000", 2),
    "add": ("Missing — To Be Added", "#800080", 2),
}


def kml_color(hex_rgb, alpha="ff"):
    """'#rrggbb' -> KML 'aabbggrr'."""
    h = hex_rgb.lstrip("#")
    return f"{alpha}{h[4:6]}{h[2:4]}{h[0:2]}"
