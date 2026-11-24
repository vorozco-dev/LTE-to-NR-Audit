# Brief para Claude Desktop: presentación "LTE to NR Audit (Ericsson)"

## Instrucciones para Claude (Desktop)

- Crea una presentación PowerPoint (.pptx) de **exactamente 5 slides**, en este orden y con estos títulos: The problem, Tiers and fan overlap, Audit rules, Neighbor Fan view, Outputs.
- No agregues slide de portada ni de cierre. Pon el nombre de la herramienta, "LTE to NR Audit (Ericsson)", en el pie de página de cada slide.
- Público: el **cliente** (operador móvil; ingenieros RF / O&M y su jefatura).
- Idioma: **inglés** (la herramienta y sus reportes están en inglés), incluidas las notas del presentador.
- Estilo: técnico, limpio, poco texto por slide. Usa diagramas y tablas donde se indica "Visual".
- Colores de las acciones (los mismos de la herramienta): ADD = verde (#1e7b34 sobre #e7f6ec), DELETE = rojo (#c62828 sobre #fde2e1), keep = blanco, "—" = gris.
- No inventes funcionalidades ni cifras que no estén aquí. No incluyas la sección "Notas para ti".

---

## Slide 1 - The problem

- EN-DC needs each LTE cell to have relations to the right NR cells (used for SgNB addition / B1)
- On the eNB this means, per neighbor gNB, an ExternalGNodeBFunction + TermPointToGNB (X2 IP), and per LTE -> NR cell pair a GUtranCellRelation
- Over the years these were created ad hoc: **missing** where needed, **left over** where not
- Missing -> UE not added to NR -> lost 5G throughput
- Excess -> unused X2 links, wrong IPs, cluttered neighbor lists, harder troubleshooting
- Manual audit across thousands of sites does not scale

- Visual: two columns "Missing" vs "Excess" with icons; small LTE eNB <-> NR gNB (X2) motif.
- Notes: The tool reads the live Ericsson ENM configuration, decides which LTE->NR relations and X2 TermPoints should exist, and produces Excel reports plus ready-to-run cmedit MOP and rollback scripts. Relations are created LTE -> NR only; the gNB side of X2 is created automatically by X2 Setup.

## Slide 2 - Tiers and fan overlap

- **Tier** = number of Delaunay hops between the LTE site and the NR site
  - Tier 0 = same site, tier 1 = immediate neighbors, tier 2 = neighbors of neighbors, ...
- **Fan overlap** = whether the coverage fans of the LTE cell and the NR cell overlap (sector azimuth + propagation footprint)
- Every LTE cell / NR cell pair gets a tier and an overlap flag
- Inputs: ENM exports (live configuration) + EPT (site and sector positions)

- Visual: map sketch with a Delaunay triangulation of sites, a central site and rings labelled Tier 1 / Tier 2 / Tier 3; two sector fans overlapping (overlap) and one pointing away (no overlap).
- Notes: Delaunay tiers measure "how many sites away" rather than kilometres, so the same rule works in dense urban and rural areas. Fan overlap adds the antenna direction, so a close cell pointing away is not treated as a neighbor.

## Slide 3 - Audit rules

**Rules (configurable in the GUI, defaults shown)**

| Rule | Default |
|---|---|
| ADD ExtGNB, TermPoint, Relation | tier <= 1 |
| ADD ExtGNB, TermPoint, Relation (fan overlap) | tier <= 4 |
| DELETE ExtGNB, TermPoint, Relation (no fan overlap) | tier >= 2 |
| DELETE ExtGNB, TermPoint, Relation | tier >= 6 |

**What the audit will do**

| Tier | Exists, overlap | Exists, no overlap | Missing, overlap | Missing, no overlap |
|---|---|---|---|---|
| 0-1 | keep | keep | ADD | ADD |
| 2-4 | keep | DELETE | ADD | — |
| 5 | keep | DELETE | — | — |
| 6+ | DELETE | DELETE | — | — |

- ESS-reserved relations are protected (never deleted)
- An ADD is put on HOLD if the cells are not UNLOCKED + ENABLED or the gNB X2 IP is unknown

- Visual: the two tables side by side; the second one colored (ADD green, DELETE red, keep white, — gray).
- Notes: "keep" means the existing relation stays; "—" means a missing relation stays missing. The GUI shows the second table live under the rules and warns if the thresholds contradict each other, so the engineer sees the consequence before running the audit.

## Slide 4 - Neighbor Fan view

- Interactive map of one site: its sectors and the neighbor NR / LTE cells with their coverage fans
- Shows the tier of each neighbor and whether each relation is added, deleted or kept
- Used to explain or check individual decisions with the optimization or field team
- Export to Excel or KMZ (Google Earth)

- Visual: large placeholder for a screenshot of the Neighbor Fan view (the user will provide it), with 2-3 callouts pointing to the fans, the tiers and the action colors.
- Notes: The audit is network-wide, but decisions are easiest to trust when you can see them on one site. This view lets anyone pick a site and see why each relation is added, deleted or kept.

## Slide 5 - Outputs

Per ENM (e.g. tul, ptl, tlt), in a folder `<ENM>_<YYYYMMDD>`; every file is time-stamped.

| File | Content |
|---|---|
| 01_lte_nr_termpoint_audit.xlsx | Source Summary, CURRENT, CREATE, DELETE, SET IP, REVIEW, TARGET TermPoint |
| 02_lte_nr_relation_audit.xlsx | Summary, CURRENT, ADD, DELETE, PROTECTED, TARGET relation |
| 02_lte_nr_relation_audit.kmz | Relations on Google Earth |
| 03_neighbor_fan_<site> | Neighbor Fan export (Excel / KMZ) |
| cmedit MOP + rollback scripts | 4 steps, below |

**Execution order (rollbacks run in reverse, 4 -> 1)**
1. TermPoint CREATE - create ExtGNB + TermPoint, or SET ipAddress
2. Relation ADD - GUtranFreqRelation / ExternalGUtranCell if missing, then GUtranCellRelation
3. Relation DELETE - relations, then unused ExternalGUtranCells
4. TermPoint DELETE - TermPoints + ExternalGNodeBFunctions no longer needed

- Visual: file table on the left; on the right the 4 steps as a vertical flow with a forward arrow (1->4) and a back arrow (rollback 4->1).
- Notes: TARGET sheets show how the network will look once all changes are applied, so the result can be reviewed before execution. The tool never writes to the network; changes are applied only when the operator runs the scripts in ENM. Rollbacks recreate deleted MOs with the attributes read from ENM.

---

## Notas para ti (no van en los slides)

- Ejemplo de una corrida real (ENM tul): TermPoints 27.896 actuales -> 27.513 objetivo (12.992 DELETE, 12.609 CREATE). Relaciones 70.242 actuales -> 303.311 objetivo (28.036 DELETE, 261.105 ADD).
- Los nombres de atributos de cmedit no se han validado aún contra el release de ENM del cliente.
- 245 ADDs necesitan crear un GUtranFreqRelation y suponen que el GUtranSyncSignalFrequency ya existe.
- Capturas para adjuntar a Claude Desktop: el Neighbor Fan view (slide 4, imprescindible) y la GUI con Audit Rules + "What the audit will do" (slide 3, opcional).
