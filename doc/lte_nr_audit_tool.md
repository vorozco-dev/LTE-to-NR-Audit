# LTE to NR Audit (Ericsson): descripción de la herramienta

## 1. Para qué sirve

En EN-DC cada celda LTE necesita relaciones hacia las celdas NR correctas para que el UE pueda agregar el SgNB (medición B1). Con los años estas relaciones y los X2 TermPoints se crearon sin un criterio único: faltan donde se necesitan y sobran donde no.

La herramienta lee la configuración real de los ENM de Ericsson y la posición de los sitios (EPT) y decide, con reglas configurables, qué relaciones LTE -> NR y qué X2 TermPoints deben existir. El resultado son reportes Excel, scripts `cmedit` (MOP y rollback) listos para ejecutar y un mapa por sitio (Graphical Audit, exportable a KMZ). La herramienta **no escribe en la red**: los cambios se aplican solo cuando el operador ejecuta los scripts en ENM.

## 2. Qué MOs audita

Todo vive en el eNB, bajo `ENodeBFunction=1,GUtraNetwork=1` y bajo cada `EUtranCellFDD`:

| MO | Uno por | Qué es |
|---|---|---|
| `ExternalGNodeBFunction` | eNB -> gNB vecino | definición del gNB vecino (gNodeBId, longitud, PLMN) |
| `TermPointToGNB` | eNB -> gNB vecino | enlace X2, con la IP del gNB |
| `ExternalGUtranCell` | eNB -> celda NR | celda NR vecina (cellLocalId, PCI, TAC, SSB) |
| `GUtranFreqRelation` | celda LTE -> frecuencia NR | apunta a `GUtranSyncSignalFrequency` (SSB) |
| `GUtranCellRelation` | celda LTE -> celda NR | la relación (`isEndcAllowed`, `isHoAllowed`) |

En la GUI se abrevian: **Relation** = GUtranCellRelation, **ExtGNB** = ExternalGNodeBFunction, **TermPoint** = TermPointToGNB.

- Las relaciones se crean solo en sentido **LTE -> NR**. En EN-DC el lado X2 del gNB lo crea automáticamente el X2 Setup.
- La IP X2 del gNB se toma del endpoint SCTP X2 de su `GNBCUCPFunction` (Router=NR).

## 3. Cómo decide: tiers y fan overlap

- **Tier**: número de saltos entre el sitio LTE y el sitio NR en una triangulación de Delaunay de los sitios NR (0 = mismo sitio, 1 = primer anillo, 2 = vecinos de los vecinos, ...). Mide "cuántos sitios de distancia" y no kilómetros, así que la misma regla sirve en zona urbana y rural. En el borde de la red Delaunay une sitios muy lejanos (un tier 1 puede estar a cientos de km); por eso la regla **Max Site distance** acota por distancia al final.
- **Fan overlap**: si los fans de cobertura de la celda LTE y de la celda NR se cruzan. Cada fan sale de la posición del EPT, con el azimut y el beam del EPT y con radio = Max85 de las tablas de propagación (e4g para LTE, e5g para NR). Una celda cercana que apunta hacia otro lado no cuenta como vecina.
- Si falta el EPT o la propagación de una celda, el overlap queda **desconocido** y el caso va a REVIEW. Lo desconocido nunca se trata como cero.

## 4. Reglas del audit

Se configuran en la GUI (Audit Rules). Valores por defecto:

| Regla | Defecto | Significado |
|---|---|---|
| ADD ExtGNB, TermPoint, Relation | tier <= 1 | agrega todas las celdas NR de esos sitios, haya overlap o no |
| ADD ExtGNB, TermPoint, Relation (fan overlap) | tier <= 4 | agrega las celdas NR con fan overlap |
| DELETE ExtGNB, TermPoint, Relation (no fan overlap) | tier >= 2 | borra las relaciones sin fan overlap |
| DELETE ExtGNB, TermPoint, Relation | tier >= 6 | borra todas las relaciones |
| Max Site distance (Km) | <= 30 | se aplica al final: hacia un sitio más lejano no se agrega nada y lo existente se borra |

Con esos valores, el resultado es este (la GUI lo muestra en vivo en "What the audit will do"):

| Tier | Existe, overlap | Existe, sin overlap | No existe, overlap | No existe, sin overlap |
|---|---|---|---|---|
| 0-1 | keep | keep | ADD | ADD |
| 2-4 | keep | DELETE | ADD | — |
| 5 | keep | DELETE | — | — |
| 6+ | DELETE | DELETE | — | — |


La tabla vale para sitios a <= 30 km (la GUI lo muestra bajo "Tier"); más lejos, en cualquier tier: lo existente se borra (DELETE) y lo que falta no se agrega.

`keep` = la relación existente se queda. `—` = la relación que falta no se agrega. Si los valores hacen que un tier se agregue y luego se borre, la GUI lo avisa en rojo.

Excepciones:
- **PROTECTED**: las relaciones reservadas por ESS (`reservedBy`) nunca se borran.
- **HOLD**: un ADD queda en espera si alguna celda no está UNLOCKED + ENABLED, si falta la IP X2 del gNB o si los datos del nodo están incompletos.
- **ExtGNB y TermPoint siguen a las relaciones**. Se crean cuando se agrega una relación hacia un gNB que el eNB todavía no tiene. Se borran cuando, después de los DELETE, ya no queda ninguna relación de ninguna celda del eNB (PROTECTED incluidas) hacia ese gNB.

## 5. Uso

Se abre con `run_lte_nr_ericsson_shortcut.lnk` (o `code/run_lte_nr_ericsson.bat`). Requiere Python 3 con los paquetes de `code/requirements.txt`. Las rutas por defecto están en `code/.env`.

1. **Extraer ENM**: los comandos `cmedit get` y el nombrado de los archivos están en [enm_cli_extraction.md](enm_cli_extraction.md). Se extraen los 3 ENM (tul, ptl, tlt) el mismo día.
2. **1. Load Data**: elige la carpeta de entrada (exports ENM, `EPT.xlsx`, tablas de propagación) y pulsa **Process**. Todo se carga en `db/lte_nr_e.sqlite` bajo un *workdir* (una extracción); pueden convivir varias extracciones.
3. **2. Run Audit**: elige el workdir, el alcance (All o una lista de nodos), las bandas LTE/NR y las reglas. Revisa la tabla "What the audit will do" y pulsa **RUN AUDIT**. Se audita cada ENM cargado, uno tras otro.
4. **Revisar** los reportes; para ver un sitio en el mapa, usar 3. Graphical Audit.
5. **Ejecutar** los scripts en ENM en el orden de la sección 7.

Otras pestañas:
- **3. Graphical Audit**: mapa de un sitio con sus fans LTE por banda, los sitios NR coloreados por tier, los fans NR, los enlaces Delaunay y las relaciones de su audit (mismo audit y mismas Audit Rules que 2. Run Audit; el bloque Audit Rules está en las dos pestañas y comparte los valores): Current — Complete (negro), Current — Missing TermPoint (azul), Current — To Be Deleted (rojo), Missing — To Be Added (morado); los sitios destino fuera de los tiers dibujados en negro. Los fans LTE del sitio y los fans NR de los vecinos se dibujan siempre. Opciones: Map background, Site max85 length (o 8% del radio), NR max85 length (o 8% del radio), LTE-NR Relations Current (by result) y LTE-NR Relations Missing; con las dos de relaciones desmarcadas no corre el audit. Run Audit genera reportes y MOPs; Graphical Audit, el mapa de un sitio. Exporta a XLSX (con la hoja Relations) o KMZ.
- **4. DB Status**: tablas cargadas por workdir y ENM, exportación a CSV y borrado de un workdir.

## 6. Archivos de salida

Por ENM, en `output/<workdir>/<ENM>_<YYYYMMDD>/`. Cada archivo termina en `_<ENM>_<YYYYMMDD>_<HHMMSS>`, así varias corridas del mismo día no se mezclan.

| Archivo | Contenido |
|---|---|
| `01_lte_nr_termpoint_audit_*.xlsx` | Source Summary, CURRENT, CREATE, DELETE, SET IP, REVIEW, TARGET TermPoint |
| `02_lte_nr_relation_audit_*.xlsx` | Summary, CURRENT, ADD, DELETE, PROTECTED, TARGET relation |
| `*_mop_cmedit_*.txt` / `*_rollback_cmedit_*.txt` | 4 MOP y 4 rollback (sección 7) |

- Las hojas **TARGET** muestran cómo queda la red si se aplican todos los cambios.
- Graphical Audit exporta a `output/<workdir>/graphical_audit/03_graphical_audit_<site>_*.xlsx|.kmz`.
- Logs: `log/lte_nr_<fecha>_<hora>.log` por sesión de la GUI y `log/run_<ENM>_*.log` por corrida.

## 7. Ejecución de los scripts

| STEP | MOP | Qué hace |
|---|---|---|
| 1 | `01_lte_nr_termpoint_create_mop_cmedit_*.txt` | crea ExtGNB + TermPoint, o corrige `ipAddress` (SET IP) |
| 2 | `02_lte_nr_relation_add_mop_cmedit_*.txt` | crea GUtranFreqRelation y ExternalGUtranCell si faltan, luego GUtranCellRelation |
| 3 | `02_lte_nr_relation_delete_mop_cmedit_*.txt` | borra relaciones, luego las ExternalGUtranCell sin uso |
| 4 | `01_lte_nr_termpoint_delete_mop_cmedit_*.txt` | borra TermPoints y ExtGNB sin relaciones |

- Los MOP se ejecutan en orden 1 -> 4. Así nunca se crea una relación antes que su TermPoint, ni se borra un TermPoint que todavía usan relaciones.
- Cada MOP tiene su rollback, y los rollbacks van en orden inverso (4 -> 1). Un MO borrado se recrea con los atributos leídos de ENM.

## 8. Antes de ejecutar en la red

- Validar los scripts en un cluster piloto y sobre el release de ENM en uso: los nombres de atributos de `cmedit` no se han verificado aún contra ese release.
- Un ADD que crea un `GUtranFreqRelation` supone que el `GUtranSyncSignalFrequency` de ese SSB ya existe en el nodo (el MOP lo indica en un comentario).
- Revisar las hojas REVIEW (falta EPT o propagación) y HOLD antes de ejecutar.
