# Турнирный Telegram-бот (Школа 21) — aiogram 3 + SQLite

Бот для турнира по настольному теннису:
- формат B1 (олимпийка / single elimination)
- 16 участников (если меньше — старт на 8/4/2)
- динамическая очередь матчей (без расписания)
- 2 стола
- вызов на матч: 5 минут
- 1 задержка (⏳), второй раз = техпоражение
- подтверждение результата / спор → admin_review

## 1) Установка

### Windows (PowerShell)
```powershell
cd "C:\Users\...\Desktop\Турнир Школа 21"
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

## 2) Настройка .env

Создай файл `.env` в корне проекта:

```env
BOT_TOKEN=ВАШ_ТОКЕН
ADMIN_IDS=123456789,987654321
DB_PATH=tournament.db
```

> Совет: если у тебя часто меняется рабочая папка (VS Code запускает из другого места), можно указать абсолютный путь:
> `DB_PATH=C:\Users\Davlatov Abdullojon\Desktop\Турнир Школа 21\tournament.db`

## 3) Запуск
```powershell
python main.py
```
