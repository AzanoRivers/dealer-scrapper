# Reviewer Report — F06

**Veredicto**: APPROVED

---

## Checkpoints

- [x] Guard 1 falla con JOB_TIMEOUT — `global_job_timeout` usa `loop.call_later` + Future, al disparar llama `fail_job(..., "JOB_TIMEOUT", ..., retry_after=600)`. `test_guard1_fires_on_timeout` pasa.
- [x] Guard 2 falla con LLM_TIMEOUT — `llm_watchdog` usa `asyncio.wait_for(event.wait(), timeout=...)` y en `TimeoutError` llama `fail_job(..., "LLM_TIMEOUT", ..., retry_after=300)`. `test_guard2_fires_on_inactivity` pasa.
- [x] Guard 2 solo en "analyzing" (exportado, no invocado en run_pipeline) — El docstring explica "NOTE: This function is NOT launched by run_pipeline. It is exported here for F07 (Reviewer) to import and launch." Verificado: `run_pipeline` no llama `llm_watchdog`. Correcto.
- [x] Guard 3 exportado para F08, no invocado en run_pipeline — `schedule_cleanup` tiene el mismo patrón de docstring. `run_pipeline` no la llama. El CHECKPOINTS.md y el plan maestro (Parte 6.4, 7.6) confirman que Guard 3 debe ser lanzado por el Packager en F08.
- [x] schedule_cleanup elimina directorio — `shutil.rmtree(job_dir, ignore_errors=True)` tras `asyncio.sleep(RESULT_TTL_MINUTES * 60)`. `test_schedule_cleanup_deletes_dir` pasa. `test_schedule_cleanup_noop_if_dir_missing` pasa (ignore_errors protege el caso sin directorio).
- [x] Reviewer detecta state.status == "failed" y aborta — El patrón está disponible en dos lugares: (a) `_check_still_running` usado entre cada fase de `run_pipeline`; (b) `llm_watchdog` chequea `state.status in (done, failed)` después de cada `event.set()` antes de continuar el loop. El checkpoint de CHECKPOINTS.md dice "Reviewer detecta state.status == 'failed' y aborta sin error adicional" — en el contexto de F06 esto se refiere al patrón disponible para F07, no a un subagente Reviewer ya implementado. El patrón está correctamente implementado en `llm_watchdog` (retorna limpiamente si job ya terminó) y en `_check_still_running`. Correcto.
- [x] DELETE cancela tasks — `router.py` cancela y awaita el `_pipeline_tasks[job_id]` antes de llamar `job_manager.delete_job`. `delete_job` a su vez llama `_cancel_job_tasks` que cancela todas las tasks registradas vía `register_task`. `test_delete_removes_directory` (F01) pasa con ENABLE_PIPELINE=0.

## Tests

- 84/85 pasan. Los 9 tests nuevos de F06 pasan 100%.
- 1 fallo (`test_f03_fetcher.py::test_partial_success`) es pre-existente: Windows-specific race condition en `os.replace` en `_write_state`, stack trace confirma que no hay ningún archivo de F06 en la cadena. No es regresión.

---

## Concerns aceptados / rechazados

### 1. `ENABLE_PIPELINE=0` en conftest.py — ACEPTADO

La solución es limpia y correcta. `os.getenv("ENABLE_PIPELINE", "1") != "0"` en el endpoint POST es un mecanismo de escape de test estándar (equivalente a un feature flag de test). Los tests F06 que validan `run_pipeline` lo llaman directamente con mocks de subagentes, por lo que no están afectados. No introduce riesgo en producción porque el env var no aparece en `.env.example` y el default es `"1"`. La alternativa (mockear el módulo de pipeline completo en todos los tests F01) hubiera sido más frágil.

### 2. `_pipeline_tasks` en router.py — ACEPTADO CON NOTA

No duplica `job_manager._job_tasks`; son complementarios con responsabilidades distintas:
- `job_manager._job_tasks`: lista de Guards (Guard 1, Guard 2 en F07, Guard 3 en F08) registrados para cancelación masiva en DELETE o cancelación interna del pipeline.
- `_pipeline_tasks`: referencia al Task de `run_pipeline` mismo (el contenedor), necesaria para que DELETE pueda `await` la cancelación completa antes de `shutil.rmtree`.

La razón técnica es válida: en Windows, `shutil.rmtree` falla si `aiofiles` tiene un handle abierto. Cancelar y awaitar el pipeline task antes del rmtree resuelve la race condition. En Linux (target de producción) esta protección es redundante pero inofensiva. La consistencia es correcta: DELETE cancela el pipeline task vía `_pipeline_tasks` Y los guards vía `job_manager._cancel_job_tasks`.

Nota para F07/F08: cuando se agreguen más tasks registradas (Guard 2, Guard 3), el patrón de DELETE sigue siendo correcto porque `_cancel_job_tasks` las cubre. No se requiere cambio en router.py.

### 3. `loop.call_later` en Guard 1 — ACEPTADO

La razón técnica es correcta y necesaria. Tests de F03 parchean `asyncio.sleep` con `AsyncMock` globalmente; si Guard 1 usara `asyncio.sleep(1800)`, ese sleep retornaría inmediatamente durante los tests F03 y dispararía falsos JOB_TIMEOUT en jobs creados en background. `loop.call_later` usa el event loop nativo, no el mock. Tradeoff: `loop.call_later` no es cancelable directamente, por eso la implementación usa un `Future` + `handle.cancel()` en el `except CancelledError`. El patrón es idiomático y correcto.

---

## Observaciones adicionales (no bloqueantes)

1. `run_pipeline` registra `guard1` via `job_manager.register_task` antes de `run_explorer`. Si `run_explorer` tarda mucho y el job es cancelado via DELETE, `_cancel_job_tasks` cancela guard1 correctamente. El `finally` en `run_pipeline` también cancela guard1 si el pipeline termina por cualquier camino. No hay posibilidad de guard1 huérfano.

2. El finally en `run_pipeline` tiene `if not guard1.done(): guard1.cancel(); await guard1`. Esto cubre tanto el path normal como el path de excepción. Correcto.

3. `asyncio.create_task(run_pipeline(job_id))` en router.py lanza el pipeline sin `await`, por lo que el endpoint POST /scrape retorna inmediatamente con `{ job_id, status: "queued" }`. Correcto según el diseño.
