# Construye desde cualquier directorio de trabajo; no instala dependencias.
param([string]$OutputDirectory = 'dist/final', [string]$WorkDirectory = 'build/final')
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$builderPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $builderPython)) {
    throw 'Falta .venv. Sigue los pasos de instalación del README.'
}
& $builderPython -c "import shutil,sys,struct,PyInstaller,tkinter; tkinter.Tcl().eval('info patchlevel'); assert struct.calcsize('P') == 8; assert PyInstaller.__version__ == '6.22.3'; sys.exit(0 if shutil.disk_usage('.').free >= 2*1024**3 else 1)"
if ($LASTEXITCODE -ne 0) { throw 'Comprueba Python de 64 bits, PyInstaller 6.22.3 y al menos 2 GiB libres y Tcl/Tk accesible.' }
& $builderPython -B tests/run_simulated.py
if ($LASTEXITCODE -ne 0) { throw 'Pruebas fallidas: construcción cancelada.' }
# No sobrescribir una distribución existente de manera silenciosa.
if (Test-Path -LiteralPath (Join-Path $OutputDirectory 'LimpiadorDeDescargas')) {
    throw 'Ya existe esa distribución. Conserva esa versión y utiliza -OutputDirectory y -WorkDirectory distintos.'
}
& $builderPython -m PyInstaller --distpath $OutputDirectory --workpath $WorkDirectory LimpiadorDeDescargas.spec
if ($LASTEXITCODE -ne 0) { throw 'PyInstaller no pudo completar la construcción.' }
Write-Host ('Paquete disponible en ' + (Join-Path $OutputDirectory 'LimpiadorDeDescargas') + '. Distribuye la carpeta completa.')
