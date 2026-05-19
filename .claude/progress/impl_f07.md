**Estado**: DONE
**Tests**: 93/93 (85 previos + 8 nuevos F07)
**Concerns**: ninguno

## Archivos creados/modificados

- **CREADO** `app/pipeline/reviewer.py` — LLMClient (openai/anthropic/deepseek/minimax), run_reviewer, LLMAuthError, LLMParseError
- **MODIFICADO** `app/core/guards.py` — Guard2 + run_reviewer wired en run_pipeline; import agregado
- **MODIFICADO** `tests/test_f06_guards.py` — test_pipeline_cancel_guard1_on_completion actualizado para esperar 2 tasks (guard1 + guard2); mock de run_reviewer agregado
- **CREADO** `tests/test_f07_reviewer.py` — 8 tests con respx

## Decisiones de implementación

1. **Formato de audit_report.json**: el auditor real escribe `valid_pages` como lista de hashes.
   La spec de tests usa `pages` como lista de dicts con `{url_hash, valid}`.
   `_resolve_valid_page_hashes()` maneja ambos formatos + fallback a disco.

2. **Check pre-status**: `run_reviewer` verifica `state.status == failed` ANTES de llamar
   `update_status(analyzing)` para no sobreescribir el estado fallido.

3. **F06 test actualizado**: `test_pipeline_cancel_guard1_on_completion` ahora espera 2 tasks
   (guard1 + guard2) porque F07 registra guard2 en run_pipeline. Run_reviewer mockeado.

4. **LLMParseError retry**: el cliente reintenta 1 vez en parse errors a nivel HTTP;
   `_parse_json_response` lanza LLMParseError si el contenido no es JSON, que run_reviewer
   captura y convierte en fail_job(LLM_PARSE_ERROR).
