# init.ps1 — Verificación de estado del harness antes de iniciar una sesión
# Uso: .\init.ps1
# Rechaza múltiples features in_progress y valida que el entorno esté listo.

$ErrorActionPreference = "Stop"
$claudeDir   = $PSScriptRoot                        # .claude/
$projectRoot = Split-Path $PSScriptRoot -Parent     # raíz del proyecto

Write-Host ""
Write-Host "=== DealerScrapper — Verificación de Harness ===" -ForegroundColor Cyan
Write-Host ""

$exitCode = 0

# ─── 1. Verificar feature_list.json ───────────────────────────────────────────
$featureListPath = Join-Path $claudeDir "feature_list.json"
if (-not (Test-Path $featureListPath)) {
    Write-Host "[ERROR] feature_list.json no encontrado." -ForegroundColor Red
    exit 1
}

$features = Get-Content $featureListPath | ConvertFrom-Json
$inProgress = $features | Where-Object { $_.status -eq "in_progress" }

if ($inProgress.Count -gt 1) {
    Write-Host "[ERROR] Múltiples features in_progress detectadas:" -ForegroundColor Red
    $inProgress | ForEach-Object { Write-Host "  - $($_.id): $($_.name)" -ForegroundColor Red }
    Write-Host "  Solo una feature puede estar in_progress a la vez." -ForegroundColor Red
    Write-Host "  Revisá feature_list.json y corregí el estado." -ForegroundColor Red
    $exitCode = 1
} elseif ($inProgress.Count -eq 1) {
    Write-Host "[OK] Feature activa: $($inProgress[0].id) — $($inProgress[0].name)" -ForegroundColor Green
} else {
    $nextPending = $features | Where-Object { $_.status -eq "pending" } | Select-Object -First 1
    if ($nextPending) {
        Write-Host "[INFO] Sin feature activa. Próxima pendiente: $($nextPending.id) — $($nextPending.name)" -ForegroundColor Yellow
    } else {
        Write-Host "[INFO] Todas las features completadas." -ForegroundColor Green
    }
}

Write-Host ""

# ─── 2. Verificar entorno virtual ─────────────────────────────────────────────
$venvPath = Join-Path $projectRoot ".venv\Scripts\python.exe"
if (Test-Path $venvPath) {
    Write-Host "[OK] Entorno virtual encontrado." -ForegroundColor Green
} else {
    Write-Host "[WARN] .venv no encontrado. Crear con: python -m venv .venv" -ForegroundColor Yellow
}

# ─── 3. Verificar requirements.txt ────────────────────────────────────────────
$reqPath = Join-Path $projectRoot "requirements.txt"
if (Test-Path $reqPath) {
    Write-Host "[OK] requirements.txt encontrado." -ForegroundColor Green
} else {
    Write-Host "[WARN] requirements.txt no encontrado." -ForegroundColor Yellow
}

# ─── 4. Verificar app importa sin errores (solo si venv existe) ───────────────
if (Test-Path $venvPath) {
    try {
        $result = & (Join-Path $projectRoot ".venv\Scripts\python.exe") -c "from app.main import app; print('OK')" 2>&1
        if ($result -match "OK") {
            Write-Host "[OK] app.main importa correctamente." -ForegroundColor Green
        } else {
            Write-Host "[WARN] app.main no importa aún (esperado si F01 no está implementado)." -ForegroundColor Yellow
            Write-Host "  $result" -ForegroundColor DarkGray
        }
    } catch {
        Write-Host "[WARN] Error verificando app.main: $_" -ForegroundColor Yellow
    }
}

# ─── 5. Correr tests si existen ───────────────────────────────────────────────
$testsPath = Join-Path $projectRoot "tests"
if (Test-Path $venvPath) {
    if (Test-Path $testsPath) {
        $testFiles = Get-ChildItem -Path $testsPath -Filter "test_*.py" -Recurse
        if ($testFiles.Count -gt 0) {
            Write-Host ""
            Write-Host "--- Corriendo tests ($($testFiles.Count) archivos) ---" -ForegroundColor Cyan
            & (Join-Path $projectRoot ".venv\Scripts\python.exe") -m pytest $testsPath -v --tb=short 2>&1 | Write-Host
            if ($LASTEXITCODE -ne 0) {
                Write-Host "[ERROR] Tests fallando. Corregir antes de continuar." -ForegroundColor Red
                $exitCode = 1
            } else {
                Write-Host "[OK] Todos los tests pasan." -ForegroundColor Green
            }
        } else {
            Write-Host "[INFO] No hay test files aún (esperado en estado inicial)." -ForegroundColor Yellow
        }
    }
}

# ─── 6. Verificar progress/ existe ────────────────────────────────────────────
$progressPath = Join-Path $claudeDir "progress"
if (Test-Path $progressPath) {
    Write-Host "[OK] Directorio progress/ existe." -ForegroundColor Green
} else {
    Write-Host "[WARN] Directorio progress/ no encontrado." -ForegroundColor Yellow
}

# ─── Resumen ──────────────────────────────────────────────────────────────────
Write-Host ""
if ($exitCode -eq 0) {
    Write-Host "=== Harness OK — listo para trabajar ===" -ForegroundColor Green
} else {
    Write-Host "=== Harness con errores — corregir antes de continuar ===" -ForegroundColor Red
}
Write-Host ""

exit $exitCode
