# X20 Bot — Инструкция установки на Windows

## ⚠️ ВАЖНО: Версия Python

Для Python 3.14 (слишком новая) может быть проблема с установкой некоторых пакетов.

**РЕКОМЕНДУЕТСЯ**: используйте **Python 3.12** или **Python 3.13**.

## 📥 Шаг 1. Установка Python 3.12

1. Откройте https://www.python.org/downloads/release/python-3122/
2. Скачайте **Windows installer (64-bit)** → `python-3.12.2-amd64.exe`
3. Запустите установщик
4. ⚠️ **ВАЖНО**: на первом экране отметьте:
   - ✅ "Add Python 3.12 to PATH" (добавить Python в PATH)
   - ✅ "Install pip"
5. Нажмите "Install Now" и дождитесь завершения
6. Перезагрузитесь или закройте все терминалы

## 🚀 Шаг 2. Подготовка проекта

1. Откройте PowerShell (нажмите `Win+X` → "PowerShell" или "Terminal")
2. Перейдите в папку проекта:
   ```powershell
   cd C:\path\to\x20_production
   ```

3. Создайте виртуальное окружение:
   ```powershell
   python -m venv venv
   ```

4. Активируйте окружение:
   ```powershell
   .\venv\Scripts\Activate.ps1
   ```
   
   Если выскочит ошибка про политику выполнения:
   ```powershell
   Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
   ```
   Затем снова:
   ```powershell
   .\venv\Scripts\Activate.ps1
   ```

   После активации слева в строке должно появиться `(venv)`.

5. Обновите pip (важно!):
   ```powershell
   python -m pip install --upgrade pip
   ```

## 📦 Шаг 3. Установка зависимостей

Для Python 3.12/3.13:
```powershell
pip install -r requirements.txt
```

Для Python 3.14 (если всё ещё используете):
```powershell
pip install -r requirements_win_py314.txt
```

⏳ Это может занять 2–5 минут в первый раз.

## ⚙️ Шаг 4. Конфигурация

1. Откройте `.env.example`:
   ```powershell
   notepad .env.example
   ```

2. Скопируйте содержимое и создайте новый файл `.env`:
   ```powershell
   copy .env.example .env
   notepad .env
   ```

3. Заполните обязательные поля:
   ```
   BOT_TOKEN=YOUR_TELEGRAM_TOKEN_HERE
   OPENAI_API_KEY=YOUR_OPENAI_KEY_HERE
   ADMIN_PASSWORD=change_me_to_secure_password
   ```

4. Сохраните файл (Ctrl+S → Close)

## 🤖 Шаг 5. Запуск бота

Убедитесь, что виртуальное окружение активировано (слева видно `(venv)`).

```powershell
python bot.py
```

Вывод должен быть примерно такой:
```
✅ Dashboard → http://localhost:8080
✅ X20 Final — started
```

## 🔗 Доступ к интерфейсам

- **Telegram**: добавьте бота в чат
- **Dashboard**: http://localhost:8080 (пароль из `.env` ADMIN_PASSWORD)

## 🛑 Если не работает

### Ошибка: "No module named 'aiogram'"
Убедитесь, что виртуальное окружение активировано:
```powershell
.\venv\Scripts\Activate.ps1
```

### Ошибка: "python: command not found"
Python не добавлена в PATH. Переустановите Python 3.12 и отметьте "Add Python to PATH".

### Ошибка при установке пакетов
Если `pip install` падает с ошибкой про C++ компилятор — используйте Python 3.12 вместо 3.14.

### Бот запустился, но не отвечает
1. Проверьте BOT_TOKEN в `.env` (правильный ли скопирован?)
2. Проверьте OPENAI_API_KEY
3. Посмотрите ошибки в консоли

## 📝 Дополнительные команды

**Остановить бот:**
```
Ctrl+C
```

**Деактивировать виртуальное окружение:**
```powershell
deactivate
```

**Удалить виртуальное окружение (если нужно переустановить):**
```powershell
Remove-Item -Recurse venv
```

---

**Удачи! 🚀 Если остались вопросы — проверьте логи в консоли.**
