# Промпт проекта: Freelance Auto-Responder (мультиплощадка)

> Скопируй этот документ в новый репозиторий как стартовое ТЗ для агента / `/team-workflow`.
> Зависимости: **BrowserMCP**, **LightRAG**, **ProxyAPI (GPT)**, **Telegram Bot**, **Zerocode2md ResponseJournal**.
> **MVP:** Kwork «Разработка и IT». **Масштабирование:** FL.ru, Telegram-каналы и др. через адаптеры.

---

## Цель

Автоматизировать поиск и отклики на **новые** проекты на фриланс-площадках (сначала Kwork, далее — другие источники):

1. Периодически просматривать новые заказы **по каждой включённой площадке**.
2. Полностью вычитывать описание задачи.
3. Сопоставлять задачу с моим техническим стеком (LightRAG + GPT через ProxyAPI).
4. Если подходит — отправить карточку в Telegram и **ждать моего подтверждения**.
5. После подтверждения — отправить отклик на площадке (Kwork: «Предложить услугу»; FL.ru: «Откликнуться»; TG: ответ в канал/ЛС — по типу источника).
6. Зафиксировать отклик в Excel-журнале (колонка «Площадка»).
7. При ответе заказчика — уведомить меня в Telegram.

**Без моего явного подтверждения в Telegram отклик НЕ отправлять.**

---

## Контекст исполнителя

- **Имя:** Александр Клычников
- **Профиль:** Python / AI / Telegram-боты / автоматизация / MVP fullstack
- **Стек:** Python, FastAPI, aiogram, PostgreSQL, Docker, OpenAI API (ProxyAPI), RAG (LightRAG), Next.js (базово), GitHub Actions, VPS
- **Портфолио:** https://portfolio.hayklyvibelexy.ru · https://github.com/alexklychnikov-ui
- **Формат:** удалёнка, MVP за короткий цикл, без лишней архитектуры на старте
- **Целевые задачи:** парсинг, Telegram-боты, AI/RAG, API-интеграции, автоматизация, небольшие веб-MVP

---

## Инфраструктура

| Компонент | Путь / подключение |
|-----------|-------------------|
| BrowserMCP server | `C:\Python\Projects\BrowserMCP\packages\mcp-server\dist\index.js` |
| Chrome extension | `C:\Python\Projects\BrowserMCP\packages\extension` (Load unpacked + Connect на вкладке Kwork) |
| LightRAG MCP | `user-lightrag` → `search_knowledge_base` (mode: `mix`) |
| Журнал откликов | `C:\Python\Projects\Zerocode2md\ResponseJournal\Мои отклики.xlsx` |
| Примеры откликов | `C:\Python\Projects\Zerocode2md\Output\Отклики\` |
| Правила документов | LightRAG: `Zerocoder_OV05_Resume_SP_KP.md`, `Zerocoder - OV05. Резюме, СП, КП.md` |
| AI анализ | ProxyAPI OpenAI-compatible (`OPENAI_API_KEY`, `OPENAI_BASE_URL`, `OPENAI_MODEL` из `.env`) |
| Telegram | `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` (мой личный chat для approve/reject) |

---

## Источники данных (площадки)

Архитектура: **единый pipeline** + **адаптер на площадку** (`PlatformAdapter`: scan, read_full, submit, monitor_reply).

### MVP — Kwork

| Параметр | Значение |
|----------|----------|
| `platform_id` | `kwork` |
| `source_key` | `kwork_dev_it` |
| URL ленты | `https://kwork.ru/projects?c=41` («Разработка и IT») |
| Отладка | `https://kwork.ru/projects?c=all` |
| ID проекта | число из `/projects/{id}` |
| Кнопка отклика | «Предложить услугу» |
| Auth | сессия Chrome (ручной логин) |

На карточке: заголовок, ссылка, бюджет желаемый/допустимый, предложения, покупатель, % нанято, дедлайн, описание.  
**Обязательно** открывать страницу проекта и читать полное ТЗ.

### Фаза 2 — FL.ru

| Параметр | Значение |
|----------|----------|
| `platform_id` | `flru` |
| `source_key` | `flru_ai` (пример; другие категории — отдельные `source_key`) |
| URL ленты | `https://www.fl.ru/projects/category/ai-iskusstvenniy-intellekt/` |
| Альтернативы | `.../programmirovanie/`, подкатегории из конфига |
| ID проекта | slug или id из URL заказа (зафиксировать при парсинге) |
| Кнопка отклика | «Откликнуться» |
| Auth | сессия Chrome на fl.ru |
| Особенности | на ленте смешаны **Заказ** / **Вакансия** — фильтровать по `listing_type`; вакансии с «фуллтайм» по умолчанию **skip** (настраивается) |

На карточке FL.ru: заголовок, бюджет (диапазон или «по договорённости»), время публикации («12 часов назад»), число ответов, тип (заказ/вакансия), краткий текст.

### Фаза 3 — Telegram-каналы

| Параметр | Значение |
|----------|----------|
| `platform_id` | `telegram` |
| `source_key` | `tg_job_webdev`, `tg_rabota_v_ii`, … (из конфига) |
| ID проекта | `{channel_id}_{message_id}` (уникально в рамках TG) |
| Scan | **не BrowserMCP** — Telethon / aiogram + `getUpdates` или MTProto userbot (отдельный модуль) |
| Отклик | часто **ссылка на заказчика** или ответ в обсуждении канала — `submit` может быть **semi-auto**: бот генерирует текст → TG approve → пользователь вставляет вручную или через forward |
| Auth | `TELEGRAM_API_ID`, `TELEGRAM_API_HASH` (userbot) или bot read-only + ручной forward |

**Важно:** канал только для **мониторинга**; approve/submit pipeline тот же. Если автопост в канал невозможен — статус `pending_manual_send` + текст отклика в TG.

### Конфиг площадок (`config/sources.yaml`)

```yaml
sources:
  - id: kwork_dev_it
    platform: kwork
    enabled: true
    url: https://kwork.ru/projects?c=41
    scan_interval_minutes: 30
    bootstrap: true

  - id: flru_ai
    platform: flru
    enabled: false          # включить после MVP
    url: https://www.fl.ru/projects/category/ai-iskusstvenniy-intellekt/
    scan_interval_minutes: 45
    filters:
      listing_types: [order]   # order | vacancy
      skip_fulltime: true

  - id: tg_job_webdev
    platform: telegram
    enabled: false
    channel: "@job_webdev"       # или numeric id
    scan_interval_minutes: 15
    last_message_id: 0           # watermark для TG
```

Scheduler обходит **только `enabled: true`** источники; у каждого свой `scan_state` и дедупликация.

---

## Периодичность

- **Интервал по умолчанию:** каждые **30 минут** (настраивается в конфиге `SCAN_INTERVAL_MINUTES`).
- **Окно работы (опционально):** 08:00–23:00 Иркутск (UTC+8).
- При первом запуске — не откликаться массово; только scan + notify (см. **Дедупликация** ниже).

---

## Дедупликация: только вновь поступившие заказы

**Правило:** при каждом периодическом SCAN обрабатывать **только проекты, которых ещё не было** в предыдущих циклах. Повторно не гонять GPT/LightRAG/Telegram по уже виденным карточкам.

### Что хранить (SQLite `data/seen_projects.db`)

Таблица `projects` (минимум):

| Поле | Назначение |
|------|------------|
| `platform` | `kwork` \| `flru` \| `telegram` |
| `source_key` | id из `config/sources.yaml` (напр. `kwork_dev_it`, `flru_ai`) |
| `project_id` | id **внутри площадки** (не глобально уникален!) |
| PK | `(platform, source_key, project_id)` |
| `first_seen_at` | ISO datetime первого обнаружения |
| `published_at` | время публикации с карточки (если доступно) |
| `status` | `new` → `scored` → `notified` → `rejected` → `submitted` → `skipped` → `pending_manual_send` |
| `fit` | bool, результат GPT |
| `score` | 0–10 |
| `title` | для логов |
| `url` | прямая ссылка на заказ |

Таблица `scan_state` (**одна строка на `source_key`**, не на категорию целиком):

| Поле | Назначение |
|------|------------|
| `platform` | kwork / flru / telegram |
| `source_key` | напр. `kwork_dev_it`, `flru_ai`, `tg_job_webdev` |
| `last_scan_at` | когда последний раз сканировали источник |
| `last_known_project_id` | watermark (для kwork/flru — max id; для TG — `last_message_id`) |
| `last_new_project_at` | время последнего **нового** проекта в этом источнике |

Файл `data/scan_state.json` допустим на MVP вместо второй таблицы — но логика та же.

### Алгоритм «только новые» на каждом цикле

Для **каждого** `source_key` с `enabled: true`:

1. Открыть URL источника (BrowserMCP для kwork/flru; Telethon для telegram).
2. Собрать карточки **с первой страницы / последних N сообщений** (новые сверху).
3. Для каждой карточки:
   - ключ `(platform, source_key, project_id)` **уже в БД** → пропустить;
   - **новый** → `status=new`, дальше pipeline.
4. Обновить `scan_state` для этого `source_key`.
5. Early-exit: N подряд известных карточек → стоп scan источника.

**Kwork:** `https://kwork.ru/projects?c=41`  
**FL.ru:** категория из конфига, напр. [AI на FL.ru](https://www.fl.ru/projects/category/ai-iskusstvenniy-intellekt/)  
**Telegram:** сообщения с `message_id > last_message_id` в канале

### Первый запуск (bootstrap)

- **Не** прогонять GPT/Telegram по всей истории биржи.
- Первый SCAN: записать все id с page 1 в БД со `status=skipped`, `fit=false`, заметка `bootstrap_seen`.
- Со **второго** цикла — в pipeline попадают только id, которых не было на bootstrap.
- Альтернатива: при первом запуске сохранить только `last_known_project_id` с page 1 и обрабатывать всё, что появится **после** него.

### Критерий «новый заказ»

Проект считается новым, если выполняется **хотя бы одно**:

- `project_id` отсутствует в `projects`;
- `project_id > last_known_project_id` (watermark; id на Kwork монотонно растут — использовать как быстрый фильтр);
- `published_at > last_scan_at` (если время публикации парсится с сайта).

Если watermark и БД расходятся — приоритет у **наличия записи в БД**.

### Что НЕ считать повторной обработкой

- Повторный SCAN той же карточки в ленте — игнор.
- Проект со `status=rejected` (ты нажал «Пропустить» в TG) — не слать в TG снова.
- Проект со `status=submitted` — только MONITOR_REPLY, без повторного отклика.

### Лог каждого цикла

```
[scan] 2026-06-21 14:30 | platform=kwork source=kwork_dev_it | seen=12 | new=2 | skipped=10
[scan] 2026-06-21 14:45 | platform=flru source=flru_ai | seen=20 | new=1 | skipped=19
[scan] 2026-06-21 14:50 | platform=telegram source=tg_job_webdev | seen=5 | new=0
```

---

## Масштабирование: адаптеры площадок

Единый контракт `PlatformAdapter`:

```python
class PlatformAdapter(Protocol):
    platform_id: str
    source_key: str

    def scan_new(self) -> list[ProjectPreview]: ...
    def read_full(self, project_id: str) -> ProjectFull: ...
    def submit_response(self, project_id: str, text: str, price: str | None) -> SubmitResult: ...
    def monitor_replies(self) -> list[ReplyEvent]: ...
```

| Модуль | Площадка | Транспорт |
|--------|----------|-----------|
| `adapters/kwork.py` | Kwork | BrowserMCP |
| `adapters/flru.py` | FL.ru | BrowserMCP |
| `adapters/telegram_channel.py` | TG-каналы | Telethon / Bot API |
| `analyzer.py` | все | LightRAG + ProxyAPI GPT |
| `telegram_bot.py` | все | approve/reject (тот же бот) |
| `journal.py` | все | Excel, колонка «Площадка» = Kwork / FL.ru / Telegram |

**Общий JSON проекта** (независимо от площадки):

```json
{
  "platform": "flru",
  "source_key": "flru_ai",
  "project_id": "...",
  "url": "https://www.fl.ru/projects/...",
  "title": "...",
  "listing_type": "order",
  "full_description": "...",
  "budget_min": 6000,
  "budget_max": null,
  "budget_text": "6 000 руб",
  "responses_count": 7,
  "published_at": "2026-06-20T15:53:00",
  "buyer": null,
  "tags": ["RAG", "Whisper"]
}
```

Telegram в TG_REVIEW:

```
🆕 FL.ru · AI
📌 {title}
...
[✅ Откликнуть] [❌ Пропустить]
```

Лимит откликов: `MAX_DAILY_RESPONSES` **на все площадки суммарно** (или per-platform в конфиге).

---

## Алгоритм (state machine)

```
SCAN → READ_FULL → LIGHT_RAG_CONTEXT → GPT_SCORE → [reject | TELEGRAM_REVIEW]
TELEGRAM_REVIEW → [user reject | user approve]
user approve → KWORK_LOGIN_CHECK → FILL_OFFER → SUBMIT → EXCEL_LOG → MONITOR_REPLY
MONITOR_REPLY → TELEGRAM_NOTIFY → EXCEL_UPDATE
```

### Шаг 1. SCAN — сбор новых проектов

1. Через BrowserMCP: `browser_navigate` → URL рубрики IT (`c=41`).
2. `browser_snapshot` / CDP `Runtime.evaluate` — извлечь карточки **первой страницы** (сверху вниз = от новых к старым).
3. **Дедупликация** (обязательно): пропустить `project_id`, уже есть в `seen_projects.db`; обрабатывать только записи со `status=new` в этом цикле.
4. Early-exit: если подряд ≥5 карточек уже в БД — прекратить scan страницы.
5. Для каждого **нового** — перейти на `https://kwork.ru/projects/{id}`, затем обновить `scan_state.last_scan_at` и watermark.

### Шаг 2. READ_FULL — полное чтение задачи

1. Раскрыть полное описание (клик «Показать полностью» если есть).
2. Собрать структуру:

```json
{
  "project_id": "3201949",
  "url": "https://kwork.ru/projects/3201949",
  "title": "...",
  "full_description": "...",
  "desired_budget": "до 5 000 ₽",
  "max_budget": "до 15 000 ₽",
  "offers_count": 16,
  "buyer": "username",
  "buyer_projects_count": 73,
  "buyer_hire_rate": "73%",
  "time_left": "1 д. 19 ч.",
  "tags": []
}
```

### Шаг 3. LIGHT_RAG_CONTEXT — контекст стека и правил отклика

Вызвать LightRAG **дважды** (mode `mix`, `only_need_context: true`):

**Запрос A — стек и релевантные кейсы:**
```
Технический стек Александра Клычниковова, проекты портфолио, опыт Python AI Telegram парсинг FastAPI Docker. Что из этого применимо к фриланс-задачам разработки?
```

**Запрос B — правила отклика:**
```
Правила отклика на фриланс-проект: структура сопроводительного письма, ошибки, формула первого абзаца, что писать для python-разработчика. Примеры хорошего и плохого отклика.
```

Дополнительно читать локальные примеры:
- `C:\Python\Projects\Zerocode2md\Output\Отклики\*.md`

### Шаг 4. GPT_SCORE — оценка применимости (ProxyAPI)

Отправить в GPT (через ProxyAPI) системный промпт + данные проекта + контекст LightRAG.

**Системный промпт (шаблон):**

```
Ты — ассистент фрилансера Александра Клычниковова (Python/AI/Telegram/MVP).

Оцени проект Kwork на соответствие стеку и целевым задачам.

Критерии «подходит» (score >= 7/10):
- Python, боты, парсинг, API, AI/LLM, RAG, автоматизация, FastAPI, интеграции, небольшой веб-MVP
- Бюджет желательный >= 3000 ₽ или допустимый >= 8000 ₽ (исключения — очень низкая конкуренция)
- Задача реализуема одним разработчиком за разумный срок

Критерии «не подходит»:
- Чистый дизайн, 1С, мобильная нативная разработка без Python
- WordPress/Тильда без кастомного кода (если не оговорено)
- Бюджет до 1000 ₽ при высокой конкуренции (>20 откликов)
- Заказчик ищет сотрудника в штат, не проект
- Задача явно вне стека и нет смежного кейса

Верни СТРОГО JSON:
{
  "score": 0-10,
  "fit": true|false,
  "reason": "кратко почему",
  "matched_skills": ["..."],
  "risks": ["..."],
  "suggested_project_type": "Парсинг | Telegram-бот | AI/RAG | Веб-MVP | Интеграция | ...",
  "competition_level": "low|medium|high",
  "recommendation": "откликаться|пропустить|наблюдать"
}
```

Если `fit === false` или `score < 7` → пометить проект как просмотренный, **не слать в Telegram**.

### Шаг 5. TELEGRAM_REVIEW — согласование со мной

Отправить сообщение в Telegram (inline-кнопки):

```
🆕 {platform_label} · {source_key}
📌 {title}
💰 {desired_budget} / {max_budget}
👥 Откликов: {offers_count} · Покупатель: {buyer} ({buyer_hire_rate})
⏱ {time_left}
🔗 {url}

📊 Оценка GPT: {score}/10 — {reason}
✅ Стек: {matched_skills}
⚠️ Риски: {risks}

📝 Кратко:
{first_300_chars_of_description}

[✅ Откликнуть] [❌ Пропустить] [👁 Открыть]
```

- Сохранить `pending_offers/{platform}_{source_key}_{project_id}.json` с полным контекстом.
- **Ждать callback** `approve` / `reject` (таймаут 24ч → auto reject).
- При `reject` — только лог, без отклика.

### Шаг 6. SUBMIT — отклик на площадке (только после approve)

**Предусловие:** залогинен на площадке в Chrome (kwork, fl.ru) или доступ к TG. Если нет — Telegram: «Нужен ручной логин на {platform}», пауза.

| Площадка | Действие |
|----------|----------|
| Kwork | «Предложить услугу» |
| FL.ru | «Откликнуться» |
| Telegram | текст в TG → `pending_manual_send` или userbot |

1. `browser_navigate` → URL проекта (kwork/flru).
2. Клик по кнопке отклика (адаптер площадки).
3. Сгенерировать текст отклика через GPT + LightRAG.

**Промпт генерации отклика:**

```
Напиши отклик на {platform} для проекта.

Правила (из LightRAG):
- Первый абзац: внимание + фокус + польза (конкретно про ЭТОТ проект)
- Показать понимание задачи (2-4 буллета)
- Релевантный кейс (1-2 ссылки GitHub/портфолио)
- Сроки/бюджет — ориентир, не выдумывать точную цену без ТЗ
- Тон: уверенный, по делу, без «здравствуйте уважаемые»
- Длина: 1500-2500 знаков (Kwork/FL.ru); для TG-канала — короче, до 1200
- Не копировать шаблон дословно

Контекст проекта: {full_project_json}
Мой стек и кейсы: {lightrag_context}
Пример стиля: {path_to_similar_response_md}

Верни только текст отклика, без markdown-заголовков.
```

4. Заполнить поле описания (`browser_fill` / `browser_type`).
5. Указать цену/срок если форма требует (из GPT-рекомендации или желаемого бюджета заказчика).
6. **Перед отправкой** — screenshot в Telegram для финальной проверки (опционально, если `REQUIRE_FINAL_SCREENSHOT=true`).
7. Нажать «Отправить» / подтвердить.
8. Сохранить `response_text` в `pending_offers/{project_id}.json`.

### Шаг 7. EXCEL_LOG — журнал

Дописать строку в `C:\Python\Projects\Zerocode2md\ResponseJournal\Мои отклики.xlsx`:

| Колонка | Значение |
|---------|----------|
| № | авто (первая пустая строка) |
| Дата отклика | `YYYY-MM-DD` |
| Площадка | `Kwork` / `FL.ru` / `Telegram` (из `platform`) |
| Ссылка на проект | URL |
| Тип проекта | из `suggested_project_type` |
| Статус | `Отправлен` |
| Результат общения | `Жду ответа` |
| Заметки | score, buyer, текст отклика (первые 200 символов) |

Использовать `openpyxl` по образцу `Zerocode2md/scripts/create_response_journal.py`.

### Шаг 8. MONITOR_REPLY — отслеживание ответов

- Периодически проверять входящие **по каждой площадке** (адаптер `monitor_replies`):
  - Kwork: «Мои отклики» / уведомления
  - FL.ru: личный кабинет / сообщения по проекту
  - Telegram: ответы в ЛС / треды (если применимо)
- При новом сообщении → Telegram + Excel (`Ответ получен`)

---

## Структура нового репозитория (рекомендация)

```
freelance-responder/
  config/
    sources.yaml           # площадки: kwork, flru, telegram channels
    scoring.yaml           # пороги GPT, фильтры вакансий
  .env
  data/
    seen_projects.db       # (platform, source_key, project_id) + scan_state per source
    pending_offers/
  src/
    adapters/
      base.py              # PlatformAdapter protocol
      kwork.py
      flru.py
      telegram_channel.py
    analyzer.py
    telegram_bot.py        # approve/reject (единый для всех площадок)
    journal.py
    scheduler.py           # loop по enabled sources
  logs/
    agent-worklog.txt
```

---

## ENV (обязательные)

```env
# ProxyAPI / OpenAI
OPENAI_API_KEY=...
OPENAI_BASE_URL=https://api.proxyapi.ru/openai/v1
OPENAI_MODEL=gpt-4o-mini

# Telegram (approve-бот)
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...

# Telegram (каналы-источники, фаза 3)
TELEGRAM_API_ID=...
TELEGRAM_API_HASH=...
# TELEGRAM_SESSION=...   # Telethon session file

# Пути
BROWSERMCP_SERVER=C:/Python/Projects/BrowserMCP/packages/mcp-server/dist/index.js
RESPONSE_JOURNAL=C:/Python/Projects/Zerocode2md/ResponseJournal/Мои отклики.xlsx
LIGHTRAG_MCP=user-lightrag

# Поведение
SCAN_INTERVAL_MINUTES=30
MIN_GPT_SCORE=7
REQUIRE_TELEGRAM_APPROVAL=true
SCAN_BOOTSTRAP_SKIP_PIPELINE=true   # первый запуск: только заполнить БД, без GPT/TG
SCAN_EARLY_EXIT_KNOWN_COUNT=5       # стоп после N подряд известных карточек
```

---

## Безопасность и ограничения

- **Никогда** не отправлять отклик без `approve` в Telegram.
- Не хранить пароль Kwork в коде — только сессия Chrome (ручной логин).
- Не слать более **5 откликов в сутки** без явного снятия лимита (`MAX_DAILY_RESPONSES=5`).
- При CAPTCHA / блокировке — стоп + Telegram alert.
- Логи без полных API-ключей.
- Скриншоты и тексты откликов — только локально.

---

## Критерии приёмки MVP

**Фаза 1 — Kwork**
1. [ ] `kwork_dev_it`: только новые заказы, дедупликация per source_key.
2. [ ] Полное ТЗ, LightRAG + GPT score, TG approve, submit, Excel.

**Фаза 2 — FL.ru**
3. [ ] Адаптер `flru_ai`: scan + dedup + pipeline (enabled в sources.yaml).
4. [ ] Фильтр: только заказы, не вакансии/fulltime (настраиваемо).

**Фаза 3 — Telegram**
5. [ ] Мониторинг канала из конфига, watermark `last_message_id`.
6. [ ] Новый пост → score → TG approve; submit manual или semi-auto.

**Общее**
7. [ ] Один approve-бот и один Excel-журнал для всех площадок.
8. [ ] Windows + Chrome + BrowserMCP (для web-площадок).

---

## Команда для старта в новом проекте

```
/team-workflow

Задача: реализовать Freelance Auto-Responder по promptFrilance.md
(MVP: Kwork dev_it; масштабирование: FL.ru AI, TG-каналы).

Использовать BrowserMCP для браузера, LightRAG для стека/правил отклика,
ProxyAPI для GPT, Telegram для approve, Excel-журнал из Zerocode2md.
```

---

## Проверено на BrowserMCP (2026-06-21)

- **Kwork:** `https://kwork.ru/projects?c=all&page=6` — snapshot + CDP extract.
- **FL.ru:** [AI — искусственный интеллект](https://www.fl.ru/projects/category/ai-iskusstvenniy-intellekt/) — лента парсится браузером (заголовок, бюджет, ответы, тип заказ/вакансия); адаптер — фаза 2.
- **Telegram:** не BrowserMCP; отдельный модуль Telethon/Bot API — фаза 3.
