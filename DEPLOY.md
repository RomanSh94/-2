# X20 — деплой на VPS

Два сценария:

- **Вариант A** — установка с нуля «по правильному» (`/opt/x20`, отдельный пользователь). Для нового сервера.
- **Вариант B** — миграция существующего деплоя (`/root/-2`, crontab `@reboot`) на systemd. Текущий прод.

Общие правила:

- ⚠️ **Только один инстанс бота на токен.** Telegram пускает один `getUpdates` —
  два запущенных бота дают `TelegramConflictError`, и сообщения теряются.
  Перед запуском на сервере убедись, что бот НЕ запущен на ПК (и наоборот).
- **`.env` никогда не коммитится.** Создаётся руками на сервере, `chmod 600`.
- **`x20.db` — боевая база.** Не удалять. Миграций нет; новые таблицы
  `init_db()` досоздаёт сам при старте (`CREATE TABLE IF NOT EXISTS`).

---

## Вариант A — установка с нуля (рекомендуемый для нового сервера)

Ubuntu 22.04/24.04, под root или sudo.

```bash
apt update && apt install -y python3.12 python3.12-venv git
adduser --system --group x20
mkdir -p /opt/x20 && chown x20:x20 /opt/x20

sudo -u x20 git clone https://github.com/RomanSh94/-2.git /opt/x20
cd /opt/x20
sudo -u x20 python3.12 -m venv venv
sudo -u x20 ./venv/bin/pip install --upgrade pip
sudo -u x20 ./venv/bin/pip install -r requirements.txt
```

Секреты:

```bash
nano /opt/x20/.env        # BOT_TOKEN=... OPENAI_API_KEY=... ADMIN_PASSWORD=...
chown x20:x20 /opt/x20/.env && chmod 600 /opt/x20/.env
```

systemd-юнит `/etc/systemd/system/x20.service` (отличается от файла в репо
путями и пользователем):

```ini
[Unit]
Description=X20 Telegram support bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=x20
WorkingDirectory=/opt/x20
EnvironmentFile=/opt/x20/.env
ExecStart=/opt/x20/venv/bin/python bot.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```bash
systemctl daemon-reload
systemctl enable --now x20
systemctl status x20
```

---

## Вариант B — миграция существующего деплоя на systemd (текущий прод)

Сейчас: код в `/root/-2`, запуск через crontab `@reboot`, логи в
`/var/log/x20.log`. Проблема crontab: если бот упал в процессе работы, он
лежит мёртвый до перезагрузки сервера. systemd с `Restart=always`
перезапускает сам через 5 секунд.

### 1. Убрать запуск из crontab

```bash
crontab -e
# удалить (или закомментировать #) строку с @reboot ... bot.py
crontab -l   # проверить, что строки больше нет
```

### 2. Остановить работающий процесс бота

```bash
pgrep -af "python.*bot.py"   # найти PID
kill <PID>
pgrep -af "python.*bot.py"   # убедиться, что пусто
```

### 3. Обновить код и зависимости

```bash
cd /root/-2
git pull origin main
./venv/bin/pip install -r requirements.txt
```

### 4. Поставить systemd-юнит

Файл `x20.service` лежит в корне репозитория, уже с путями `/root/-2`:

```bash
cp /root/-2/x20.service /etc/systemd/system/x20.service
systemctl daemon-reload
systemctl enable --now x20
systemctl status x20          # должно быть active (running)
```

### 5. Проверка

```bash
journalctl -u x20 -f          # живые логи (вместо /var/log/x20.log)
# В Telegram: написать боту с тестового аккаунта, убедиться что отвечает.
```

Если что-то пошло не так: `systemctl stop x20`, смотреть
`journalctl -u x20 -n 100`, чинить, `systemctl start x20`.

---

## Перенос боевой `x20.db` с ПК на сервер

Делается ОТДЕЛЬНО, после того как systemd-миграция проверена.

⚠️ **КРИТИЧНО: остановить локального бота на ПК ПЕРЕД копированием.**
SQLite в режиме WAL держит последние записи в `x20.db-wal` — если копировать
на горячую, база на сервере окажется без последних данных или битой.
Копировать нужно ВСЕ три файла (`x20.db`, `x20.db-wal`, `x20.db-shm`;
`-wal`/`-shm` могут отсутствовать после чистой остановки — это нормально).

Порядок (ни в один момент не должно быть двух работающих ботов):

```powershell
# 1. На ПК: остановить локального бота (Ctrl+C в его окне / закрыть процесс).
```

```bash
# 2. На сервере: остановить серверного бота.
systemctl stop x20

# 3. Бэкап пустой/старой серверной базы — НЕ удаляем, переименовываем:
cd /root/-2
mv x20.db x20.db.bak-$(date +%F) 2>/dev/null
mv x20.db-wal x20.db-wal.bak-$(date +%F) 2>/dev/null
mv x20.db-shm x20.db-shm.bak-$(date +%F) 2>/dev/null
```

```powershell
# 4. На ПК (PowerShell, из папки проекта) — копируем все файлы базы:
scp x20.db x20.db-wal x20.db-shm root@SERVER_IP:/root/-2/
# если -wal/-shm нет — скопировать только x20.db, это ок
```

```bash
# 5. На сервере: запустить и проверить.
systemctl start x20
journalctl -u x20 -n 50
# Написать боту: история/память пользователей должна быть на месте.
```

---

## Дашборд (Flask, порт 8080)

Слушает только `127.0.0.1` — снаружи недоступен, и это правильно (простой
пароль, нет HTTPS). Смотреть через SSH-туннель:

```bash
ssh -L 8080:127.0.0.1:8080 root@SERVER_IP
# затем открыть http://localhost:8080 в браузере
```

nginx + Let's Encrypt — только когда понадобится доступ внешним людям.
