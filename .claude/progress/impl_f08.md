**Estado**: DONE
**Tests**: 10/10 (F08) + 103/103 (suite completa, sin regresiones)
**Concerns**: ninguno

---

## Archivos creados/modificados

### Creado: `app/pipeline/packager.py`
- `run_packager(job_id)` implementado completo
- `_download_image(client, url, idx, images_dir)` — descarga individual, non-fatal
- `_content_type_to_ext(content_type, url)` — valida Content-Type; fallback a URL ext solo si Content-Type está ausente (no si es explícitamente no-imagen como text/html)
- Limpieza de temporales: pages/, chunk_summaries/, routes.json, fetch_results.json, extract_results.json, audit_report.json
- Retorna True siempre (errores de imagen son non-fatal)

### Modificado: `app/core/guards.py`
- Import de `run_packager` agregado en sección de imports
- Bloque F08 insertado después de `run_reviewer`: check_still_running → run_packager → Guard3 (schedule_cleanup)
- Guard 3 registrado con `job_manager.register_task` pero NO cancelado en `finally` (debe sobrevivir para limpiar)
- Comentario placeholder "F08 will be wired..." reemplazado

### Creado: `tests/test_f08_packager.py`
- 10 tests usando `respx.mock` para mockear descargas HTTP
- `_make_job_with_result()` helper crea job completo con result.json, pages/, archivos temporales y state.json
- Override de settings via `patch.object(cfg, "DOWNLOAD_IMAGES", True/False)`

## Decisión de diseño: _content_type_to_ext

La spec original muestra fallback a URL extension sin condición. Sin embargo, el test
`test_packager_image_invalid_content_type_skipped` verifica que `text/html` sea rechazado
incluso cuando la URL termina en `.jpg`. Se ajustó la lógica: el fallback a URL ext solo
aplica cuando `content_type` está vacío (header ausente). Si el servidor retorna un
Content-Type explícito no-imagen, se rechaza sin importar la URL.
