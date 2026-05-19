# F02 — Explorer: Route Discovery

## Objetivo

Implementar `app/pipeline/explorer.py` — el subagente Explorer.
Dado el `job_id` y la URL base del job, descubre todas las rutas a scrapear
y escribe `routes.json` en `/tmp/dealerscrapper/<job_id>/routes.json`.

---

## Contrato de entrada

El Explorer recibe:
- `job_id: str` — para leer `state.json` (url, options) y escribir `routes.json`
- `job_manager` — para actualizar estado y fallar el job si NO_ROUTES_FOUND

Lee la URL y `max_pages` desde `state.json` via `job_manager.get_state()`.

---

## Contrato de salida

Archivo: `/tmp/dealerscrapper/<job_id>/routes.json`

```json
{
  "job_id": "uuid-v4",
  "base_url": "https://example.com",
  "total_routes": 12,
  "discovery_method": "sitemap",
  "routes": [
    {
      "url": "https://example.com/about",
      "source": "sitemap",
      "priority": 0.8
    },
    {
      "url": "https://example.com/contact",
      "source": "homepage_links",
      "priority": null
    }
  ]
}
```

`discovery_method` es el método PRIMARIO que encontró rutas (el más alto en prioridad que tuvo éxito):
- `"sitemap"` — se encontró y parseó sitemap.xml o variante
- `"homepage_links"` — se extrajeron links de la homepage
- `"fallback"` — se usaron rutas hardcoded

`source` por URL puede ser diferente si se combinaron métodos.

---

## Estrategias de descubrimiento (orden de prioridad)

```
1. GET /robots.txt          → extraer directivas Sitemap:
2. GET /sitemap.xml         → parsear URLs (seguir sitemaps hijos, profundidad máx 2)
3. GET /sitemap_index.xml   → iterar sitemaps hijos
4. Patrones comunes:
   /sitemap-pages.xml, /sitemap-posts.xml, /sitemap-products.xml, /page-sitemap.xml
5. GET / (homepage)         → extraer <a href> internos del HTML
6. Fallback hardcoded:
   /about, /contact, /products, /blog, /faq, /services
```

Siempre se intenta la homepage (`/`) independientemente del método de descubrimiento.
La homepage se agrega como primera ruta si no está ya en la lista.

---

## Filtros de rutas (OBLIGATORIOS)

### Excluir extensiones
`.css`, `.js`, `.png`, `.jpg`, `.jpeg`, `.gif`, `.webp`, `.svg`,
`.pdf`, `.zip`, `.ico`, `.woff`, `.woff2`, `.ttf`, `.eot`, `.mp4`, `.mp3`

### Excluir parámetros de tracking
`?utm_`, `?ref=`, `?session=`, `?token=`, `?fbclid=`, `?gclid=`
(quitar parámetros de tracking pero conservar la URL base si es válida)

### Excluir rutas admin/sistema
`/wp-admin`, `/wp-login`, `/admin`, `/login`, `/dashboard`,
`/_next`, `/.well-known`, `/wp-content`, `/wp-includes`, `/__`, `/api/`

### Normalización
- Trailing slash: `https://example.com/about/` == `https://example.com/about` (deduplicar)
- Solo URLs del mismo dominio base (sin subdominios diferentes)
- Convertir URLs relativas a absolutas usando `base_url`

---

## Límite de páginas

Respetar `MAX_PAGES_PER_JOB` (default: 50) de `settings`.
Si hay más rutas que el límite, tomar las primeras N (priorizando sitemap order).
La homepage siempre está incluida si fue encontrada.

---

## Lógica de profundidad en sitemaps

```
robots.txt → puede tener múltiples Sitemap: directivas
sitemap_index.xml → tiene <sitemap><loc> que apuntan a sitemaps hijos
sitemap.xml → tiene <url><loc> que son páginas reales

Profundidad máx 2: sitemap_index → sitemap_hijo → URLs
No seguir más niveles para evitar bucles infinitos.
Detectar bucles: si un sitemap URL ya fue procesado, saltarlo.
```

---

## Actualización de estado del job

```python
# Al iniciar (antes de comenzar fetches)
await job_manager.update_status(job_id, JobStatus.exploring)

# Al terminar con éxito (después de escribir routes.json)
# NO cambiar estado aquí — el Orchestrer lo hace al pasar a fetching
# Solo escribir routes.json y retornar

# Si NO_ROUTES_FOUND:
await job_manager.fail_job(job_id, "NO_ROUTES_FOUND",
    "No se encontraron rutas para scrapear. El sitio puede requerir JavaScript para renderizar.")
```

---

## Signatura de la función principal

```python
async def run_explorer(job_id: str) -> bool:
    """
    Discovers routes for the job's URL and writes routes.json.
    Returns True on success, False if job was failed (NO_ROUTES_FOUND).
    Updates job status to 'exploring' at the start.
    """
```

---

## httpx — configuración del cliente

```python
headers = {
    "User-Agent": "Mozilla/5.0 (compatible; DealerScrapper/1.0; +https://azanolabs.com)",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}
timeout = httpx.Timeout(15.0)
follow_redirects = True
max_redirects = 3
```

---

## Manejo de errores HTTP

- `404`, `403`, `410` para un sitemap: silencioso, intentar siguiente estrategia
- `robots.txt 404`: silencioso, continuar sin él
- Timeout en cualquier request: silencioso, continuar sin esa URL
- Si TODAS las estrategias fallan → `NO_ROUTES_FOUND`

---

## Tests requeridos (tests/test_f02_explorer.py)

1. **test_sitemap_xml_parsed** — sitemap.xml con varias URLs, verifica routes.json correcto
2. **test_sitemap_index_nested** — sitemap_index.xml con 2 hijos, verifica profundidad 2
3. **test_robots_txt_sitemap_directive** — robots.txt con `Sitemap:` header
4. **test_homepage_links_fallback** — sin sitemap, extrae links de homepage
5. **test_hardcoded_fallback** — sin sitemap ni links útiles, usa rutas hardcoded
6. **test_filters_assets** — URLs con .css/.js/imágenes son excluidas
7. **test_filters_admin_routes** — /wp-admin, /login etc. excluidos
8. **test_filters_tracking_params** — ?utm_ removido, URL base conservada
9. **test_max_pages_respected** — limita a MAX_PAGES_PER_JOB
10. **test_no_routes_found_fails_job** — cuando no hay nada, falla con NO_ROUTES_FOUND
11. **test_deduplication** — trailing slash deduplicado
12. **test_homepage_always_included** — homepage en la lista si fue accesible

Usar `respx` para mockear httpx (ya está en requirements-dev.txt si no, agregar).

---

## Archivos a crear/modificar

- **Crear**: `app/pipeline/explorer.py`
- **Crear**: `tests/test_f02_explorer.py`
- **No tocar**: nada más. No modificar main.py, router.py, job_manager.py en esta feature.

---

## Notas de implementación

- `app/pipeline/__init__.py` ya existe (vacío) — no recrear.
- Usar `aiofiles` para escribir `routes.json` a disco (consistente con el resto del proyecto).
- El Explorer NO lanza Guards. Los Guards son responsabilidad del pipeline runner (F06).
- Parseo XML: usar `lxml` (ya en requirements.txt) o `xml.etree.ElementTree` de stdlib.
  Preferir `lxml` por robustez con XML malformado de sitios reales.
- Parseo HTML para homepage links: usar `beautifulsoup4` con parser `lxml`.
