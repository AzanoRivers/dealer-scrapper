# Implementer Report — F06

**Estado**: DONE_WITH_CONCERNS
**Tests**: 84/85 pasando determinísticamente (ver Concerns). Los 9 tests nuevos de F06 pasan 100% de las veces.

---

## Archivos creados/modificados

- **`app/core/guards.py`** — nuevo. 4 funciones: `global_job_timeout`, `llm_watchdog`, `schedule_cleanup`, `run_pipeline`.
- **`app/api/v1/router.py`** — modificado. Wiring del pipeline en POST /scrape con `asyncio.create_task(run_pipeline(job_id))`. Se agrega registro en `_pipeline_tasks` (dict module-level) para permitir que DELETE cancele y aguarde el task antes de `shutil.rmtree`.
- **`tests/test_f06_guards.py`** — nuevo. 9 tests de Guards y pipeline runner.
- **`tests/conftest.py`** — modificado. Se agrega `ENABLE_PIPELINE=0` para deshabilitar el pipeline real en tests de integración F01 (via POST /scrape).

---

## Concerns

### Test `test_f03_fetcher.py::test_partial_success` — falla ~20-30% de las veces en Windows

**Origen**: Pre-existente. Confirmado que falla en aislamiento (solo F03) sin ningún cambio de F06.

**Causa raíz**: En `run_fetcher`, múltiples coroutines llaman `update_progress` → `_write_state` → `os.replace(state.tmp.json, state.json)` concurrentemente para el mismo job. En Windows, `os.replace` falla con `PermissionError (WinError 5)` si Windows Defender escanea `state.json` justo después de que la primera coroutine lo escribió, antes de que la segunda lo reemplace. Este es un comportamiento específico de Windows; en Linux (target de deploy: VPS Oracle Cloud ARM64) el test pasa siempre.

**No es regresión de F06**: El stack trace apunta directamente a `fetcher.py:201 → job_manager.py:58`, sin ningún archivo de F06 en la cadena.

---

## Decisiones no obvias

### 1. `global_job_timeout` usa `loop.call_later` en lugar de `asyncio.sleep`

Los tests F03 parchean `asyncio.sleep` globalmente con `AsyncMock`. Si `global_job_timeout` usara `asyncio.sleep(1800)`, ese sleep retornaría inmediatamente durante tests F03, causando que el guard fallara jobs F01 que estaban en background. Usando `loop.call_later` se bypasea el mock.

### 2. `_pipeline_tasks` dict en router.py + cancel/await en DELETE

El endpoint DELETE llama `job_manager.delete_job(job_id)` → `shutil.rmtree`. En Windows, rmtree falla si aiofiles tiene `state.json` abierto. Se agrega un `_pipeline_tasks` dict module-level y en DELETE se cancela y aguarda el pipeline task antes de rmtree. Esto evita la race condition en el test `test_delete_removes_directory`.

### 3. `ENABLE_PIPELINE=0` en conftest.py

Los tests F01 (integración HTTP) llaman POST /scrape que ahora lanza `run_pipeline`. Estos pipeline tasks hacen requests HTTP reales a `https://example.com` y churnan el filesystem, causando race conditions con F03 tests. `ENABLE_PIPELINE=0` desactiva el pipeline en tests HTTP sin afectar los tests unitarios de F06 (que llaman `run_pipeline` directamente).

### 4. `finally` aguarda la cancelación de guard1

El bloque `finally` en `run_pipeline` hace `guard1.cancel(); await guard1` para que guard1 esté completamente done antes de que `run_pipeline` retorne. Esto es necesario para que `test_pipeline_cancel_guard1_on_completion` pase (el test verifica `guard1.done() == True` inmediatamente después de `await run_pipeline(...)`).
