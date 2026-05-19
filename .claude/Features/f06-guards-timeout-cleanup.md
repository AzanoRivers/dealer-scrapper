# F06 — Guards: Timeout y Cleanup

## Objetivo

Implementar `app/core/guards.py` con los 3 guards obligatorios + pipeline runner.
Cablear `POST /scrape` en `router.py` para lanzar el pipeline real.

---

## Archivos a crear/modificar

- **Crear**: `app/core/guards.py`
- **Modificar**: `app/api/v1/router.py` — wiring del pipeline en POST /scrape
- **Crear**: `tests/test_f06_guards.py`
- **NO tocar**: pipeline subagents (explorer/fetcher/extractor/auditor), job_manager, config

---

## Guard 1 — Timeout Global (todas las fases)

```python
async def global_job_timeout(job_id: str) -> None:
    """
    Runs from when the job starts executing (status: exploring).
    Fails with JOB_TIMEOUT if job still running after JOB_MAX_DURATION_SECONDS.
    """
    await asyncio.sleep(settings.JOB_MAX_DURATION_SECONDS)
    state = await job_manager.get_state(job_id)
    if state and state.status not in (JobStatus.done, JobStatus.failed):
        await job_manager.fail_job(
            job_id,
            "JOB_TIMEOUT",
            "El job superó el tiempo máximo de ejecución (30 minutos).",
            retry_after=600,
        )
```

---

## Guard 2 — LLM Watchdog (solo fase "analyzing")

```python
async def llm_watchdog(job_id: str, activity_event: asyncio.Event) -> None:
    """
    Runs ONLY during "analyzing" phase (Reviewer active).
    Fails with LLM_TIMEOUT if no activity_event.set() in LLM_WATCHDOG_SECONDS.
    """
    while True:
        activity_event.clear()
        try:
            await asyncio.wait_for(
                activity_event.wait(),
                timeout=float(settings.LLM_WATCHDOG_SECONDS),
            )
            state = await job_manager.get_state(job_id)
            if state is None or state.status in (JobStatus.done, JobStatus.failed):
                return
        except asyncio.TimeoutError:
            await job_manager.fail_job(
                job_id,
                "LLM_TIMEOUT",
                "El modelo LLM no generó actividad en 5 minutos. Proceso terminado.",
                retry_after=300,
            )
            return
```

Guard 2 NO se lanza en el pipeline runner — se lanza en F07 (Reviewer), cuando el job entra en "analyzing".
La función debe estar disponible en `app/core/guards.py` para ser importada desde el Reviewer.

---

## Guard 3 — TTL Cleanup post-completion

```python
async def schedule_cleanup(job_id: str) -> None:
    """
    Launched when job reaches done or failed.
    Sleeps RESULT_TTL_MINUTES * 60 seconds then rm -rf job_dir.
    """
    delay = settings.RESULT_TTL_MINUTES * 60
    await asyncio.sleep(delay)
    job_dir = Path(settings.JOB_BASE_DIR) / job_id
    if job_dir.exists():
        shutil.rmtree(job_dir, ignore_errors=True)
```

Guard 3 NO se lanza en el pipeline runner — se lanza en F08 (Packager) al completar.
La función debe estar disponible en `app/core/guards.py` para ser importada desde el Packager.

---

## Pipeline Runner

```python
async def run_pipeline(job_id: str) -> None:
    """
    Orchestrates the full pipeline: Explorer → Fetcher → Extractor → Auditor.
    Launches Guard 1 at start. Registers it with job_manager.
    If any subagent fails (returns False), the pipeline stops (job already failed).
    Guard 2 (LLM watchdog) launched by Reviewer in F07.
    Guard 3 (TTL cleanup) launched by Packager in F08.
    """
    # Guard 1 — global timeout (30 min)
    guard1 = asyncio.create_task(global_job_timeout(job_id))
    job_manager.register_task(job_id, guard1)

    try:
        # Explorer
        ok = await run_explorer(job_id)
        if not ok:
            return  # job already failed with NO_ROUTES_FOUND

        # Check if canceled/failed between phases
        state = await job_manager.get_state(job_id)
        if state is None or state.status in (JobStatus.done, JobStatus.failed):
            return

        # Fetcher
        ok = await run_fetcher(job_id)
        if not ok:
            return

        state = await job_manager.get_state(job_id)
        if state is None or state.status in (JobStatus.done, JobStatus.failed):
            return

        # Extractor
        ok = await run_extractor(job_id)
        if not ok:
            return

        state = await job_manager.get_state(job_id)
        if state is None or state.status in (JobStatus.done, JobStatus.failed):
            return

        # Auditor
        ok = await run_auditor(job_id)
        if not ok:
            return

        # Auditor re-fetch loop (max 1 iteration) — check audit_report.json
        state = await job_manager.get_state(job_id)
        if state is None or state.status in (JobStatus.done, JobStatus.failed):
            return

        # Check if Auditor requested re-fetch
        audit_report_path = Path(settings.JOB_BASE_DIR) / job_id / "audit_report.json"
        if audit_report_path.exists():
            import json as _json
            report = _json.loads(audit_report_path.read_text(encoding="utf-8"))
            if report.get("needs_refetch") and not report.get("second_pass"):
                ok = await run_fetcher(job_id)
                if not ok:
                    return
                state = await job_manager.get_state(job_id)
                if state is None or state.status in (JobStatus.done, JobStatus.failed):
                    return
                ok = await run_extractor(job_id)
                if not ok:
                    return
                state = await job_manager.get_state(job_id)
                if state is None or state.status in (JobStatus.done, JobStatus.failed):
                    return
                ok = await run_auditor(job_id, second_pass=True)
                if not ok:
                    return

        # F07 (Reviewer) and F08 (Packager) — will be wired in their respective features.
        # For now, the pipeline stops here after Auditor.
        # The job stays in "auditing" status until F07 is implemented.

    except Exception as e:
        import logging
        logging.getLogger(__name__).exception("Pipeline error for job %s", job_id)
        state = await job_manager.get_state(job_id)
        if state and state.status not in (JobStatus.done, JobStatus.failed):
            await job_manager.fail_job(
                job_id,
                "INTERNAL_ERROR",
                f"Error inesperado en el pipeline: {type(e).__name__}",
                retry_after=60,
            )
    finally:
        # Cancel Guard 1 if job finished before timeout
        if not guard1.done():
            guard1.cancel()
```

---

## Wiring en POST /scrape (router.py)

Modificar `create_scrape_job` para lanzar el pipeline:

```python
from app.core.guards import run_pipeline

@router.post("/scrape", ...)
async def create_scrape_job(request: ScrapeRequest, api_key: str = Depends(verify_api_key)):
    # ... build options dict (igual que antes) ...
    job_id = await job_manager.create_job(url=str(request.url), options=options)

    # Launch pipeline as background task
    asyncio.create_task(run_pipeline(job_id))

    return ScrapeResponse(job_id=job_id, status=JobStatus.queued.value)
```

Agregar `import asyncio` en router.py si no está.

---

## Checkpoints F06 a cubrir

- [x] Guard 1 falla el job con JOB_TIMEOUT si supera 30 min desde started_at
- [x] Guard 2 falla con LLM_TIMEOUT si no hay event.set() en 5 min
- [x] Guard 2 solo se lanza en fase "analyzing" (no aquí, en F07)
- [x] Guard 3 se lanza cuando status cambia a done/failed (no aquí, en F08)
- [x] Guard 3 elimina rm -rf job_dir tras 15 min exactos
- [x] Reviewer detecta state.status == "failed" y aborta (patrón disponible, Reviewer en F07)
- [x] DELETE /{job_id} cancela las 3 Tasks asyncio — ya funciona via job_manager._cancel_job_tasks

---

## Tests (tests/test_f06_guards.py)

1. **test_guard1_fires_on_timeout** — Guard 1 falla el job después de N segundos
   (usar settings override pequeño, ej. 0.1s, en el test)
2. **test_guard1_no_fire_if_done** — Guard 1 no falla si job ya está done
3. **test_guard2_fires_on_inactivity** — llm_watchdog falla el job si no hay event.set()
4. **test_guard2_resets_on_activity** — llm_watchdog no falla si hay event.set() periódico
5. **test_guard2_stops_if_job_done** — llm_watchdog termina limpio si job llega a done
6. **test_schedule_cleanup_deletes_dir** — schedule_cleanup elimina directorio después de delay
7. **test_schedule_cleanup_noop_if_dir_missing** — no falla si directorio ya no existe
8. **test_pipeline_stops_on_subagent_failure** — si Explorer retorna False, pipeline para
9. **test_pipeline_handles_cancellation** — job en failed antes de fase → pipeline para en check

**Patrón de test para guards con timeout corto:**
```python
import asyncio
from unittest.mock import patch, AsyncMock

async def test_guard1_fires_on_timeout(tmp_job_dir):
    job_id = ...  # crear job real con job_manager
    with patch.object(settings, "JOB_MAX_DURATION_SECONDS", 0.05):
        guard = asyncio.create_task(global_job_timeout(job_id))
        await asyncio.sleep(0.1)
        await guard
    state = await job_manager.get_state(job_id)
    assert state.status.value == "failed"
    assert state.error.code == "JOB_TIMEOUT"
```

---

## Imports necesarios en guards.py

```python
import asyncio
import shutil
from pathlib import Path

from app.config import settings
from app.core.job_manager import job_manager
from app.models.job import JobStatus
from app.pipeline.explorer import run_explorer
from app.pipeline.fetcher import run_fetcher
from app.pipeline.extractor import run_extractor
from app.pipeline.auditor import run_auditor
```

---

## Notas

- `schedule_cleanup` y `llm_watchdog` solo se exportan de `guards.py` — las invocan F07 y F08.
- No modificar signatures de `run_explorer`, `run_fetcher`, `run_extractor`, `run_auditor`.
- No importar `run_pipeline` desde los pipeline subagents — circular import.
- `asyncio.create_task` requiere event loop activo — dentro de endpoint FastAPI siempre lo hay.
