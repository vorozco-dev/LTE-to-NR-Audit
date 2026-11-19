# Extracción de datos ENM por CLI — LTE to NR Audit (Ericsson)

Lista definitiva de comandos `cmedit` que necesita la herramienta (`code/lne_run.py`).
Se reemplazan las tablas `e_cm_*` de WDNA.

## 1. ENM a extraer

Hay que repetir la extracción completa en **los 3 ENM**:

| ENM (sufijo) | Plataforma | GUI | IP |
|---|---|---|---|
| `tul` | ENM219 | ENM_ERICSSON_3 GUI | 10.111.21.9 |
| `ptl` | ENM14 | ENM_ERICSSON GUI | 10.150.131.14 |
| `tlt` | ENM9 | ENM_ERICSSON_2 GUI | 10.150.216.9 |

Los ENIQ (ENIQ23, ENIQ61, ENIQ57) no se usan.

## 2. Procedimiento (por cada ENM)

1. Entrar a la GUI del ENM y abrir **Command Line Interface**.
2. Ejecutar cada comando de la sección 3, **uno por uno y en una sola línea** (si la línea se corta al pegarla, ENM responde `Error 4004`).
3. Guardar la salida **completa** como texto con el nombre de la sección 4. No editar el archivo:
   las líneas iniciales, el encabezado y el pie `N instance(s)` (o el `Error NNNN`) los usa la herramienta para validar que la lectura está completa.
4. Al terminar los 3 ENM, copiar todos los archivos a la misma carpeta de entrada
   (`INPUT_DIR` en `code/.env`, hoy `input/11172026`), junto con `EPT.xlsx` y las tablas de propagación
   de la semana (`propagation_tables_wWW_YYYY.csv.zip`; se usan `propagation_table_e4g` y `e5g`).
5. Extraer los 3 ENM el mismo día: la herramienta cruza datos entre ellos (vecinos y X2 hacia gNB de otro ENM).

## 3. Comandos

| # | Tabla (MO) | Comando |
|---|---|---|
| 1 | `EUtranCellFDD` | `cmedit get * EUtranCellFDD.(earfcndl,freqBand,cellId,physicalLayerCellId,tac,administrativeState,operationalState) -t` |
| 2 | `GUtranCellRelation` | `cmedit get * GUtranCellRelation.(neighborCellRef,isRemoveAllowed,isEndcAllowed,isHoAllowed,isVoiceHoAllowed,coverageIndicator,createdBy,mobilityStatusNR,essEnabled,reservedBy,timeOfCreation) -t` |
| 3 | `GUtranFreqRelation` | `cmedit get * GUtranFreqRelation.(gUtranSyncSignalFrequencyRef,cellReselectionPriority) -t` |
| 4 | `GUtranSyncSignalFrequency` | `cmedit get * GUtranSyncSignalFrequency.(arfcn,band,smtcPeriodicity,smtcOffset,smtcDuration) -t` |
| 5 | `ExternalGNodeBFunction` | `cmedit get * ExternalGNodeBFunction.(gNodeBId,gNodeBIdLength,gNodeBPlmnId,createdBy) -t` |
| 6 | `ExternalGUtranCell` | `cmedit get * ExternalGUtranCell.(localCellId,nRPCI,nRTAC,configuredTAC,gUtranSyncSignalFrequencyRef,bandList,cellState,serviceStatus,isRemoveAllowed,createdBy,reservedBy) -t` |
| 7 | `TermPointToGNB` | `cmedit get * TermPointToGNB.(ipAddress,usedIpAddress,ipv6Address,domainName,administrativeState,operationalState,availabilityStatus,createdBy) -t` |
| 8 | `NRCellDU` | `cmedit get * NRCellDU.(nCI,cellLocalId,nRPCI,nRTAC,configuredEpsTAC,ssbFrequency,ssbSubCarrierSpacing,bandList,administrativeState,operationalState,cellState,serviceState) -t` |
| 9 | `GNBCUCPFunction` | `cmedit get * GNBCUCPFunction.(gNBId,gNBIdLength,pLMNId) -t` |
| 10 | `LocalSctpEndpoint` | `cmedit get * LocalSctpEndpoint.(interfaceUsed,sctpEndpointRef) -t` |
| 11 | `SctpEndpoint` | `cmedit get * SctpEndpoint.(localIpAddress,portNumber) -t` |
| 12 | `AddressIPv4` | `cmedit get * AddressIPv4.(address) -t` |

Para copiar y pegar:

```
cmedit get * EUtranCellFDD.(earfcndl,freqBand,cellId,physicalLayerCellId,tac,administrativeState,operationalState) -t
cmedit get * GUtranCellRelation.(neighborCellRef,isRemoveAllowed,isEndcAllowed,isHoAllowed,isVoiceHoAllowed,coverageIndicator,createdBy,mobilityStatusNR,essEnabled,reservedBy,timeOfCreation) -t
cmedit get * GUtranFreqRelation.(gUtranSyncSignalFrequencyRef,cellReselectionPriority) -t
cmedit get * GUtranSyncSignalFrequency.(arfcn,band,smtcPeriodicity,smtcOffset,smtcDuration) -t
cmedit get * ExternalGNodeBFunction.(gNodeBId,gNodeBIdLength,gNodeBPlmnId,createdBy) -t
cmedit get * ExternalGUtranCell.(localCellId,nRPCI,nRTAC,configuredTAC,gUtranSyncSignalFrequencyRef,bandList,cellState,serviceStatus,isRemoveAllowed,createdBy,reservedBy) -t
cmedit get * TermPointToGNB.(ipAddress,usedIpAddress,ipv6Address,domainName,administrativeState,operationalState,availabilityStatus,createdBy) -t
cmedit get * NRCellDU.(nCI,cellLocalId,nRPCI,nRTAC,configuredEpsTAC,ssbFrequency,ssbSubCarrierSpacing,bandList,administrativeState,operationalState,cellState,serviceState) -t
cmedit get * GNBCUCPFunction.(gNBId,gNBIdLength,pLMNId) -t
cmedit get * LocalSctpEndpoint.(interfaceUsed,sctpEndpointRef) -t
cmedit get * SctpEndpoint.(localIpAddress,portNumber) -t
cmedit get * AddressIPv4.(address) -t
```

Notas:
- En `ExternalGNodeBFunction` y `TermPointToGNB` no se piden `timeOfCreation` ni `upIpAddress`: con ellos algunos nodos de software más antiguo devolvieron `Error 5010`.
- Si un atributo no existe en la versión de nodo (`Error 1010 ... unknown attribute`), quitarlo del comando y avisar; se valida con AMOS (`get <MO>`).

## 4. Regla de nombrado

```
<tabla>_<enm>.txt
```

- `<tabla>`: nombre exacto del MO de la columna "Tabla (MO)", respetando mayúsculas.
- `<enm>`: `tul`, `ptl` o `tlt`, en minúsculas.

Ejemplos:

```
EUtranCellFDD_tul.txt      EUtranCellFDD_ptl.txt      EUtranCellFDD_tlt.txt
GUtranCellRelation_tul.txt GUtranCellRelation_ptl.txt GUtranCellRelation_tlt.txt
TermPointToGNB_tul.txt     TermPointToGNB_ptl.txt     TermPointToGNB_tlt.txt
```

Total esperado por extracción completa: 13 archivos × 3 ENM = **39 archivos**.

**Re-extracción parcial:** si un comando falla para algunos nodos, se puede repetir solo para ellos
(`cmedit get NODO1;NODO2 <MO>.(...) -t`) y guardarlo como `<tabla>_<enm>_fix.txt`
(ej. `TermPointToGNB_tul_fix.txt`). La herramienta junta automáticamente el archivo principal con los `_fix`.

## 5. Revisión rápida antes de correr la herramienta

- Cada archivo termina en `N instance(s)`. Si termina en `Error NNNN`, la herramienta usa las filas
  que sí llegaron y marca los nodos afectados como **datos incompletos** (no propone cambios sobre ellos).
- Un archivo de pocos bytes con solo un `Error` significa que el comando falló completo: corregir y repetir.
- La hoja **Input files** del reporte muestra, por tabla, filas leídas, filas esperadas y estado.
