#!/usr/bin/env bash
# X20 — Вариант A: установка с нуля на НОВОМ сервере под отдельным
# непривилегированным пользователем (/opt/x20, user x20, без root).
# Идемпотентно — можно прогонять повторно.
#
# ВНИМАНИЕ: это для НОВОГО сервера. Действующий прод (/root/-2) этот скрипт НЕ
# трогает. Перенос боевой базы x20.db — отдельным шагом (см. deploy/README.md).
#
# Запуск (на новом сервере, под root):
#   bash deploy/setup_opt.sh
set -euo pipefail

REPO="${X20_REPO:-https://github.com/RomanSh94/-2.git}"
DIR=/opt/x20

echo "==> Пакеты"
apt-get update -y
apt-get install -y python3 python3-venv git

echo "==> Пользователь x20"
id -u x20 >/dev/null 2>&1 || adduser --system --group x20
mkdir -p "$DIR"
chown x20:x20 "$DIR"

echo "==> Код"
if [ -d "$DIR/.git" ]; then
  sudo -u x20 git -C "$DIR" pull --ff-only
else
  sudo -u x20 git clone "$REPO" "$DIR"
fi

echo "==> venv + зависимости"
sudo -u x20 python3 -m venv "$DIR/venv"
sudo -u x20 "$DIR/venv/bin/pip" install --upgrade pip
sudo -u x20 "$DIR/venv/bin/pip" install -r "$DIR/requirements.txt"

if [ ! -f "$DIR/.env" ]; then
  echo "==> .env не найден — создаю шаблон (ЗАПОЛНИ вручную!)"
  sudo -u x20 cp "$DIR/.env.example" "$DIR/.env" 2>/dev/null || \
    sudo -u x20 bash -c "printf 'BOT_TOKEN=\nOPENAI_API_KEY=\nADMIN_PASSWORD=\n' > $DIR/.env"
  chmod 600 "$DIR/.env"; chown x20:x20 "$DIR/.env"
  echo "    !!! Впиши BOT_TOKEN / OPENAI_API_KEY / ADMIN_PASSWORD в $DIR/.env"
fi

echo "==> systemd unit"
cp "$DIR/deploy/x20-opt.service" /etc/systemd/system/x20.service
systemctl daemon-reload
systemctl enable x20

cat <<'EOF'

==> Готово (но НЕ запущено).
Дальше:
  1) Заполни /opt/x20/.env (токены).
  2) Перенеси боевую x20.db (если переезжаешь со старого сервера):
       - на старом: systemctl stop x20
       - scp x20.db x20.db-wal x20.db-shm на новый в /opt/x20/, chown x20:x20
  3) systemctl start x20 && systemctl status x20
  4) Убедись, что СТАРЫЙ инстанс остановлен (один getUpdates на токен!).
EOF
