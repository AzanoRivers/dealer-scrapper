# Review F08 — Packager: Output

**Reviewer**: Claude Sonnet 4.6
**Fecha**: 2026-05-19
**Feature**: F08 — packager_output
**Veredicto**: APPROVED

---

## Archivos Revisados

- `app/pipeline/packager.py`
- `app/core/guards.py` (bloque run_packager + Guard 3)
- `tests/test_f08_packager.py`

---

## Resultado de Tests

```
103 passed in 19.03s
```

F08 agrega 10 tests nuevos (93 previos → 103 total). Todos pasan.

---

## Validación de Checkpoints

### CP1: Descarga imágenes solo si DOWNLOAD_IMAGES=true
PASS. El bloque de descarga en `run_packager` está envuelto en `if settings.DOWNLOAD_IMAGES and images_in_result:`. Cuando `DOWNLOAD_IMAGES=false`, el directorio `images/` no se crea. Cubierto por `test_packager_no_images_download_disabled` y `test_packager_downloads_images_when_enabled`.

### CP2: Verifica Content-Type y tamaño de cada imagen descargada
PASS. La función `_content_type_to_ext` rechaza tipos no listados en `_VALID_IMAGE_TYPES` (ej: `text/html` retorna `None`). El límite de tamaño se aplica via `settings.MAX_IMAGE_SIZE_MB`. Ambas rutas de rechazo retornan `("", False)` sin excepcionar. Cubierto por `test_packager_image_too_large_skipped` y `test_packager_image_invalid_content_type_skipped`.

### CP3: Actualiza local_path en result.json por cada imagen descargada
PASS. En el loop de descarga, si `success=True`, se escribe `img["local_path"] = f"images/{filename}"` sobre el objeto de referencia del dict. Luego `result.json` se reescribe a disco. Cubierto por `test_packager_updates_local_path` (verifica `images[0]["local_path"] == "images/img_001.jpg"`).

### CP4: Elimina pages/, routes.json, fetch_results.json, extract_results.json, audit_report.json
PASS. El paso 7 en `run_packager` usa `shutil.rmtree` para `pages/` y `chunk_summaries/`, y `Path.unlink(missing_ok=True)` para los 4 archivos JSON de trabajo. Cubierto por `test_packager_cleans_temp_files`.

### CP5: result.zip contiene result.json + images/ (si aplica)
PASS. El ZIP siempre incluye `result.json`. Cuando `DOWNLOAD_IMAGES=true` e `images/` existe, itera `images_dir.iterdir()` excluyendo `index.json` y agrega cada imagen bajo `images/<filename>`. Cubierto por `test_packager_creates_zip` y `test_packager_zip_contains_images`.

### CP6: state.json → status: "done", done_at: <timestamp>
PASS. `run_packager` llama `await job_manager.complete_job(job_id)` al final. El estado resultante se verifica en `test_packager_sets_done_status` (`state.status == JobStatus.done` y `state.done_at is not None`).

### CP7: asyncio.create_task(schedule_cleanup(job_id)) lanzado DESPUÉS de complete_job
PASS. En `guards.py`, dentro de `run_pipeline`, el orden es:
1. `ok = await run_packager(job_id)` — llama `complete_job` internamente
2. `guard3 = asyncio.create_task(schedule_cleanup(job_id))` — lanzado después de que `run_packager` retorna

Guard 3 no se cancela en el bloque `finally` (correcto: debe sobrevivir para hacer el cleanup a los 15 min). `job_manager.register_task(job_id, guard3)` lo registra para que `DELETE` pueda cancelarlo si el usuario lo solicita.

---

## Observaciones Adicionales

- La función `_content_type_to_ext` implementa una lógica correcta de fallback: usa la extensión de URL solo cuando el `Content-Type` está completamente ausente, evitando falsos positivos en respuestas que devuelven `text/html` para recursos inexistentes.
- Los errores de imagen son non-fatal en todos los casos (`_download_image` tiene un `except Exception: return "", False`), por lo que el job siempre completa con `status=done` independientemente de fallos de imagen.
- `images/index.json` se crea con metadata de descarga (filename, original_url, alt, size_bytes), listo para ser servido por F09.
- La imagen descargada como SVG (`image/svg+xml`) también es soportada.
- No hay importación circular: `packager.py` no importa `guards.py`; Guard 3 es lanzado por `run_pipeline` en `guards.py`.

---

## Veredicto Final

**APPROVED**

Todos los checkpoints de F08 están implementados y validados. El código cumple con los constraints del proyecto (no RAM acumulada, non-fatal para errores de imagen, Guard 3 lanzado post-`complete_job`). La suite pasa en 103/103.
