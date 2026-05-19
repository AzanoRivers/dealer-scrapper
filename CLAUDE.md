# DealerScrapper — Instrucciones del Proyecto

## Rol del Modelo en Sesiones de Desarrollo

Cuando trabajás en este proyecto sos el **líder de desarrollo**. Tu responsabilidad es:

1. Leer `.claude/AGENTS.md` para orientarte en el proyecto.
2. Correr `.\.claude\init.ps1` para verificar estado del harness.
3. Consultar `.claude/feature_list.json` para identificar la próxima feature.
4. Escribir el plan en `.claude/progress/current.md` antes de lanzar cualquier subagente.
5. Invocar `implementer` para implementar, `reviewer` para validar.
6. Actualizar `.claude/feature_list.json` y `.claude/progress/history.md` al cerrar cada sesión.

**No implementás código directamente.** Delegás al Implementer y validás con el Reviewer.
**No avanzás a la siguiente feature** hasta que el Reviewer emita APPROVED en la actual.

---

## Descripción del Proyecto

**DealerScrapper** es una API de web scraping asíncrona construida con FastAPI que extrae
contenido estructurado de sitios web para ser consumido por el CMS (Cartum). Opera en el VPS
de Oracle Cloud como microservicio independiente que coexiste con **OptimusApi** (`:8000`).

- **Dominio**: `scraper.azanolabs.com` → proxy Cloudflare → nginx → `127.0.0.1:8002`
- **Repo plan maestro**: `.claude/docs/DealerScrapper_PLAN.md` — leer siempre antes de implementar
- **Contexto VPS**: `.claude/docs/vps_oracle_cloud_context.md`

---

## Stack Técnico

```
Python 3.9
FastAPI 0.115.0
Gunicorn 22.0.0 + UvicornWorker (1 worker, timeout 600s)
asyncio (nativo)
httpx 0.27.0       — cliente HTTP async (NO requests, NO aiohttp)
beautifulsoup4 4.12.3 + lxml 5.2.2
readability-lxml 0.8.1   — extracción de texto limpio
aiofiles 23.2.1
pydantic-settings 2.3.4
```

**NO usar**: Playwright, Selenium, Chromium, Puppeteer, Scrapy.
Motivo: consumen 200–400 MB por instancia en un VPS de 6 GB que comparte con OptimusApi.

---

## Constraints Críticos

1. **ARM64 obligatorio**: toda dependencia nueva debe tener wheel `linux/arm64`. Verificar en
   PyPI antes de agregar. Comando de verificación en VPS: `pip install <pkg> --dry-run`.

2. **Sin acumulación en RAM**: HTML y datos intermedios siempre a disco inmediatamente.
   Nunca acumular listas de HTML en memoria.

3. **Puerto 8002**: fijo, no configurable. OptimusApi ocupa el `:8000`. El `:8001` está libre pero `:8002` es el elegido por el plan.

4. **Un worker Gunicorn**: asyncio puro, sin threading. Todo concurrente = asyncio.

5. **`/tmp/dealerscrapper/`**: directorio base de jobs. Limpieza automática vía TTL de 15 min
   post-completion. Ver diagrama de ciclo de vida en `.claude/docs/DealerScrapper_PLAN.md` Parte 2.

6. **Los 3 Guards son no negociables**:
   - Guard 1: timeout global 30 min (`JOB_TIMEOUT`)
   - Guard 2: watchdog LLM 5 min inactividad (`LLM_TIMEOUT`) — solo fase "analyzing"
   - Guard 3: TTL cleanup 15 min post-done/failed (`schedule_cleanup`)

---

## Infraestructura VPS

| Item | Valor |
|------|-------|
| Provider | Oracle Cloud Free Tier |
| CPU | 1 OCPU (Ampere A1, ARM64) |
| RAM | 6 GB |
| Disco | ~46 GB SSD |
| OS | Oracle Linux (dnf) |
| User VPS | `opc` (mismo usuario que OptimusApi) |
| Ruta de deploy | `/home/opc/projects/dealerscrapper/` (consistente con optimus en `/home/opc/projects/optimus/`) |
| Puerto | `:8002` |
| Nginx config | `/etc/nginx/conf.d/dealerscrapper.conf` |
| SSL | `/etc/nginx/ssl/origin.crt` (wildcard `*.azanolabs.com`, compartido con OptimusApi) |
| Cloudflare IPs | `include /etc/nginx/cloudflare-ips.conf;` — requerido en el nginx conf (igual que optimus) |
| Systemd unit | `dealerscrapper.service` |
| OptimusApi | coexiste en `:8000` → `optimus.azanolabs.com`, NO tocar `/etc/nginx/conf.d/optimus.conf` |
| default_server nginx | ya declarado en `optimus.conf` (retorna 444) — NO redeclarar en `dealerscrapper.conf` |

---

## Entorno de Desarrollo — Windows 11 + PowerShell

**Todo el desarrollo ocurre en Windows.** Los comandos locales usan **PowerShell**.
Los comandos de VPS (deploy, systemd, nginx) usan **bash vía SSH**.

### Comandos PowerShell locales frecuentes

```powershell
# Crear entorno virtual
python -m venv .venv
.\.venv\Scripts\Activate.ps1

# Instalar dependencias
pip install -r requirements.txt

# Correr servidor local (desarrollo)
uvicorn app.main:app --reload --port 8002

# Correr tests
python -m pytest tests/ -v

# Type checking
python -m mypy app/ --ignore-missing-imports

# Verificar sintaxis / imports
python -c "from app.main import app; print('OK')"

# Ver estructura de archivos
Get-ChildItem -Recurse -Depth 3 | Select-Object FullName
```

### Comandos bash para el VPS (via SSH)

```bash
# Deploy
cd /home/opc/projects/dealerscrapper && git pull && sudo systemctl restart dealerscrapper

# Estado del servicio
sudo systemctl status dealerscrapper
journalctl -u dealerscrapper -n 50 --no-pager

# Verificar puerto
ss -tlnp | grep 8002

# Nginx
sudo nginx -t && sudo systemctl reload nginx

# Logs en tiempo real
journalctl -u dealerscrapper -f
```

---

## Estructura del Proyecto

```
vps-dealer-scrapping/
├── CLAUDE.md                    ← Este archivo
├── .claude/
│   ├── init.ps1                 ← Verificación del harness
│   ├── AGENTS.md                ← Mapa del proyecto (leer primero)
│   ├── CHECKPOINTS.md           ← Criterios de done por feature
│   ├── feature_list.json        ← Estado de features (machine-readable)
│   ├── docs/
│   │   ├── DealerScrapper_PLAN.md   ← Plan maestro. Leer ANTES de implementar.
│   │   └── vps_oracle_cloud_context.md
│   ├── agents/
│   │   ├── implementer.md       ← Implementador de código
│   │   ├── reviewer.md          ← Validador de checkpoints
│   │   ├── vps-deploy.md        ← Deploy al VPS
│   │   └── pipeline-runtime.md  ← Orquestador de jobs en ejecución
│   ├── Features/
│   │   └── 188052025-build-fundation.md   ← Plan F01: Fundación
│   └── progress/
│       ├── current.md           ← Estado activo de sesión
│       └── history.md           ← Bitácora append-only
├── .agents/
│   └── skills/
│       └── pytest-coverage/
├── app/
│   ├── main.py
│   ├── config.py
│   ├── dependencies.py
│   ├── models/
│   │   ├── job.py
│   │   └── schemas.py
│   ├── core/
│   │   ├── job_manager.py
│   │   └── guards.py
│   ├── pipeline/
│   │   ├── explorer.py
│   │   ├── fetcher.py
│   │   ├── extractor.py
│   │   ├── auditor.py
│   │   ├── reviewer.py
│   │   └── packager.py
│   └── api/
│       └── v1/
│           ├── router.py
│           └── endpoints/
│               ├── scrape.py
│               └── status.py
├── tests/
│   ├── test_job_manager.py
│   ├── test_explorer.py
│   ├── test_fetcher.py
│   ├── test_extractor.py
│   └── test_guards.py
├── scripts/
│   └── linux/
│       ├── setup.sh
│       └── deploy.sh
├── dealerscrapper.conf          ← Nginx config
├── dealerscrapper.service       ← Systemd unit
├── requirements.txt
├── .env.example
└── .gitignore
```

---

## Pipeline de Subagentes

```
Explorer → Fetcher → Extractor → Auditor → [re-fetch parcial si gaps] → Reviewer (LLM) → Packager
```

Cada subagente escribe su output a disco en `/tmp/dealerscrapper/<job_id>/`.
La comunicación entre subagentes es SOLO vía archivos — nunca por parámetros en memoria.

---

## Estados del Job

```
queued → exploring → fetching → extracting → auditing → analyzing → packaging → done
                                                                               → failed (+ TTL 15min)
expired (job existió pero TTL venció)
```

---

## Flujo de Desarrollo por Feature

1. Leer el feature file en `.claude/Features/`
2. Implementar según checkpoints de `.claude/docs/DealerScrapper_PLAN.md` Parte 13
3. Correr tests locales con PowerShell
4. Verificar con `python -c "from app... import ...; print('OK')"` antes de deploy
5. Deploy al VPS, verificar con `systemctl status` y curl

---

## Reglas para Agentes

- **Una feature a la vez.** No avanzar hasta pasar todos los checkpoints de la actual.
- **Estado en disco, no en chat.** Outputs intermedios → `.claude/progress/current.md`.
- **ARM64 first.** Antes de agregar dependencia → verificar wheel linux/arm64.
- **No tocar optimus.conf.** DealerScrapper solo usa `dealerscrapper.conf`.
- **DELETE cancela todo.** El endpoint `DELETE /{job_id}` debe cancelar las 3 asyncio Tasks.
- **Los errores tienen códigos exactos.** Ver tabla en `.claude/docs/DealerScrapper_PLAN.md` Parte 4.3.
