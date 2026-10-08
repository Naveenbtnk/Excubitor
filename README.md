# CloudOps AI

CloudOps AI is an AIOps dashboard for alert simulation, Gemini-powered incident triage, pgvector-backed runbook retrieval, and AI-generated post-mortems.

## Features

- **Alert simulation and incident triage** — generate realistic alerts, then have Gemini triage one into an incident using pgvector-retrieved runbook context.
- **Streaming Sandbox** — answers arrive token by token over SSE, with a Stop control that keeps the partial answer, and failures reported as a sentence the user can act on rather than the provider's payload.
- **Multi-worker-safe rate limiting and quota** — per-user request limits and the project-wide provider budget are enforced through a single counter store, so every worker shares one ceiling instead of multiplying it. With `REDIS_URL` set the counters live in Redis and hold across the whole deployment; without it they stay in-process, and an unreachable Redis degrades to the local worker's own counters rather than failing open. See [Rate limiting](#rate-limiting) and [Provider budget](#provider-budget).
- **Provider resilience** — retries with full jitter, per-attempt and stream-idle timeouts, and quota failures classified apart from momentary rate limits so the wait it reports is the real one.
- **Runbooks and post-mortems** — index operational runbooks with embeddings and generate markdown post-mortems from incidents.

## Stack

- Next.js 16, React 19, Tailwind CSS 4, SWR, React Markdown
- FastAPI, async SQLAlchemy, Alembic
- PostgreSQL 16 with pgvector
- Redis 7 for the counters behind the rate limit and the provider budget (optional)
- Gemini (`gemini-3.8-flash` and `gemini-embedding-001`)
- JWT authentication in an httpOnly cookie
- Docker Compose for the full local production stack

## Run with Docker

1. Copy `backend/.env.example` to `backend/.env`.
2. Set `GEMINI_API_KEY` and replace `JWT_SECRET` and `ADMIN_PASSWORD` with strong values.
3. Start the stack:

   ```bash
   docker compose up --build
   ```

4. Open `http://localhost:3000` and sign in with `ADMIN_EMAIL` / `ADMIN_PASSWORD` from `backend/.env`.

Services:

| Service | Address |
| --- | --- |
| Dashboard | `http://localhost:3000` |
| API | `http://localhost:8010` |
| API health | `http://localhost:8010/health` |
| PostgreSQL / pgvector | `localhost:5434` |
| Redis | `localhost:6379` |

Connect pgAdmin using host `localhost`, port `5434`, database `cloudops_ai`, user `cloudops`, password `cloudops`.

## Local development

Start only the database:

```bash
docker compose up -d db
```

Backend:

```bash
cd backend
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
alembic upgrade head
uvicorn app.main:app --reload --port 8010
```

Frontend:

```bash
cd frontend
npm install
# frontend/.env.local should contain BACKEND_URL=http://127.0.0.1:8010
npm run dev
```

## Verification

```bash
cd backend
.venv\Scripts\python -m ruff check app tests
.venv\Scripts\python -m pytest

cd ../frontend
npm run typecheck
npm run build
npm audit --omit=dev --audit-level=high
```

The backend suite is a mix of unit tests and integration tests. The integration tests need a
reachable PostgreSQL/pgvector server: they create and migrate an isolated `cloudops_ai_test`
database on the same host as `DATABASE_URL` and never touch the development database. Override
the target with `TEST_DATABASE_URL` when PostgreSQL is not on the default port:

```bash
cd backend
docker compose -f ../compose.yaml up -d db
# PowerShell
$env:TEST_DATABASE_URL = "postgresql+asyncpg://cloudops:cloudops@localhost:5434/cloudops_ai_test"
.venv\Scripts\python -m pytest
```

The suite replaces the LLM provider with a deterministic offline fake, so tests never call a
paid API.

The tests that prove the rate limit and the provider budget hold *across workers* need a real
Redis, because that is the only thing that can prove it. They look for one at `TEST_REDIS_URL`
(default `redis://localhost:6379/15`) and skip with a reason when none answers, rather than
reporting a pass they did not earn. `docker compose up -d redis` provides one; CI runs a service
container so the shared-ceiling guarantee is exercised on every push.

## Security

- Gemini is only called from FastAPI; the API key is never sent to the browser.
- The dashboard is protected by a signed, httpOnly session cookie.
- There is no public registration route. The first admin comes from environment variables.
- **Three roles, enforced per route.** `admin` can do everything including managing accounts,
  `operator` can create alerts, run triage, generate post-mortems and use the Sandbox, and
  `viewer` can only read. The check is a FastAPI dependency on each route, so a hidden button
  is a courtesy rather than the control: a Viewer calling a write route directly gets `403`.
  Roles live in `users.role` (constrained to those three values) and are read from the row on
  every request, so a demotion takes effect on the next call rather than at the next login.
  The seeded administrator is an `admin`.
- Do not commit `backend/.env` or use the default credentials outside local development.
- Set `COOKIE_SECURE=true` and restrict `CORS_ORIGINS` for HTTPS deployments.

## API conventions

### Pagination

`GET /alerts`, `GET /incidents` and `GET /knowledge/runbooks` return a page envelope instead of a
bare array:

```json
{ "items": [], "total": 0, "limit": 50, "offset": 0, "has_more": false }
```

`limit` and `offset` are query parameters, bounded by `PAGINATION_DEFAULT_LIMIT` and
`PAGINATION_MAX_LIMIT`. Lists are ordered by `created_at` with an `id` tiebreaker so pages are
stable and never overlap.

### Rate limiting

The routes that spend money on the provider are limited per authenticated user, per route group:
`LLM_RATE_LIMIT` requests per `RATE_LIMIT_WINDOW_SECONDS` for chat, streaming, runbook query,
triage and post-mortems, and `EMBEDDING_RATE_LIMIT` for runbook indexing. Exceeding a budget
returns `429` with a `Retry-After` header.

The counters live behind `REDIS_URL`. Left unset they stay in the process, which is correct for
one worker but makes the real ceiling `workers x limit` — three workers granting 20 requests a
minute is 60 a minute. Docker Compose sets `REDIS_URL`, so the stack you get from
`docker compose up` enforces one ceiling for the whole deployment. A Redis that becomes
unreachable narrows the limit to the worker that noticed rather than removing it: a shared
ceiling that quietly disappears under load is worse than a stricter one that only covers itself.

These limits bound how fast one user may ask for work. They say nothing about whether the account
can afford it, which is a project-wide question: see the provider budget below.

### Provider budget

`LLM_RATE_LIMIT` is per user and per minute, so it cannot protect a quota that is per project and
per day. `LLM_QUOTA_LIMIT` is the ceiling that can. It is shared by every user and counts
**upstream attempts**, because attempts are what the provider bills: one HTTP request can fan out
into several calls (a long runbook embeds once per 900-character chunk), and every replayed
attempt is another request, so counting requests rather than attempts would under-report real
usage.

Left unset the limit follows the provider: **20** for Gemini, matching the free tier's 20
`generateContent` requests per day per project per model, and unlimited for Ollama, which has no
quota to protect. Set `LLM_QUOTA_LIMIT=0` to stop counting or raise it to match a billed plan;
`LLM_QUOTA_WINDOW_SECONDS` defaults to a day. One budget covers chat, streams and embeddings
together, which is deliberately conservative — Gemini meters those methods separately, and
refusing a call we might have got away with is cheaper than discovering the ceiling mid-demo.

Like the rate limit, the budget is only as real as the counters behind it, and with `REDIS_URL`
set it is one allowance for the whole deployment rather than one per worker.

A spent budget returns `503` with a `Retry-After` for the wait that is genuinely left: the same
contract as an upstream `429`, except it fires *before* the request is sent instead of after the
provider refuses it. `GET /health/llm` reports the allowance as
`quota: {limit, used, remaining, window_seconds, resets_in, store}` so an approaching ceiling is
visible before it is hit; the probe itself is exempt, so monitoring cannot spend the budget it
reports.
On `POST /sandbox/stream` the `200` and SSE headers are already committed when the first token is
pulled, so a refusal there is delivered as a terminal `error` event followed by `done` — a
started response cannot be rewritten into a `503`.

That stream also sets `Cache-Control: no-transform`, and it is load-bearing rather than
housekeeping. The dashboard runs behind the Next.js server, which compresses responses by
default; a gzip encoder buffers until it has a block to emit, so every token was held until the
generation finished and arrived as one lump. Browsers always send `Accept-Encoding` and curl does
not, so this failure is invisible to every convenient test and obvious only in the real one.

### Provider resilience

Provider failures are classified by the provider and replayed by a provider-agnostic wrapper.
Transient ones (`5xx`, timeouts, dropped connections) are retried with exponential backoff and
full jitter — `LLM_MAX_RETRIES` extra attempts (2 by default), capped by `LLM_RETRY_MAX_DELAY`.
Streaming is only replayed before the first token, since resuming mid-stream would duplicate
text. Quota failures (`429 RESOURCE_EXHAUSTED`) are not replayed locally, because the window
outlives the request and retrying would consume more quota.

Every attempt is also bounded: `LLM_REQUEST_TIMEOUT` caps one whole non-streaming call and
`LLM_STREAM_IDLE_TIMEOUT` caps the silence between streamed chunks (not the total length, so a
slow but actively producing generation is never cut off). `LLM_CONNECT_TIMEOUT` bounds connection
setup. `0` disables a bound. A deadline that fires is a transient failure, so it is retried like
any other, and a stream is only ever replayed while no token has been emitted.

When retries are spent the caller gets `503` with `Retry-After`. If the provider stated its own
wait, that value is used verbatim — Gemini reports it as `RetryInfo.retryDelay`, and it matters:
the free tier caps `gemini-3.8-flash` at **20 requests per day**, so "retry in 15 hours" and
"retry in 60 seconds" are different answers. Otherwise the header falls back to the backoff
delay, or the quota window for a `429`. A permanent provider failure stays `502`. Only this
service retries: the Gemini SDK's own retry support is left disabled, so a retry can never
duplicate a paid request.

### Observability

Logs are structured JSON on stdout. Every request emits one `http_request` line with
`request_id`, `method`, `path`, `status`, `duration_ms` and `user_id`; the same id is returned in
the `X-Request-ID` response header and honoured if the caller supplies one. `GET /health` checks
PostgreSQL connectivity and reports the counter backend as `counters` (`ok`, `unconfigured` when
no Redis is configured, or why it is unreachable), and `GET /health/llm` probes provider
reachability and reports the remaining provider budget (both are public, for orchestrator probes).

## Product flows

1. Simulate an alert on Overview or Alerts.
2. Select **AI Triage** to create an incident using relevant runbook context.
   Ask the Sandbox a question and the answer arrives token by token, with a **Stop** button that
   keeps whatever was generated so far.
3. Add operational runbooks in Knowledge to enrich future triage.
4. Generate a markdown post-mortem from the incident list.

## Continuous integration

`.github/workflows/ci.yml` runs on every push and pull request: backend lint plus the full test
suite against a pgvector service container, frontend typecheck/audit/build, and Docker image
builds for both services.
