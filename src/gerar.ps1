param([string]$Python = "python", [string]$InnoCompiler = "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe")
$ErrorActionPreference = "Stop"
Push-Location $PSScriptRoot
try {
    & $Python -m pip install -r requirements-build.txt
    if ($LASTEXITCODE -ne 0) { throw "Falha ao instalar dependências" }
    & $Python -m PyInstaller --noconfirm --onefile --windowed --name SincronizadorRede --icon app.ico --add-data "sincronizador.bat;." --add-data "config.ini;." --exclude-module numpy --distpath .. --workpath build --specpath . app.py
    if ($LASTEXITCODE -ne 0) { throw "Falha ao gerar EXE" }
    & $InnoCompiler instalador.iss
    if ($LASTEXITCODE -ne 0) { throw "Falha ao gerar instalador" }
} finally { Pop-Location }

