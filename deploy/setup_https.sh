#!/usr/bin/env bash
# X20 — публикация дашборда наружу по HTTPS (nginx + Let's Encrypt).
#
# ПРЕДУСЛОВИЯ (без них не запускать):
#   1) Есть домен (или сабдомен), A-запись которого указывает на IP сервера.
#   2) В Hetzner firewall открыт порт 443 (и 80 для выпуска сертификата).
#   3) Ты осознанно хочешь открыть дашборд наружу. До этого SSH-туннель —
#      достаточно и безопаснее (дашборд защищён лишь одним паролем).
#
# Запуск (под root):
#   DOMAIN=dash.example.com EMAIL=you@example.com bash deploy/setup_https.sh
set -euo pipefail

: "${DOMAIN:?Укажи DOMAIN=твой.домен}"
: "${EMAIL:?Укажи EMAIL=твоя@почта (для Let's Encrypt)}"

apt-get update -y
apt-get install -y nginx certbot python3-certbot-nginx

CONF=/etc/nginx/sites-available/x20.conf
sed "s/DASHBOARD_DOMAIN/${DOMAIN}/g" "$(dirname "$0")/nginx-x20.conf" > "$CONF"
ln -sf "$CONF" /etc/nginx/sites-enabled/x20.conf
nginx -t && systemctl reload nginx

# Выпуск/установка сертификата и авто-настройка TLS в nginx.
certbot --nginx -d "$DOMAIN" --non-interactive --agree-tos -m "$EMAIL" --redirect

echo "==> Готово. Дашборд: https://${DOMAIN}"
echo "    Сертификат продлевается автоматически (certbot.timer)."
echo "    Проверь, что порт 443 открыт в Hetzner firewall."
