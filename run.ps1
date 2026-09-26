# Builds tempo.exe (incrementally -- sqlite3.c only recompiles when it
# actually changes, which is essentially never) and launches the GUI.
# This is the VS-Code-independent equivalent of the "Build Tempo C++ (g++)"
# task in .vscode/tasks.json plus "py gui/main.py" -- run.bat is the
# double-click entry point that invokes this.

$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $PSScriptRoot

function Test-Newer($source, $target) {
    if (-not (Test-Path $target)) { return $true }
    if (-not (Test-Path $source)) { return $false }
    return (Get-Item $source).LastWriteTime -gt (Get-Item $target).LastWriteTime
}

try {
    if (Test-Newer "sqlite3.c" "sqlite3.o") {
        Write-Host "Compiling sqlite3.c (one-time, or after a vendored-library update)..."
        & gcc -O2 -c sqlite3.c -o sqlite3.o
        if ($LASTEXITCODE -ne 0) { throw "sqlite3.c compilation failed (exit $LASTEXITCODE)" }
    } else {
        Write-Host "sqlite3.o is up to date."
    }

    $headers = Get-ChildItem *.h -ErrorAction SilentlyContinue
    $needsBuild = Test-Newer "main.cpp" "tempo.exe"
    foreach ($h in $headers) {
        if (Test-Newer $h.FullName "tempo.exe") { $needsBuild = $true }
    }

    if ($needsBuild) {
        Write-Host "Building tempo.exe..."
        & g++ -std=c++17 -O2 -o tempo.exe main.cpp sqlite3.o
        if ($LASTEXITCODE -ne 0) { throw "tempo.exe build failed (exit $LASTEXITCODE)" }
    } else {
        Write-Host "tempo.exe is up to date."
    }
} catch {
    Write-Host ""
    Write-Host "Build failed: $_" -ForegroundColor Red
    exit 1
}

Write-Host "Launching Tempo..."
& py gui/main.py
exit $LASTEXITCODE
