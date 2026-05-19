# F10 — nginx / systemd / scripts de deploy

## Objetivo

Crear los 4 archivos de infraestructura necesarios para correr DealerScrapper en el VPS.
No hay tests automatizados — la validación es manual en el VPS.

---

## Archivos a crear

| Archivo | Destino en VPS |
|---------|---------------|
| `dealerscrapper.conf` | `/etc/nginx/conf.d/dealerscrapper.conf` |
| `dealerscrapper.service` | `/etc/systemd/system/dealerscrapper.service` |
| `scripts/linux/setup.sh` | corre una sola vez al instalar |
| `scripts/linux/deploy.sh` | corre en cada deploy |

**NO tocar**: ningún archivo Python, requirements.txt, ni ningún otro archivo existente.

---

## Contexto VPS

- **OS**: Oracle Linux (dnf) — ARM64
- **Usuario**: `opc`
- **Ruta del proyecto**: `/home/opc/projects/dealerscrapper/`
- **Puerto**: `127.0.0.1:8002`
- **Dominio**: `scraper.azanolabs.com`
- **SSL cert**: `/etc/nginx/ssl/origin.crt` y `/etc/nginx/ssl/origin.key` (wildcard `*.azanolabs.com`, compartido con OptimusApi)
- **Cloudflare IPs**: `include /etc/nginx/cloudflare-ips.conf;` — OBLIGATORIO en el bloque server
- **`default_server`**: ya declarado en `optimus.conf` — NO declararlo en `dealerscrapper.conf`
- **OptimusApi**: coexiste en `:8000` (o `:8001` según el plan) — NO tocar `/etc/nginx/conf.d/optimus.conf`

---

## 1. dealerscrapper.conf

```nginx
# Rate limiting zone (usar $http_cf_connecting_ip para IP real detrás de Cloudflare)
limit_req_zone $http_cf_connecting_ip zone=scraper:10m rate=1r/s;

server {
    listen 80;
    server_name scraper.azanolabs.com;
    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl;
    server_name scraper.azanolabs.com;

    ssl_certificate     /etc/nginx/ssl/origin.crt;
    ssl_certificate_key /etc/nginx/ssl/origin.key;

    # Solo aceptar tráfico desde Cloudflare
    include /etc/nginx/cloudflare-ips.conf;
    deny all;

    client_max_body_size 10m;
    proxy_read_timeout   600s;
    proxy_send_timeout   600s;

    location / {
        limit_req zone=scraper burst=5 nodelay;

        proxy_pass         http://127.0.0.1:8002;
        proxy_set_header   Host              $host;
        proxy_set_header   X-Real-IP         $remote_addr;
        proxy_set_header   X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header   X-Forwarded-Proto $scheme;
    }
}
```

**Notas**:
- `limit_req_zone` va fuera del bloque `server` (nivel http)
- `include /etc/nginx/cloudflare-ips.conf` + `deny all` — solo Cloudflare puede conectarse
- `proxy_read_timeout 600s` necesario para jobs largos (pipeline puede tardar hasta 30 min)
- NO usar `default_server` — ya está en `optimus.conf`

---

## 2. dealerscrapper.service

```ini
[Unit]
Description=DealerScrapper API
After=network.target

[Service]
User=opc
Group=opc
WorkingDirectory=/home/opc/projects/dealerscrapper
EnvironmentFile=-/home/opc/projects/dealerscrapper/.env
ExecStart=/home/opc/projects/dealerscrapper/.venv/bin/gunicorn app.main:app \
    -w 1 \
    -k uvicorn.workers.UvicornWorker \
    --bind 127.0.0.1:8002 \
    --timeout 600 \
    --access-logfile /home/opc/projects/dealerscrapper/logs/access.log \
    --error-logfile /home/opc/projects/dealerscrapper/logs/error.log
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

**Notas**:
- `User=opc` y `Group=opc` — mismo usuario que OptimusApi
- `EnvironmentFile=-/.../.env` — el `-` prefix hace que el archivo sea opcional (no falla si no existe)
- `-w 1` — un solo worker Gunicorn (asyncio puro, no threading)
- `--timeout 600` — cubre el timeout máximo del pipeline (30 min + margen)
- `logs/` se crea en `setup.sh`

---

## 3. scripts/linux/setup.sh

Script one-shot para instalar desde cero. Se corre UNA VEZ en el VPS.

```bash
#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="/home/opc/projects/dealerscrapper"
VENV_DIR="$PROJECT_DIR/.venv"
SERVICE_NAME="dealerscrapper"

echo "=== DealerScrapper Setup ==="

# 1. Crear directorio base si no existe
mkdir -p "$PROJECT_DIR"
mkdir -p "$PROJECT_DIR/logs"
mkdir -p /tmp/dealerscrapper

# 2. Python 3.9+ debe estar ya instalado. Verificar.
python3 --version || { echo "ERROR: python3 no encontrado"; exit 1; }

# 3. Crear virtual environment
if [ ! -d "$VENV_DIR" ]; then
    python3 -m venv "$VENV_DIR"
    echo "venv creado en $VENV_DIR"
fi

# 4. Instalar dependencias
"$VENV_DIR/bin/pip" install --upgrade pip
"$VENV_DIR/bin/pip" install -r "$PROJECT_DIR/requirements.txt"
echo "Dependencias instaladas."

# 5. Copiar .env si no existe
if [ ! -f "$PROJECT_DIR/.env" ]; then
    if [ -f "$PROJECT_DIR/.env.example" ]; then
        cp "$PROJECT_DIR/.env.example" "$PROJECT_DIR/.env"
        echo "ADVERTENCIA: .env copiado desde .env.example — editar antes de iniciar el servicio."
    else
        echo "ADVERTENCIA: no se encontró .env ni .env.example — crear manualmente."
    fi
fi

# 6. Instalar systemd service
if [ -f "$PROJECT_DIR/dealerscrapper.service" ]; then
    sudo cp "$PROJECT_DIR/dealerscrapper.service" /etc/systemd/system/
    sudo systemctl daemon-reload
    sudo systemctl enable "$SERVICE_NAME"
    echo "Servicio systemd instalado y habilitado."
else
    echo "ADVERTENCIA: dealerscrapper.service no encontrado en $PROJECT_DIR"
fi

# 7. Instalar nginx config
if [ -f "$PROJECT_DIR/dealerscrapper.conf" ]; then
    sudo cp "$PROJECT_DIR/dealerscrapper.conf" /etc/nginx/conf.d/
    sudo nginx -t && sudo systemctl reload nginx
    echo "nginx recargado."
else
    echo "ADVERTENCIA: dealerscrapper.conf no encontrado en $PROJECT_DIR"
fi

echo ""
echo "=== Setup completado ==="
echo "Próximos pasos:"
echo "  1. Editar .env con las variables de entorno reales"
echo "  2. sudo systemctl start dealerscrapper"
echo "  3. sudo systemctl status dealerscrapper"
echo "  4. curl -H 'X-API-Key: TU_KEY' https://scraper.azanolabs.com/api/v1/guide-ai"
```

---

## 4. scripts/linux/deploy.sh

Script para actualizar el proyecto en el VPS. Se corre en cada deploy.

```bash
#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="/home/opc/projects/dealerscrapper"
VENV_DIR="$PROJECT_DIR/.venv"
SERVICE_NAME="dealerscrapper"

echo "=== DealerScrapper Deploy ==="

cd "$PROJECT_DIR"

# 1. Pull latest changes
git pull origin main
echo "Git pull completado."

# 2. Actualizar dependencias (solo si requirements.txt cambió)
"$VENV_DIR/bin/pip" install -r requirements.txt --quiet
echo "Dependencias actualizadas."

# 3. Verificar importación principal
"$VENV_DIR/bin/python" -c "from app.main import app; print('Import OK')"

# 4. Recargar nginx si el conf cambió
if git diff HEAD~1 HEAD --name-only 2>/dev/null | grep -q "dealerscrapper.conf"; then
    sudo cp "$PROJECT_DIR/dealerscrapper.conf" /etc/nginx/conf.d/
    sudo nginx -t && sudo systemctl reload nginx
    echo "nginx config actualizado."
fi

# 5. Reiniciar servicio
sudo systemctl restart "$SERVICE_NAME"
sleep 2
sudo systemctl status "$SERVICE_NAME" --no-pager -l

echo ""
echo "=== Deploy completado ==="
```

---

## Notas de instalación manual

El proceso completo de primer deploy en el VPS es:

```bash
# En el VPS (SSH como opc)
mkdir -p /home/opc/projects
cd /home/opc/projects
git clone <repo-url> dealerscrapper
cd dealerscrapper

# Hacer ejecutable y correr setup
chmod +x scripts/linux/setup.sh
./scripts/linux/setup.sh

# Editar .env con las variables reales
nano .env

# Iniciar
sudo systemctl start dealerscrapper
sudo systemctl status dealerscrapper
```

**Para deploys subsiguientes**:
```bash
cd /home/opc/projects/dealerscrapper
chmod +x scripts/linux/deploy.sh
./scripts/linux/deploy.sh
```

---

## Variables de entorno requeridas (.env)

Ver `.env.example` para la lista completa. Las críticas son:

```
API_KEY=<key-segura>
JOB_BASE_DIR=/tmp/dealerscrapper
LLM_PROVIDER=openai
LLM_API_KEY=<key-del-proveedor>
```
