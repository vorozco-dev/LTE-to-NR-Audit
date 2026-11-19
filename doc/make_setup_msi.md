# Cómo generar una nueva versión del instalador `.msi` y del `.exe`

Guía para producir con PyInstaller y WiX Toolset, en `dist\`:

- `LteNrAuditEricsson_Setup_v<versión>.msi`: instalador por usuario.
- `LteNrAuditEricsson_v<versión>.exe`: ejecutable único, sin instalación.

Todos los comandos se ejecutan en PowerShell, desde la carpeta raíz del proyecto.

En resumen, una versión nueva son tres pasos: cambiar la versión en el código, ejecutar
`build_tools\build_msi.ps1` y probar el instalador.

## Archivos que intervienen

| Archivo | Función |
|---|---|
| `build_tools\build_msi.ps1` | Ejecuta PyInstaller y WiX y genera el `.msi` y el `.exe`; es lo único que hay que correr |
| `build_tools\installer\Package.wxs` | Definición del instalador (carpeta de destino, accesos directos, actualización) |
| `build_tools\rthook_root.py` | Hace que `db\`, `output\` y `log\` se creen junto al `.exe` (fija `ROOT_DIRECTORY`) |
| `code\lne_config.py` | Lee `ROOT_DIRECTORY` de `code\.env` o, si no está, de la variable de entorno que fija el hook |
| `code\lne_run.py` | `VERSION`: número de versión de la app y, por defecto, del `.msi` |
| `conf\freq_band.csv` | Tabla ARFCN → banda; el script la copia junto al `.exe` del instalador y la mete dentro del `.exe` único |
| `code\requirements.txt` | Paquetes de Python que se empaquetan |

## 1. Preparar el equipo (una sola vez)

Hace falta Python 3.11, el SDK de .NET 8 y WiX Toolset 5.0.2.

```powershell
# Entorno virtual con las dependencias de la app y PyInstaller
py -3.11 -m venv venv
venv\Scripts\python -m pip install -r code\requirements.txt pyinstaller

# SDK de .NET (pide permiso de administrador) y WiX
winget install Microsoft.DotNet.SDK.8
dotnet tool install --global wix --version 5.0.2
```

Después de instalar el SDK de .NET hay que cerrar y volver a abrir la terminal.

Para comprobar que todo quedó instalado:

```powershell
venv\Scripts\python -m PyInstaller --version
wix --version
```

PyInstaller debe instalarse dentro de `venv`, no en el Python global: el script usa
`venv\Scripts\python.exe` y solo empaqueta lo que hay en ese entorno. Si se agrega un
paquete nuevo al código, debe ir también en `code\requirements.txt` e instalarse en `venv`.

## 2. Cambiar la versión en el código

En `code\lne_run.py`:

```python
VERSION = "2.1.0"
```

En `code\tk_lte_nr_ericsson_app.py`, al inicio del archivo:

```python
EXPIRY_DATE = date(2027, 5, 19)
```

- `VERSION` es lo que se ve en el título de la ventana y en el encabezado (`v2.1.0`).
- `EXPIRY_DATE` es la fecha de vencimiento de la evaluación. Desde ese día la app muestra
  "Por favor contactar a Soporte OTLKM" y no abre. El texto "Evaluation - Expires …" del
  encabezado se actualiza solo a partir de esta fecha.

## 3. Generar el instalador

Cierra la aplicación si está abierta y ejecuta:

```powershell
pwsh build_tools\build_msi.ps1
```

Sin parámetros, el script toma la versión de `VERSION` en `code\lne_run.py`. También se
puede indicar (`-Version 2.1.1`), pero debe coincidir con `VERSION`; si no, el script se detiene.

Tarda unos cinco minutos. El script hace tres cosas:

1. PyInstaller empaqueta la app en modo carpeta en `build_tools\dist_onedir\LteNrAuditEricsson\`.
2. WiX comprime esa carpeta en `dist\LteNrAuditEricsson_Setup_v2.1.0.msi`.
3. PyInstaller genera el ejecutable único `dist\LteNrAuditEricsson_v2.1.0.exe`.

Al terminar muestra la ruta y el tamaño de cada archivo: el `.msi` pesa unos 62 MB, con cerca
de 2,050 archivos empaquetados, y el `.exe`, unos 83 MB.

Para generar solo el `.msi`, más rápido, agrega `-SkipExe`:

```powershell
pwsh build_tools\build_msi.ps1 -SkipExe
```

Reglas para el número de versión:

- Debe tener tres partes numéricas: `2.1.0`, sin la `v`. El script lo verifica.
- Debe ser mayor que la versión anterior. Si se reutiliza el mismo número, Windows no
  reemplaza la instalación existente y quedan dos entradas en "Aplicaciones instaladas".

Estos avisos son normales y no afectan el resultado:

- `Datas for pyproj not found at: ...\venv\Library\share\proj`. Los datos de proj se
  empaquetan desde el propio paquete `pyproj` (`_internal\pyproj\proj_dir\share\proj\proj.db`).
- `Hidden import "scipy.special._cdflib" not found!`. El módulo no existe en la versión de scipy
  instalada y la app no lo usa.

## 4. Probar antes de repartir

**La app empaquetada.** Abre `build_tools\dist_onedir\LteNrAuditEricsson\LteNrAuditEricsson.exe`
y comprueba:

- La versión y la fecha de vencimiento en el encabezado.
- Que `log\` (y `db\` después de Process) se crean junto al `.exe`.
- Load Data: Search / Load files y Process con una carpeta de entrada real (EPT.xlsx,
  propagación e4g/e5g y los exports ENM, sueltos o en `.zip`).
- Run Audit: X2 TermPoint y Neighbor Relation, con la exportación a Excel.
- Neighbor Fan: un sitio con el mapa de fondo y la exportación a Excel y KMZ.
- DB Status: listado de workdirs y exportación a CSV.

Los fallos de empaquetado (un módulo o un archivo que falta) solo aparecen al usar cada
función, no al abrir la ventana. Esta prueba crea `log\` y `db\` dentro de
`dist_onedir\LteNrAuditEricsson\`; el script las borra en la siguiente construcción para que
no entren en el instalador.

**El instalador.** Haz doble clic en el `.msi` y comprueba:

- Se instala sin pedir administrador en `%LocalAppData%\LteNrAuditEricsson`.
- Aparecen los accesos directos "LTE to NR Audit (Ericsson)" en el menú Inicio y en el Escritorio.
- Si había una versión anterior instalada, queda una sola entrada en "Aplicaciones instaladas"
  y la carpeta `db\` conserva sus datos.
- Otras apps OTLKM instaladas (por ejemplo, Propagation Vector) siguen instaladas.

**El ejecutable único.** Copia `dist\LteNrAuditEricsson_v2.1.0.exe` a una carpeta vacía y ábrelo
desde ahí: crea `db\`, `output\` y `log\` en esa misma carpeta, así que no lo pruebes dentro de
`dist\`. Tarda unos 30 segundos en mostrar la ventana, porque en cada arranque descomprime
todo su contenido en una carpeta temporal; la primera vez puede tardar más mientras el antivirus
lo revisa. Basta con comprobar que abre y muestra la versión y la fecha de vencimiento: el código
empaquetado es el mismo que el de la app del instalador.

## 5. Calcular el SHA-256

Publica el hash junto con cada archivo para que quien lo reciba pueda verificar que no se
alteró:

```powershell
Get-FileHash dist\LteNrAuditEricsson_Setup_v2.1.0.msi -Algorithm SHA256
Get-FileHash dist\LteNrAuditEricsson_v2.1.0.exe -Algorithm SHA256
```

El hash cambia en cada construcción, aunque el código sea el mismo. Calcúlalo sobre el
archivo exacto que vas a repartir.

## Qué hace el ejecutable único

- **Sin instalación:** se copia a cualquier carpeta en la que el usuario pueda escribir y se abre
  con doble clic. No crea accesos directos ni aparece en "Aplicaciones instaladas".
- **Datos:** crea `db\`, `output\` y `log\` junto al `.exe`. Para actualizar, basta con reemplazar
  el `.exe`; esas carpetas se conservan.
- **Configuración:** lleva `conf\freq_band.csv` dentro. Si hay una carpeta `conf\` junto al `.exe`,
  usa esa copia en su lugar, lo que permite cambiar la tabla sin reconstruir.
- **Arranque:** más lento que la app instalada (unos 30 segundos), porque se descomprime en
  `%TEMP%` cada vez que se abre.
- **Firma:** tampoco está firmado; Windows SmartScreen puede mostrar una advertencia al abrirlo.

## 6. Guardar en git

Se versionan el código, `doc\` y los archivos de `build_tools\`. No se versionan `venv\`,
`build\`, `dist\`, `build_tools\dist_onedir\`, los `.msi` ni el `.exe`; ya están en el `.gitignore`.

```powershell
git add code doc build_tools .gitignore
git commit -m "Version 2.1.0"
git tag v2.1.0
```

## Qué hace el instalador

- **Destino:** `%LocalAppData%\LteNrAuditEricsson`, por usuario y sin permisos de administrador.
- **Datos:** la app crea `db\` (base SQLite y caché de mapas en `db\tiles`), `output\` y `log\`
  en esa misma carpeta. El instalador no las crea, así que se conservan al actualizar y al desinstalar.
- **Entrada:** la carpeta de entrada (EPT, propagación, exports ENM) se elige en la pestaña
  Load Data; no forma parte del instalador.
- **Configuración:** `conf\freq_band.csv` se instala con la app y se reemplaza al actualizar;
  un cambio local a ese archivo se pierde con la siguiente versión. La app instalada no usa
  `code\.env`.
- **Base de datos inicial:** vacía. Para llevar workdirs ya cargados, copia la carpeta `db\`
  a `%LocalAppData%\LteNrAuditEricsson\`.
- **Actualización:** una versión mayor reemplaza a la anterior.
- **Firma:** el `.msi` no está firmado; Windows muestra la advertencia de editor desconocido.

## Lo que no se debe cambiar

- **`UpgradeCode` en `Package.wxs`** (`A60B6691-3F8F-4438-99BA-3B6A49AEE439`). Es el identificador
  que permite que una versión nueva reemplace a la anterior. Si cambia, las versiones nuevas se
  instalan al lado de las viejas. Es exclusivo de esta app: no debe copiarse a otro instalador
  ni tomarse de otro (Propagation Vector tiene el suyo), porque Windows trataría las dos apps
  como el mismo producto y una desinstalaría a la otra.
- **El `Guid` del componente `Shortcuts`**, por la misma razón.
- **La carpeta de destino**, salvo que también se cambie dónde guarda los datos la app.
  En `C:\Program Files` un usuario sin administrador no puede escribir y la app no podría
  crear la base.

## Problemas frecuentes

| Síntoma | Causa y solución |
|---|---|
| `No se encontró ...\venv\Scripts\python.exe` | Falta el entorno virtual. Repite el paso 1. |
| `No se encontró ...\.dotnet\tools\wix.exe` | WiX no está instalado para tu usuario. Repite el paso 1 y abre una terminal nueva. |
| `No module named PyInstaller` | PyInstaller quedó en el Python global. Instálalo con `venv\Scripts\python -m pip install pyinstaller`. |
| `La versión ... no coincide con VERSION` | El `-Version` indicado es distinto de `VERSION` en `code\lne_run.py`. Cambia uno de los dos u omite `-Version`. |
| Error de acceso denegado al construir | La app empaquetada (o el `.exe` de `dist\`) está abierta. Ciérrala y vuelve a ejecutar el script. |
| El `.exe` único no muestra nada durante un rato | Se está descomprimiendo; espera unos 30 segundos. Si nunca abre, revisa si el antivirus lo bloqueó. |
| La app instalada no abre y no muestra nada | El ejecutable no tiene consola. Ejecuta el comando de PyInstaller del script sin `--windowed` y abre el `.exe` desde una terminal para ver el error. |
| No lee exports `.rar` o `.7z` | La app usa 7-Zip para esos formatos y no viene en el instalador. Instala 7-Zip en el equipo o entrega los exports sueltos o en `.zip`. |
| Quedan dos entradas tras instalar | Se reutilizó el número de versión. Desinstala ambas, sube la versión y reconstruye. |
| `wix msi validate` reporta ICE38, ICE64 e ICE91 | Es lo esperado en una instalación por usuario dentro del perfil. No impide construir ni instalar. |

## Notas

- **Línea de comandos.** El instalador incluye solo la aplicación gráfica. `lne_run.py`,
  `lne_load.py` y `ept_process.py` siguen ejecutándose desde el código con Python.
- **Licencia de evaluación.** Depende solo del reloj de Windows; si se atrasa la fecha del
  sistema, la app vuelve a abrir.
- **Licencia de WiX.** Se usa la versión 5.0.2. Las versiones recientes de WiX piden una cuota
  de mantenimiento a las organizaciones con ingresos que usen los binarios oficiales; revisa
  las condiciones en wixtoolset.org antes de actualizar o de repartir a clientes.
