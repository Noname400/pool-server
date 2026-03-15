# GPU Pool Server v3

Центральный сервер управления пулом GPU-вычислений.  
FastAPI + React SPA + SQLite + KeyDB. Один Docker-контейнер. Nginx и KeyDB — systemd-сервисы на хосте.

## Архитектура

```
Trainer (GPU)                     Pool Server
  │                                 │
  │  HTTPS (Cloudflare → Nginx)     │
  ├─ GET  /get_number?count=N ─────►│──► KeyDB Lua: touch + cmd check + ZPOPMIN
  │◄─ {"numbers":[...],             │      ZADD pool:inflight (score=expire_ts)
  │    "leases":{x:lid},            │      SET pool:lease:{x} (machine:lid, TTL)
  │    "lease_ttl":60,              │
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

Background loops (leader-only):
  ready_queue_filler  (every 1s)   — keeps pool:ready above READY_LOW_WATERMARK
  requeue_expired     (every 5s)   — returns expired inflight → pool:ready
  persist_keydb_state (every 300s) — saves step/completed to SQLite
  telegram_stats      (configurable)

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
5. **Strict mode**: `mark_done` без `leases` отклоняется с 400 (legacy mode удален).

### Инварианты

- X может быть только в одном из {`pool:ready`, `pool:inflight`} в любой момент.
- `mark_done` без валидного `lease_id` НЕ увеличивает `completed:count`.
- `requeue` возвращает только элементы с `expire_ts <= now`.
- `completed + |inflight| + |ready|` учитывает все выданные X (с поправкой на requeue).
- Все trainer-запросы идемпотентны или безопасно повторяемы.

### Hot path (single Lua round-trip)

`GET /get_number` для verified машин выполняет один Lua-скрипт:
1. `SET alive:{machine} 1 EX ttl` — touch liveness
2. `GETDEL cmd:{machine}` — check pending command
3. `ZPOPMIN pool:ready` + `ZADD pool:inflight` + `SET pool:lease:{x}` — allocate with leases

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
| GET | `/health` | — | Deep health (KeyDB + ready queue depth) |
| GET | `/get_number?count=N` | Token + X-Machine-Id | Запрос X с lease (max 1000), count=0 — heartbeat |
| POST | `/mark_done` | Token + X-Machine-Id | Подтверждение X с lease_id (**обязательно**) |
| POST | `/set_found` | Token + X-Machine-Id | Сообщение о находке (x, y) |
| GET | `/stats` | Token | Статистика |
| GET | `/machines` | Token | Список машин |
| GET | `/found_keys` | Token | Найденные ключи |

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
| GET | `/stats` | Общая статистика с timestamp |
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
| `SECRET_KEY` | — | JWT signing key (**обязательно для production**) |
| `TRAINER_AUTH_TOKEN` | — | Токен трейнеров (**обязательно**) |
| `EXPORT_TOKEN` | — | Токен для /export/ (отдельный от trainer) |
| `CORS_ORIGINS` | — | Allowed origins (через запятую) |
| `TELEGRAM_BOT_TOKEN` | — | Telegram Bot API |
| `TELEGRAM_CHAT_ID` | — | Telegram chat ID |
| `TELEGRAM_STATS_INTERVAL` | `15` | Интервал Telegram (мин, 0=выкл) |
| `MACHINE_ALIVE_TTL` | `60` | TTL alive в KeyDB (сек) |
| `LEASE_TTL` | `60` | Время аренды X (сек). По истечении — requeue |
| `REQUEUE_INTERVAL` | `5` | Интервал проверки просроченных leases (сек) |
| `REQUEUE_BATCH` | `500` | Max leases per requeue iteration |
| `READY_LOW_WATERMARK` | `10000` | Порог, ниже которого начинается refill |
| `READY_TARGET` | `50000` | Целевой размер pool:ready при refill |
| `REFILL_INTERVAL` | `1.0` | Интервал проверки watermark (сек) |
| `REFILL_BATCH` | `5000` | Max X per refill iteration |
| `HOST` | `0.0.0.0` | Bind host |
| `PORT` | `8421` | Bind port |
| `DEBUG` | `false` | Debug mode |

## Фоновые задачи

| Задача | Интервал | Действие |
|--------|----------|----------|
| `ready_queue_filler` | 1 сек | Держит pool:ready выше READY_LOW_WATERMARK |
| `requeue_expired_leases` | 5 сек | Возвращает просроченные X из inflight в ready |
| `persist_keydb_state` | 300 сек | Сохраняет step, completed в SQLite |
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

## Тестирование

```bash
# Unit / integration tests (requires running KeyDB on localhost:6379)
pip install pytest pytest-asyncio
pytest tests/ -v

# Load test
python tests/load_test.py \
  --url http://localhost:8421 \
  --token $TRAINER_AUTH_TOKEN \
  --machines 500 \
  --duration 60
```

## Безопасность

| Уровень | Защита |
|---------|--------|
| Cloudflare | WAF, DDoS, TLS |
| Nginx | Origin cert, rate limit (100r/s per IP burst 200), security headers |
| SPA | Path traversal protection (resolved path check) |
| CORS | Whitelist origins |
| Trainer | TRAINER_AUTH_TOKEN (hmac.compare_digest) |
| Admin | JWT cookie (HttpOnly, SameSite=Strict, Secure) |
| API keys | bcrypt (legacy SHA-256) |
| Config | SECRET_KEY обязателен для multi-worker |

## Первый запуск

1. `init_db()` — SQLite schema, WAL
2. `init_keydb()` — подключение к KeyDB
3. `_restore_keydb_state()` — step/completed из SQLite при пустом KeyDB
4. Если нет admin — создаётся admin + API key в лог
5. Background tasks (refill + requeue + persist + telegram) + Uvicorn :8421
