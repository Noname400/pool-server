# GPU Pool Server v2

Центральный сервер управления пулом GPU-вычислений.  
FastAPI + React SPA + SQLite + KeyDB. Один Docker-контейнер. Nginx и KeyDB — systemd-сервисы на хосте.

## Архитектура

```
Trainer (GPU)                     Pool Server
  │                                 │
  │  HTTPS (Cloudflare → Nginx)     │
  ├─ GET  /get_number?count=N ─────►│──► KeyDB: INCRBY partx:step
  │◄─ {"numbers":[...], "command"}  │      SET active:{x} (TTL)
  │                                 │      touch alive:{id}
  │                                 │      UPSERT machines (X-GPU-Count и др.)
  │                                 │
  ├─ POST /mark_done {nums:[...]} ─►│──► KeyDB: INCRBY completed:count
  │◄─ {"ok":true}                   │      DEL active:{x}, touch alive
  │                                 │
  ├─ POST /set_found {x, y} ──────►│──► SQLite: INSERT found_keys
  │◄─ {"ok":true}                   │      touch alive
  │                                 │
  └─────────────────────────────────┘

Admin UI (React SPA)
  ├─ /admin              Overview: step, completed, found, machines online
  ├─ /admin/machines     Fleet view: машины по IP, GPU × count, команды
  ├─ /admin/found-keys   Таблица найденных ключей
  ├─ /admin/connect      Инструкция подключения трейнеров
  └─ /admin/settings     PartX start/step, Telegram, test seeds, auth token
```

### Хранилища

| Хранилище | Данные | Персистентность |
|-----------|--------|------------------|
| **KeyDB** | partx:step, completed, alive:{id}, active:{x}, cmd:{id} | нет (восст. из SQLite) |
| **SQLite** | users, api_keys, machines, found_keys, settings, test_items, machine_verify | да (WAL) |
| **In-memory** | verified machines, test_mode, last DB write | нет |

## Файлы

### Backend (`app/`)

| Файл | Назначение |
|------|-----------|
| `main.py` | FastAPI, lifespan, SPA serve |
| `config.py` | Pydantic Settings из `.env` |
| `cache/keydb.py` | KeyDB: step/completed, alive TTL, active X, pending commands |
| `db/sqlite.py` | SQLite schema + CRUD |
| `workers/trainer_router.py` | Trainer API: get_number, mark_done, set_found, stats, machines, found_keys |
| `workers/partx_generator.py` | PartX: атомарный INCRBY, MAX_X = 2^32-1 |
| `dashboard/admin_router.py` | Admin API: stats, machines, commands, settings, test API, found keys |
| `background/tasks.py` | persist KeyDB→SQLite (10s), Telegram stats |
| `auth/*` | Login (API key → JWT), Logout, Me, require_admin |
| `security/middleware.py` | CORS, CSP, HSTS, request logging |

### Frontend (`frontend/src/pages/admin/`)

| Страница | Назначение |
|----------|-----------|
| `Overview.jsx` | Дашборд: step, completed, progress %, found keys, machines online |
| `Machines.jsx` | Fleet view: группировка по IP, GPU × count, онлайн/офлайн точки |
| `FoundKeys.jsx` | Таблица найденных ключей |
| `Connect.jsx` | URL, headers, curl пример подключения |
| `Settings.jsx` | PartX, Telegram, test seeds, auth token (read-only) |

## SQLite

### Таблицы

```
users            — id, username, role, email, is_active, created_at
api_keys         — key_hash, key_prefix, user_id, label, role
machines         — machine_id, hostname, ip, name, tags, gpu_name, gpu_count, gpu_mem_mb,
                   version, pending_command, verified, first_seen, last_seen
machine_verify   — machine_id, x_value (PK), found, found_at
found_keys       — id, x_value, y_value, machine_id, found_at
settings         — key (PK), value, updated_at
test_items       — id, x_value, status, machine_id, assigned_at, completed_at, found, found_y, found_at
```

## KeyDB

| Ключ | Назначение |
|------|-----------|
| `partx:step` | Текущий указатель раздачи X |
| `partx:start` | Нижняя граница (Settings) |
| `completed:count` | Счётчик обработанных X |
| `alive:{machine_id}` | Машина жива, TTL = MACHINE_ALIVE_TTL |
| `active:{x}` | X в работе, TTL = ACTIVE_X_TTL (stuck detection) |
| `cmd:{machine_id}` | pending command (stop/pause/restart), TTL 3600 |

Персистентность: каждые 10 сек `partx:step` и `completed:count` сохраняются в SQLite. При старте восстанавливаются, если KeyDB пуст.

## API

### Trainer (без префикса)

| Метод | Путь | Auth | Описание |
|-------|------|------|----------|
| GET | `/status` | — | Health check |
| GET | `/get_number?count=N` | Token + X-Machine-Id | Запрос X (max 1000), count=0 — только регистрация |
| POST | `/mark_done` | Token + X-Machine-Id | Пометка X обработанными |
| POST | `/set_found` | Token + X-Machine-Id | Сообщение о находке (x, y) |
| GET | `/stats` | Token | Статистика |
| GET | `/machines` | Token | Список машин |
| GET | `/found_keys` | Token | Найденные ключи |

**Headers трейнера** (обновляются при get_number, mark_done, set_found):

```
Authorization: <TRAINER_AUTH_TOKEN>
X-Machine-Id: <unique id>
X-Hostname: <hostname>
X-GPU-Name: <model>
X-GPU-Count: <число карт>
X-GPU-Mem: <MB>
X-Version: <version>
```

### Admin (`/api/admin`)

| Метод | Путь | Описание |
|-------|------|----------|
| GET | `/stats` | Общая статистика |
| GET | `/machines` | Список машин |
| GET | `/machines/{id}` | Детали машины |
| PATCH | `/machines/{id}` | Обновить name/tags |
| DELETE | `/machines/{id}` | Удалить машину |
| POST | `/machines/{id}/command` | Команда: stop / pause / restart |
| POST | `/machines/command-all` | Команда всем (фильтр: online, tag) |
| GET | `/found-keys` | Найденные ключи |
| GET/POST | `/settings` | Настройки |
| POST | `/test/start` | Запуск теста {x_values} |
| POST | `/test/stop` | Остановка теста |
| GET | `/test/status` | Статус теста |
| GET | `/users` | Пользователи |

### Auth (`/api/auth`)

| Метод | Путь | Описание |
|-------|------|----------|
| POST | `/login` | API key → JWT cookie (7 дней) |
| POST | `/logout` | Удаление cookie |
| GET | `/me` | Текущий пользователь |

## PartX Generator

Пространство X: `0 .. 2^32-1`.

```
1. ensure_step_above_start()  — KeyDB: step ≥ start
2. claim_range(count)        — INCRBY partx:step (атомарно)
3. range(start, min(start+count, MAX_X))
4. pipeline SET active:{x} machine_id EX ACTIVE_X_TTL
```

- **mark_done** → INCRBY completed:count + DEL active:{x}
- Stuck: active с TTL автоматически истекают

## Верификация машин

При `test_seeds` в настройках (через запятую) новая машина сначала получает эти X с `command: "verify"`. Трейнер находит Y и шлёт `/set_found`. После всех seeds — `verified=1`, машина получает реальные X.

## Команды машинам

1. Admin → `POST /machines/{id}/command` → KeyDB: `SET cmd:{id}`
2. Trainer → `GET /get_number` → KeyDB: `GETDEL cmd:{id}`
3. Трейнер получает `{"numbers":[], "command":"stop"}` и останавливается

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

### docker-compose

```yaml
services:
  pool:
    image: bbdata/pool-server:latest
    build:
      context: .
      dockerfile: Dockerfile
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
| `DATA_DIR` | `/data` | Путь к SQLite (pool.db) |
| `KEYDB_URL` | `redis://127.0.0.1:6379/0` | KeyDB/Redis URL (с паролем: `redis://:pass@host:port/0`) |
| `SECRET_KEY` | auto | JWT signing key |
| `TRAINER_AUTH_TOKEN` | — | Токен трейнеров (**обязательно**) |
| `CORS_ORIGINS` | — | Allowed origins (через запятую) |
| `TELEGRAM_BOT_TOKEN` | — | Telegram Bot API |
| `TELEGRAM_CHAT_ID` | — | Telegram chat ID |
| `TELEGRAM_STATS_INTERVAL` | `15` | Интервал Telegram (мин, 0=выкл) |
| `MACHINE_ALIVE_TTL` | `60` | TTL alive в KeyDB (сек) |
| `ACTIVE_X_TTL` | `300` | TTL active X (сек) |
| `HOST` | `0.0.0.0` | Bind host |
| `PORT` | `8421` | Bind port |
| `DEBUG` | `false` | Debug mode |

## Зависимости

| Пакет | Назначение |
|-------|-----------|
| fastapi | Web framework |
| uvicorn[standard] | ASGI |
| pydantic-settings | Config from env |
| aiosqlite | Async SQLite |
| redis | KeyDB/Redis async |
| httpx | HTTP (Telegram) |
| python-jose[cryptography] | JWT |
| python-multipart | Form data |
| bcrypt | Password hashing |

## Фоновые задачи

| Задача | Интервал | Действие |
|--------|----------|----------|
| `persist_keydb_state` | 10 сек | Сохраняет step, completed в SQLite |
| `telegram_stats_loop` | настраиваемый | Статистика в Telegram |

## Безопасность

| Уровень | Защита |
|---------|--------|
| Cloudflare | WAF, DDoS, TLS |
| Nginx | Origin cert, rate limit, security headers |
| CORS | Whitelist origins |
| Trainer | TRAINER_AUTH_TOKEN (hmac) |
| Admin | JWT cookie (HttpOnly, SameSite=Strict, Secure) |
| API keys | bcrypt (legacy SHA-256) |
| CSP | scripts, styles, fonts (Google Fonts) |

## Первый запуск

1. `init_db()` — SQLite schema, WAL
2. `init_keydb()` — подключение к KeyDB
3. `_restore_keydb_state()` — step/completed из SQLite при пустом KeyDB
4. Если нет admin — создаётся admin + API key в лог (**сохраните!**)
5. Background tasks + Uvicorn :8421

Создать новый API key: `scripts/create-admin.sh`
