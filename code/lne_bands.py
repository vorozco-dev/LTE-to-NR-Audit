"""
Band of a cell from its downlink ARFCN, using <ROOT_DIRECTORY>/conf/freq_band.csv
(code/.env FREQ_BAND to change it):

    tech,band_indicator,band_width,ul_arfcn,dl_arfcn,band,type
    4g,band_4_aws,20,20250,2250,B4,J

LTE: EUtranCellFDD.earfcndl -> band_indicator of the 4g rows (e.g. 2250 -> band_4_aws).
NR:  NRCellDU.ssbFrequency -> band_indicator of the 5g rows (e.g. 532000 -> band_7_2600); an
     ssbFrequency not in the file falls back to NRCellDU.bandList ([7] -> B7 -> band_7_2600).
Audit Neighbor Relation LTE to 5G selects its LTE source cells (settings.source_bands) and NR target cells
(settings.target_bands) by band_indicator.
"""

import re
from functools import lru_cache
from pathlib import Path

import pandas as pd

from lne_config import FREQ_BAND

FREQ_BAND_CSV = Path(FREQ_BAND)


@lru_cache(maxsize=None)
def _table(path=str(FREQ_BAND_CSV)):
    df = pd.read_csv(path, dtype=str, encoding="utf-8-sig").fillna("")
    for c in df.columns:
        df[c] = df[c].str.strip()
    return df


def arfcn_map(tech, path=str(FREQ_BAND_CSV)):
    """{dl_arfcn: band_indicator} of one technology (3g / 4g / 5g)."""
    df = _table(path)
    df = df[df["tech"].str.lower() == tech.lower()]
    return dict(zip(df["dl_arfcn"], df["band_indicator"]))


def band_indicators(tech, path=str(FREQ_BAND_CSV)):
    """band_indicator values of one technology, in file order."""
    df = _table(path)
    return list(dict.fromkeys(df[df["tech"].str.lower() == tech.lower()]["band_indicator"]))


def band_by_number(tech, path=str(FREQ_BAND_CSV)):
    """{'7': 'band_7_2600'} from the `band` column (B7) when the number has a single band_indicator."""
    df = _table(path)
    df = df[df["tech"].str.lower() == tech.lower()]
    out = {}
    for b, g in df.groupby(df["band"].str.extract(r"^B(\d+)$", expand=False)):
        ind = list(dict.fromkeys(g["band_indicator"]))
        if len(ind) == 1:
            out[b] = ind[0]
    return out


def nr_band(ssb_frequency, band_list, mapping, by_number):
    """band_indicator of an NR cell: ssbFrequency in the 5g dl_arfcn of the file, else the bandList of
    NRCellDU ([7] -> B7 -> band_7_2600), else 'unknown (ssbFrequency X)'."""
    key = str(ssb_frequency).strip()
    if key.endswith(".0"):
        key = key[:-2]
    if mapping.get(key):
        return mapping[key]
    nums = [n for n in re.findall(r"\d+", str(band_list or ""))]
    if len(nums) == 1 and nums[0] in by_number:
        return by_number[nums[0]]
    return f"unknown (ssbFrequency {key or '-'})"


def lte_band(earfcn_dl, mapping):
    """band_indicator of an LTE cell, or 'unknown (earfcndl X)' when the ARFCN is not in the file."""
    key = str(earfcn_dl).strip()
    if key.endswith(".0"):
        key = key[:-2]
    return mapping.get(key) or f"unknown (earfcndl {key or '-'})"
