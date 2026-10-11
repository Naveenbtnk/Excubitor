# Excubitor

Production-grade CloudOps AI — AIOps console with AI triage, incident timelines, and RAG-powered post-mortems.

Excubitor is an AIOps dashboard for alert simulation, Gemini-powered incident triage, pgvector-backed runbook retrieval, and AI-generated post-mortems.

## Features

- **Alert simulation and incident triage** — generate realistic alerts, then have Gemini triage one into an incident using pgvector-retrieved runbook context.
- **Streaming Sandbox** — answers arrive token by token over SSE, with a Stop control that keeps the partial answer, and failures reported as a sentence the user can act on rather than the provider's payload.
- **Multi-worker-safe rate limiting and quota** — per-user request limits and the project-wide provider budget are enforced through a single counter store, so every worker shares one ceiling instead of multiplying it. With `REDIS_URL` set the counters live in Redis and hold across the whole deployment; without it they stay in-process, and an unreachable Redis degrades to the local worker's own counters rather than failing open. See [Rate limiting](#rate-limiting) and [Provider budget](#provider-budget).
- **Incident timeline** — every status change, assignment and comment is recorded with who did it and when, and rendered as a timeline on the incident's own page, so an incident has a history rather than only a current status. See [Incident timeline](#incident-timeline).
- **Alertmanager ingestion** — `POST /webhooks/alertmanager` accepts a real Prometheus Alertmanager notification group behind a shared token, creating one alert per entry so a firing group and its resolutions arrive as they happened. See [Alert ingestion](#alert-ingestion).
- **Provider resilience** — retries with full jitter, per-attempt and stream-idle timeouts, and quota failures classified apart from momentary rate limits so the wait it reports is the real one.
- **Runbooks and post-mortems** — index operational runbooks with embeddings and generate markdown post-mortems from incidents.
- **A post-mortem is a workflow, not a blob.** A generation lands as a `draft` with the sections a team actually reviews (timeline, root cause, contributing factors, impact) beside its narrative; it moves through `in_review` to `published`, every transition recorded on the incident's own timeline, and it downloads as a markdown file. Follow-ups carry an owner and a due date, and the Overview shows what is overdue. See [Post-mortems](#post-mortems).

## Architecture

```
Browser ──HTTPS──▶ reverse proxy ──▶ Next.js (3000) ──/api/*──▶ FastAPI (8000) ──▶ PostgreSQL 16 + pgvector
                                          │                            └──▶ Redis (counters, optional)
                                          └── renders the dashboard         └──▶ Gemini (server-side only)

Alertmanager ──POST /webhooks/alertmanager (X-API-Key)──▶ FastAPI
```

The dashboard never talks to the database or the provider. Next.js proxies `/api/*` to the API,
which keeps the session cookie first-party, keeps the Gemini key out of the browser entirely, and
means the only thing that needs to be published is the frontend. The API is a stateless
Gunicorn/Uvicorn process; PostgreSQL holds the only durable state, and Redis holds the counters
behind the rate limits and the provider budget.

Reads and writes all pass through one FastAPI dependency that resolves the caller's role, so the
UI is a convenience layer rather than the control: hiding a button spares a click, and the route
decides what happens.

For the operational side — proxy configuration, secrets, sizing, backups — see
[DEPLOYMENT.md](DEPLOYMENT.md).

## Stack

- Next.js 16, React 19, Tailwind CSS 4, SWR, React Markdown
- FastAPI, async SQLAlchemy, Alembic
- PostgreSQL 16 with pgvector
- Redis 7 for the counters behind the rate limit and the provider budget (optional)
- Gemini (`gemini-3.8-flash` and `gemini-embedding-001`)
- JWT authentication in an httpOnly cookie
- Docker Compose for the full local production stack

## Quickstart

Requirements: Docker with Compose, and a Gemini API key (or `LLM_PROVIDER=ollama` against a local
runtime — nothing else needs a paid account).

```bash
# 1. Configuration. Every setting is documented in the file itself.
cp backend/.env.example backend/.env

# 2. The three values that matter. Generate the secret; do not invent one by hand.
python -c "import secrets; print(secrets.token_urlsafe(48))"   # -> JWT_SECRET
#    ADMIN_PASSWORD  -> the login you will use below
#    GEMINI_API_KEY  -> from aistudio.google.com (server-side only, never sent to a browser)

# 3. Start everything: PostgreSQL + pgvector, Redis, the API and the dashboard.
docker compose up --build -d

# 4. Wait for health, then look at it. The database is migrated on first boot.
until curl -fsS http://localhost:8010/health > /dev/null; do sleep 2; done
curl -fsS http://localhost:8010/health | jq   # {"status":"ok","database":"ok","counters":"ok",...}
```

Open <http://localhost:3000> and sign in with `ADMIN_EMAIL` / `ADMIN_PASSWORD`. From there:

1. **Overview** — simulate an alert from the dropdown; it appears in the queue below.
2. **Alerts** — run **AI Triage** on it. The recommendation appears with a link to the incident's
   timeline.
3. **Incident timeline** — reassign it, comment, move its status; every change is recorded with
   your name.
4. **Knowledge** — paste a runbook and index it, then ask a question and watch the retrieved
   context come back with the answer.
5. **Sandbox** — ask anything and watch the answer arrive token by token.
6. **Post-Mortems** — generate a markdown post-mortem from an incident (this resolves it).

Demo alerts appear on a fresh database outside production. Set `SEED_DEMO_DATA=false` to start
with an empty queue instead.

Going to production is a different checklist: [DEPLOYMENT.md](DEPLOYMENT.md).

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

## Configuration

All settings live in `backend/.env` in development and in the API's environment in production;
`backend/.env.example` documents every one of them, and the test suite fails if a setting the code
reads is missing from that file or listed there without being read.

Two worth knowing before you start:

- `ENVIRONMENT=production` enables a startup guard. A missing or still-default `JWT_SECRET`, an
  empty `ADMIN_PASSWORD`, an empty `GEMINI_API_KEY` with `LLM_PROVIDER=gemini`, or `DEBUG=true`
  all stop the process with a message naming what to fix. Weak-but-present values are logged as
  `insecure_setting` and accepted — the full table is in [DEPLOYMENT.md](DEPLOYMENT.md#2-the-startup-guard).
- `SEED_DEMO_DATA` controls the three demo alerts on an empty database. Unset it follows the
  environment: on outside production, off inside it.

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
- **A production deployment is checked before it serves anything.** A missing secret stops the
  process at startup rather than silently defaulting to a value published in this repository; a
  weak one is logged by name, never by value.
- **Unexpected failures never leak internals.** An exception that escapes a route becomes a JSON
  `500` with a stable `internal_error` code and a request id that matches the log line holding the
  traceback, and the same contract applies inside the SSE stream, where the status line is already
  committed. See [Error handling](#error-handling).
- `POST /webhooks/alertmanager` is authenticated by a shared token compared in constant time, and
  answers `503` rather than accepting anything when that token is unset.
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

### Incident timeline

An incident carries an append-only log of what happened to it. Nothing updates or deletes an
entry, so the timeline is a record rather than a rendering of current state.

| Route | Role | What it does |
| --- | --- | --- |
| `GET /incidents/{id}` | any | The incident, including its `assignee` |
| `GET /incidents/{id}/activity` | any | The timeline, oldest first |
| `PATCH /incidents/{id}` | operator | Sets `status` and/or `assignee`, recording what changed |
| `POST /incidents/{id}/comments` | operator | Adds a comment to the timeline |

```bash
curl -X PATCH http://localhost:8010/incidents/1 \
  -H "Content-Type: application/json" -b cookies.txt \
  -d '{"status": "mitigated", "assignee": "night-ops@cloudops.local"}'

curl -X POST http://localhost:8010/incidents/1/comments \
  -H "Content-Type: application/json" -b cookies.txt \
  -d '{"body": "Failover completed, watching error rates."}'
```

Entries read forwards -- oldest first, which is the one list in the API that does. Every other
one is a queue whose newest entry matters most; a timeline is read the way it happened.

```json
{
  "items": [
    {
      "id": 1,
      "kind": "created",
      "actor": "admin@cloudops.local",
      "from_value": null,
      "to_value": "triage",
      "body": "Created by AI triage from the alert “Database pool exhaustion”, with 75% confidence.",
      "created_at": "2026-10-08T09:12:45.101Z"
    }
  ],
  "total": 1, "limit": 50, "offset": 0, "has_more": false
}
```

`kind` is one of `created`, `status`, `assignee`, `comment` or `post_mortem`. `from_value` and
`to_value` hold the old and new value for the two kinds that change something, which is what lets
a reader see the diff without parsing the sentence in `body`; both are `null` for a comment.

A change writes an event only when something actually changes: re-saving the same status is a
`200` with no entry, because a log people read to find out what happened should not fill up with
no-ops. `actor` is the email of whoever acted, so the history survives that account being renamed.
Implementing a `PATCH` as "assignee absent means leave it, `null` means unassign" is deliberate:
the two have to be distinguishable or changing a status would unassign somebody.

Triage writes the first entry, and generating a post-mortem writes the last one (it resolves the
incident). Reviewing a document writes to the same log: moving a post-mortem between states is a
`post_mortem` entry carrying the old and new value, so "who published this, and when" is answered
by the incident rather than only by a field the document keeps. The incident list on the
Post-Mortems page links to each incident's timeline.

### Post-mortems

Generating a document is an incident action, so it stays on the incident (and it resolves the
incident). Everything that happens to the document afterwards — reading it, editing it, moving it
through review, downloading it — is its own collection, because a review queue and an export are
not incident-scoped.

| Route | Role | What it does |
| --- | --- | --- |
| `GET /post-mortems` | any | Every document the caller may read, most recently updated first |
| `GET /post-mortems/{id}` | any | One document, with its action items |
| `PATCH /post-mortems/{id}` | operator | Edits the sections, replaces the action items, moves the status |
| `PATCH /post-mortems/{id}/actions/{id}` | operator | Edits one follow-up, so ticking a box is one small request |
| `GET /post-mortems/{id}/export.md` | any | The rendered markdown, as a download |
| `POST /incidents/{id}/post-mortem` | operator | Generates one — idempotent, rate limited and budgeted like any other provider call |

`GET /post-mortems` accepts `status` and `incident_id` filters and the usual pagination envelope,
ordered by `updated_at` rather than `created_at`: a review queue is sorted by what somebody last
touched, not by when the document was first written.

**Visibility is a role question, and the answer is wider than "its author and Admins".** A Viewer
sees `published` documents only — a draft is a claim nobody has stood behind yet. An Operator or
Admin sees every draft, because the review pipeline needs a reader: whoever moves a document into
`in_review` and whoever publishes it have to be able to open it, and a reviewer who can only see
their own drafts is not a reviewer. A Viewer asking for a draft by id gets `404` rather than `403`:
whether a hidden document exists is itself information that role has no use for.

**The status is a pipeline, not a flag.** `draft` is where every generation lands, `in_review` is
the state a document is read in, and `published` is what a Viewer may see. Transitions are not
forced through the middle — a one-line correction can go straight from `draft` to `published` —
because what matters is the event being written down, not the route taken. `published_at` is set
when a document is published and cleared when it leaves that state.

**The narrative and the sections are both kept**, and nothing is parsed out of the prose.
`content` is what the model wrote and what a reader skims; the four structured fields are what the
review, the export and the action-item strip are built from. A parser deciding what a paragraph
meant is how a post-mortem quietly loses a detail nobody notices until an audit.

**Action items are rows, not a JSON blob**, because "what is outstanding, by when" is a query
rather than a document. On a `PATCH`, `action_items` replaces the list: an item sent back with its
`id` is the same item, so a link or a half-finished edit elsewhere does not point at a row that was
deleted and recreated underneath it, an item without an `id` is created, and anything the client no
longer lists is removed — a form that edits a set of rows has no other way to say "this one is
gone". Array order is the stored `position`, which is what makes an edited list stay edited.
`owner` is an email, resolved to a user when one matches and kept as written when none does,
because a follow-up is often assigned to somebody who has not signed in yet. Owners and due dates
from the model are dropped: those are commitments between people, and inventing them would put a
fabricated deadline on somebody's list.

`GET /overview` carries the strip that makes them actable — `action_items: {open, overdue, items}`
counting every open follow-up across the documents the caller may read, earliest due date first
with undated items last, capped at five entries. The two counts are the number that matters; the
list is the handful worth acting on today.

**The export is a pure function of the document**, which is why the exact bytes a download produces
are pinned by a test. Sections come out in a fixed order, and an empty one is written as
`_Not recorded._` rather than omitted: an export that silently drops a section is indistinguishable
from a document that never had one, and the whole point of a post-mortem is that the gaps are
visible. Action items render as a table, with a due date in the past marked `(overdue)` at render
time and pipes or newlines in a cell flattened so a stray character cannot break the row. The
response is `text/markdown`, served as an attachment.

### Alert ingestion

`POST /webhooks/alertmanager` takes Prometheus Alertmanager's webhook payload (`webhook_configs`
`send_resolved: true`) and turns each entry of `alerts[]` into a row in the alert list.

It is authenticated by a shared token, not a session: the caller is Prometheus, which has no
cookie and no role. Send the token from `ALERTMANAGER_WEBHOOK_TOKEN` in the `X-API-Key` header, and
compare it in constant time server-side. An **unset** token answers `503` — "not configured" —
rather than accepting the request, so a deployment that has not been set up for ingestion is never
an open write route; a missing or wrong token answers `401`. An empty `alerts[]` is a valid request
and creates nothing.

```yaml
# alertmanager.yml
receivers:
  - name: cloudops-ai
    webhook_configs:
      - url: http://backend:8010/webhooks/alertmanager
        send_resolved: true
        http_config:
          authorization:
            type: APIKey
            credentials_file: /etc/alertmanager/cloudops.token  # the ALERTMANAGER_WEBHOOK_TOKEN value
```

```bash
curl -X POST http://localhost:8010/webhooks/alertmanager \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $ALERTMANAGER_WEBHOOK_TOKEN" \
  -d '{"version":"4","status":"firing","alerts":[{"status":"firing","labels":{"alertname":"PostgresPoolExhausted","instance":"orders-api-5f9c:9187","job":"postgres-exporter","severity":"critical"},"annotations":{"summary":"Pool above 95%"},"startsAt":"2026-10-08T09:12:44Z","generatorURL":"http://prometheus:9090/graph"}]}'
```

The response reports what was created, so Alertmanager's logs and ours can be lined up:

```json
{ "accepted": 1, "opened": 1, "resolved": 0, "alert_ids": [7] }
```

| Alertmanager | Our alert field |
| --- | --- |
| `labels.alertname`, plus `labels.instance` when present (`HighCPU on node-3:9100`) | `title` |
| `labels.severity`, lower-cased; defaults to `warning` | `severity` |
| `labels.job`, else `labels.cluster`, else `alertmanager` | `source` |
| `annotations.summary` and `annotations.description`, then `labels`, `startsAt`, `generatorURL`, `externalURL` | `detail` |
| alert `status: firing` (or anything unrecognised) → `open`; `resolved` → `resolved` | `status` |

The **per-alert** status is what is read, not the group's: a group is `firing` as a whole even when
one of its alerts has just recovered, and collapsing them would hide which one did.

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

### Error handling

A failure that escapes a route is projected, never forwarded. The caller receives
`{"detail": "…", "error": {"code": "internal_error", "message": "…", "request_id": "…"}}` with a
`500`, and the traceback goes to the `cloudops.error` log line carrying the same request id — so a
user's screenshot is enough to find the stack, and the response itself holds nothing about the
schema, the path or the query.

On `POST /sandbox/stream` the `200` and the SSE headers are already committed when a token is
pulled, so the same failure is delivered as a terminal `error` event followed by `done`. The
partial answer is kept: an answer that got halfway is worth more than a reset connection.

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
4. Point Alertmanager at `POST /webhooks/alertmanager` and alerts arrive without anyone clicking
   **Simulate** — see [Alert ingestion](#alert-ingestion).
5. Open an incident's **Timeline** to see who changed what, comment on it, reassign it, and move
   its status forward — see [Incident timeline](#incident-timeline).
6. Generate a post-mortem from the incident list, edit its sections, move it to **In review**,
   publish it, and download the markdown — see [Post-mortems](#post-mortems).

## Known limitations

- **The apple-touch-icon is an SVG, and iOS wants a PNG.** The mark is hand-written SVG on
  purpose — no binary assets in the tree — so `public/apple-touch-icon.svg` is what the link
  points at. Browsers that accept an SVG touch icon use it; iOS falls back to a screenshot of
  the page. Generating the PNG sizes is a build step that has not been added.
- **Webhook ingestion does not dedupe yet.** Alertmanager re-sends a group on every
  `repeat_interval` and again when it resolves, and each delivery creates new alert rows, so a
  long-firing alert appears once per notification. `POST /webhooks/alertmanager` accepts the
  fingerprint field and ignores it. Coalescing on it needs one decision first — whether a
  `resolved` notification should close the original row or reopen it — and that is a design call
  rather than a missing line of code, so it is not half-implemented here. Until then, treat the
  alert list as a delivery log rather than a current-state table. The behaviour is pinned by
  `test_a_repeated_delivery_creates_the_alerts_again` so a fix has something to change.

## Deployment

[DEPLOYMENT.md](DEPLOYMENT.md) covers the rest of the path to production: generating secrets, the startup guard in full, Caddy and nginx
configurations for HTTPS (including the stream-buffering settings a Sandbox needs), Gunicorn
worker sizing against your PostgreSQL connection budget, private Redis, `pg_dump`/`pg_restore`
backups and restores, upgrades, and a post-deploy checklist that ends in a real Alertmanager
delivery.

## Continuous integration

`.github/workflows/ci.yml` runs on every push and pull request: backend lint plus the full test
suite against a pgvector service container, frontend typecheck/audit/build, and Docker image
builds for both services.
