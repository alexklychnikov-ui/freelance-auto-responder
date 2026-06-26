# Freelance Auto-Responder

Автоматический пайплайн откликов на фриланс-проекты (сейчас Kwork): daemon каждые **15 минут** сканирует ленту → для новых проектов подтягивает контекст из **LightRAG** и примеры откликов → **GPT** оценивает fit и риски → в **Telegram** приходит карточка с кнопкой «Откликнуть». После approve бот генерирует текст, оценивает цену/срок и **заполняет черновик формы Kwork** (Playwright на VPS): поэтапная оплата, 2+ задачи с суммами, описание, цена, срок — **без нажатия «Предложить»**. Подтверждённый отклик попадает в Excel-журнал на ПК через `Sync-Journal.bat`. Синхронизация статусов с Kwork (`/offers`) обновляет журнал и тип проекта.

## Архитектура

| Где | Роль |
|-----|------|
| **VPS** (`/opt/freelance-responder`) | daemon: скан, GPT, TG-бот, Playwright prepare на Kwork |
| **ПК (Windows)** | разработка, Kwork-login, синхронизация Excel |
| **LightRAG** | на том же VPS (`http://127.0.0.1:9621`) |
| **Excel** | только локально: `C:\Python\Projects\Zerocode2md\ResponseJournal\journal.xlsx` (лист «Мои отклики») |

VPS **не** пишет в твой локальный Excel. После prepare отклик лежит в `data/prepared_responses/*.json` на сервере. На ПК запускаешь `Sync-Journal.bat` — строки подтягиваются в `journal.xlsx`.

---

## Последние изменения (июнь 2025)

| Область | Что сделано |
|---------|-------------|
| **Интервал скана** | `SCAN_INTERVAL_MINUTES` и daemon: **15 мин** (было 30) |
| **Prepare Kwork** | Клик «По мере выполнения задач» до заполнения этапов; reassert после price/deadline |
| **Черновик этапов** | Сохранение через `updatePaymentType` + `updateDataStages` + `changeDraftContent` (без `setRequestDataStages`) |
| **Verify prepare** | Проверка этапов, milestone, цены и описания в той же сессии; retry GPT+форма (2 попытки) |
| **TG** | Кнопка «Заполнить форму снова» при ошибке prepare; `/journal_sync` для синка Excel |
| **Журнал Excel** | Гиперссылки в колонке D; тип проекта при sync с `/offers`; repair строк |
| **VPS sync** | `deploy/sync_journal_from_vps.py`, `sync_journal_on_vps.py` — offers + prepared на сервере |

---

## Что в Git, что нет

**В репозитории:** `src/`, `config/`, `deploy/`, `tests/`, `.env.example`, `requirements.txt`.

**Не коммитить** (см. `.gitignore`):

- `.env` — ключи
- `data/kwork_storage.json`, `data/kwork_browser_profile/` — сессия Kwork
- `data/seen_projects.db`, `data/prepared_responses/`, `data/pending_offers/`
- `logs/`

Перед push: `git status` — секретов и runtime-данных быть не должно.

---

## Восстановление ПК (Windows)

### 1. Клонировать и окружение

```powershell
git clone <repo-url> "C:\Python\Projects\Freelance Auto-Responder"
cd "C:\Python\Projects\Freelance Auto-Responder"
python -m venv venv
.\venv\Scripts\activate
pip install -r requirements.txt
playwright install chromium
```

### 2. `.env`

```powershell
copy .env.example .env
```

Заполнить минимум:

| Переменная | ПК |
|------------|-----|
| `OPENAI_API_KEY` | ключ OpenAI / ProxyAPI |
| `TELEGRAM_BOT_TOKEN` | токен бота |
| `TELEGRAM_CHAT_ID` | твой chat id |
| `RESPONSE_JOURNAL` | `C:/Python/Projects/Zerocode2md/ResponseJournal/journal.xlsx` |
| `RESPONSE_EXAMPLES_DIR` | `C:/Python/Projects/Zerocode2md/Output/Отклики` |
| `BROWSER_ADAPTER` | `external` (локальная отладка) или `playwright` |

Для локальной отладки браузера через BrowserMCP — `BROWSERMCP_SERVER`.

### 3. SSH к VPS

В `~/.ssh/config`:

```
Host LightRAG_Naive
    HostName 45.144.28.49
    User root
    IdentityFile C:/Users/User/.ssh/alexklyvibe
```

Имя хоста используется в `deploy/*.ps1`, `deploy/*.py`, `Sync-Journal.bat`.

### 4. Excel-журнал (ярлык на рабочий стол)

Скопировать на рабочий стол:

```powershell
copy deploy\Sync-Journal.bat "%USERPROFILE%\Desktop\Sync-Journal.bat"
```

**Порядок:** закрыть Excel → двойной клик `Sync-Journal.bat` → откроется `journal.xlsx`.

### 5. Сессия Kwork (раз в N недель / после logout)

```powershell
.\venv\Scripts\activate
python deploy\kwork_login_interactive.py
```

1. Залогиниться в открывшемся Chrome  
2. Enter в терминале  
3. Скрипт сохранит `data/kwork_storage.json` и зальёт на VPS + перезапустит daemon  

Если интерактивный скрипт упал после логина:

```powershell
python deploy\export_kwork_profile_session.py
```

### 6. Локальные тесты

```powershell
pytest
python -m src.scheduler run-test   # нужен рабочий .env и браузер
```

---

## Восстановление VPS (с нуля)

**Путь:** `/opt/freelance-responder`  
**Сервис:** `freelance-responder.service`  
**Логи:** `/opt/freelance-responder/logs/daemon.log`

### 1. Подготовка сервера

```bash
ssh LightRAG_Naive
mkdir -p /opt/freelance-responder/{data/examples,data/prepared_responses,data/pending_offers,logs}
```

### 2. Деплой с ПК

```powershell
cd "C:\Python\Projects\Freelance Auto-Responder"
.\deploy\deploy.ps1
```

Скрипт копирует `src`, `config`, `deploy`, `.env`, примеры откликов, журнал-шаблон и запускает `install.sh`.

### 3. Ручной деплой (без deploy.ps1)

```powershell
scp -r src config deploy requirements.txt LightRAG_Naive:/opt/freelance-responder/
scp .env LightRAG_Naive:/opt/freelance-responder/.env
scp "C:\Python\Projects\Zerocode2md\ResponseJournal\journal.xlsx" LightRAG_Naive:/opt/freelance-responder/data/response_journal.xlsx
scp -r "C:\Python\Projects\Zerocode2md\Output\Отклики\*" LightRAG_Naive:/opt/freelance-responder/data/examples/
ssh LightRAG_Naive "bash /opt/freelance-responder/deploy/install.sh"
```

### 4. `.env` на VPS (отличия от ПК)

`install.sh` нормализует пути. Проверить вручную:

```bash
grep -E '^(BROWSER_ADAPTER|OPENAI_BASE_URL|LIGHTRAG|KWORK|PREPARE|RESPONSE)' /opt/freelance-responder/.env
```

| Переменная | VPS |
|------------|-----|
| `BROWSER_ADAPTER` | `playwright` |
| `OPENAI_BASE_URL` | `https://api.openai.com/v1` |
| `LIGHTRAG_BASE_URL` | `http://127.0.0.1:9621` |
| `LIGHTRAG_API_KEY` | из `/opt/LightRAG/.env` |
| `KWORK_AUTO_LOGIN` | `false` (капча) |
| `KWORK_STORAGE_STATE` | `/opt/freelance-responder/data/kwork_storage.json` |
| `PREPARE_ONLY_NO_SUBMIT` | `true` |
| `SCAN_INTERVAL_MINUTES` | `15` |
| `RESPONSE_JOURNAL` | `/opt/freelance-responder/data/response_journal.xlsx` (не используется для твоего Excel) |
| `RESPONSE_EXAMPLES_DIR` | `/opt/freelance-responder/data/examples` |

### 5. Kwork-сессия на VPS

С ПК после логина:

```powershell
python deploy\export_kwork_profile_session.py
# или
python deploy\kwork_login_interactive.py
```

Проверка:

```bash
ssh LightRAG_Naive "cd /opt/freelance-responder && PYTHONPATH=. .venv/bin/python deploy/verify_kwork_session.py"
```

### 6. Запуск и проверка

```bash
sudo systemctl start freelance-responder
sudo systemctl enable freelance-responder
systemctl status freelance-responder
tail -f /opt/freelance-responder/logs/daemon.log
```

Тестовый прогон:

```bash
cd /opt/freelance-responder
PYTHONPATH=. .venv/bin/python -m src.scheduler run-test
```

---

## Обновление после `git push`

### ПК

```powershell
git pull
.\venv\Scripts\activate
pip install -r requirements.txt
```

### VPS

```powershell
# с ПК
scp -r src config deploy requirements.txt LightRAG_Naive:/opt/freelance-responder/
ssh LightRAG_Naive "cd /opt/freelance-responder && .venv/bin/pip install -r requirements.txt && sudo systemctl restart freelance-responder"
```

Или полный `deploy\deploy.ps1` (перезапишет `.env` на VPS — осторожно).

---

## Рабочий процесс (TG)

1. Daemon находит проект → карточка в Telegram  
2. **Откликнуть** → GPT-черновик  
3. Reply на черновик с финальным текстом  
4. Бот заполняет форму Kwork (без «Предложить») + скрин  
5. На ПК: **`Sync-Journal.bat`** → строка в `journal.xlsx`

Команда `/journal` в TG **не используется** — Excel только через bat на ПК.

---

## Бэкап runtime-данных (не в Git)

Периодически сохранять:

| Что | ПК | VPS |
|-----|-----|-----|
| Секреты | `.env` | `/opt/freelance-responder/.env` |
| Kwork session | `data/kwork_storage.json` | то же на VPS |
| Очередь Excel | — | `data/prepared_responses/*.json` |
| Pending TG | — | `data/pending_offers/*.json` |
| БД скана | `data/seen_projects.db` | то же |
| Excel | `journal.xlsx` | — |

Пример бэкапа VPS:

```bash
tar czf /root/freelance-backup-$(date +%F).tar.gz \
  /opt/freelance-responder/.env \
  /opt/freelance-responder/data/kwork_storage.json \
  /opt/freelance-responder/data/prepared_responses \
  /opt/freelance-responder/data/pending_offers \
  /opt/freelance-responder/data/seen_projects.db
```

---

## Полезные скрипты

| Скрипт | Назначение |
|--------|------------|
| `deploy/deploy.ps1` | полный деплой на VPS |
| `deploy/install.sh` | venv + playwright + systemd на VPS |
| `deploy/kwork_login_interactive.py` | логин Kwork → session |
| `deploy/export_kwork_profile_session.py` | экспорт session из профиля |
| `deploy/sync_journal_from_vps.py` | VPS → локальный Excel |
| `deploy/Sync-Journal.bat` | bat для ПК (копировать на Desktop) |
| `deploy/verify_kwork_session.py` | проверка логина на VPS |
| `deploy/rerun_prepare.py` | повторный prepare по project_id |
| `deploy/probe_draft_stages_persist.py` | проверка сохранения этапов в черновик |
| `deploy/reset_journal_exported.py` | сброс флага для повторной синхронизации |

---

## Troubleshooting

| Симптом | Решение |
|---------|---------|
| Кнопки TG не работают | `systemctl status freelance-responder` — нужен daemon, не только run-test |
| Prepare: этапы пустые после reload | обновить `kwork.py`; проверить milestone + autosave 15s; F5 на `new_offer` |
| Prepare: not_logged_in | обновить `kwork_storage.json` с ПК |
| Prepare: greenlet error | обновить `src/pipeline/orchestrator.py` на VPS |
| Sync-Journal: дубли / неверный № | закрыть Excel, `reset_journal_exported.py` на VPS, bat снова |
| Excel пустой после bat | на VPS нет `journal_exported=false` в `prepared_responses/` |
| 0 проектов в скане | проверить Kwork-селекторы / сессию |

---

## Структура проекта

```
src/
  adapters/          # Kwork, auth, pricing
  analyzer/          # GPT scorer, LightRAG client
  browser/           # Playwright / MCP
  journal/           # Excel writer
  pipeline/          # orchestrator
  telegram_bot/      # aiogram bot
  responses/         # prepared_store
deploy/              # деплой, sync, kwork login
config/sources.yaml  # источники скана
data/                # runtime (не в git)
tests/
```
