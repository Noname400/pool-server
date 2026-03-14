# GPU Pool Server v2

Центральный сервер управления пулом GPU-вычислений.  
FastAPI + React SPA + SQLite (persistent) + KeyDB (in-memory). Один Docker-контейнер, Nginx и KeyDB работают на хосте как systemd-сервисы.

## Архитектура

```
Trainer (GPU)                     Pool Server (training.bbdata.net)
  │                                 │
  │  HTTPS (Cloudflare → Nginx)     │
  ├─ GET  /get_number?count=N ─────►│──► KeyDB: INCRBY partx:step
  │◄─ {"numbers":[...], "command"}  │      SET active:{x} (TTL 300s)
  │                                 │
  ├─ POST /mark_done {nums:[...]} ─►│──► KeyDB: INCRBY completed:count
  │◄─ {"ok":true}                   │      DEL active:{x}
  │                                 │
  ├─ POST /set_found {x, y} ──────►│──► SQLite: INSERT found_keys
  │◄─ {"ok":true}                   │
  │                                 │
  │  (implicit heartbeat:           │──► KeyDB: SET alive:{id} (TTL 60s)
  │   every request = alive)        │──► SQLite: UPSERT machines (throttled 30s)
  └─────────────────────────────────┘

Admin UI (React SPA)
  │
  ├─ /admin              Overview: step, completed, found, machines online
  ├─ /admin/machines     Список машин (online/offline), команды stop/pause/restart
  ├─ /admin/found-keys   Таблица найденных ключей
  ├─ /admin/connect      Инструкция подключения трейнеров
  ├─ /admin/settings     PartX start/step, Telegram, test seeds, auth token
  └─ /admin/test         Тестовый стенд (конкретные X значения)
```

### Разделение хранилищ

| Хранилище | Данные | Скорость | Персистентность |
|-----------|--------|----------|-----------------|
| **KeyDB** | step, completed count, alive machines, active X | ~200+ RPS | нет (восстанавливается из SQLite) |
| **SQLite** | users, api_keys, machines, found_keys, settings, test_items, machine_verify | ~50 RPS | да (WAL mode) |
| **In-process cache** | verified machines, test_mode flag, last DB write time | мгновенно | нет |

## Файлы

### Backend (`app/`)

| Файл | Назначение |
|------|-----------|
| `main.py` | FastAPI app, lifespan (init DB/KeyDB, restore state, create admin), SPA serving |
| `config.py` | Pydantic Settings из `.env` |
| `cache/keydb.py` | KeyDB клиент: step/start/completed counters, alive TTL, active X tracking |
| `db/sqlite.py` | SQLite схема (6 таблиц) + все CRUD функции |
| `workers/trainer_router.py` | Trainer API: get_number, mark_done, set_found, stats, machines, found_keys |
| `workers/partx_generator.py` | PartX раздача через KeyDB INCRBY (atomic, 0 contention), MAX_X = 2^32-1 |
| `dashboard/admin_router.py` | Admin API: stats, machines CRUD, commands, settings, test stand, found keys |
| `background/tasks.py` | Фоновые задачи: persist KeyDB→SQLite (10s), Telegram stats |
| `auth/router.py` | Login (API key → JWT cookie), Logout, Me |
| `auth/dependencies.py` | JWT/API key validation, `require_admin` |
| `auth/api_keys.py` | Генерация и SHA-256 хеширование API ключей |
| `security/middleware.py` | CORS, security headers (HSTS, CSP, X-Frame), request logging |
| `notifications/telegram.py` | Telegram Bot API уведомления |

### Frontend (`frontend/src/pages/admin/`)

| Страница | Назначение |
|----------|-----------|
| `Overview.jsx` | Дашборд: step, completed, progress %, found keys, machines online |
| `Machines.jsx` | Список машин с online/offline статусом, модальное окно с деталями, команды |
| `FoundKeys.jsx` | Таблица всех найденных ключей (x, y, machine, time) |
| `Connect.jsx` | Инструкция подключения трейнеров (URL, headers, curl пример) |
| `Settings.jsx` | Настройки PartX, Telegram, test seeds, auth token (read-only) |
| `TestStand.jsx` | Тестовый стенд: ввод X значений, запуск/остановка, результаты |

## База данных (SQLite)

### Таблицы

```
users            — id, username, role, email, is_active, created_at
api_keys         — id, user_id, key_hash, key_prefix, label, role, is_active, last_used_at/ip
machines         — machine_id, hostname, ip, name, tags, gpu_name/count/mem, version,
                   pending_command, verified, first_seen, last_seen
machine_verify   — machine_id + x_value (PK), found, found_at
found_keys       — id, x_value, y_value, machine_id, found_at
settings         — key (PK), value, updated_at
test_items       — id, x_value, status, machine_id, assigned_at, completed_at, found, found_y, found_at
```

## KeyDB (in-memory)

| Ключ | Тип | TTL | Назначение |
|------|-----|-----|-----------|
| `partx:step` | int | — | Текущий указатель раздачи X |
| `partx:start` | int | — | Нижняя граница (задаётся в Settings) |
| `completed:count` | int | — | Общий счётчик обработанных X |
| `alive:{machine_id}` | "1" | 60s | Машина жива (implicit heartbeat) |
| `active:{x_number}` | machine_id | 300s | X в работе (stuck detection) |

Каждые 10 секунд `persist_keydb_state` сохраняет `partx:step` и `completed:count` в SQLite settings. При старте пула `_restore_keydb_state` восстанавливает эти значения из SQLite если KeyDB пуст.

## API

### Trainer API (без префикса)

| Метод | Путь | Auth | Описание |
|-------|------|------|----------|
| GET | `/status` | — | Health check |
| GET | `/get_number?count=N` | Token + X-Machine-Id | Запрос X номеров (max 1000) |
| POST | `/mark_done` | Token + X-Machine-Id | Пометка X как обработанные |
| POST | `/set_found` | Token + X-Machine-Id | Сообщение о находке (x, y) |
| GET | `/stats` | Token | Статистика кластера |
| GET | `/machines` | Token | Список машин |
| GET | `/found_keys` | Token | Найденные ключи |

**Headers трейнера:**
```
Authorization: <TRAINER_AUTH_TOKEN>
X-Machine-Id: <hostname-based unique id>
X-Hostname: <hostname>
X-GPU-Name: <gpu model>
X-GPU-Count: <number>
X-GPU-Mem: <MB>
X-Version: <trainer version>
```

### Admin API (`/api/admin`)

| Метод | Путь | Описание |
|-------|------|----------|
| GET | `/stats` | Общая статистика |
| GET | `/machines` | Список машин |
| GET | `/machines/{id}` | Детали машины |
| PATCH | `/machines/{id}` | Обновить name/tags |
| DELETE | `/machines/{id}` | Удалить машину |
| POST | `/machines/{id}/command` | Команда (stop/pause/restart) |
| POST | `/machines/command-all` | Команда всем (фильтр: online, tag) |
| GET | `/found-keys` | Найденные ключи |
| GET/POST | `/settings` | Настройки пула |
| POST | `/test/start` | Запуск теста {x_values} |
| POST | `/test/stop` | Остановка теста |
| GET | `/test/status` | Статус теста |
| GET | `/users` | Список пользователей |

### Auth (`/api/auth`)

| Метод | Путь | Описание |
|-------|------|----------|
| POST | `/login` | API key → JWT cookie (7 дней) |
| POST | `/logout` | Удаление cookie |
| GET | `/me` | Текущий пользователь |

## PartX Generator

Пространство X: `0 .. 2^32-1` (4 294 967 295).

```
Trainer → GET /get_number?count=10
  │
  Pool:
  │  1. ensure_step_above_start()     — KeyDB: if step < start → step = start
  │  2. claim_range(10)               — KeyDB: INCRBY partx:step 10 (atomic)
  │  3. range(start, min(start+10, MAX_X))
  │  4. pipeline SET active:{x} machine_id EX 300  (×10)
  │
  └→ {"numbers": [100..109], "command": "work"}
```

- **Раздача**: атомарный INCRBY — нет блокировок, нет дублей
- **Stuck detection**: active ключи с TTL 300s автоматически истекают
- **Завершение**: mark_done → INCRBY completed:count + DEL active:{x}

## Верификация новых машин

Когда `test_seeds` заданы в настройках (через запятую), новая машина сначала получает эти seed-значения с `command: "verify"`. Трейнер должен найти Y для каждого X и отправить через `/set_found`. Когда все seeds найдены — машина помечается `verified=1` и начинает получать реальные X из диапазона.

## Управление машинами

Команды (stop/pause/restart) доставляются через `pending_command`:
1. Admin UI → `POST /machines/{id}/command` → SQLite: `UPDATE machines SET pending_command='stop'`
2. Trainer → `GET /get_number` → SQLite: `SELECT/consume pending_command`
3. Trainer получает `{"numbers":[], "command":"stop"}` и останавливается

## Docker

```bash
# Сборка
docker build -t bbdata/pool-server:latest .

# Запуск (KeyDB и Nginx на хосте)
docker run -d \
  --name pool-app \
  --network host \
  -v /data/pool:/data \
  --env-file .env \
  --restart unless-stopped \
  bbdata/pool-server:latest
```

### docker-compose.yml

```yaml
services:
  pool:
    image: bbdata/pool-server:latest
    container_name: pool-app
    network_mode: host
    volumes:
      - /data/pool:/data
    env_file:
      - .env
    restart: unless-stopped
```

## Переменные окружения

| Переменная | Default | Описание |
|-----------|---------|----------|
| `DATA_DIR` | `/data` | Путь к SQLite базе (pool.db) |
| `KEYDB_URL` | `redis://127.0.0.1:6379/0` | URL подключения к KeyDB |
| `SECRET_KEY` | auto | JWT signing key |
| `TRAINER_AUTH_TOKEN` | — | Токен авторизации трейнеров |
| `CORS_ORIGINS` | — | Allowed origins (через запятую) |
| `TELEGRAM_BOT_TOKEN` | — | Telegram Bot API token |
| `TELEGRAM_CHAT_ID` | — | Telegram chat ID |
| `TELEGRAM_STATS_INTERVAL` | `15` | Интервал Telegram статистики (мин, 0=откл) |
| `MACHINE_ALIVE_TTL` | `60` | TTL alive ключа в KeyDB (сек) |
| `ACTIVE_X_TTL` | `300` | TTL active X ключа в KeyDB (сек) |
| `HOST` | `0.0.0.0` | Bind host |
| `PORT` | `8421` | Bind port |
| `DEBUG` | `false` | Debug mode |

## Зависимости

| Пакет | Назначение |
|-------|-----------|
| fastapi | Web framework |
| uvicorn[standard] | ASGI server |
| pydantic-settings | Config from env |
| aiosqlite | Async SQLite driver |
| redis | KeyDB/Redis async client |
| httpx | HTTP client (Telegram) |
| python-jose[cryptography] | JWT |
| python-multipart | Form data |

## Фоновые задачи

| Задача | Интервал | Что делает |
|--------|----------|-----------|
| `persist_keydb_state` | 10 сек | Сохраняет `partx:step` и `completed:count` из KeyDB в SQLite settings |
| `telegram_stats_loop` | настраиваемый | Отправляет статистику кластера в Telegram |

## Безопасность

| Уровень | Защита |
|---------|--------|
| Cloudflare | WAF, DDoS, TLS termination |
| Nginx (хост) | Origin Certificate, rate limiting, security headers |
| CORS | Whitelist origins |
| Trainer auth | `TRAINER_AUTH_TOKEN` (hmac.compare_digest) |
| Admin auth | JWT cookie (HttpOnly, SameSite=Strict, Secure) |
| API key auth | SHA-256 hash, 128-bit entropy |
| Request logging | IP, method, path, timing, X-Request-ID |
| Security headers | HSTS, X-Frame-Options DENY, CSP, nosniff |

## Lifecycle

```
1. init_db()             → SQLite: CREATE TABLE IF NOT EXISTS (7 таблиц), WAL mode
2. init_keydb()          → KeyDB: PING
3. _restore_keydb_state()→ Если KeyDB пуст — восстановить step/completed из SQLite
4. Create admin user     → Если нет админа — создать + вывести API key в лог
5. Background tasks      → persist_keydb_state + telegram_stats_loop
6. Uvicorn serve         → :8421
```
