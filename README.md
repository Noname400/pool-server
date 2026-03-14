# GPU Pool Server v3

Центральный сервер управления пулом GPU-вычислений.  
FastAPI + React SPA + SQLite + KeyDB. Один Docker-контейнер. Nginx и KeyDB — systemd-сервисы на хосте.

## Архитектура

```
Trainer (GPU)                     Pool Server
  │                                 │
  │  HTTPS (Cloudflare → Nginx)     │
  ├─ GET  /get_number?count=N ─────►│──► KeyDB: ZPOPMIN pool:ready
  │◄─ {"numbers":[...],             │      ZADD pool:inflight (score=expire_ts)
  │    "leases":{x:lid},            │      SET pool:lease:{x} (machine:lid, TTL)
  │    "lease_ttl":60,              │      touch alive:{id}
  │    "command":"work"}            │
  │                                 │
  ├─ POST /mark_done ──────────────►│──► Lua ACK: validate lease_id
  │  {"nums":[...],                 │      ZREM pool:inflight, INCRBY completed
  │   "leases":{x:lid}}            │      reject if lease reassigned
  │◄─ {"ok":true,"count":N}        │
  │                                 │
  ├─ POST /set_found {x, y} ──────►│──► SQLite: INSERT found_keys
  │◄─ {"ok":true}                   │
  │                                 │
  └─────────────────────────────────┘

Background: requeue_expired_leases (every 5s)
  └─ ZRANGEBYSCORE pool:inflight(-inf, now) → ZADD pool:ready

Admin UI (React SPA)
  ├─ /admin              Overview: step, completed, inflight, ready, requeued, found
  ├─ /admin/machines     Fleet view
  ├─ /admin/found-keys   Таблица найденных ключей
  ├─ /admin/connect      Инструкция подключения (v3 lease protocol)
  └─ /admin/settings     PartX start/step, Telegram, test seeds, auth token
```

## Lease model (v3)

Каждый X выдаётся с `lease_id` и TTL (default 60s). Гарантии:

1. **Нет потерь**: если trainer не подтвердил X за TTL — X автоматически возвращается в очередь.
2. **Нет двойного зачёта**: `mark_done` валидирует `lease_id`; поздний ack после reassign отклоняется.
3. **Идемпотентность**: повторный `mark_done` для уже подтверждённого X — безопасный no-op.
4. **O(log N)**: все операции через ZSET и Lua-скрипты, без полных сканирований.

### KeyDB структуры

| Ключ | Тип | Назначение |
|------|-----|-----------|
| `pool:ready` | ZSET (score=x) | X, готовые к выдаче |
| `pool:inflight` | ZSET (score=expire_ts) | X под активными leases |
| `pool:lease:{x}` | STRING | `machine_id:lease_id`, TTL = lease_ttl × 3 |
| `pool:lease_seq` | INT | Автоинкремент lease ID |
| `pool:stats:requeued` | INT | Счётчик requeue (observability) |
| `partx:step` | INT | Текущий указатель генерации X |
| `partx:start` | INT | Нижняя граница (Settings) |
| `completed:count` | INT | Счётчик подтверждённых X |
| `alive:{machine_id}` | STRING | Машина жива, TTL = MACHINE_ALIVE_TTL |
| `cmd:{machine_id}` | STRING | Pending command, TTL 3600 |

### Хранилища

| Хранилище | Данные | Персистентность |
|-----------|--------|------------------|
| **KeyDB** | leases, step, completed, alive, commands | нет (восст. из SQLite) |
| **SQLite** | users, api_keys, machines, found_keys, settings, test_items, machine_verify | да (WAL) |

## API

### Trainer (без префикса)

| Метод | Путь | Auth | Описание |
|-------|------|------|----------|
| GET | `/status` | — | Health check |
| GET | `/get_number?count=N` | Token + X-Machine-Id | Запрос X с lease (max 1000), count=0 — регистрация |
| POST | `/mark_done` | Token + X-Machine-Id | Подтверждение X с lease_id |
| POST | `/set_found` | Token + X-Machine-Id | Сообщение о находке (x, y) |
| GET | `/stats` | Token | Статистика |
| GET | `/machines` | Token | Список машин |
| GET | `/found_keys` | Token | Найденные ключи |

**GET /get_number response:**

```json
{
  "numbers": [100, 101, 102],
  "leases": {"100": "42", "101": "43", "102": "44"},
  "lease_ttl": 60,
  "command": "work"
}
```

**POST /mark_done request:**

```json
{
  "nums": [100, 101, 102],
  "leases": {"100": "42", "101": "43", "102": "44"}
}
```

**POST /mark_done response:**

```json
{"ok": true, "count": 3, "rejected": 0, "already_done": 0}
```

**Headers трейнера:**

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
| GET | `/stats` | Общая статистика (включая inflight, ready, requeued) |
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

## Переменные окружения

| Переменная | Default | Описание |
|-----------|---------|----------|
| `DATA_DIR` | `/data` | Путь к SQLite (pool.db) |
| `KEYDB_URL` | `redis://127.0.0.1:6379/0` | KeyDB/Redis URL |
| `SECRET_KEY` | auto | JWT signing key |
| `TRAINER_AUTH_TOKEN` | — | Токен трейнеров (**обязательно**) |
| `CORS_ORIGINS` | — | Allowed origins (через запятую) |
| `TELEGRAM_BOT_TOKEN` | — | Telegram Bot API |
| `TELEGRAM_CHAT_ID` | — | Telegram chat ID |
| `TELEGRAM_STATS_INTERVAL` | `15` | Интервал Telegram (мин, 0=выкл) |
| `MACHINE_ALIVE_TTL` | `60` | TTL alive в KeyDB (сек) |
| `LEASE_TTL` | `60` | Время аренды X (сек). По истечении — requeue |
| `REQUEUE_INTERVAL` | `5` | Интервал проверки просроченных leases (сек) |
| `REQUEUE_BATCH` | `500` | Max leases per requeue iteration |
| `HOST` | `0.0.0.0` | Bind host |
| `PORT` | `8421` | Bind port |
| `DEBUG` | `false` | Debug mode |

## Фоновые задачи

| Задача | Интервал | Действие |
|--------|----------|----------|
| `persist_keydb_state` | 300 сек | Сохраняет step, completed в SQLite |
| `requeue_expired_leases` | 5 сек | Возвращает просроченные X из inflight в ready |
| `telegram_stats_loop` | настраиваемый | Статистика в Telegram |

## Docker

```bash
docker build -t bbdata/pool-server:latest .

docker run -d \
  --name pool-app \
  --network host \
  -v /data/pool:/data \
  --env-file .env \
  --restart unless-stopped \
  bbdata/pool-server:latest
```

## Безопасность

| Уровень | Защита |
|---------|--------|
| Cloudflare | WAF, DDoS, TLS |
| Nginx | Origin cert, rate limit, security headers |
| CORS | Whitelist origins |
| Trainer | TRAINER_AUTH_TOKEN (hmac) |
| Admin | JWT cookie (HttpOnly, SameSite=Strict, Secure) |
| API keys | bcrypt (legacy SHA-256) |

## Первый запуск

1. `init_db()` — SQLite schema, WAL
2. `init_keydb()` — подключение к KeyDB
3. `_restore_keydb_state()` — step/completed из SQLite при пустом KeyDB
4. Если нет admin — создаётся admin + API key в лог
5. Background tasks (persist + requeue + telegram) + Uvicorn :8421
