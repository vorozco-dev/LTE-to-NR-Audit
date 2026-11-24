"""
Relation audit of one site for the Graphical Audit map (GUI tab 3).

It runs the same audit as 2. Run Audit (lne_part2.audit, Audit Rules of the GUI) on the LTE nodes of
the site only, so the map shows exactly what the reports and MOPs would do. Nothing is written.
Inventory, EPT and propagation are read once per workdir and ENM and kept in memory.
"""

import pandas as pd

import lne_db
import lne_inventory
import lne_part2
from lne_geo_inputs import Ept, Propagation
from lne_ids import site_code


class SiteAudit:
    def __init__(self, db, workdir, log=print):
        self.db, self.workdir, self.log = db, workdir, log
        self.node_enm = lne_db.node_enms(db, workdir)
        self.enms = lne_db.ready_enms(db, workdir)
        self._inv = {}
        self._ept = self._prop = None

    def nodes_of(self, site):
        """(enm, LTE nodes of the site) — the ENM with most nodes of the site; (None, []) when absent."""
        by_enm = {}
        for node, enm in self.node_enm.items():
            if site_code(node) == site and enm in self.enms:
                by_enm.setdefault(enm, []).append(node)
        if not by_enm:
            return None, []
        enm = max(by_enm, key=lambda e: len(by_enm[e]))
        return enm, sorted(by_enm[enm])

    def _inventory(self, enm, settings):
        if enm not in self._inv:
            quiet = lambda _m: None
            self.log(f"Graphical Audit: loading ENM {enm} (once per workdir) ...")
            tables = lne_db.read_tables(self.db, self.workdir, enm, log=quiet)
            others = [e for e in self.enms if e != enm]
            other_gnbs = pd.concat([lne_inventory.gnb_directory(
                lne_db.read_tables(self.db, self.workdir, e, mos=lne_inventory.GNB_MOS, log=quiet), settings)
                .assign(enm=e) for e in others], ignore_index=True) if others else None
            self._inv[enm] = lne_inventory.build(tables, settings, quiet, other_gnbs)
        return self._inv[enm]

    def _geo(self):
        if self._ept is None:
            quiet = lambda _m: None
            label = f"{self.db} [{self.workdir}]"
            self._ept = Ept(label, quiet, df=lne_db.read_frame(self.db, "ept", self.workdir))
            self._prop = Propagation.from_frames({k: lne_db.read_frame(self.db, f"propagation_{k}", self.workdir)
                                                  for k in Propagation.KEYS.values()}, label, quiet)
        return self._ept, self._prop

    def relations(self, site, settings):
        """(enm, nodes, relations, note): one dict per relation of the site's LTE cells (lne_part2._add_line),
        note explains an empty result."""
        enm, nodes = self.nodes_of(site)
        if enm is None:
            return None, [], [], f"no LTE node of site {site} in the ENMs of workdir {self.workdir}"
        s = settings
        s.workdir, s.enm, s.scope, s.scope_names = self.workdir, enm, "nodes", ",".join(nodes)
        inv = self._inventory(enm, s)
        ept, prop = self._geo()
        res = lne_part2.audit(inv, ept, prop, s, lambda _m: None, want_lines=True)
        if res is None:
            return enm, nodes, [], ("no source cell (no NR cell of the 5G target bands on the node, "
                                    "or no LTE cell of the source bands)")
        return enm, nodes, res[1], ""
