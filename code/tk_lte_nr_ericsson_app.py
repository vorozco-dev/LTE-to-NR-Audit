"""
OTLKM - LTE to NR Audit (Ericsson) - Desktop application (Tkinter).

Tabs:
  1. Load Data    Search / Load files: check that the input folder has every file (EPT.xlsx,
                  propagation e4g/e5g, <table>_<enm>.txt); Process: EPT, propagation and every ENM export
                  parsed, checked and loaded into the database under the workdir name
  2. Run Audit    X2 TermPoint and Neighbor Relation audits (LTE to 5G) of a loaded workdir
  3. Neighbor Fan one site: its LTE fans and the NR sites around it by tier (map, Excel, KMZ)
  4. DB Status    tables loaded per workdir; export to CSV, delete a workdir
  Instructions    how to use the application (last tab)
Long tasks run in a worker thread; the log and status bar show progress.
"""

import os
import queue
import threading
import time
import tkinter as tk
import tkinter.font as tkfont
import traceback
import re
from datetime import date, datetime
from pathlib import Path
from tkinter import filedialog, messagebox, scrolledtext, ttk

import pandas as pd
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk
from matplotlib.figure import Figure

import lne_basemap
import lne_db
import lne_fanview
import lne_load
import lne_report
from lne_config import DB_PATH, INPUT_DIR, LOG_DIR, LTE_BANDS, NR_BANDS, OUTPUT_ROOT, ROOT, Settings
from lne_run import VERSION, run

APP_VERSION = f"v{VERSION}"
APP_TITLE = f"OTLKM - LTE to NR Audit (Ericsson) - {APP_VERSION}"
APP_SUBTITLE = "ENM | Audit X2 TermPoint LTE to 5G | Audit Neighbor Relation LTE to 5G (fans + tiers)"
CODE_DIR = Path(__file__).resolve().parent

# Evaluation license: the application runs while today's date is before EXPIRY_DATE
EXPIRY_DATE = date(2027, 5, 19)
EXPIRY_LABEL = f"Evaluation - Expires {EXPIRY_DATE.day} {EXPIRY_DATE:%B %Y}"
EXPIRED_MESSAGE = "Por favor contactar a Soporte OTLKM"

UI_FONT = "Segoe UI"
C_BG, C_WHITE, C_BORDER = "#f2f4f7", "#ffffff", "#d3d9e1"
C_TEXT, C_MUTED = "#1f2328", "#6b7280"
C_ACCENT, C_ACCENT_DARK, C_ACCENT_TEXT = "#0a8fd6", "#0872b0", "#1565c0"
C_HOVER, C_STATUS = "#e6f2fb", "#1f1f1f"
C_OK, C_WARN, C_ERR = "#e7f6ec", "#fff4d6", "#fde2e1"

INSTRUCTIONS = [
    ("h1", "LTE to NR Audit (Ericsson) — User Guide\n"),
    ("p", "On-demand audit: the tool reads one extraction of the Ericsson ENM exports (cmedit), the EPT and the "
          "propagation tables (Max85) and produces two audits. Nothing is executed in the network: proposals are "
          "written to Excel and to text files with cmedit commands to review.\n"),
    ("h2", "1. Load Data\n"),
    ("p", "Workdir: every output goes to output\\<workdir>. Type only the workdir name before Process; it comes "
          "pre-filled with today's date (YYYY-MM-DD). Process and every run of 2. Run Audit write there.\n"),
    ("p", "Put every input in one folder and press Search to select it (or type it and press Load files). "
          "The panel lists the files and checks that the required ones are present:\n"),
    ("li2", "EPT.xlsx\n"
            "propagation tables e4g (LTE) and e5g (NR): propagation_tables_*.zip or the csv files\n"
            "<table>_<enm>.txt for tul, ptl, tlt     e.g. TermPointToGNB_tul.txt (loose or in zip/rar)\n"
            "<table>_<enm>_fix.txt                   partial re-extraction, merged automatically\n"),
    ("p", "The commands for the 12 tables are in doc/enm_cli_extraction.md. An ENM is ready when its required "
          "tables are present. Then press Process:\n"),
    ("li", "•  EPT.xlsx -> table ept (a few minutes). Not converted again when a workdir of the database was "
           "already loaded from the same EPT.xlsx (same path, size and date): its ept rows are reused. "
           "Sheets in priority order: EPT_3G_LTE_OUTDOOR, EPT_3G_LTE_INDOOR, PLAN_OUTDOOR, PLAN_INDOOR, "
           "EVENTOS_ESPECIALES; CellName, or ATT_CELL_ID_Name when CellName is empty.\n"),
    ("li", "•  every ENM export is read and checked: rows read against the rows announced by ENM "
           "(N instance(s)). A file that ends with an ENM error is used, but the nodes it missed are marked "
           "INCOMPLETE and no change is proposed for them.\n"),
    ("li", "•  everything is loaded into the database db\\lte_nr.sqlite under the workdir name (first column "
           "workdir of every table): the 13 ENM tables of tul, ptl and tlt (column enm), ept, propagation_e4g / "
           "propagation_e5g (cell_name, max85coverage_km), load_status and loads. Several workdirs (extractions) "
           "live side by side; processing a workdir again replaces only its rows. Each workdir takes about "
           "500 MB. It can be opened with any SQLite viewer.\n"),
    ("p", "The panel text is saved as load_report.txt in the workdir.\n"),
    ("h2", "2. Run Audit\n"),
    ("p", "Workdir: the extraction to audit (the list shows the workdirs loaded; it is the same workdir as in "
          "1. Load Data). RUN AUDIT reads only that workdir's rows of the database and audits every ENM loaded by "
          "Process (tul, ptl, tlt), one after the other; the reports go to output\\<workdir>. Scope: All (every node) "
          "or Node list (type the node names, comma separated, in the box below; the box is disabled with All). "
          "An ENM without any node of the list is skipped (no output).\n"),
    ("p", "Audit X2 TermPoint LTE to 5G (ExternalGNodeBFunction and TermPointToGNB, one per eNB -> gNB). The X2 "
          "address of the gNB is its GNBCUCPFunction X2 SCTP endpoint on Router=NR (column Target IP X2 NR). "
          "Actions, with the MOs in execution order: NO_CHANGE; SET TermPointToGNB ipAddress (ipAddress 0.0.0.0); "
          "CREATE ExternalGNodeBFunction; TermPointToGNB or CREATE TermPointToGNB (GUtranCellRelation pending to that "
          "gNB within the ADD tier); DELETE TermPointToGNB; ExternalGNodeBFunction (no GUtranCellRelation left and "
          "the DELETE tier / overlap rules); REVIEW; OUT_OF_SCOPE (gNB in none of the loaded ENMs). CREATE and DELETE "
          "need the relation audit selected. Column Tier = hops between the eNB and gNB sites.\n"),
    ("p", "Audit Neighbor Relation LTE to 5G (GUtranCellRelation): LTE source cells of the bands selected in "
          "'LTE source bands' (default All) on nodes that have an NR cell of the bands selected in '5G target "
          "bands' (default All); the targets are the NR cells of those bands. Both dropdowns are multi-select "
          "(click to open, check the bands, click outside to close) and list the bands active in the EPT "
          "(4G: band_2_pcs, band_4_aws, band_5_850, band_7_2600, band_26_800; 5G: band_7_2600), a fixed list "
          "in lne_config.py (LTE_BANDS / NR_BANDS). The band of each cell comes from conf\\freq_band.csv (dl_arfcn -> "
          "band_indicator): LTE earfcndl in the 4g rows, NR ssbFrequency in the 5g rows (if absent, the NRCellDU "
          "bandList, e.g. [7] -> B7 -> band_7_2600). A new ARFCN must be added to that file. "
          "Each cell "
          "is a fan: EPT position, azimuth and beam, radius = Max85 of the propagation tables. Tier = hops between "
          "NR sites in a Delaunay triangulation (0 = same site, 1 = first ring).\n"),
    ("li", "•  In the Audit Rules: Relation = GUtranCellRelation, ExtGNB = ExternalGNodeBFunction, TermPoint = "
           "TermPointToGNB. The tiers apply to the relations; ExtGNB and TermPoint follow them (created when a "
           "relation to the gNB is added, deleted when no relation to the gNB is left).\n"),
    ("li", "•  DELETE: tier >= the value of 'DELETE ExtGNB, TermPoint, Relation', or no fan overlap "
           "and tier >= the value of 'DELETE ExtGNB, TermPoint, Relation (no fan overlap)'. Relations reserved by ESS (reservedBy) are PROTECTED, never deleted.\n"),
    ("li", "•  ADD (no relation exists): every NR cell of the sites with tier <= the value of 'ADD ExtGNB, "
           "TermPoint, Relation' (overlap or not), and the NR cells whose fan overlaps the LTE fan with "
           "tier <= the value of 'ADD ExtGNB, TermPoint, Relation (fan overlap)' (geometry from the EPT, not only the site position). Both "
           "need the cells UNLOCKED and ENABLED (option) and the node data complete; otherwise HOLD. Each ADD tier "
           "must be lower than the matching DELETE tier.\n"),
    ("li", "•  'What the audit will do' (under Audit Rules) shows the action per tier with the selected values: "
           "Exists = the relation is defined (keep / delete), Missing = it is not (add / — = not added), each with "
           "and without fan overlap. It is updated when a tier changes and warns in red when a relation would be "
           "added and then deleted. Exceptions not shown in the table: relations reserved by ESS are never deleted "
           "(PROTECTED), and an add becomes HOLD when a cell is not UNLOCKED and ENABLED (option), the gNB X2 "
           "address is missing or the node data is incomplete.\n"),
    ("li", "•  TermPoints: CREATE when a GUtranCellRelation ADD goes to a gNB the eNB has no TermPointToGNB for "
           "(without the gNB X2 address the relation is HOLD). DELETE only when, after the relation DELETEs, no "
           "GUtranCellRelation of any cell of the eNB (any band, PROTECTED included) goes to that gNB, and the tier "
           "or the fan overlap meets the DELETE rules above. Its ExternalGUtranCells are deleted in 02, the "
           "TermPointToGNB and ExternalGNodeBFunction in 01.\n"),
    ("li", "•  REVIEW: overlap or tier unknown (missing EPT or propagation). Unknown is never treated as zero.\n"),
    ("h2", "Run Audit results\n"),
    ("p", "The Results table shows the input and output directories and, per ENM, its run folder and the summary "
          "of each report and the files it wrote; double-click a row to open that folder or file. Each ENM "
          "writes to output\\<workdir>\\<ENM>_<YYYYMMDD>\\ (one folder per ENM and day); every file name ends "
          "with _<ENM>_<YYYYMMDD>_<HHMMSS>, the time it was written, so runs of the same day do not mix:\n"),
    ("li2", "01_lte_nr_termpoint_audit_*.xlsx      02_lte_nr_relation_audit_*.xlsx / .kmz (Google Earth)\n"
            "MOP and rollback cmedit scripts, run in this order (rollbacks in the reverse order, 4 -> 1):\n"
            "  STEP 1  01_lte_nr_termpoint_create_mop_cmedit_*.txt   (CREATE and SET ipAddress)\n"
            "  STEP 2  02_lte_nr_relation_add_mop_cmedit_*.txt       (GUtranFreqRelation / ExternalGUtranCell if missing)\n"
            "  STEP 3  02_lte_nr_relation_delete_mop_cmedit_*.txt    (relations, then ExternalGUtranCells)\n"
            "  STEP 4  01_lte_nr_termpoint_delete_mop_cmedit_*.txt\n"
            "Each MOP has its *_rollback_cmedit_*.txt; a deleted MO is recreated only with the attributes read "
            "from ENM.\n"),
    ("h2", "3. Neighbor Fan\n"),
    ("p", "A map of one site: its LTE fans (EPT position, azimuth and beam, length = Max85 of the propagation "
          "table; dashed 500 m when the cell has no Max85) colored by band, and the NR sites around it colored by "
          "tier (hops in the Delaunay triangulation of the NR sites, the same tiers as the audit; gray lines = "
          "Delaunay links). It reads the EPT and propagation of the workdir from the database (EPT cells on air, "
          "not PLAN).\n"),
    ("li", "•  Workdir and att_site (type part of the name to filter the list), Radius (km) of the map, "
           "Map background (OpenStreetMap, cached in db\\tiles; white when offline).\n"),
    ("li", "•  Neighbor NR fans (by tier): draws the fans of the NR cells of every neighbor site in its tier color "
           "(azimuth and beam). Use NR max85 length: checked = real NR max85 (dashed 500 m when missing; long "
           "fans overlap a lot), unchecked = short markers, 8% of the map radius. The real max85 is always in "
           "Results -> Cells and in the KMZ.\n"),
    ("li", "•  Filters: Tiers (0-6), LTE bands (fans drawn), 5G bands (NR sites used for the tiers and NR fans).\n"),
    ("li", "•  Fan Plot draws the map and fills Results in two tabs: Nodes (the selected site, then the NR "
           "sites with tier, distance and bearing) and Cells (LTE cells of the site, then the NR cells of the "
           "neighbors with azimuth, beam and max85). Bearing = direction of the neighbor seen from the "
           "selected site, degrees clockwise from north. Fan overlaps (counter): LTE cell = NR fans overlapping it, "
           "NR cell = LTE fans of the site overlapping it, node = sum of its cells; max85 fans with area > 0, as "
           "the audit (unknown when the cell has no max85). Clear Filter sets every filter back to All.\n"),
    ("li", "•  Export XLSX / Export KMZ write output\\<workdir>\\neighbor_fan\\03_neighbor_fan_<site>_<YYYYMMDD>_<HHMMSS>; "
           "the XLSX has the sheets Nodes, Cells and Settings; the KMZ opens in Google Earth (folders: LTE fans "
           "by band, NR sites by tier, NR fans by tier, Delaunay links).\n"),
    ("h2", "4. DB Status\n"),
    ("p", "One row per table loaded: Table, ENM, Workdir, Rows, Status (PARTIAL: nodes ENM did not read, in yellow) and Execution Date. "
          "Select Workdir filters the list (All = every workdir).\n"),
    ("li", "•  Check Status (first button): refresh the list (also done when the tab is opened and after "
           "Process).\n"),
    ("li", "•  Export to CSV: the selected rows -> their data to output\\<workdir>\\db_export\\<table>_<enm>.csv "
           "(Ctrl/Shift for several rows); with no row selected, the status list itself.\n"),
    ("li", "•  Delete Workdir: removes every row of the selected workdir and compacts the file. The output "
           "folder is not touched.\n"),
    ("li", "•  Output Folder: opens output\\<workdir> (or output\\ for All).\n"),
    ("h2", "Logs\n"),
    ("p", "Every line of 'Progress and log' and of the 1. Load Data panel (with errors and their details) is "
          "written at once to log\\lte_nr_<date>_<time>.log, one file per session of the application (the first "
          "line of the log panel shows its name). The panel starts empty: earlier sessions are in their files. "
          "Each audit run of an ENM also writes log\\run_<ENM>_<YYYYMMDD>_<HHMMSS>.log and each Process its "
          "load_report.txt in the workdir.\n"),
    ("p", "Default paths come from code/.env (ROOT_DIRECTORY, INPUT_DIR, OUTPUT_DIR, DB, LOG_DIR).\n"),
]


def apply_theme(root):
    for name in ("TkDefaultFont", "TkTextFont", "TkMenuFont", "TkHeadingFont"):
        try:
            tkfont.nametofont(name).configure(family=UI_FONT, size=9)
        except tk.TclError:
            pass
    root.configure(background=C_BG)
    root.option_add("*TCombobox*Listbox.font", (UI_FONT, 9))
    root.option_add("*TCombobox*Listbox.selectBackground", C_ACCENT)
    st = ttk.Style(root)
    st.theme_use("clam")
    st.configure(".", background=C_BG, foreground=C_TEXT, font=(UI_FONT, 9), bordercolor=C_BORDER,
                 lightcolor=C_BG, darkcolor=C_BG, troughcolor="#dfe4ea", focuscolor=C_ACCENT,
                 selectbackground=C_ACCENT, selectforeground=C_WHITE, fieldbackground=C_WHITE)
    st.map(".", foreground=[("disabled", "#9aa3ad")])
    st.configure("TLabelframe", background=C_BG, bordercolor=C_BORDER, relief="solid", borderwidth=1)
    st.configure("TLabelframe.Label", background=C_BG, foreground=C_ACCENT_TEXT, font=(UI_FONT, 9, "bold"))
    st.configure("TButton", background=C_WHITE, foreground=C_TEXT, bordercolor=C_BORDER, lightcolor=C_WHITE,
                 darkcolor=C_WHITE, relief="flat", padding=(10, 4))
    st.map("TButton", background=[("disabled", "#e9edf1"), ("pressed", "#d4e8f7"), ("active", C_HOVER)],
           bordercolor=[("active", C_ACCENT)])
    st.configure("Accent.TButton", background=C_ACCENT, foreground=C_WHITE, bordercolor=C_ACCENT,
                 lightcolor=C_ACCENT, darkcolor=C_ACCENT, font=(UI_FONT, 9, "bold"), padding=(14, 6))
    st.map("Accent.TButton", background=[("disabled", "#9ccbe8"), ("pressed", "#06608f"),
                                         ("active", C_ACCENT_DARK)], foreground=[("disabled", C_WHITE)])
    for w in ("TEntry", "TCombobox", "TSpinbox"):
        st.configure(w, fieldbackground=C_WHITE, background=C_WHITE, bordercolor=C_BORDER, lightcolor=C_WHITE,
                     darkcolor=C_WHITE, arrowcolor=C_TEXT, padding=3)
        st.map(w, bordercolor=[("focus", C_ACCENT)], fieldbackground=[("readonly", C_WHITE), ("disabled", "#eceff2")])
    for w in ("TCheckbutton", "TRadiobutton"):
        st.configure(w, background=C_BG, indicatorbackground=C_WHITE, indicatorforeground=C_ACCENT)
        st.map(w, background=[("active", C_BG)])
    st.configure("TNotebook", background=C_BG, bordercolor=C_BORDER, tabmargins=(0, 4, 0, 0))
    st.configure("TNotebook.Tab", background="#e1e6ec", foreground=C_MUTED, bordercolor=C_BORDER,
                 padding=(14, 4), font=(UI_FONT, 9, "bold"))
    st.map("TNotebook.Tab", background=[("selected", C_BG), ("active", C_HOVER)],
           foreground=[("selected", C_ACCENT_TEXT), ("active", C_TEXT)])
    st.configure("Treeview", background=C_WHITE, fieldbackground=C_WHITE, bordercolor=C_BORDER, rowheight=22)
    st.map("Treeview", background=[("selected", C_ACCENT)], foreground=[("selected", C_WHITE)])
    st.configure("Treeview.Heading", background="#e4e9ef", foreground=C_TEXT, relief="flat",
                 font=(UI_FONT, 9, "bold"), padding=(4, 4))
    st.configure("Header.TFrame", background=C_WHITE)
    st.configure("Header.TLabel", background=C_WHITE, foreground=C_TEXT)
    st.configure("HeaderTitle.TLabel", background=C_WHITE, foreground=C_TEXT, font=(UI_FONT, 12, "bold"))
    st.configure("HeaderSub.TLabel", background=C_WHITE, foreground=C_MUTED, font=(UI_FONT, 8))
    st.configure("HeaderLink.TLabel", background=C_WHITE, foreground=C_ACCENT_TEXT, font=(UI_FONT, 9, "bold"))
    st.configure("HeaderLine.TFrame", background=C_ACCENT)
    st.configure("Status.TFrame", background=C_STATUS)
    st.configure("Status.TLabel", background=C_STATUS, foreground=C_WHITE, font=(UI_FONT, 9))
    st.configure("Status.Horizontal.TProgressbar", background=C_ACCENT, troughcolor="#3a3a3a",
                 bordercolor=C_STATUS, lightcolor=C_ACCENT, darkcolor=C_ACCENT)
    st.configure("Muted.TLabel", foreground=C_MUTED)


def make_tree(parent, columns, widths, height=10):
    frame = ttk.Frame(parent)
    tree = ttk.Treeview(frame, columns=columns, show="headings", height=height)
    for c, w in zip(columns, widths):
        tree.heading(c, text=c)
        tree.column(c, width=w, anchor="w", stretch=True)
    sb = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
    tree.configure(yscrollcommand=sb.set)
    tree.pack(side="left", fill="both", expand=True)
    sb.pack(side="right", fill="y")
    tree.tag_configure("ok", background=C_OK)
    tree.tag_configure("warn", background=C_WARN)
    tree.tag_configure("err", background=C_ERR)
    return frame, tree


class MultiSelect(ttk.Menubutton):
    """
    Drop-down list with check boxes (same widget as the Propagation Vector tool): "All" first, then one
    entry per value. The list stays open while checking values and closes on a click outside it or
    Escape. The button shows "All", "None", the chosen values or "N selected". get() -> tuple (list order).
    """

    def __init__(self, master, items, selected=(), width=24):
        super().__init__(master, width=width)
        self.items = list(items)
        self.all_var = tk.BooleanVar(value=True)
        self.vars = {}
        self.popup = None
        self._syncing = False
        for v in self.items:
            var = tk.BooleanVar(value=v in selected)
            var.trace_add("write", lambda *_: self._item_changed())
            self.vars[v] = var
        self.bind("<Button-1>", self._toggle)
        self.bind("<Key-space>", self._toggle)
        self.bind("<Key-Down>", self._toggle)
        self._item_changed()

    def get(self):
        return tuple(v for v in self.items if self.vars[v].get())

    def select_all(self):
        self.all_var.set(True)
        self._all_changed()

    def _item_changed(self):
        if self._syncing:
            return
        self._syncing = True
        self.all_var.set(bool(self.vars) and all(v.get() for v in self.vars.values()))
        self._syncing = False
        self._refresh()

    def _all_changed(self):
        self._syncing = True
        for var in self.vars.values():
            var.set(self.all_var.get())
        self._syncing = False
        self._refresh()

    def _refresh(self):
        chosen = self.get()
        if not self.vars or len(chosen) == len(self.vars):
            text = "All"
        elif not chosen:
            text = "None"
        elif len(chosen) <= 2:
            text = ", ".join(chosen)
        else:
            text = f"{len(chosen)} selected"
        self.configure(text=text)

    def _toggle(self, _event=None):
        if self.popup is not None:
            self._close()
        elif str(self.cget("state")) != "disabled":
            self._open()
        return "break"

    def _open(self):
        top = self.popup = tk.Toplevel(self)
        top.withdraw()
        top.overrideredirect(True)
        frame = ttk.Frame(top, padding=(6, 4), relief="solid", borderwidth=1)
        frame.pack(fill=tk.BOTH, expand=True)
        ttk.Checkbutton(frame, text="All", variable=self.all_var, command=self._all_changed).pack(anchor="w")
        ttk.Separator(frame).pack(fill=tk.X, pady=3)
        for v in self.items:
            ttk.Checkbutton(frame, text=v, variable=self.vars[v]).pack(anchor="w")
        top.update_idletasks()
        width = max(self.winfo_width(), top.winfo_reqwidth())
        top.geometry(f"{width}x{top.winfo_reqheight()}+{self.winfo_rootx()}+{self.winfo_rooty() + self.winfo_height()}")
        top.deiconify()
        top.lift()
        top.grab_set()
        top.bind("<ButtonPress-1>", self._click_in_popup)
        top.bind("<Escape>", lambda e: self._close())
        top.focus_set()

    def _click_in_popup(self, event):
        top = self.popup
        x, y = event.x_root, event.y_root
        if not (top.winfo_rootx() <= x < top.winfo_rootx() + top.winfo_width()
                and top.winfo_rooty() <= y < top.winfo_rooty() + top.winfo_height()):
            self._close()

    def _close(self):
        if self.popup is not None:
            try:
                self.popup.grab_release()
                self.popup.destroy()
            except tk.TclError:
                pass
            self.popup = None


class SiteCombobox(ttk.Combobox):
    """Combobox that filters its values while typing (thousands of sites)."""

    def __init__(self, master, **kw):
        super().__init__(master, **kw)
        self.all_values = []
        self.bind("<KeyRelease>", self._filter)

    def set_all(self, values):
        self.all_values = list(values)
        self["values"] = self.all_values[:500]

    def _filter(self, event):
        if event.keysym in ("Up", "Down", "Return", "Escape", "Tab"):
            return
        text = self.get().strip().upper()
        matches = [v for v in self.all_values if text in str(v).upper()] if text else self.all_values
        self["values"] = matches[:500]


class App:
    def __init__(self, root):
        self.root = root
        root.title(APP_TITLE)
        root.geometry("1180x820")
        root.minsize(980, 680)
        try:
            root.state("zoomed")
        except tk.TclError:
            pass
        try:
            root.iconbitmap(str(CODE_DIR / "icon" / "app.ico"))
        except tk.TclError:
            pass
        self.s = Settings()
        self.events = queue.Queue()
        self.busy = False
        self.processed = None
        self._open_log_file()
        self._build_ui()
        root.protocol("WM_DELETE_WINDOW", self._close)
        root.after(100, self._poll)
        self.log(f"Session log: {self.log_path}" if self.log_file else "Session log: not available")
        self.log(f"Settings from {CODE_DIR / '.env'}")

    # ------------------------------------------------------------------ infra
    def _open_log_file(self):
        """log/lte_nr_<date>_<time>.log: every line of the GUI logs of this session, written at once."""
        self.log_lock = threading.Lock()
        self.log_path = Path(LOG_DIR) / f"lte_nr_{datetime.now():%Y-%m-%d_%H%M%S}.log"
        try:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            self.log_file = self.log_path.open("a", encoding="utf-8", buffering=1)
            self.log_file.write(f"{APP_TITLE} · session started {datetime.now():%Y-%m-%d %H:%M:%S}\n")
        except OSError:
            self.log_file = None

    def write_log(self, text):
        """Append to the session log file (any thread)."""
        if self.log_file:
            with self.log_lock:
                try:
                    self.log_file.write(text.rstrip("\n") + "\n")
                except (OSError, ValueError):
                    pass

    def _close(self):
        self.write_log(f"[{datetime.now():%H:%M:%S}] Session closed")
        if self.log_file:
            with self.log_lock:
                self.log_file.close()
                self.log_file = None
        self.root.destroy()

    def log(self, msg):
        line = f"[{datetime.now():%H:%M:%S}] {msg}"
        self.write_log(line)
        self.events.put(("log", line))

    def run_bg(self, label, func, on_done=None):
        if self.busy:
            messagebox.showwarning(APP_TITLE, "A task is already running. Please wait until it finishes.")
            return
        self._set_busy(True, label)

        def worker():
            try:
                self.events.put(("done", (on_done, func(), None)))
            except Exception as e:
                self.log(traceback.format_exc())
                self.events.put(("done", (on_done, None, e)))

        threading.Thread(target=worker, daemon=True).start()

    def _poll(self):
        try:
            while True:
                kind, payload = self.events.get_nowait()
                if kind == "load":
                    self._show_load([payload], clear=False)
                elif kind == "result":
                    self.add_result(*payload)
                elif kind == "log":
                    self.log_text.configure(state="normal")
                    self.log_text.insert(tk.END, payload + "\n")
                    self.log_text.see(tk.END)
                    self.log_text.configure(state="disabled")
                else:
                    on_done, result, error = payload
                    self._set_busy(False)
                    if error:
                        messagebox.showerror(APP_TITLE, str(error))
                    elif on_done:
                        on_done(result)
        except queue.Empty:
            pass
        if self.busy:
            self.status_var.set(f"⏳ {self.busy_label}... {int(time.monotonic() - self.t0)} s")
        self.root.after(100, self._poll)

    def _set_busy(self, busy, label=""):
        self.busy, self.busy_label, self.t0 = busy, label, time.monotonic()
        self.status_var.set(f"⏳ {label}..." if busy else "Ready")
        self.progress.start(12) if busy else self.progress.stop()
        self.root.configure(cursor="watch" if busy else "")
        for b in self.action_buttons:
            b.configure(state="disabled" if busy else "normal")
        if hasattr(self, "process_btn"):
            self._update_process_btn()

    # -------------------------------------------------------------------- UI
    def _build_ui(self):
        apply_theme(self.root)
        header = ttk.Frame(self.root, style="Header.TFrame", padding=(12, 5))
        header.pack(side=tk.TOP, fill=tk.X)
        try:
            self.logo = tk.PhotoImage(file=str(CODE_DIR / "icon" / "otlkm.png"))
            ttk.Label(header, image=self.logo, style="Header.TLabel").pack(side=tk.LEFT, padx=(0, 16))
        except tk.TclError:
            ttk.Label(header, text="OTLKM", style="HeaderTitle.TLabel").pack(side=tk.LEFT, padx=(0, 16))
        titles = ttk.Frame(header, style="Header.TFrame")
        titles.pack(side=tk.LEFT)
        ttk.Label(titles, text="LTE to NR Audit (Ericsson)", style="HeaderTitle.TLabel").pack(anchor="w")
        ttk.Label(titles, text=APP_SUBTITLE, style="HeaderSub.TLabel").pack(anchor="w")
        link = ttk.Label(header, text="Instructions", style="HeaderLink.TLabel", cursor="hand2")
        link.pack(side=tk.RIGHT)
        link.bind("<Button-1>", lambda _e: self.nb.select(self.nb.index("end") - 1))
        ttk.Label(header, text=APP_VERSION, style="HeaderLink.TLabel").pack(side=tk.RIGHT, padx=(0, 24))
        ttk.Label(header, text=EXPIRY_LABEL, style="HeaderLink.TLabel").pack(side=tk.RIGHT, padx=(0, 24))
        ttk.Frame(self.root, style="HeaderLine.TFrame", height=2).pack(side=tk.TOP, fill=tk.X)

        status = ttk.Frame(self.root, style="Status.TFrame", padding=(10, 4))
        status.pack(side=tk.BOTTOM, fill=tk.X)
        self.status_var = tk.StringVar(value="Ready")
        ttk.Label(status, textvariable=self.status_var, style="Status.TLabel").pack(side=tk.LEFT)
        self.progress = ttk.Progressbar(status, mode="indeterminate", length=180,
                                        style="Status.Horizontal.TProgressbar")
        self.progress.pack(side=tk.RIGHT)

        # Outputs always go to <OUTPUT_DIR>\<workdir>; the user only types the workdir name (tab 1)
        self.workdir = tk.StringVar(value=date.today().isoformat())
        self.output_dir = tk.StringVar(value=os.path.join(OUTPUT_ROOT, self.workdir.get()))
        self.workdir.trace_add("write", lambda *_: self.output_dir.set(
            os.path.join(OUTPUT_ROOT, self.workdir.get().strip())))

        paned = ttk.PanedWindow(self.root, orient=tk.VERTICAL)
        paned.pack(fill=tk.BOTH, expand=True, padx=8, pady=4)
        self.nb = ttk.Notebook(paned)
        paned.add(self.nb, weight=5)
        lf = ttk.LabelFrame(paned, text="Progress and log", padding=4)
        paned.add(lf, weight=1)
        self.log_text = scrolledtext.ScrolledText(lf, height=8, font=("Consolas", 9), state="disabled",
                                                  background=C_WHITE, relief="flat", highlightthickness=1,
                                                  highlightbackground=C_BORDER)
        self.log_text.pack(fill=tk.BOTH, expand=True)

        self.action_buttons = []
        self._tab_load()
        self._tab_run()
        self._tab_fan()
        self._tab_db()
        self._tab_instructions()
        self.nb.select(0)
        self.nb.bind("<<NotebookTabChanged>>", self._tab_changed)
        self.input_dir.trace_add("write", lambda *_: self._input_changed())

    def _input_changed(self):
        """A different folder must be loaded again before Process."""
        if self.scan and os.path.normcase(self.scan.folder) != os.path.normcase(self.input_dir.get().strip()):
            self.scan = None
            self._update_process_btn()

    def workdir_ok(self):
        """The workdir is a single folder name under output\\ (no path, no invalid characters)."""
        name = self.workdir.get().strip()
        if not name or re.search(r'[\\/:*?"<>|]', name) or name in (".", ".."):
            messagebox.showwarning(APP_TITLE, f"Workdir '{name}' is not a valid folder name.\n\n"
                                              f"Outputs go to {OUTPUT_ROOT}\\<workdir>, e.g. "
                                              f"{date.today().isoformat()}")
            return False
        return True

    # ---- Tab 1: Load Data
    def _tab_load(self):
        tab = ttk.Frame(self.nb, padding=10)
        self.nb.add(tab, text="  1. Load Data  ")
        box = ttk.LabelFrame(tab, text="Input", padding=10)
        box.pack(fill=tk.X)
        box.columnconfigure(1, weight=1)
        self.input_dir = tk.StringVar(value=INPUT_DIR)
        ttk.Label(box, text="Folder").grid(row=0, column=0, sticky="w", padx=(0, 8))
        ttk.Entry(box, textvariable=self.input_dir).grid(row=0, column=1, sticky="ew")
        b = ttk.Button(box, text="Search", command=self.search_input)
        b.grid(row=0, column=2, padx=(6, 0))
        self.action_buttons.append(b)
        b = ttk.Button(box, text="Load files", style="Accent.TButton", command=self.load_files)
        b.grid(row=0, column=3, padx=(6, 0))
        self.action_buttons.append(b)

        ttk.Label(box, text="Workdir").grid(row=1, column=0, sticky="w", padx=(0, 8), pady=(10, 0))
        bar = ttk.Frame(box)
        bar.grid(row=1, column=1, columnspan=3, sticky="w", pady=(10, 0))
        ttk.Entry(bar, textvariable=self.workdir, width=22).pack(side=tk.LEFT)
        self.process_btn = ttk.Button(bar, text="⚙  Process", style="Accent.TButton", command=self.process_files,
                                      state="disabled")
        self.process_btn.pack(side=tk.LEFT, padx=(8, 0))

        self.load_text = scrolledtext.ScrolledText(tab, font=("Consolas", 9), state="disabled", wrap="none",
                                                   background=C_WHITE, relief="flat", highlightthickness=1,
                                                   highlightbackground=C_BORDER)
        self.load_text.pack(fill=tk.BOTH, expand=True, pady=(10, 0))
        self.load_text.tag_configure("ok", foreground="#1e7b34")
        self.load_text.tag_configure("err", foreground="#c62828", font=("Consolas", 9, "bold"))
        self.load_text.tag_configure("warn", foreground="#b26a00")
        self.load_text.tag_configure("head", foreground=C_ACCENT_TEXT, font=("Consolas", 9, "bold"))
        self.scan = None

    def _show_load(self, lines, clear=True):
        t = self.load_text
        t.configure(state="normal")
        if clear:
            t.delete("1.0", tk.END)
        for line in lines:
            s = line.strip()
            tag = ("err" if s.startswith("✗") else "ok" if s.startswith("✓") else "warn" if s.startswith("⚠")
                   else "head" if line and not line.startswith(" ") else "")
            t.insert(tk.END, line + "\n", tag)
        t.see("1.0" if clear else tk.END)
        t.configure(state="disabled")

    def search_input(self):
        current = self.input_dir.get().strip()
        start = current if current and os.path.isdir(current) else str(ROOT)
        p = filedialog.askdirectory(initialdir=start, title="Input folder")
        if p:
            self.input_dir.set(os.path.normpath(p))
            self.load_files()

    def load_files(self):
        folder = self.input_dir.get().strip()
        self.scan = None

        def done(sc):
            self.scan = sc
            self._show_load(sc.lines)
            self.write_log(f"[{datetime.now():%H:%M:%S}] Load files\n" + "\n".join(sc.lines))
            self._update_process_btn()
            self.status_var.set("Files loaded: ready to Process" if sc.ready else "Files loaded: something is missing")
        self.run_bg("Loading files", lambda: lne_load.scan(folder, lambda _m: None), done)

    def process_files(self):
        if not (self.scan and self.scan.ready):
            messagebox.showwarning(APP_TITLE, "Load the files first; every required file must be present.")
            return
        if not self.workdir_ok():
            return
        sc, work = self.scan, self.output_dir.get().strip()
        name = Path(work).name
        if name in lne_db.workdirs(DB_PATH):
            m = lne_db.meta(DB_PATH, name)
            if not messagebox.askyesno(APP_TITLE, f"Workdir '{name}' is already loaded in the database\n"
                                                  f"(loaded {m.get('loaded_at', '?')} from {m.get('input_dir', '?')}).\n\n"
                                                  f"Replace it with {sc.folder}?\n\n"
                                                  "Only the rows of this workdir are replaced; earlier audit "
                                                  "run folders are kept."):
                return
        self._show_load(["", "-" * 100, ""], clear=False)

        def say(msg):
            self.events.put(("load", msg))
            self.log(msg) if msg.strip() else None

        def done(loaded):
            self.processed = work
            self.status_var.set(f"Processed — workdir {Path(work).name} in {DB_PATH}")
            self.refresh_db()
            messagebox.showinfo(APP_TITLE, f"Input data processed ({', '.join(loaded)}).\n\n"
                                           f"Workdir: {Path(work).name}\nDatabase: {DB_PATH}\n\n"
                                           "Go to 2. Run Audit.")
        self.run_bg("Processing input data",
                    lambda: lne_load.process(sc, work, DB_PATH, log=say), done)

    def _update_process_btn(self):
        ok = bool(self.scan and self.scan.ready) and not self.busy
        self.process_btn.configure(state="normal" if ok else "disabled")

    # ---- Tab 2: run
    def _tab_run(self):
        tab = ttk.Frame(self.nb, padding=10)
        self.nb.add(tab, text="  2. Run Audit  ")
        # sidebar (options + RUN AUDIT) on the left, Results on the right
        side = ttk.Frame(tab)
        side.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 10))

        what = ttk.LabelFrame(side, text="Select Audit", padding=10)
        what.pack(fill=tk.X)
        what.columnconfigure(1, weight=1)
        ttk.Label(what, text="Workdir").grid(row=0, column=0, sticky="w", pady=2)
        self.run_wd = ttk.Combobox(what, textvariable=self.workdir, width=22)
        self.run_wd.grid(row=0, column=1, sticky="ew", padx=(8, 0), pady=2)
        self.p1, self.p2 = tk.BooleanVar(value=True), tk.BooleanVar(value=True)
        for row, (label, var) in enumerate((("Audit X2 TermPoint LTE to 5G", self.p1),
                                            ("Audit Neighbor Relation LTE to 5G", self.p2)), start=1):
            ttk.Label(what, text=label).grid(row=row, column=0, sticky="w", pady=2)
            ttk.Checkbutton(what, variable=var).grid(row=row, column=1, sticky="w", padx=(8, 0), pady=2)
        ttk.Label(what, text="Scope").grid(row=3, column=0, sticky="w", pady=2)
        self.scope = tk.StringVar(value="All")      # All | Node list (Settings.scope all | nodes)
        scope_cb = ttk.Combobox(what, textvariable=self.scope, values=["All", "Node list"], state="readonly",
                                width=10)
        scope_cb.grid(row=3, column=1, sticky="w", padx=(8, 0), pady=2)
        scope_cb.bind("<<ComboboxSelected>>", lambda _e: self._scope_toggle())
        self.names = tk.StringVar()
        self.names_entry = ttk.Entry(what, textvariable=self.names)
        self.names_entry.grid(row=4, column=0, columnspan=2, sticky="ew", pady=(2, 0))
        self._scope_toggle()

        rules = ttk.LabelFrame(side, text="Audit Rules", padding=10)
        rules.pack(fill=tk.X, pady=(10, 0))
        rules.columnconfigure(1, weight=1)
        self.active = tk.BooleanVar(value=self.s.active_only_for_add)
        self.lte_bands = MultiSelect(rules, LTE_BANDS, self.s.source_bands)
        self.nr_bands = MultiSelect(rules, NR_BANDS, self.s.target_bands)
        self.zero_tier = tk.IntVar(value=self.s.zero_overlap_min_tier)
        self.del_tier = tk.IntVar(value=self.s.min_delete_tier)
        self.add_tier = tk.IntVar(value=self.s.max_add_tier)
        self.add_all_tier = tk.IntVar(value=self.s.max_add_all_tier)
        # one rule per row: text on the left, its option on the right (aligned in column 1)
        for row, (label, var, values) in enumerate([
                ("LTE source bands", self.lte_bands, "widget"),
                ("5G target bands", self.nr_bands, "widget"),
                ("ADD only if both cells are UNLOCKED and ENABLED", self.active, None),
                ("ADD ExtGNB, TermPoint, Relation", self.add_all_tier, ("tier <=", [0, 1, 2, 3, 4, 5])),
                ("ADD ExtGNB, TermPoint, Relation (fan overlap)", self.add_tier, ("tier <=", [0, 1, 2, 3, 4, 5])),
                ("DELETE ExtGNB, TermPoint, Relation (no fan overlap)", self.zero_tier, ("tier >=", [1, 2, 3, 4, 5, 6])),
                ("DELETE ExtGNB, TermPoint, Relation", self.del_tier, ("tier >=", [4, 5, 6, 7, 8, 9, 10]))]):
            ttk.Label(rules, text=label).grid(row=row, column=0, sticky="w", pady=2)
            if values == "widget":
                var.grid(row=row, column=1, sticky="w", padx=(8, 0), pady=2)
            elif values is None:
                ttk.Checkbutton(rules, variable=var).grid(row=row, column=1, sticky="w", padx=(8, 0), pady=2)
            else:                                # "tier <=" / "tier >=" right before the value
                op, values = values
                cell = ttk.Frame(rules)
                cell.grid(row=row, column=1, sticky="w", padx=(8, 0), pady=2)
                ttk.Label(cell, text=op, width=7).pack(side=tk.LEFT)
                ttk.Combobox(cell, textvariable=var, values=values, state="readonly", width=4).pack(side=tk.LEFT)

        # live preview of what the tier rules do (recomputed whenever a tier changes)
        prev = ttk.LabelFrame(side, text="What the audit will do", padding=10)
        prev.pack(fill=tk.X, pady=(10, 0))
        self.rule_grid = tk.Frame(prev, bg=C_BORDER)     # 1px gaps between cells show as grid lines
        self.rule_grid.pack(anchor="w")
        self.rule_warn = ttk.Label(prev, foreground="#c62828", wraplength=420, justify="left")
        for v in (self.add_all_tier, self.add_tier, self.zero_tier, self.del_tier):
            v.trace_add("write", lambda *_: self._rule_preview())
        self._rule_preview()

        # same label column width in both boxes, so their options line up
        side.update_idletasks()
        labels = [w for f in (what, rules) for w in f.grid_slaves(column=0) if isinstance(w, ttk.Label)]
        width = max(w.winfo_reqwidth() for w in labels)
        for f in (what, rules):
            f.columnconfigure(0, minsize=width)

        b = ttk.Button(side, text="▶  RUN AUDIT", style="Accent.TButton", command=self.start)
        b.pack(fill=tk.X, pady=(12, 0))
        self.action_buttons.append(b)

        res = ttk.LabelFrame(tab, text="Results", padding=6)
        res.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        frame, self.res_tree = make_tree(res, ("ENM", "Report", "Item", "Value"), (60, 220, 260, 500), height=8)
        frame.pack(fill=tk.BOTH, expand=True)
        self.res_tree.bind("<Double-1>", self._open_result)
        self.run_outs = {}                      # tree item -> file or folder to open

    def _rule_preview(self):
        """Table of the action per tier for existing / missing relations, with and without fan overlap
        (same rules as lne_part2._classify_existing / _classify_missing); tiers with the same actions are merged."""
        try:
            add_all, add_ov = self.add_all_tier.get(), self.add_tier.get()
            zero, dele = self.zero_tier.get(), self.del_tier.get()
        except tk.TclError:
            return
        def actions(t):
            return ("delete" if t >= dele else "keep",                       # exists, overlap
                    "delete" if t >= dele or t >= zero else "keep",          # exists, no overlap
                    "add" if t <= add_all or t <= add_ov else "—",           # missing, overlap
                    "add" if t <= add_all else "—")                          # missing, no overlap
        last = max(dele, zero, add_all + 1, add_ov + 1)                      # from here on nothing changes
        groups = []                                                          # [first tier, last tier, actions]
        for t in range(last + 1):
            if groups and groups[-1][2] == actions(t) and t < last:
                groups[-1][1] = t
            else:
                groups.append([t, t, actions(t)])

        g = self.rule_grid
        for w in g.winfo_children():
            w.destroy()
        head = dict(bg="#e4e9ef", fg=C_TEXT, font=(UI_FONT, 9, "bold"), padx=8, pady=2)
        tk.Label(g, text="Tier", **head).grid(row=0, column=0, rowspan=2, sticky="nsew", padx=1, pady=1)
        for c, text in ((1, "Exists"), (3, "Missing")):
            tk.Label(g, text=text, **head).grid(row=0, column=c, columnspan=2, sticky="nsew", padx=1, pady=1)
        for c in range(4):
            tk.Label(g, text=("overlap", "no overlap")[c % 2], **dict(head, font=(UI_FONT, 9))).grid(
                row=1, column=c + 1, sticky="nsew", padx=1, pady=1)
        style = {"add": ("#e7f6ec", "#1e7b34"), "delete": ("#fde2e1", "#c62828"),
                 "keep": (C_WHITE, C_TEXT), "—": (C_WHITE, "#9aa3ad")}
        for r, (a, b, acts) in enumerate(groups, start=2):
            tier = f"{a}+" if b == last else (str(a) if a == b else f"{a}-{b}")
            tk.Label(g, text=tier, bg=C_WHITE, fg=C_TEXT, font=(UI_FONT, 9, "bold"), padx=8, pady=2).grid(
                row=r, column=0, sticky="nsew", padx=1, pady=1)
            for c, act in enumerate(acts, start=1):
                bg, fg = style[act]
                tk.Label(g, text=act, bg=bg, fg=fg, font=(UI_FONT, 9), width=9, pady=2).grid(
                    row=r, column=c, sticky="nsew", padx=1, pady=1)

        # thresholds that contradict each other: a missing relation would be added and then deleted
        bad = [f"{'no overlap' if c else 'overlap'} tier {t}" for t in range(last + 1)
               for c, (ex, mi) in enumerate(((actions(t)[0], actions(t)[2]), (actions(t)[1], actions(t)[3])))
               if ex == "delete" and mi == "add"]
        if bad:
            self.rule_warn.configure(text="Check the tiers: added and then deleted at " + ", ".join(bad))
            self.rule_warn.pack(anchor="w", pady=(6, 0))
        else:
            self.rule_warn.pack_forget()

    def _scope_toggle(self):
        """Node names can only be typed with Scope = Node list (All audits every node)."""
        self.names_entry.state(["!disabled"] if self.scope.get() == "Node list" else ["disabled"])
        if self.scope.get() == "Node list":
            self.names_entry.focus_set()

    def settings(self, enm):
        s = Settings()
        s.enm, s.output_dir, s.workdir = enm, self.output_dir.get().strip(), self.workdir.get().strip()
        s.part1, s.part2 = self.p1.get(), self.p2.get()
        node_list = self.scope.get() == "Node list"
        s.scope, s.scope_names = ("nodes", self.names.get()) if node_list else ("all", "")
        s.active_only_for_add = self.active.get()
        s.zero_overlap_min_tier, s.min_delete_tier = self.zero_tier.get(), self.del_tier.get()
        s.max_add_tier, s.max_add_all_tier = self.add_tier.get(), self.add_all_tier.get()
        s.source_bands, s.target_bands = self.lte_bands.get(), self.nr_bands.get()
        return s

    def start(self):
        if not self.workdir_ok():
            return
        wd = self.workdir.get().strip()
        enms = lne_db.ready_enms(DB_PATH, wd)
        if not enms:
            loaded = lne_db.workdirs(DB_PATH)
            messagebox.showwarning(APP_TITLE, f"Workdir '{wd}' is not loaded in the database:\n{DB_PATH}\n\n"
                                   + (f"Loaded workdirs: {', '.join(loaded)}\n\n" if loaded else "")
                                   + "Select a loaded workdir or go to 1. Load Data -> Load files -> Process.")
            return
        meta = lne_db.meta(DB_PATH, wd)
        s = self.settings(enms[0])
        if not (s.part1 or s.part2):
            messagebox.showwarning(APP_TITLE, "Select at least one audit.")
            return
        if s.part2 and s.rule_problems():
            messagebox.showwarning(APP_TITLE, "Contradictory Audit Rules:\n\n" + "\n\n".join(s.rule_problems()))
            return
        if s.part2 and not (s.source_bands and s.target_bands):
            messagebox.showwarning(APP_TITLE, "Audit Neighbor Relation LTE to 5G: select at least one LTE source band and one 5G target band.")
            return
        if s.scope != "all" and not s.scope_set():
            messagebox.showwarning(APP_TITLE, "Scope Node list: type at least one node name.")
            return

        t = self.res_tree
        t.delete(*t.get_children())
        self.run_outs = {}
        t.insert("", tk.END, values=("", "Workdir", "", wd))
        t.insert("", tk.END, values=("", "Input directory", "", meta.get("input_dir", "")))
        self.run_outs[t.insert("", tk.END, values=("", "Database", f"loaded {meta.get('loaded_at', '')}",
                                                   DB_PATH))] = Path(DB_PATH).parent
        self.run_outs[t.insert("", tk.END, values=("", "Output directory", "", s.output_dir))] = s.output_dir

        skipped = []

        def work():
            done = {}
            for n, enm in enumerate(enms, 1):
                self.log(f"===== ENM {enm} ({n}/{len(enms)}) =====")
                try:
                    out = run(self.settings(enm), self.log)
                    if out is None:               # no name of the scope in this ENM
                        skipped.append(enm)
                    else:
                        done[enm] = str(out.folder)
                    self.events.put(("result", (enm, out, None)))
                except Exception as e:
                    self.log(traceback.format_exc())
                    self.events.put(("result", (enm, None, e)))
            return done

        def finished(done):
            failed = [e for e in enms if e not in done and e not in skipped]
            self.status_var.set(f"Completed {len(done)}/{len(enms)} ENM — {s.output_dir}")
            if not done and not failed:
                messagebox.showwarning(APP_TITLE, f"Node list: no node found in any ENM "
                                                  f"({', '.join(enms)}). Nothing audited.")
                return
            messagebox.showinfo(APP_TITLE, f"Audit completed for {', '.join(done) or 'no ENM'}"
                                + (f"\nSkipped (scope not in the ENM): {', '.join(skipped)}" if skipped else "")
                                + (f"\nFailed: {', '.join(failed)}" if failed else "")
                                + f"\n\n{s.output_dir}")
        self.run_bg(f"Running audit ({', '.join(enms)})", work, finished)

    def add_result(self, enm, out, error):
        t = self.res_tree
        if error:
            t.insert("", tk.END, values=(enm, "ERROR", "", str(error)), tags=("err",))
            return
        if out is None:
            t.insert("", tk.END, values=(enm, "Skipped", "", "no name of the scope in this ENM (no output)"))
            return
        self.run_outs[t.insert("", tk.END, values=(enm, "Run folder", "", str(out.folder)), tags=("ok",))] = out.folder
        for p in out.files:
            name = p.name
            if p.suffix != ".xlsx":
                self.run_outs[t.insert("", tk.END, values=(enm, name, "", ""))] = p
            elif p.is_file():
                try:
                    df = pd.read_excel(p, sheet_name="Summary")
                    for item, value in zip(df.iloc[:, 0], df.iloc[:, 1]):
                        v = f"{value:,}" if isinstance(value, (int, float)) and not pd.isna(value) else value
                        self.run_outs[t.insert("", tk.END, values=(enm, name, item, v))] = p
                except Exception as e:
                    t.insert("", tk.END, values=(enm, name, "Summary not readable", str(e)), tags=("warn",))

    def _open_result(self, _event):
        sel = self.res_tree.selection()
        target = self.run_outs.get(sel[0]) if sel else None
        if target and Path(target).exists():
            os.startfile(str(target))

    # ---- Tab 3: Neighbor Fan
    def _tab_fan(self):
        tab = ttk.Frame(self.nb, padding=10)
        self.nb.add(tab, text="  3. Neighbor Fan  ")
        side = ttk.Frame(tab)
        side.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 10))
        main = ttk.Frame(tab)
        main.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        box = ttk.LabelFrame(side, text="Site", padding=10)
        box.pack(fill=tk.X)
        box.columnconfigure(1, weight=1)
        ttk.Label(box, text="Workdir").grid(row=0, column=0, sticky="w", pady=2)
        self.fan_wd = ttk.Combobox(box, textvariable=self.workdir, width=20)
        self.fan_wd.grid(row=0, column=1, sticky="ew", padx=(8, 0), pady=2)
        self.fan_wd.bind("<<ComboboxSelected>>", lambda _e: self._fan_load_sites())
        ttk.Label(box, text="att_site").grid(row=1, column=0, sticky="w", pady=2)
        self.fan_site = SiteCombobox(box, width=20)
        self.fan_site.grid(row=1, column=1, sticky="ew", padx=(8, 0), pady=2)
        self.fan_site.bind("<Return>", lambda _e: self.fan_plot())
        ttk.Label(box, text="Radius (km)").grid(row=2, column=0, sticky="w", pady=2)
        self.fan_radius = tk.DoubleVar(value=3.0)
        ttk.Spinbox(box, from_=0.5, to=50, increment=0.5, textvariable=self.fan_radius, width=6).grid(
            row=2, column=1, sticky="w", padx=(8, 0), pady=2)
        self.fan_basemap = tk.BooleanVar(value=True)
        ttk.Checkbutton(box, text="Map background (OpenStreetMap)", variable=self.fan_basemap).grid(
            row=3, column=0, columnspan=2, sticky="w", pady=(4, 0))
        self.fan_nr_fans = tk.BooleanVar(value=True)
        ttk.Checkbutton(box, text="Neighbor NR fans (by tier)", variable=self.fan_nr_fans,
                        command=self._fan_nr_toggle).grid(row=4, column=0, columnspan=2, sticky="w", pady=(2, 0))
        self.fan_nr_max85 = tk.BooleanVar(value=False)
        self.fan_nr_max85_cb = ttk.Checkbutton(box, text="Use NR max85 length (else 8% of radius)",
                                               variable=self.fan_nr_max85)
        self.fan_nr_max85_cb.grid(row=5, column=0, columnspan=2, sticky="w", padx=(20, 0), pady=(2, 0))

        filt = ttk.LabelFrame(side, text="Filters", padding=10)
        filt.pack(fill=tk.X, pady=(10, 0))
        filt.columnconfigure(1, weight=1)
        self.fan_tiers = MultiSelect(filt, [f"Tier {t}" for t in lne_fanview.TIERS],
                                     [f"Tier {t}" for t in lne_fanview.TIERS], width=18)
        self.fan_lte = MultiSelect(filt, LTE_BANDS, LTE_BANDS, width=18)
        self.fan_nr = MultiSelect(filt, NR_BANDS, NR_BANDS, width=18)
        for row, (label, w) in enumerate((("Tiers", self.fan_tiers), ("LTE bands", self.fan_lte),
                                          ("5G bands", self.fan_nr))):
            ttk.Label(filt, text=label).grid(row=row, column=0, sticky="w", pady=2)
            w.grid(row=row, column=1, sticky="ew", padx=(8, 0), pady=2)

        b = ttk.Button(side, text="Fan Plot", style="Accent.TButton", command=self.fan_plot)
        b.pack(fill=tk.X, pady=(12, 2))
        self.action_buttons.append(b)
        row = ttk.Frame(side)
        row.pack(fill=tk.X)
        for i, (text, cmd) in enumerate((("Clear Filter", self.fan_clear), ("Export XLSX", self.fan_export_xlsx),
                                         ("Export KMZ", self.fan_export_kmz))):
            row.columnconfigure(i, weight=1, uniform="fanbtn")
            ttk.Button(row, text=text, command=cmd, padding=(2, 4)).grid(
                row=0, column=i, sticky="ew", padx=(0 if i == 0 else 2, 0))

        body = ttk.PanedWindow(main, orient=tk.VERTICAL)
        body.pack(fill=tk.BOTH, expand=True)
        plot = ttk.Frame(body)
        body.add(plot, weight=3)
        self.fan_fig = Figure(figsize=(8, 6), dpi=100)
        self.fan_fig.text(0.5, 0.5, "Select a site and press Fan Plot", ha="center", va="center",
                          fontsize=12, color=C_MUTED)
        self.fan_canvas = FigureCanvasTkAgg(self.fan_fig, master=plot)
        toolbar = NavigationToolbar2Tk(self.fan_canvas, plot, pack_toolbar=False)
        toolbar.pack(side=tk.BOTTOM, fill=tk.X)
        self.fan_canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)
        res = ttk.LabelFrame(body, text="Results", padding=2)
        body.add(res, weight=2)
        self.fan_res_nb = ttk.Notebook(res)
        self.fan_res_nb.pack(fill=tk.BOTH, expand=True)
        self.fan_tables = {}          # "Nodes" / "Cells" -> Treeview
        for name in ("Nodes", "Cells"):
            page = ttk.Frame(self.fan_res_nb)
            self.fan_res_nb.add(page, text=f" {name} ")
            tv = ttk.Treeview(page, show="headings", height=8)
            ysb = ttk.Scrollbar(page, orient=tk.VERTICAL, command=tv.yview)
            xsb = ttk.Scrollbar(page, orient=tk.HORIZONTAL, command=tv.xview)
            tv.configure(yscrollcommand=ysb.set, xscrollcommand=xsb.set)
            ysb.pack(side=tk.RIGHT, fill=tk.Y)
            xsb.pack(side=tk.BOTTOM, fill=tk.X)
            tv.pack(fill=tk.BOTH, expand=True)
            self.fan_tables[name] = tv
        body.bind("<Map>", lambda e: body.after_idle(
            lambda: body.sashpos(0, int(body.winfo_height() * 0.72))), add="+")
        self.fan_data = None          # lne_fanview.FanData of (workdir, loaded_at)
        self.fan_key = None
        self.fan_view = None

    def _fan_key_now(self):
        wd = self.workdir.get().strip()
        return wd, lne_db.meta(DB_PATH, wd).get("loaded_at")

    def _fan_data_job(self, key):
        """Worker: the FanData of workdir key = (workdir, loaded_at), read once per Process."""
        if not key[1]:
            raise ValueError(f"Workdir '{key[0]}' is not loaded in the database: run 1. Load Data -> Process")
        if self.fan_key == key and self.fan_data is not None:
            return self.fan_data, key
        return lne_fanview.FanData(DB_PATH, key[0], self.log), key

    def _fan_set_data(self, data, key):
        if self.fan_key != key:
            self.fan_data, self.fan_key = data, key
            self.fan_site.set_all(data.sites)

    def _fan_load_sites(self):
        """Fill the site list when the tab is opened (EPT read once per workdir)."""
        if self.busy or (self.fan_key == self._fan_key_now() and self.fan_data is not None):
            return
        key = self._fan_key_now()
        if not key[1]:
            wds = lne_db.workdirs(DB_PATH)
            if not wds:
                self.log(f"Neighbor Fan: no workdir loaded in {DB_PATH} -> run 1. Load Data -> Process")
                return
            # today's workdir is not loaded: use the last one loaded
            self.log(f"Neighbor Fan: workdir '{key[0] or '-'}' not in the database, using {wds[0]}")
            self.workdir.set(wds[0])
            key = self._fan_key_now()
        self.run_bg("Loading sites (EPT)", lambda: self._fan_data_job(key), lambda r: self._fan_set_data(*r))

    def fan_plot(self):
        site = self.fan_site.get().strip().upper()
        tiers = tuple(int(t.split()[1]) for t in self.fan_tiers.get())
        lte, nr = self.fan_lte.get(), self.fan_nr.get()
        if not site:
            messagebox.showwarning(APP_TITLE, "Select a site (att_site).")
            return
        if not (tiers and lte and nr):
            messagebox.showwarning(APP_TITLE, "Select at least one tier, one LTE band and one 5G band.")
            return
        try:
            radius = float(self.fan_radius.get())
        except (tk.TclError, ValueError):
            radius = 3.0
        basemap = self.fan_basemap.get()
        nr_fans = self.fan_nr_fans.get()
        nr_max85 = self.fan_nr_max85.get()
        key = self._fan_key_now()

        def work():
            data, key2 = self._fan_data_job(key)
            view = data.build(site, lte, nr, tiers, radius, nr_fans=nr_fans, nr_max85=nr_max85)
            bm = lne_basemap.fetch_basemap(*view.extent()) if basemap else None
            return data, key2, view, bm

        def done(result):
            data, key, view, bm = result
            self._fan_set_data(data, key)
            self.fan_view = view
            lne_fanview.draw(self.fan_fig, view, bm)
            self.fan_canvas.draw()
            nodes, cells = view.nodes_table(), view.cells_table()
            self._fan_fill_table("Nodes", nodes)
            self._fan_fill_table("Cells", cells)
            self.fan_res_nb.tab(0, text=f" Nodes ({len(nodes)}) ")
            self.fan_res_nb.tab(1, text=f" Cells ({len(cells)}) ")
            self.log(f"Neighbor Fan {site}: {len(view.fans)} LTE fans, {len(view.sites)} NR sites, "
                     f"{len(view.nr_fans)} NR fans "
                     f"(tiers {', '.join(map(str, tiers))})" + ("" if bm is not None or not basemap else
                                                                  " · map background not available (offline)"))
        self.run_bg(f"Neighbor Fan {site}", work, done)

    def _fan_fill_table(self, name, df):
        t = self.fan_tables[name]
        t.delete(*t.get_children())
        cols = list(df.columns)
        t.configure(columns=cols)
        widths = {"Type": 70, "Site": 100, "Cell": 170, "Tier": 45, "Band": 150, "Bands": 200,
                  "Max85 radius (m)": 130}
        head = tkfont.nametofont("TkHeadingFont")
        for c in cols:
            t.heading(c, text=c)
            # never narrower than the heading text (bold heading font + padding)
            t.column(c, width=max(widths.get(c, 85), head.measure(c) + 28), anchor="w", stretch=False)
        for r in df.itertuples(index=False):
            t.insert("", tk.END, values=["" if pd.isna(v) else v for v in r])

    def fan_clear(self):
        for w in (self.fan_tiers, self.fan_lte, self.fan_nr):
            w.select_all()
        self.fan_radius.set(3.0)
        self.fan_basemap.set(True)
        self.fan_nr_fans.set(True)
        self.fan_nr_max85.set(False)
        self._fan_nr_toggle()

    def _fan_nr_toggle(self):
        """'Use NR max85 length' only applies when the neighbor NR fans are drawn."""
        self.fan_nr_max85_cb.state(["!disabled"] if self.fan_nr_fans.get() else ["disabled"])

    def _fan_out(self, ext):
        if self.fan_view is None:
            messagebox.showwarning(APP_TITLE, "Press Fan Plot first.")
            return None
        folder = Path(OUTPUT_ROOT) / self.fan_view.filters["workdir"] / "neighbor_fan"
        folder.mkdir(parents=True, exist_ok=True)
        return folder / f"03_neighbor_fan_{self.fan_view.site}_{datetime.now():%Y%m%d_%H%M%S}.{ext}"

    def fan_export_xlsx(self):
        path = self._fan_out("xlsx")
        if path:
            lne_report.write_excel(path, {"Nodes": self.fan_view.nodes_table(),
                                            "Cells": self.fan_view.cells_table(), "Settings": self.fan_view.settings_rows()})
            self.log(f"Exported {path}")
            os.startfile(str(path.parent))

    def fan_export_kmz(self):
        path = self._fan_out("kmz")
        if path:
            lne_fanview.write_kmz(path, self.fan_view)
            self.log(f"Exported {path} (open it with Google Earth)")
            os.startfile(str(path.parent))

    # ---- Tab 4: DB Status
    def _tab_db(self):
        tab = ttk.Frame(self.nb, padding=10)
        self.nb.add(tab, text="  4. DB Status  ")
        left = ttk.Frame(tab, width=210)
        left.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 10))
        box = ttk.LabelFrame(left, text="Workdir", padding=8)
        box.pack(fill=tk.X)
        ttk.Label(box, text="Select Workdir").pack(anchor="w")
        self.db_wd = tk.StringVar(value="All")
        self.db_combo = ttk.Combobox(box, textvariable=self.db_wd, values=["All"], state="readonly", width=24)
        self.db_combo.pack(fill=tk.X, pady=(2, 0))
        self.db_combo.bind("<<ComboboxSelected>>", lambda _e: self.refresh_db())
        for text, cmd, style in (("Check Status", self.refresh_db, "Accent.TButton"),
                                 ("Export to CSV", self.export_db, "TButton"),
                                 ("Delete Workdir", self.delete_db, "TButton")):
            b = ttk.Button(left, text=text, command=cmd, style=style)
            b.pack(fill=tk.X, pady=(10 if text == "Check Status" else 4, 0))
            self.action_buttons.append(b)
        ttk.Button(left, text="Output Folder", command=self.open_db_out).pack(fill=tk.X, pady=(24, 0))
        frame, self.db_tree = make_tree(tab, ("Table", "ENM", "Workdir", "Rows", "Status", "Execution Date"),
                                        (220, 60, 120, 90, 260, 150), height=20)
        frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.db_tree.column("Rows", anchor="e")

    def _tab_changed(self, _e=None):
        name = self.nb.tab(self.nb.select(), "text").strip()
        if name.startswith("4."):
            self.refresh_db()
        elif name.startswith("2."):
            self.run_wd.configure(values=lne_db.workdirs(DB_PATH))
        elif name.startswith("3."):
            self.fan_wd.configure(values=lne_db.workdirs(DB_PATH))
            self._fan_load_sites()

    def refresh_db(self):
        wds = lne_db.workdirs(DB_PATH)
        self.db_combo.configure(values=["All"] + wds)
        self.run_wd.configure(values=wds)
        self.fan_wd.configure(values=wds)
        if self.db_wd.get() not in ["All"] + wds:
            self.db_wd.set("All")
        sel = self.db_wd.get()
        df = lne_db.status(DB_PATH, None if sel == "All" else sel)
        t = self.db_tree
        t.delete(*t.get_children())
        for r in df.itertuples(index=False):
            st = str(r.status or "")
            tag = "warn" if ("not read" in st or st.startswith(("PARTIAL", "INCOMPLETE", "ROW", "ENM error"))) else ""
            t.insert("", tk.END, values=(r.table_name, r.enm or "", r.workdir, f"{int(r.rows):,}", st,
                                         r.executed_at), tags=(tag,) if tag else ())
        size = f"{Path(DB_PATH).stat().st_size / 1e6:,.0f} MB" if Path(DB_PATH).is_file() else "not created yet"
        self.log(f"Database: {DB_PATH} ({size}) · workdirs: {', '.join(wds) or 'none'}")

    def _db_selected(self):
        return [self.db_tree.item(i, "values") for i in self.db_tree.selection()]

    def export_db(self):
        rows = self._db_selected()
        if not rows:
            sel = self.db_wd.get()
            df = lne_db.status(DB_PATH, None if sel == "All" else sel)
            out = Path(OUTPUT_ROOT) / f"db_status_{datetime.now():%Y%m%d_%H%M%S}.csv"
            out.parent.mkdir(parents=True, exist_ok=True)
            df.to_csv(out, index=False, encoding="utf-8-sig")
            self.log(f"DB status exported: {out}  (select rows to export their data)")
            os.startfile(str(out.parent))
            return

        def work():
            folders = set()
            for table, enm, wd, *_ in rows:
                df = lne_db.read_frame(DB_PATH, table, wd, enm or None)
                folder = Path(OUTPUT_ROOT) / wd / "db_export"
                folder.mkdir(parents=True, exist_ok=True)
                out = folder / (f"{table}_{enm}.csv" if enm else f"{table}.csv")
                df.to_csv(out, index=False, encoding="utf-8-sig")
                self.log(f"Exported {len(df):,} rows: {out}")
                folders.add(folder)
            return folders

        def done(folders):
            for f in folders:
                os.startfile(str(f))
        self.run_bg(f"Exporting {len(rows)} table(s)", work, done)

    def delete_db(self):
        wd = self.db_wd.get()
        if wd == "All":
            messagebox.showwarning(APP_TITLE, "Select one workdir in 'Select Workdir' to delete it.")
            return
        if not messagebox.askyesno(APP_TITLE, f"Delete every row of workdir '{wd}' from the database?\n\n"
                                              f"{DB_PATH}\n\nThe output folder {Path(OUTPUT_ROOT) / wd} "
                                              "is not touched. This cannot be undone."):
            return

        def done(_n):
            self.db_wd.set("All")
            self.refresh_db()
        self.run_bg(f"Deleting workdir {wd}", lambda: lne_db.delete_workdir(DB_PATH, wd, self.log), done)

    def open_db_out(self):
        wd = self.db_wd.get()
        p = Path(OUTPUT_ROOT) / wd if wd != "All" else Path(OUTPUT_ROOT)
        if not p.is_dir():
            p = Path(OUTPUT_ROOT)
        p.mkdir(parents=True, exist_ok=True)
        os.startfile(str(p))

    # ---- Instructions
    def _tab_instructions(self):
        tab = ttk.Frame(self.nb, padding=10)
        self.nb.add(tab, text="  Instructions  ")
        text = scrolledtext.ScrolledText(tab, wrap="word", font=(UI_FONT, 10), padx=16, pady=12,
                                         relief="flat", background=C_WHITE)
        text.pack(fill=tk.BOTH, expand=True)
        text.tag_configure("h1", font=(UI_FONT, 16, "bold"), spacing3=8)
        text.tag_configure("h2", font=(UI_FONT, 12, "bold"), spacing1=14, spacing3=4, foreground="#1f4e8c")
        text.tag_configure("p", spacing3=4)
        text.tag_configure("li", lmargin1=18, lmargin2=34, spacing3=2)
        text.tag_configure("li2", lmargin1=40, lmargin2=40, font=("Consolas", 10), spacing3=6)
        for tag, content in INSTRUCTIONS:
            text.insert(tk.END, content, tag)
        text.configure(state="disabled")


def _taskbar_identity():
    """Own Windows taskbar identity: without it the window is grouped under pythonw.exe and the
    taskbar shows the Python icon instead of icon/app.ico (depends on how the app was started)."""
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("OTLKM.LteNrAudit")
    except (AttributeError, OSError):
        pass


def main():
    _taskbar_identity()
    root = tk.Tk()
    try:
        root.iconbitmap(default=str(CODE_DIR / "icon" / "app.ico"))     # every window, popups included
    except tk.TclError:
        pass
    if date.today() >= EXPIRY_DATE:
        root.withdraw()
        messagebox.showerror(APP_TITLE, EXPIRED_MESSAGE)
        root.destroy()
        return
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
