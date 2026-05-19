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
