# Reviewer Report — F07

**Veredicto**: APPROVED

## Checkpoints

- [x] activity_event ≥5 calls por batch
- [x] Check state.failed antes de LLM call
- [x] 4 providers soportados
- [x] LLM_AUTH_ERROR falla inmediato
- [x] 429 retry con backoff
- [x] JSON malformado → LLM_PARSE_ERROR
- [x] chunk_summaries/ limpiado
- [x] result.json schema completo
- [x] Tests pasan (93/93)

## Análisis detallado

### activity_event.set() ≥5 por batch
Verificado en `run_reviewer`: Point 1 (batch start, línea 564), Point 2 (response received, línea 605),
Point 3 (chunk saved, línea 624), Point 4 (merge start, línea 631), Point 5 (result written, línea 704).
Además se llaman `activity_event.set()` extras cuando un batch no tiene páginas cargables (líneas 575–576).
`test_reviewer_activity_event_set` confirma set_count ≥ 5.

### Check state.failed antes de LLM call
Doble check pre-LLM implementado:
1. Al inicio de `run_reviewer` (líneas 494–496) — antes de `update_status(analyzing)`.
2. Antes del merge call (líneas 627–629) — después de procesar todos los batches.
3. En cada iteración de batch (líneas 560–562) — antes de cargar datos.
`test_reviewer_aborts_if_failed` confirma que LLM no se llama si el job ya está failed.

### 4 providers soportados
`_PROVIDER_URLS` define `openai`, `deepseek`, `anthropic`, `minimax`.
`_build_headers` maneja Anthropic con `x-api-key`/`anthropic-version`; el resto con `Bearer`.
`_build_payload` maneja formato Anthropic (system separado) vs OpenAI-compatible.
`_extract_content` maneja ambas estructuras de respuesta.
Tests cubren openai (`test_reviewer_success_openai`) y anthropic (`test_reviewer_success_anthropic`).
deepseek y minimax comparten código con openai (OpenAI-compatible), sin diferenciación de tests pero
la configuración de URL y headers es correcta para ambos.

### LLM_AUTH_ERROR falla inmediato
En `LLMClient.complete`, `LLMAuthError` se re-lanza directamente sin reintentos (líneas 191–192).
En `run_reviewer`, el catch en los batches (líneas 585–590) y en el merge (líneas 657–662) llaman
`fail_job("LLM_AUTH_ERROR")` y retornan False inmediatamente.
`test_reviewer_auth_error` confirma `state.error.code == "LLM_AUTH_ERROR"`.

### 429 retry con backoff
`LLMClient.complete` captura HTTPStatusError 429, parsea `Retry-After` header (min con 60s),
duerme con `asyncio.sleep`, reintenta una sola vez (líneas 197–214).
`test_reviewer_429_retry` mockea `asyncio.sleep` y confirma que el resultado es exitoso tras el retry.

### JSON malformado → LLM_PARSE_ERROR
`_parse_json_response` lanza `LLMParseError` si el contenido no es JSON válido (líneas 353–371).
`LLMClient.complete` atrapa `LLMParseError` y reintenta 1 vez vía `_do_request` (líneas 231–238).
Si ambos intentos fallan, `run_reviewer` captura `LLMParseError` y llama `fail_job("LLM_PARSE_ERROR")`.
`test_reviewer_parse_error` confirma `state.error.code == "LLM_PARSE_ERROR"`.

### chunk_summaries/ limpiado
`shutil.rmtree(chunk_dir, ignore_errors=True)` en línea 707, después de escribir `result.json`.
`test_chunk_summaries_cleaned` confirma que el directorio no existe tras un run exitoso.

### result.json schema completo
`_build_result_json` produce: `job_id`, `url`, `scraped_at`, `llm_provider`, `llm_model`,
`business` (name, type, description, language, address, phone, email, social_links),
`content` (main_topics, key_pages), `assets` (images con src/alt/local_path/width/height),
`metadata` (total_pages_discovered, pages_fetched, pages_analyzed, coverage_percent).
`test_reviewer_success_openai` verifica todos los campos de primer nivel y anidados.

### Guard 2 wired en run_pipeline
`guards.py` crea `activity_event`, lanza `guard2 = asyncio.create_task(llm_watchdog(...))`,
registra con `job_manager.register_task`, y cancela en `finally` tras `run_reviewer`.
`test_pipeline_cancel_guard1_on_completion` verifica que 2 tasks se registran (guard1 + guard2)
y ambas quedan en estado `done` al finalizar el pipeline.

## Issues
Ninguno.
