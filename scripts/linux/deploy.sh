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
