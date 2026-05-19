# F07 — Reviewer: LLM Analysis

## Objetivo

Implementar `app/pipeline/reviewer.py` — analiza las páginas extraídas con un LLM
y produce `result.json`. Lanzar Guard 2 (llm_watchdog) internamente.

---

## Archivos a crear/modificar

- **Crear**: `app/pipeline/reviewer.py`
- **Modificar**: `app/core/guards.py` — agregar llamada a `run_reviewer` en `run_pipeline`
- **Crear**: `tests/test_f07_reviewer.py`
- **NO tocar**: job_manager, config, otros pipeline subagents

---

## Diseño de módulo — reviewer.py

### Signatura principal

```python
async def run_reviewer(job_id: str, activity_event: asyncio.Event) -> bool:
    """
    Analyzes extracted pages with an LLM and writes result.json.
    Expects audit_report.json and pages/*.json to be present.
    Calls activity_event.set() at minimum 5 points per batch.
    Guard 2 (llm_watchdog) is managed by the caller (run_pipeline in guards.py).
    Returns True on success, False if job was failed (LLM error).
    """
```

**Nota crítica**: `activity_event` es creado y el Guard 2 es lanzado por `run_pipeline` en `guards.py`,
NO por `run_reviewer`. Esto evita importación circular:
- `guards.py` importa `run_reviewer` desde `reviewer.py`
- `reviewer.py` NO importa nada de `guards.py`

---

## LLM Client — clase LLMClient

```python
class LLMClient:
    """
    Wrapper async sobre httpx para 4 providers LLM.
    Soporta: openai, anthropic, deepseek, minimax
    """
    def __init__(self, provider: str, model: str, api_key: str):
        ...

    async def complete(self, messages: list[dict], *, max_tokens: int, temperature: float) -> str:
        """
        Sends a chat completion request. Returns the response text.
        Error handling:
        - httpx.TimeoutException  → return "" (Watchdog detectará inactividad)
        - 401 / 403               → raise LLMAuthError
        - 429                     → asyncio.sleep(min(retry_after, 60)), reintentar 1 vez
        - 5xx                     → reintentar 1 vez con mismos parámetros
        - JSON malformado         → raise LLMParseError
        - Si reintento falla      → raise LLMParseError
        """
```

### Exceptions personalizadas

```python
class LLMAuthError(Exception):
    """Raised on 401/403 — fail job with LLM_AUTH_ERROR."""

class LLMParseError(Exception):
    """Raised when response JSON is malformed after 1 retry."""
```

### URLs de API por provider

```python
_PROVIDER_URLS = {
    "openai":     "https://api.openai.com/v1/chat/completions",
    "deepseek":   "https://api.deepseek.com/v1/chat/completions",
    "anthropic":  "https://api.anthropic.com/v1/messages",
    "minimax":    "https://api.minimax.chat/v1/text/chatcompletion_v2",
}
```

### Formato de request por provider

**openai / deepseek** (OpenAI-compatible):
```json
{
  "model": "<model>",
  "messages": [...],
  "max_tokens": 4000,
  "temperature": 0.2,
  "response_format": {"type": "json_object"}
}
```
Header: `Authorization: Bearer <api_key>`

**anthropic**:
```json
{
  "model": "<model>",
  "messages": [...],
  "max_tokens": 4000,
  "system": "<system_prompt>"
}
```
Headers: `x-api-key: <api_key>`, `anthropic-version: 2023-06-01`

**minimax**:
```json
{
  "model": "<model>",
  "messages": [...],
  "max_tokens": 4000,
  "temperature": 0.2
}
```
Header: `Authorization: Bearer <api_key>`

### Parsing de respuesta por provider

```python
# openai / deepseek / minimax
content = response_json["choices"][0]["message"]["content"]

# anthropic
content = response_json["content"][0]["text"]
```

---

## Flujo principal de run_reviewer

```python
async def run_reviewer(job_id: str, activity_event: asyncio.Event) -> bool:
    # 1. Update status to "analyzing"
    await job_manager.update_status(job_id, JobStatus.analyzing)

    # 2. Read audit_report.json to get valid page list
    valid_pages = [p for p in audit_report["pages"] if p.get("valid", True)]

    # 3. Setup LLM client
    # Use job options override if present, else settings
    state = await job_manager.get_state(job_id)
    provider = state.options.get("llm_provider") or settings.LLM_PROVIDER
    model = state.options.get("llm_model") or settings.LLM_MODEL
    client = LLMClient(provider, model, settings.LLM_API_KEY)

    # 4. Create chunk_summaries/ directory
    chunk_dir = job_dir / "chunk_summaries"
    chunk_dir.mkdir(exist_ok=True)

    # 5. Process batches of 5 pages
    chunk_summaries = []
    for i, batch in enumerate(batches_of(valid_pages, 5)):
        # Check failed before each LLM call
        state = await job_manager.get_state(job_id)
        if state is None or state.status == JobStatus.failed:
            return False

        activity_event.set()  # START of batch

        pages_data = [load_page_data(job_dir, p) for p in batch]
        try:
            raw_response = await client.complete(build_batch_prompt(pages_data), ...)
        except LLMAuthError:
            await job_manager.fail_job(job_id, "LLM_AUTH_ERROR", ...)
            return False
        except LLMParseError:
            await job_manager.fail_job(job_id, "LLM_PARSE_ERROR", ...)
            return False

        if not raw_response:  # Timeout — Watchdog will handle it
            return False

        activity_event.set()  # RESPONSE received

        chunk = parse_chunk_response(raw_response)
        chunk_path = chunk_dir / f"chunk_summary_{i}.json"
        chunk_path.write_text(json.dumps(chunk, ensure_ascii=False, indent=2), encoding="utf-8")
        chunk_summaries.append(chunk)

        activity_event.set()  # CHUNK saved

    # 6. Merge call
    state = await job_manager.get_state(job_id)
    if state is None or state.status == JobStatus.failed:
        return False

    activity_event.set()  # MERGE start

    try:
        merged_raw = await client.complete(build_merge_prompt(chunk_summaries, job_url), ...)
    except LLMAuthError:
        await job_manager.fail_job(job_id, "LLM_AUTH_ERROR", ...)
        return False
    except LLMParseError:
        await job_manager.fail_job(job_id, "LLM_PARSE_ERROR", ...)
        return False

    if not merged_raw:
        return False

    result = build_result_json(job_id, state.url, merged_raw, valid_pages, provider, model)

    # 7. Write result.json
    result_path = job_dir / "result.json"
    result_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    activity_event.set()  # RESULT written

    # 8. Clean up chunk_summaries/
    shutil.rmtree(chunk_dir, ignore_errors=True)

    return True
```

---

## Prompts LLM

### Batch prompt (análisis por páginas)

```python
BATCH_SYSTEM_PROMPT = """Eres un asistente especializado en análisis de sitios web de concesionarios de automóviles y negocios.
Analiza las páginas proporcionadas y extrae información estructurada en JSON.
Responde ÚNICAMENTE con JSON válido, sin texto adicional."""

def build_batch_prompt(pages_data: list[dict]) -> list[dict]:
    pages_text = "\n\n---\n\n".join([
        f"URL: {p['url']}\nTítulo: {p['title']}\nContenido:\n{p['text_content'][:2000]}"
        for p in pages_data
    ])
    return [
        {"role": "system", "content": BATCH_SYSTEM_PROMPT},
        {"role": "user", "content": f"""Analiza estas páginas y devuelve JSON con esta estructura:
{{
  "business_name": "nombre del negocio o null",
  "business_type": "tipo de negocio (car_dealer, service, retailer, etc.) o null",
  "description": "descripción del negocio en 1-2 oraciones",
  "language": "código de idioma (es, en, pt, etc.)",
  "key_topics": ["tema1", "tema2"],
  "contact_info": {{"phone": null, "email": null, "address": null}},
  "pages_summary": [
    {{"url": "...", "title": "...", "summary": "...", "key_points": ["..."]}}
  ],
  "images": [
    {{"src": "url absoluta", "alt": "texto alt"}}
  ]
}}

Páginas a analizar:
{pages_text}"""}
    ]
```

### Merge prompt (consolidación)

```python
def build_merge_prompt(chunk_summaries: list[dict], base_url: str) -> list[dict]:
    summaries_text = json.dumps(chunk_summaries, ensure_ascii=False, indent=2)
    return [
        {"role": "system", "content": BATCH_SYSTEM_PROMPT},
        {"role": "user", "content": f"""Consolida estos análisis parciales en un único resultado JSON completo.
URL base del sitio: {base_url}

Devuelve JSON con esta estructura exacta:
{{
  "business_name": "...",
  "business_type": "...",
  "description": "...",
  "language": "...",
  "address": null,
  "phone": null,
  "email": null,
  "social_links": [],
  "main_topics": ["..."],
  "key_pages": [
    {{"url": "...", "title": "...", "summary": "...", "key_points": ["..."]}}
  ],
  "images": [
    {{"src": "...", "alt": "...", "local_path": null, "width": null, "height": null}}
  ]
}}

Análisis parciales:
{summaries_text}"""}
    ]
```

---

## Schema de result.json

```json
{
  "job_id": "uuid-v4",
  "url": "https://example.com",
  "scraped_at": "2026-05-19T10:00:00Z",
  "llm_provider": "openai",
  "llm_model": "gpt-4o-mini",
  "business": {
    "name": "Example Dealership",
    "type": "car_dealer",
    "description": "Concesionario de automóviles en...",
    "language": "es",
    "address": null,
    "phone": null,
    "email": null,
    "social_links": []
  },
  "content": {
    "main_topics": ["vehicles", "financing", "service"],
    "key_pages": [
      {
        "url": "https://example.com/about",
        "title": "About Us",
        "summary": "...",
        "key_points": ["..."]
      }
    ]
  },
  "assets": {
    "images": [
      {
        "src": "https://example.com/img/hero.jpg",
        "alt": "Hero image",
        "local_path": null,
        "width": null,
        "height": null
      }
    ]
  },
  "metadata": {
    "total_pages_discovered": 20,
    "pages_fetched": 18,
    "pages_analyzed": 12,
    "coverage_percent": 90.0
  }
}
```

**Nota**: `assets.images[].local_path` es `null` aquí — el Packager (F08) lo rellena si `DOWNLOAD_IMAGES=true`.

---

## Modificación a guards.py (run_pipeline)

Agregar después del bloque del Auditor (y del re-fetch loop), antes del comentario F07/F08:

```python
# Guard 2 — LLM watchdog (analyzing phase only)
activity_event = asyncio.Event()
guard2 = asyncio.create_task(llm_watchdog(job_id, activity_event))
job_manager.register_task(job_id, guard2)

try:
    ok = await run_reviewer(job_id, activity_event)
    if not ok:
        return
finally:
    if not guard2.done():
        guard2.cancel()
        try:
            await guard2
        except (asyncio.CancelledError, Exception):
            pass
```

Agregar import en guards.py: `from app.pipeline.reviewer import run_reviewer`

---

## Tests (tests/test_f07_reviewer.py)

8 tests. Usar `respx` para mockear llamadas HTTP al LLM.

**Setup helper**: crear job con state.json + audit_report.json + pages/*.json válidos.

**audit_report.json mínimo para tests**:
```json
{
  "job_id": "...",
  "coverage_percent": 100.0,
  "critical": false,
  "second_pass": false,
  "needs_refetch": false,
  "pages": [
    {"url": "https://example.com/", "url_hash": "abc123", "valid": true}
  ]
}
```

**pages/<hash>.json mínimo**:
```json
{
  "url": "https://example.com/",
  "url_hash": "abc123",
  "title": "Home",
  "text_content": "Este es el contenido de la página principal. " * 10,
  ...todos los campos de PageData...
}
```

### Tests:

1. **test_reviewer_success_openai** — mock respx para openai, verifica result.json creado
2. **test_reviewer_success_anthropic** — mock respx para anthropic (headers distintos)
3. **test_reviewer_auth_error** — LLM retorna 401 → job falla con LLM_AUTH_ERROR
4. **test_reviewer_429_retry** — primer call retorna 429 con retry-after, segundo retorna 200
5. **test_reviewer_parse_error** — LLM retorna JSON malformado dos veces → LLM_PARSE_ERROR
6. **test_reviewer_activity_event_set** — verificar que activity_event se llama ≥5 veces
7. **test_reviewer_aborts_if_failed** — job.status=failed antes de llamada LLM → retorna False sin llamar LLM
8. **test_chunk_summaries_cleaned** — después de éxito, chunk_summaries/ no existe

### Patrón de test con respx:

```python
@respx.mock
async def test_reviewer_success_openai(tmp_job_dir):
    job_id = _make_job_with_audit(tmp_job_dir)
    
    # Mock LLM batch call
    respx.post("https://api.openai.com/v1/chat/completions").mock(
        return_value=httpx.Response(200, json={
            "choices": [{"message": {"content": json.dumps({
                "business_name": "Test Dealer",
                "business_type": "car_dealer",
                "description": "A dealer",
                "language": "en",
                "key_topics": ["cars"],
                "contact_info": {"phone": None, "email": None, "address": None},
                "pages_summary": [{"url": "https://example.com/", "title": "Home", "summary": "...", "key_points": []}],
                "images": []
            })}}]
        })
    )
    # Mock merge call
    respx.post("https://api.openai.com/v1/chat/completions").mock(
        return_value=httpx.Response(200, json={...merge response...})
    )
    
    activity_event = asyncio.Event()
    result = await run_reviewer(job_id, activity_event)
    
    assert result is True
    result_path = Path(os.environ["JOB_BASE_DIR"]) / job_id / "result.json"
    assert result_path.exists()
    data = json.loads(result_path.read_text())
    assert data["business"]["name"] == "Test Dealer"
    assert "assets" in data
```

---

## Notas de implementación

- `LLMClient` usa `httpx.AsyncClient` con `timeout=httpx.Timeout(60.0)` (el watchdog maneja timeouts más largos)
- Para el retry de 429: leer header `Retry-After` (puede ser segundos o fecha). Si es > 60 → cap a 60.
- Anthropic no usa `response_format: json_object` — el prompt debe pedir JSON explícitamente
- `build_result_json` construye el schema final combinando merged_data + metadata del job
- Para `pages_analyzed` en metadata: len(valid_pages) del audit_report
- Para `coverage_percent` en metadata: leer de audit_report.json
- Escribir result.json con `Path.write_text()` sync (consistente con el proyecto) o `aiofiles` — preferir sync para atomicidad
- `chunk_summaries/` puede tener cualquier número de archivos — `shutil.rmtree` los elimina todos

## Archivos de pipeline que necesitás leer

- `app/pipeline/auditor.py` — formato de audit_report.json que produce
- `app/models/job.py` — JobStatus.analyzing existe? Si no, agregarlo
- `app/core/guards.py` — donde agregar el bloque Guard2 + run_reviewer
