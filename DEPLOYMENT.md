# Deploying Excubitor

This is the operational companion to the [README](README.md): what to set, what to put in front
of the stack, how to back it up, and how to tell that a deployment is actually working.

Everything here assumes the Docker Compose stack in [compose.yaml](compose.yaml) — PostgreSQL 16
with pgvector, Redis 7, the FastAPI backend and the Next.js dashboard — but nothing in the
application requires Compose. It requires PostgreSQL with the `vector` extension and, above one
worker, a Redis.

## What runs, and what it needs

| Service | Default port | State | Notes |
| --- | --- | --- | --- |
| Dashboard (Next.js) | 3000 | none | Stateless. Needs `BACKEND_URL` at **build** time, see below. |
| API (FastAPI/Gunicorn) | 8010 → 8000 | none | Stateless. Holds provider credentials. |
| PostgreSQL + pgvector | 5434 → 5432 | durable | The only thing that must be backed up. |
| Redis | 6379 | ephemeral | Rate-limit and provider-budget counters. Losing it costs nothing but the counters. |

## 1. Secrets

All secrets live in `backend/.env` (Compose passes it to the API container through `env_file`) or
in the environment of whatever runs the API. `backend/.env` is git-ignored; never commit it, and
never put a value from it in a ticket, a chat or an image.

```bash
# A signing key: 48 bytes of URL-safe randomness.
python -c "import secrets; print(secrets.token_urlsafe(48))"

# A webhook token, same idea. Alertmanager sends it as X-API-Key.
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

| Variable | Required | What it is |
| --- | --- | --- |
| `JWT_SECRET` | yes | Signs the session cookie. Rotating it signs everyone out — which is the intended way to do that. |
| `ADMIN_PASSWORD` | yes | Password for the seeded administrator. Set before the first start; changing it later does not change the existing row (`seed_admin` only inserts). |
| `GEMINI_API_KEY` | yes when `LLM_PROVIDER=gemini` | Server-side only. It never reaches the browser. |
| `ALERTMANAGER_WEBHOOK_TOKEN` | if you ingest alerts | Shared secret for `POST /webhooks/alertmanager`. Unset means that route answers `503`. |
| `ADMIN_EMAIL` | yes | Login for the seeded administrator. |

`backend/.env.example` documents every setting the code reads, and the test suite fails if a new
setting appears in code without appearing there. The ones worth knowing about beyond the secrets:

| Variable | Default | Effect |
| --- | --- | --- |
| `ENVIRONMENT` | `development` | `production` turns on the startup guard below and stops the API creating its own schema. |
| `DATABASE_URL` | local compose DSN | Point it at your real PostgreSQL. Must include `+asyncpg`. |
| `REDIS_URL` | empty | Empty keeps counters in-process. **Set this whenever more than one worker serves traffic**, or the real ceiling is `workers × limit`. |
| `COOKIE_SECURE` | `false` | Set `true` behind HTTPS. |
| `CORS_ORIGINS` | localhost:3000 | Comma-separated list of the origins the dashboard is served from. |
| `LLM_QUOTA_LIMIT` | provider's free tier (20 for Gemini) | Project-wide provider budget per `LLM_QUOTA_WINDOW_SECONDS`. `0` disables it. |
| `DEBUG` | `false` | Returns tracebacks to callers. Refused in production. |
| `SEED_DEMO_DATA` | on outside production | Seeds three invented alerts into an empty database. |
| `BACKEND_URL` | `http://backend:8000` | Where the Next.js server proxies `/api/*`. **A build argument, not a runtime one** — see below. |

## 2. The startup guard

With `ENVIRONMENT=production` the API refuses to start when a critical secret is missing or is
still the value this repository ships with:

```
RuntimeError: Refusing to start: JWT_SECRET is unset or is still the value this repository
ships with; GEMINI_API_KEY is unset while LLM_PROVIDER is gemini. Set real values in the
environment (see DEPLOYMENT.md).
```

| Fatal in production | Warned about, then accepted |
| --- | --- |
| `JWT_SECRET` unset or still a shipped default | `JWT_SECRET` shorter than 32 characters, or reading like a placeholder |
| `ADMIN_PASSWORD` unset | `COOKIE_SECURE=false` (the cookie will travel over plain HTTP) |
| `GEMINI_API_KEY` unset while the provider is Gemini | `REDIS_URL` unset (per-worker counters) |
| `DEBUG=true` (it returns tracebacks) | `ALERTMANAGER_WEBHOOK_TOKEN` unset (ingestion answers 503) |

The split is deliberate. A missing secret is fatal because there is no safe default to fall back
on; a weak one is the operator's call, and a process that refuses to start over a string that
merely looks weak is a process whose guard gets deleted. Warnings appear as
`insecure_setting` lines in the startup log, and never include the value — only the name.

## 3. Running the stack

```bash
cp backend/.env.example backend/.env
# Set JWT_SECRET, ADMIN_PASSWORD, GEMINI_API_KEY, ADMIN_EMAIL, ALERTMANAGER_WEBHOOK_TOKEN.
docker compose up --build -d
docker compose ps           # every service should reach "healthy"
curl -fsS http://localhost:8010/health | jq
```

The API image runs `alembic upgrade head` before Gunicorn starts, so a fresh volume is migrated
on first boot. With more than one API replica, migrate as a separate step instead of in every
container's command:

```bash
docker compose run --rm backend alembic upgrade head
```

## 4. Putting HTTPS in front of it

Expose only the dashboard. The API is reachable through the Next.js server at `/api/*`, which is
also what keeps the session cookie first-party and the Gemini key off the browser. If you publish
the API directly, do it on a private network and keep CORS narrow.

### Caddy

Caddy obtains and renews certificates itself, which is the least moving parts:

```caddyfile
ops.example.com {
    encode zstd gzip

    # Event streams must not be buffered or transformed: a gzip encoder holds tokens until it
    # has a block to flush, so the Sandbox would deliver an answer in one lump at the end.
    @sse path /api/sandbox/stream
    handle @sse {
        reverse_proxy frontend:3000 {
            flush_interval -1
            transport http {
                read_timeout 1h
            }
        }
    }

    handle {
        reverse_proxy frontend:3000
    }
}
```

### nginx

```nginx
server {
    listen 443 ssl http2;
    server_name ops.example.com;

    ssl_certificate     /etc/letsencrypt/live/ops.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/ops.example.com/privkey.pem;

    add_header Strict-Transport-Security "max-age=31536000" always;

    # The event stream: no buffering, no compression, and a long read timeout. Without these
    # the Sandbox looks frozen until the whole answer is ready and then prints it at once.
    location /api/sandbox/stream {
        proxy_pass http://frontend:3000;
        proxy_http_version 1.1;
        proxy_set_header Connection "";
        proxy_buffering off;
        proxy_cache off;
        gzip off;
        proxy_read_timeout 3600s;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    location / {
        proxy_pass http://frontend:3000;
        proxy_http_version 1.1;
        proxy_set_header Connection "";
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}

server {
    listen 80;
    server_name ops.example.com;
    return 301 https://$host$request_uri;
}
```

With HTTPS in place: `COOKIE_SECURE=true`, and `CORS_ORIGINS=https://ops.example.com`.

### The dashboard's backend address is baked in at build time

`next.config.js` evaluates `rewrites()` during `npm run build`, so `BACKEND_URL` is a **build
argument**. Changing it means rebuilding the frontend image, not restarting it:

```bash
docker compose build --build-arg BACKEND_URL=http://backend:8000 frontend
```

## 5. Sizing Gunicorn

The API image starts Gunicorn with two Uvicorn workers. That number is about database
connections more than CPU: each worker holds its own SQLAlchemy pool (5 connections plus 10
overflow), so `workers × 15` is the ceiling one API node can ask PostgreSQL for.

- **Workers.** Start at `(2 × vCPU) + 1`, then cap it by your PostgreSQL `max_connections`
  budget: with `max_connections = 100` and two PostgreSQL clients to spare, four workers is
  already 60 connections. Real usage settles far below the pool's maximum.
- **Sizing signal.** Watch `duration_ms` in the `http_request` log lines and the API's CPU. The
  work is waiting on the provider or the database, not on the CPU, so more workers will not make
  triage faster — a spent quota will.
- **Rate limits and the provider budget are per worker without Redis.** With three workers and
  `LLM_RATE_LIMIT=20`, one user can make 60 requests a minute before anything refuses. Set
  `REDIS_URL` and the ceiling is one number for the whole deployment.
- **Streams.** The Sandbox holds a worker connection open for the length of a generation. Keep
  worker count comfortably above your concurrent-generations estimate, or a handful of long
  answers will starve the JSON routes. The Gunicorn timeout must be larger than your slowest
  generation; the request-level timeouts (`LLM_REQUEST_TIMEOUT`, `LLM_STREAM_IDLE_TIMEOUT`)
  exist so a stuck generation is cut off long before Gunicorn's.

```bash
# docker-compose override, or your own CMD
gunicorn app.main:app --bind 0.0.0.0:8000 --workers 4 --timeout 120 \
  --worker-class uvicorn.workers.UvicornWorker --access-logfile - --error-logfile -
```

The image reads two container-level variables for this, so the common case needs no new image:
`GUNICORN_WORKERS` (default 2) and `GUNICORN_TIMEOUT` (default 120 seconds — larger than any
provider timeout, so a slow generation is cut off by `LLM_STREAM_IDLE_TIMEOUT` long before
Gunicorn decides the worker is stuck).

```yaml
# docker-compose.override.yml
services:
  backend:
    environment:
      GUNICORN_WORKERS: "4"
```

## 6. Redis, if you run it

Redis holds counters: per-user rate limits and the project-wide provider budget. Two ways to keep
it private, and you want at least one:

```bash
# 1. A password. REDIS_URL then carries it: redis://:THE_PASSWORD@redis:6379/0
docker run -d --name cloudops-redis \
  -e REDIS_PASSWORD="$(python -c 'import secrets; print(secrets.token_urlsafe(24))')" \
  bitnami/redis:7

# 2. Network isolation: do not publish 6379. Inside Compose, the API reaches redis:6379 on the
#    internal network, and nothing outside it can.
```

A Redis that becomes unreachable does not fail open: the counter store degrades to the local
worker's own counters, which is stricter than the shared ceiling, not looser. `GET /health`
reports the state as `counters`, and the quota block names its store (`redis` or `memory`).

## 7. Health checks and a post-deploy checklist

`GET /health` (database + counters) and `GET /health/llm` (provider reachability and remaining
budget) are public on purpose, so an orchestrator can probe them. The probe is exempt from the
provider budget, so monitoring it cannot spend what it reports.

After a deploy, in order:

1. **`curl -fsS https://ops.example.com/api/../health`** — the dashboard's proxy path, not just
   the container port: it proves the frontend can reach the API.
2. **Sign in** with `ADMIN_EMAIL` / `ADMIN_PASSWORD`. A `401` here with a correct password
   usually means `JWT_SECRET` changed under a browser that still holds an old cookie.
3. **Watch the role chip** next to your name: it should read `admin`. Create one `viewer` account
   on the Users page and confirm every write control is disabled for it, and that a forced write
   from the API returns `403`.
4. **Simulate an alert**, run **AI Triage**, and open the incident's timeline: the first entry
   should name your account.
5. **Deliver a webhook by hand** — a real Alertmanager is not always at hand:

   ```bash
   curl -fsS -X POST https://ops.example.com/webhooks/alertmanager \
     -H "Content-Type: application/json" -H "X-API-Key: $ALERTMANAGER_WEBHOOK_TOKEN" \
     -d '{"version":"4","status":"firing","alerts":[{"status":"firing","labels":{"alertname":"DeployCheck","severity":"warning","job":"manual"}}]}'
   ```

   `{"accepted": 1, ...}` and the alert on the Alerts page. `503` means the token is unset;
   `401` means the header does not match it.
6. **Open the Sandbox and send something.** Tokens should appear as they are generated. If the
   answer arrives in one lump at the end, the proxy in front is buffering the stream — see the
   `proxy_buffering off` / `flush_interval -1` above.
7. **Check the logs** for `insecure_setting` lines and for any `internal_error`, which carries the
   request id and traceback a user's report will quote.
8. **Confirm the demo data decision**: a production deploy that was not asked for demo alerts
   should show an empty alert list, not three invented ones.

## 8. Backups and restore

PostgreSQL is the only state. Back it up before every upgrade, and practise the restore — an
untested backup is a guess.

```bash
# Backup: custom format, compressed, restorable table by table.
docker compose exec -T db pg_dump -U cloudops -d cloudops_ai -Fc \
  > "cloudops-$(date +%F-%H%M).dump"

# Plain SQL, if you would rather read it.
docker compose exec -T db pg_dump -U cloudops -d cloudops_ai > cloudops.sql

# Restore into an EMPTY database. --clean drops objects first; do not run it against a live one
# you still need.
docker compose exec -T db pg_restore -U cloudops -d cloudops_ai --clean --if-exists \
  < cloudops-2026-10-08-1200.dump

# Or, for the plain dump:
docker compose exec -T db psql -U cloudops -d cloudops_ai < cloudops.sql

# Verify: the tables, then the row counts you expect.
docker compose exec -T db psql -U cloudops -d cloudops_ai -c '\dt'
docker compose exec -T db psql -U cloudops -d cloudops_ai \
  -c 'select (select count(*) from alerts) alerts, (select count(*) from incidents) incidents,
             (select count(*) from incident_events) events, (select count(*) from users) users;'
```

A nightly cron on the host that keeps 14 days:

```bash
0 3 * * * cd /srv/cloudops && docker compose exec -T db pg_dump -U cloudops -d cloudops_ai -Fc \
  > backups/cloudops-$(date +\%F).dump && find backups -name 'cloudops-*.dump' -mtime +14 -delete
```

Two things the dump does **not** contain, both by design:

- **Redis counters.** They are ephemeral. Flushing them resets every per-user rate limit *and the
  project-wide provider budget* — so a flush does not hand back quota the provider has already
  spent, it only removes the local guard that was tracking it. Treat a flush as "the next 20
  Gemini calls will go out unchecked", which is fine locally and careless in production.
- **The `vector` extension.** It must exist before the restore; `CREATE EXTENSION IF NOT EXISTS
  vector` is part of the schema the API creates on a development start, and the pgvector image
  already ships it.

Also back up `backend/.env` in a secret manager, not in the same place as the dumps: a database
restored next to a lost signing key is a database nobody can log into.

## 9. Upgrades

```bash
git pull
docker compose exec -T db pg_dump -U cloudops -d cloudops_ai -Fc > pre-upgrade.dump
docker compose build
docker compose up -d                  # alembic upgrade head runs in the API container
docker compose ps                     # wait for healthy
```

Migrations are forward-only in practice: `alembic downgrade` exists for every revision, but a
downgrade that drops a column is not a rollback of the data that was in it. The dump is the
rollback.

## 10. Troubleshooting

| Symptom | Likely cause |
| --- | --- |
| API exits at startup with `Refusing to start: …` | A critical secret is missing or still the shipped default; the message names it. |
| `503` from the Alertmanager webhook | `ALERTMANAGER_WEBHOOK_TOKEN` is unset. |
| `401` from the webhook | The `X-API-Key` header does not match the token, or is missing. |
| Everyone signed out after a deploy | `JWT_SECRET` changed. |
| Sandbox answers arrive all at once | A proxy is buffering the stream; see section 4. |
| Rate limits feel looser than configured | `REDIS_URL` is unset with more than one worker, so each has its own ceiling. |
| `503` with `Retry-After` on triage, post-mortems or the Sandbox | The provider budget is spent (`GET /health/llm` shows the allowance) or the provider is rate-limiting us. The wait is the provider's own number. |
| A user reports an error with a request id | `grep <request_id>` in the API logs: every request line, the `internal_error` record and its traceback carry it. |
