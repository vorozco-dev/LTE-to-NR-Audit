# Genera dist\LteNrAuditEricsson_Setup_v<Version>.msi y dist\LteNrAuditEricsson_v<Version>.exe
#   1) PyInstaller (modo carpeta)  -> build_tools\dist_onedir\LteNrAuditEricsson\
#   2) WiX Toolset 5               -> dist\LteNrAuditEricsson_Setup_v<Version>.msi
#   3) PyInstaller (archivo único) -> dist\LteNrAuditEricsson_v<Version>.exe  (se omite con -SkipExe)
#
# Requisitos (una sola vez):
#   py -3.11 -m venv venv
#   venv\Scripts\python -m pip install -r code\requirements.txt pyinstaller
#   winget install Microsoft.DotNet.SDK.8
#   dotnet tool install --global wix --version 5.0.2
#
# Uso, desde cualquier carpeta:
#   pwsh build_tools\build_msi.ps1                  # versión leída de VERSION en code\lne_run.py
#   pwsh build_tools\build_msi.ps1 -Version 2.1.1   # versión explícita (debe coincidir con VERSION)
#   pwsh build_tools\build_msi.ps1 -SkipExe         # solo el .msi, sin el .exe de archivo único
param([string]$Version = "", [switch]$SkipExe)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$name = "LteNrAuditEricsson"
$python = Join-Path $root "venv\Scripts\python.exe"
$wix = Join-Path $env:USERPROFILE ".dotnet\tools\wix.exe"
$appDir = Join-Path $root "build_tools\dist_onedir\$name"

# Versión de la app: VERSION = "x.y.z" en code\lne_run.py (es la que se ve en el encabezado)
$runPy = Join-Path $root "code\lne_run.py"
$match = Select-String -Path $runPy -Pattern '^VERSION\s*=\s*"([^"]+)"' | Select-Object -First 1
if (-not $match) { throw "No se encontró VERSION en $runPy." }
$appVersion = $match.Matches[0].Groups[1].Value
if (-not $Version) { $Version = $appVersion }
if ($Version -notmatch '^\d+\.\d+\.\d+$') { throw "La versión '$Version' debe tener tres partes numéricas, por ejemplo 2.1.0." }
if ($Version -ne $appVersion) { throw "La versión $Version no coincide con VERSION = `"$appVersion`" en code\lne_run.py." }
$msi = Join-Path $root "dist\${name}_Setup_v$Version.msi"
$exe = Join-Path $root "dist\${name}_v$Version.exe"

foreach ($tool in $python, $wix) {
    if (-not (Test-Path $tool)) { throw "No se encontró $tool (ver requisitos al inicio de este archivo)." }
}

Write-Host "== 1/3 PyInstaller (modo carpeta), versión $Version =="
& $python -m PyInstaller --noconfirm --clean --windowed --onedir `
    --name $name `
    --icon (Join-Path $root "code\icon\app.ico") `
    --paths (Join-Path $root "code") `
    --add-data ((Join-Path $root "code\icon") + ";icon") `
    --runtime-hook (Join-Path $root "build_tools\rthook_root.py") `
    --distpath (Join-Path $root "build_tools\dist_onedir") `
    --workpath (Join-Path $root "build\onedir") `
    --specpath (Join-Path $root "build\onedir_spec") `
    --log-level WARN `
    (Join-Path $root "code\tk_lte_nr_ericsson_app.py")
if ($LASTEXITCODE -ne 0) { throw "PyInstaller falló (código $LASTEXITCODE)." }

# Si se abrió la app desde esa carpeta para probarla, sus datos no deben ir dentro del instalador
foreach ($data in "db", "output", "log", "input") {
    $path = Join-Path $appDir $data
    if (Test-Path $path) { Remove-Item $path -Recurse -Force -Confirm:$false }
}

# conf\freq_band.csv se lee desde <raíz>\conf; en la app empaquetada la raíz es la carpeta del .exe
Copy-Item (Join-Path $root "conf") (Join-Path $appDir "conf") -Recurse -Force

Write-Host "== 2/3 WiX =="
New-Item -ItemType Directory -Force (Split-Path -Parent $msi) | Out-Null
& $wix build (Join-Path $root "build_tools\installer\Package.wxs") -arch x64 `
    -d "AppDir=$appDir" `
    -d ("AppIcon=" + (Join-Path $root "code\icon\app.ico")) `
    -d "Version=$Version" `
    -pdbtype none `
    -intermediatefolder (Join-Path $root "build\wix") `
    -o $msi
if ($LASTEXITCODE -ne 0) { throw "WiX falló (código $LASTEXITCODE)." }

$files = Get-ChildItem $appDir -Recurse -File
"{0}  ({1:N1} MB, {2} archivos empaquetados)" -f $msi, ((Get-Item $msi).Length / 1MB), $files.Count

if ($SkipExe) { return }

# Ejecutable único, sin instalador: lleva icon\ y conf\ dentro; db\, output\ y log\ se crean junto al .exe
Write-Host "== 3/3 PyInstaller (archivo único) =="
& $python -m PyInstaller --noconfirm --clean --windowed --onefile `
    --name $name `
    --icon (Join-Path $root "code\icon\app.ico") `
    --paths (Join-Path $root "code") `
    --add-data ((Join-Path $root "code\icon") + ";icon") `
    --add-data ((Join-Path $root "conf") + ";conf") `
    --runtime-hook (Join-Path $root "build_tools\rthook_root.py") `
    --distpath (Join-Path $root "build\onefile_dist") `
    --workpath (Join-Path $root "build\onefile") `
    --specpath (Join-Path $root "build\onefile_spec") `
    --log-level WARN `
    (Join-Path $root "code\tk_lte_nr_ericsson_app.py")
if ($LASTEXITCODE -ne 0) { throw "PyInstaller (archivo único) falló (código $LASTEXITCODE)." }
Copy-Item (Join-Path $root "build\onefile_dist\$name.exe") $exe -Force
"{0}  ({1:N1} MB)" -f $exe, ((Get-Item $exe).Length / 1MB)
