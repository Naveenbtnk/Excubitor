# Excubitor — Engineering Log: decisions, verification, known limitations

Excubitor is an AIOps dashboard whose less obvious parts — provider retries, a project-wide
budget, an append-only incident timeline, a post-mortem a team signs off — were each built
against a failure that had been observed rather than imagined. This log records what exists,
how it was verified, why the non-obvious choices were made, and what is deliberately still
missing. It is written for a human picking the project up — a reviewer, a new maintainer, or the
same developer six months later — and it is a record of work done, not a plan for work to come.

## 1. What the project is

**Excubitor** (CloudOps AI) is an AIOps dashboard modelled on tools like PagerDuty and Datadog
incident-response consoles. It ingests cloud infrastructure alerts, triages them with an LLM
(root cause, remediation, confidence), retrieves relevant runbooks by vector search, and
generates post-mortems that a team reviews and publishes. It is a two-service application:

- **`backend/`** — Python 3.12 in the image (`pyproject.toml` requires 3.11+), FastAPI, async
  SQLAlchemy, PostgreSQL with pgvector, Alembic migrations, JWT cookie auth, Google Gemini via
  `google-genai`, with Ollama as a local alternative.
- **`frontend/`** — Next.js 16 (App Router), React 19, Tailwind CSS 4, SWR for data fetching,
  `react-markdown` for rendered AI output.
- **`compose.yaml`** at the repository root runs the stack — PostgreSQL/pgvector, Redis, the
  backend and the frontend — with Docker.

There are eight pages: **Overview**, **Alerts**, **Sandbox** (free-form chat), **Knowledge**
(runbook upload and retrieval), **Post-Mortems** (the review queue), an incident's **Timeline**,
**Login**, and **Admin → Users**. Three roles — `admin`, `operator`, `viewer` — are enforced by
a FastAPI dependency on every data route, not by hiding buttons.

## 2. Current state

### Backend

- Layered by responsibility: `app/core` (config, security, RBAC, startup guard, observability),
  `app/db` (session, seeders), `app/models` (SQLAlchemy: `User`, `Alert`, `Incident`,
  `IncidentEvent`, `PostMortem`, `PostMortemAction`, `RunbookChunk` with a pgvector `Vector`
  column), `app/schemas` (Pydantic at every API boundary), `app/api/routes`, `app/services`
  (LLM providers, retry, quota, RAG, triage, alert ingestion, post-mortems).
- **Auth** is a JWT in an httpOnly cookie (`app/core/security.py`, `app/api/routes/auth.py`,
  `app/api/deps.py::get_current_user`). The first administrator is seeded from `ADMIN_EMAIL` /
  `ADMIN_PASSWORD` at startup with `INSERT ... ON CONFLICT DO NOTHING`, so two Gunicorn workers
  racing on the same row cannot take the container down. There is no public signup route.
- **The provider layer is swappable** (`app/services/llm/base.py::LLMProvider`): Gemini by
  default and Ollama as a local fallback, each wrapped by retries, per-attempt deadlines and the
  provider budget. Every capability is added to the interface first, so a second provider always
  has a defined — possibly degraded — behaviour.
- **Retrieval** chunks runbook text (900 characters, 120 overlap), embeds it, stores it in
  `runbook_chunks` and queries by cosine distance. **Triage** asks for a Pydantic schema
  (`TriageResult`), not free text. **Post-mortems** are generated on the incident and reviewed
  as their own collection.
- **Routes**, all behind the session cookie and a role except the two health probes,
  `POST /auth/login`, `GET /auth/demo` and the webhook:
  - public: `GET /health`, `GET /health/llm`, `POST /auth/login`, `GET /auth/demo`,
    `POST /webhooks/alertmanager` (shared token, not a session);
  - session: `POST /auth/logout`, `GET /auth/me`, `GET /overview`, `GET /alerts`,
    `POST /alerts`, `POST /alerts/simulate`, `GET /alerts/scenarios`, `POST /alerts/{id}/ack`,
    `GET /knowledge/runbooks`, `POST /knowledge/runbooks`, `POST /knowledge/runbooks/upload`,
    `POST /knowledge/query`, `POST /sandbox/chat`, `POST /sandbox/stream` (SSE), `GET /incidents`,
    `GET /incidents/{id}`, `PATCH /incidents/{id}`, `GET /incidents/{id}/activity`,
    `POST /incidents/{id}/comments`, `POST /incidents/triage/{alert_id}`,
    `GET /incidents/{id}/post-mortem`, `POST /incidents/{id}/post-mortem`;
  - the document collection: `GET /post-mortems`, `GET /post-mortems/{id}`,
    `PATCH /post-mortems/{id}`, `PATCH /post-mortems/{id}/actions/{action_id}`,
    `GET /post-mortems/{id}/export.md`;
  - admin only: `GET /users`, `POST /users`, `PATCH /users/{id}`, `DELETE /users/{id}`. Deletion
    refuses the caller's own account and the last administrator;
    `scripts/verify_live_stack.py` exercises both refusals against a running deployment.
- **Four migrations** (`202609290001` initial schema with the `vector` extension and an HNSW
  index, `202610080001` roles, `202610080002` the incident timeline, `202610090001` the
  post-mortem workflow).
- **Ceilings**: a per-user, per-scope sliding window (`app/services/ratelimit.py`), a
  project-wide provider budget charged per upstream attempt (`app/services/llm/quota.py`), and
  one counter store behind both (`app/core/counter_store.py`) that uses Redis when
  `REDIS_URL` is set and degrades to per-process counters when it is not.
- **Observability**: JSON logs, a request id echoed as `X-Request-ID` and logged on one
  `http_request` line, a shaped `500` that never leaks internals, and health probes that report
  the provider, the database, the counter store and the remaining budget.
- **API docs are a development aid.** `/docs`, `/redoc` and `/openapi.json` are served outside
  production and off by default inside it; `EXPOSE_API_DOCS` overrides either default.

### Frontend

- Next.js 16 App Router with Tailwind **v4**. The v3 → v4 migration was taken to clear a
  high-severity transitive `npm audit` finding; going back would reintroduce it.
- `proxy.ts` (Next 16's replacement for `middleware.ts`) guards every page except `/login` and
  redirects a stranger to `/login?next=…`, using the session cookie as its signal.
- `lib/api.ts` is the single fetch wrapper: it sends `credentials: "include"`, understands
  FastAPI's `{"detail": …}` and its 422 array, and throws `ApiError`. `lib/stream.ts` is the SSE
  reader used by the Sandbox.
- Every page uses SWR, paginated lists share `lib/paged.ts` and `components/LoadMore.tsx`, and
  the layout holds from 1440 px down to 390 px.
- `next.config.js` rewrites `/api/*` to `BACKEND_URL` **at build time**, which is why the Docker
  build takes `BACKEND_URL` as a build argument.

### Infrastructure

- `compose.yaml` defines `db` (pgvector/pgvector:pg16, host port 5434), `redis` (no persistence:
  these are windows, not data), `backend` (8010) and `frontend` (3000). Host ports are
  overridable with `DB_PORT`, `REDIS_PORT`, `BACKEND_PORT`, `FRONTEND_PORT`.
- Both Dockerfiles are multi-stage, run as a non-root user and carry healthchecks; the backend
  waits for both dependency healthchecks before starting.
- CI (`.github/workflows/ci.yml`) runs backend lint and tests against pgvector and Redis service
  containers, frontend typecheck, tests and an audit gate, then both image builds.

### Verification as it stands

- `ruff check app tests` — clean.
- `pytest -q` — **325 passing**, against a real PostgreSQL/pgvector and Redis. The suite creates
  and migrates its own `cloudops_ai_test` database, truncates between tests, and swaps in a
  deterministic offline provider, so no test spends provider quota.
- `npm run test` — **103 passing** across 14 files (Vitest and React Testing Library).
- `tsc --noEmit` and `next build` — clean.
- The browser checks recorded below were made against the production Next build, not `next dev`.

## 3. Remaining work

Everything below was checked against the tree while this log was last revised; each entry names
what would have to change, so a future reader can tell a missing feature from a deleted one.

1. **Self-service password reset does not exist.** An administrator can set someone's password
   through `PATCH /users/{id}`, and that is the reset path today. It is deliberate up to a point
   — there is no signup, no email transport configured and no token store — but an operator who
   forgets their password has to find an administrator.
2. **A metrics endpoint does not exist.** The operational surface is `GET /health` and
   `GET /health/llm`, which between them report database, provider, counter store and budget.
   Anything that needs a time series has to scrape logs.
3. **Webhook deliveries are not deduplicated.** Alertmanager re-sends a group on every
   `repeat_interval` and again when it resolves, and each delivery creates new alert rows; a
   long-firing alert therefore appears once per notification. The fingerprint field is accepted
   and ignored because coalescing it needs a product decision first — whether a `resolved`
   notification closes the original row or reopens it. Pinned by
   `test_a_repeated_delivery_creates_the_alerts_again`.
4. **The provider budget is not shown in the dashboard.** It is reported by `GET /health/llm`
   as `limit/used/remaining/resets_in`, so an operator can see the ceiling coming, but nothing
   on the Overview page renders it.
5. **Frontend coverage stops at components.** The pages are driven through real render trees
   with a stubbed `fetch`, which catches rule drift and error handling, but there is no
   end-to-end suite; the flows involving a live provider and a real browser were verified by
   hand and are recorded below.
6. **Budgets are not split per provider method.** Chat, streams and embeddings draw on one
   project-wide allowance, which is deliberately conservative: Gemini meters them separately,
   and refusing a call that might have succeeded is cheaper than discovering the ceiling
   mid-demo.
7. **The apple-touch-icon is an SVG, and iOS wants a PNG.** The mark is hand-written SVG on
   purpose, so `public/apple-touch-icon.svg` is what the page links to; iOS falls back to a
   screenshot. Generating PNG sizes is a build step that has not been added.
8. **The demo login is shared by construction.** Its credential is public and it is a Viewer;
   it is bounded by `DEMO_ACCOUNT_RATE_LIMIT` per quota window, and it should only be enabled
   where a stranger reading the demo data is acceptable.

## 4. Running it

`docs/development.md` is the current guide; this is the short form.

```bash
# whole stack
docker compose up --build
# dashboard http://localhost:3000 | api http://localhost:8010 | postgres localhost:5434

# backend without Docker
cd backend
python -m venv .venv && .venv\Scripts\Activate.ps1
pip install -e ".[dev]"
alembic upgrade head            # needs PostgreSQL+pgvector at DATABASE_URL
uvicorn app.main:app --reload --port 8010

# frontend without Docker
cd frontend
npm install
npm run dev                     # frontend/.env.local: BACKEND_URL=http://127.0.0.1:8010
```

`backend/.env` is required and is gitignored; `backend/.env.example` is the template and the
authoritative list of what can be configured. `GEMINI_API_KEY`, `JWT_SECRET`, `ADMIN_EMAIL`,
`ADMIN_PASSWORD` and `DATABASE_URL` are the five a deployment cannot start without in
production.

## 5. Conventions and pitfalls

**Conventions.** The provider API key never reaches a browser: every LLM call goes through
FastAPI. Every data route depends on `get_current_user` and a role, and the role is read from
the user row on every request. New provider capabilities are added to `LLMProvider` first.
Pydantic schemas sit at every API boundary. The backend is async throughout. Comments explain
*why* a thing is the way it is, never *what* the next line does. On the frontend, Tailwind v4
reads its theme from `@theme` in `app/globals.css` and loads the typography plugin with
`@plugin`; there is no `tailwind.config.ts`, and a new one would not be read.

**Pitfalls that have already bitten this project once each.**

- *Startup seeding must be idempotent and concurrency-safe.* Two Gunicorn workers racing to
  insert the seeded administrator took the container down until the insert became
  `ON CONFLICT DO NOTHING`. Both seeders — the administrator and the demo account — are written
  that way.
- *Next 16 evaluates `rewrites()` at build time.* A `BACKEND_URL` supplied only at runtime is
  ignored, which is why it is a Docker build argument.
- *Unlayered CSS beats Tailwind v4's `@layer utilities`.* The component classes in
  `app/globals.css` live in `@layer components`; outside it, `.pill-nav`'s `display` silently
  overrode `hidden`, and the desktop nav overflowed a 390 px viewport.
- *Compression buffers streams.* A gzip encoder held every token until the generation finished
  in the production build while `next dev` and curl looked fine. Responses that stream set
  `Cache-Control: no-store, no-transform`.
- *An exception raised after an SSE status line is committed cannot become a `503`.* The stream
  routes wrap their generators and end with an `error` frame plus `done` instead.
- *Anything that must be shared across workers has to live outside the process.* Rate limits and
  the provider budget go through the counter store; without Redis each worker enforces its own
  copy and the real ceiling is `workers × limit`.
- *A setting that only exists in the code is a setting nobody can configure.* A test asserts
  that `Settings` and `backend/.env.example` name exactly the same variables.

## 6. Where the rest is written down

| Document | What it covers |
| --- | --- |
| [README.md](README.md) | The tour, the gallery, the security model, known limitations. |
| [docs/api.md](docs/api.md) | Route-by-route conventions: pagination envelope, timeline, post-mortem workflow, ingestion. |
| [docs/reliability.md](docs/reliability.md) | Rate limits, provider budget, retry and timeout taxonomy, SSE, error projection, observability. |
| [docs/development.md](docs/development.md) | Local setup, verification including the Redis-dependent tests, CI jobs. |
| [DEPLOYMENT.md](DEPLOYMENT.md) | Secrets, the startup guard, HTTPS proxies and their read timeouts, Gunicorn sizing, backups, upgrades. |

## 7. Browser verification, and the three defects it found

Typecheck, build, lint and container health all passed while three real defects were sitting in
the running app. A click-through at 1440×900 and 390×844 found them:

1. **Login was impossible with the documented default administrator.**
   `admin@cloudops.local` was declared as pydantic's `EmailStr`, and `email-validator` rejects
   reserved TLDs outright, so `POST /auth/login` answered `422`. A lenient `Email` type in
   `app/schemas/common.py` falls back to a shape check for reserved domains while still
   validating ordinary addresses.
2. **Validation errors rendered as a bare status code.** `lib/api.ts` only understood
   `{"detail": "..."}`; 422 bodies are arrays. It now flattens them into a readable sentence.
3. **The mobile nav was still broken.** `hidden lg:flex` lost to unlayered component CSS, so
   the desktop nav overflowed at 390 px. Moving the component classes into
   `@layer components` fixed it and silently repaired two other overrides (`bg-brand-rose` on
   the triage button, `px-0` on the sandbox send button).

After the fixes, every page rendered at both widths with no horizontal overflow
(`scrollWidth == clientWidth`) and a working hamburger menu.

The same round closed six items of the original brief: the dead scaffold modules were deleted;
integration tests against a real PostgreSQL were added; CI was wired up; per-user rate limiting
landed on every provider-calling route; list endpoints gained a pagination envelope (a breaking
change, with all four consumers moved to `useSWRInfinite`); and observability gained structured
logs, request ids and `GET /health/llm`.

## 8. Provider retry and backoff

Transient `503 UNAVAILABLE` responses from the model were reaching users verbatim as `502`s at
roughly one call in three under load. The provider layer now distinguishes failures a later
attempt can recover from.

- **`LLMTransientError`** carries `replayable`: a dropped socket or an overloaded 5xx clears in
  milliseconds and is replayed here, while a quota window (`429 RESOURCE_EXHAUSTED`) outlives
  the request, so replaying it only burns more quota and it goes to the caller instead. A plain
  `LLMError` still means permanent.
- **`RetryingProvider`** wraps any provider with exponential backoff and full jitter, capped at
  `LLM_RETRY_MAX_DELAY`. `chat` and `embed` are replayed; `stream` is replayed only before the
  first token, because replaying mid-stream would duplicate text; `health` is never retried,
  because a probe has to answer promptly. `LLM_MAX_RETRIES` counts attempts after the first, so
  the default of 2 means at most three upstream calls, and `0` disables the wrapper.
- **HTTP contract**: exhausted and retryable → `503` with `Retry-After`; a quota `429` → `503`
  with the rate-limit window; a permanent failure stays `502`. Starlette resolves the more
  specific exception class first, so both handlers hang off the existing one.
- **Verified against the live provider**: `429 RESOURCE_EXHAUSTED` → `503 Retry-After: 60` with
  no `llm_retry` line (no quota burned); `503 UNAVAILABLE` → jittered `llm_retry` lines and a
  `503` once the attempts were spent; ordinary requests stayed `200`.

The gap this exposed was per-call latency rather than count: one attempt was measured at 70 s,
holding a worker and a rate-limit slot for that whole time.

## 9. Per-attempt provider timeouts

§8 bounded how many times a call is replayed; it did not bound how long one attempt may hang.

- **Settings**: `LLM_REQUEST_TIMEOUT=30` bounds one non-streaming attempt,
  `LLM_STREAM_IDLE_TIMEOUT=60` bounds the silence *between* streamed chunks, and
  `LLM_CONNECT_TIMEOUT=5` bounds connection setup. `0` disables a bound.
- **Enforcement** lives in `AttemptTimeouts` around each call, so the deadline is per attempt
  for both providers and all four methods. A deadline that fires raises
  `LLMTransientError(replayable=True)` and inherits the existing `503` + `Retry-After` contract;
  nothing in the route layer changed.
- **Streams** apply the deadline to each `__anext__()`, not to the whole response, so a slow but
  actively producing generation is never cut off. `contextlib.aclosing` closes the upstream
  generator when a client disconnects, which previously waited for garbage collection.
- **Neither SDK is the retry authority.** `google-genai`'s `retry_options` is deliberately unset
  (its default is no retries), so the bounded policy above is the only source of extra requests —
  the constraint being that no retry may create a duplicate paid request.
- **Provider-supplied waits are honoured.** `Retry-After` used to be a local guess, which is
  wrong whenever the window is not ours to guess. `LLMTransientError.retry_after` carries what
  the provider asked for, parsed from `RetryInfo.retryDelay` or an httpx `Retry-After` header,
  and the handler prefers it. Verified live: the header went from a hardcoded `60` to `55316`,
  counting down correctly.
- **A bug the tests caught**: `google-genai`'s `APIError` accepts the body as `response_json`
  but exposes it as `details`, so the first version of the hint lookup silently returned `None`
  on the real path. `error_body()` checks both names, and a test pins it so an SDK rename fails
  loudly.
- **The finding that prompted §10**: the free tier caps the model at 20 `generateContent`
  requests per day per project, while `LLM_RATE_LIMIT` allowed 20 per *minute* per user. A
  single user could spend the day's allowance in a minute, and every later caller got a `503`
  with an hours-long `Retry-After`.
- **`.gitignore` audit**: `git check-ignore` confirmed `.env` and `.env.local` were covered and
  found five generated paths that were not (`*.egg-info/`, `.pytest_cache/`, `.ruff_cache/`,
  `*.tsbuildinfo`, `.freebuff/`), all of which existed on disk. Every rule was re-checked
  against the files that must stay committable.

## 10. The provider budget

The ceiling §9 asked for now exists, and the unit of account is the decision that matters.

- **The unit is the upstream attempt, not the HTTP request.** One request can fan out into
  several provider calls — indexing a runbook embeds once per 900-character chunk — and every
  replayed attempt is another request as far as the provider is concerned. Counting requests
  would under-report both.
- **`QuotaGuardedProvider` sits inside `RetryingProvider`**, so replays are charged. Wrapping the
  other way round would charge once per logical call and let retries slip past the ceiling;
  two tests pin the ordering and its consequence.
- **Refusals reuse the existing contract** rather than inventing a second one: a spent budget
  raises `LLMTransientError(replayable=False, retry_after=…)`, so callers still get `503` plus
  `Retry-After`, with one improvement — the refusal fires before the request is sent, so the
  wait it advertises is the time actually left.
- **The default follows the provider**: 20 for Gemini, matching the free tier; unlimited for
  Ollama, which has no quota to protect. An explicit `LLM_QUOTA_LIMIT` always wins, including
  `0` to disable. The window is a day.
- **Chat, streams and embeddings share one budget on purpose.** Refusing a call that might have
  succeeded is much cheaper than discovering the ceiling mid-demo; splitting it later is a key
  change in `quota.py`, not a refactor.
- **`GET /health/llm` reports it** as `quota: {limit, used, remaining, window_seconds,
  resets_in}`, omitted when the budget is disabled. The reachability probe itself is exempt: a
  monitoring loop polls far more often than 20 times a day and must not be able to spend the
  allowance users need.
- **A crash the probe caught**: an exception raised while an SSE response is being iterated
  cannot become a `503`, because the status line and headers are already on the wire. This was
  pre-existing, but a spent budget triggers it exactly when someone opens the sandbox stream, so
  `/sandbox/stream` now wraps its generator and ends with an `error` frame followed by `done`.
- **`SlidingWindowLimiter` moved to `app/core/window.py`.** The provider budget lives in the LLM
  layer, and reaching back into a module that imports API auth dependencies would have inverted
  the layering the swappable-provider constraint depends on. It gained a read-only `state()`,
  and a documented rule that one instance serves one window length: `check()` prunes every key
  against the caller's window, so one instance holding a 60-second limit and a day-long budget
  would erase the daily counter. The budget therefore has its own instance.
- **What is still true**: the budget limits spend, not request rate. One user can still consume
  the whole shared allowance quickly — they just cannot exceed it, and the failure names the
  setting instead of being a fifteen-hour provider outage.

## 11. The repository, and shared counters

**A repository at last.** The project had no history of its own; it sat untracked inside the
home-directory repository, which had no commits and thousands of unrelated staged paths. It was
initialised here with one commit of 96 files. The parent repository was left untouched, and that
was verified rather than assumed: the checksums of its index and `HEAD` were identical before and
after, and it still reported no commits. The sensitive and generated paths (`backend/.env`,
`frontend/.env.local`, `.venv`, `node_modules`, caches) were excluded, so the first commit
contains only the template files. Nothing was pushed; there is no remote. A later `git add` in
the parent directory will warn about a nested repository: the fix is to leave it alone rather
than record it as a gitlink.

**Counters moved behind a store** (`app/core/counter_store.py`). Both ceilings — the per-user
rate limit and the provider budget — go through one interface with two backends:

- `MemoryCounterStore` keeps the previous behaviour exactly and remains the default when
  `REDIS_URL` is empty. Single-worker and local runs are unaffected.
- `RedisCounterStore` puts the window in Redis: a Lua script prunes a sorted set, counts it and
  claims a slot in one atomic step, using the server's `TIME` so worker clock skew cannot stretch
  or shorten a window. Atomicity is the point — two workers both reading "19 used" and writing 20
  is how a shared 20/day budget quietly becomes 40/day.
- **An unreachable Redis degrades instead of failing open.** The store falls back to its own
  process-local counters and logs one throttled warning a minute, so the ceiling narrows to the
  worker that noticed rather than disappearing.
- **Health visibility**: `GET /health` gained `counters` (`ok`, `unconfigured`, or the reason it
  is unreachable), and the quota block gained `store`, which is the field that says whether those
  numbers mean anything deployment-wide. The probe runs on every health call, so an orchestrator
  notices a lost Redis.
- **Demonstrated, not argued**: with Redis configured in Compose, a separate process inside the
  container claimed three calls, and the API server — a different process — then reported
  `used: 3, remaining: 17, store: redis`, with the sorted set visible in `redis-cli`.
- The Redis-dependent tests skip with a reason when no Redis answers rather than reporting an
  unearned pass; CI runs a `redis:7-alpine` service so they run on every push.

## 12. Sandbox streaming, and the bug only a browser could show

The Sandbox used the blocking `POST /sandbox/chat`; it now streams from `POST /sandbox/stream`,
which already existed and had no consumer.

- **The client** (`frontend/lib/stream.ts`) reads the body as bytes and splits SSE frames.
  `EventSource` cannot be used: it only issues GET requests and this endpoint takes a JSON body.
  Handled explicitly: CRLF endings, `:` keep-alive comments, multi-line `data`, a multi-byte
  character split across chunks, and the difference between a refusal that arrives as a status
  code and one that arrives as an `error` frame. The `done` frame is tracked, so a stream that
  ends without one is reported instead of being shown as a finished answer.
- **The page** appends tokens as they arrive, shows a caret while the answer is live, and offers
  **Stop** in place of Send. Stopping aborts the fetch and keeps the partial answer, and the two
  cases are distinguished: "Stopped before any output." when nothing had arrived, and the partial
  text plus a note when it had.
- **A bug visible only in a browser.** The stream worked in `next dev` and in every curl probe
  and arrived in one lump in the production build: the Next server gzips by default, and a gzip
  encoder buffers until it has a block to emit. Browsers always send `Accept-Encoding: gzip`;
  curl does not, which is why the probes kept passing. The response now sets
  `Cache-Control: no-store, no-transform`, verified at the header level, in the browser and by a
  test. This class of bug cannot be caught by testing the API, only by testing what the user
  looks at.
- **Verified in the browser through the production Next server** (`npm start`, not `next dev`)
  with a local stub provider: partial text visible while the answer was generated, both stop
  branches, the `done` path, and the refusal note once the shared budget was spent. The stub
  lives outside the repository.
- **Two consequences for callers**: `lib/api.ts` grew `toApiError()` so the stream client and
  the JSON client explain failures the same way, and the Sandbox no longer calls
  `/sandbox/chat`. That endpoint stays as the documented non-streaming interface.

## 13. The provider's words stop at the server

The Sandbox showed a raw Gemini 429: about a kilobyte of JSON naming the quota metric, the quota
id, the model, the project and a documentation URL. Useful to an operator, noise in a chat
bubble, and project metadata to anyone else.

- **One projection point** (`app/services/llm/errors.py`). `public_llm_error()` logs the failure
  in full and returns only what may travel: a code, a sentence written here, the provider *name*
  from configuration, an optional wait and a request id. Splitting log-and-project into two calls
  would let one of them be forgotten.
- **A closed vocabulary** — `quota_exhausted`, `budget_exhausted`, `rate_limited`, `timeout`,
  `unavailable`, `provider_error` — carried as `kind` alongside the internal, log-only message.
- **A quota is told from a rate limit by the wait, not the payload.** `kind_for_wait()` splits
  them at one hour: per-minute limits ask for around 60 s, the free-tier daily cap asked for
  around 56 000 s. Reading the quota metadata instead would mean parsing exactly the material
  that must not travel.
- **The sentence** is composed from our own configuration and the wait, and states a wait only
  when the provider gave one — a guessed countdown would be worse than none. On the Sandbox it
  reads: *"Gemini's daily quota has been reached. Sandbox responses will be available again in
  approximately 15 hours 33 minutes. Switch to the local Ollama provider to keep working in the
  meantime."*
- **Wire shape**: SSE `error` frames carry `{"code", "message", "provider", "retry_after"?,
  "request_id"?}`; HTTP 503/502 bodies carry the sentence as `detail` and the same object as
  `error`. `retry_after` is absent when unknown, so a client can tell "wait this long" from "no
  idea".
- **The frontend** accepts only that exact field set, so a provider payload that happens to be
  valid JSON is still discarded. The page renders the server's sentence verbatim, keeps the code
  as a `data-error-code` attribute, and offers the request id as **Copy diagnostic ID** instead
  of printing it.
- **A bug the tests found**: `alembic/env.py` called `fileConfig()` with
  `disable_existing_loggers=True`, which switches off every logger already in the process.
  Harmless for a standalone migration, but the test harness migrates in-process, so
  `cloudops.*` went silent for the rest of the run. Fixed at the source.
- **Tests**: 27 backend tests drive a verbatim copy of the live 429 through the projection and
  the HTTP surface, asserting on leak markers (`generativelanguage.googleapis.com`,
  `quotaMetric`, `ai.google.dev`, `@type`, `RetryInfo`, …) in both directions — nothing in the
  response, the full text in the log line. Frontend tests cover SSE framing, progressive
  rendering, both stop branches, the note with and without a wait, and the copy action.

## 14. Roles, an incident's history, and real webhooks

- **Roles are enforced per route** (`app/core/rbac.py`, `app/api/deps.py::require_role`).
  Privilege is a *rank* rather than a permission matrix, so "may this caller do X" stays one
  comparison and a route cannot end up with a list that drifts out of step with the others.
  `rank()` fails **closed**: an unrecognised stored value ranks below Viewer and grants nothing,
  because the alternative turns a typo into an administrator. `ROLE_VALUES` also generates the
  migration's check constraint, so the database and the module cannot disagree about what a
  valid role is. The role is read from the user row on every request, so a demotion takes effect
  on the next call — which is what revoking access during an incident has to mean.
- **An incident has an append-only history** (`app/models/incident_event.py`,
  `app/services/timeline.py`). Nothing updates or deletes an entry; `actor` is the email of
  whoever acted, so the record survives a rename; `from_value`/`to_value` hold the diff next to
  the sentence rather than making a reader parse it. The helper deliberately does not commit: the
  caller is already in the transaction that changes the incident, and an event in its own commit
  could outlive a change that rolled back. Re-saving the same status writes nothing, and `PATCH`
  treats an absent `assignee` as "leave it" while `null` means "unassign" — the two have to be
  distinguishable or changing a status would unassign somebody.
- **Alertmanager ingestion** (`app/api/routes/webhooks.py`, `app/services/alertmanager.py`). The
  caller is Prometheus, which has no cookie and no role, so `X-API-Key` compared with
  `compare_digest` is its whole identity — constant time, because a check that returns early on
  the first wrong byte is a token oracle. The check is a FastAPI dependency, which matters: body
  validation runs before the endpoint, so an unauthenticated caller would otherwise get a schema
  error back, telling someone with a wrong key the shape of a private API. An unset token answers
  `503` rather than accepting anything; a missing or wrong key answers `401`. Each entry of
  `alerts[]` becomes one row — the per-alert status is read, not the group's, because a group is
  `firing` as a whole even when one of its alerts has recovered. The payload models are lenient
  on purpose (`extra="ignore"`): Alertmanager owns that shape.
- **Migrations** `202610080001_rbac_roles.py` (adds `users.role`, backfilled to `admin` for the
  seeded account, plus the check constraint) and `202610080002_incident_timeline.py` (creates
  `incident_events`, backfilling one `created` entry per existing incident so old incidents are
  not blank).
- **Tests**: `test_rbac.py` sweeps every data route as every role, so a new route without a role
  dependency fails the sweep; `test_api_incidents.py` covers the timeline and the no-op rule;
  `test_webhooks.py` covers the token states, the mapping table, resolved versus firing, an empty
  group, and the repeated delivery the README lists as a limitation.

## 15. The name, the front door, and the post-mortem workflow

**The name.** The product is **Excubitor**, with "CloudOps AI" as the subtitle. What changed is
presentation only: the `APP_NAME` value, the triage system prompt, the configuration default and
the package description. No environment variable *name*, database name or DSN, image name or
Python package name moved, so nothing a deployment stores or addresses changed and no migration
was needed. The mark is hand-written SVG — a watchfire inside a shield, one path with two
subpaths filled `evenodd` so the flame is a real hole, drawn in `currentColor` so the same
geometry sits on the dark nav tile, the brand panel and the favicon.
`components/Logo.tsx` exports the path data and both icon files are built from that constant, so
the tab icon cannot drift from the nav; the geometry is coarse on purpose because it has to
survive 16 px.

**The sign-in page** is a two-panel layout that collapses to a header and form below `lg`. Three
decisions worth keeping: the fields are **empty on purpose** — the earlier page pre-filled the
seeded administrator's credentials, which turns a sign-in page into a button anyone who can reach
it may press, and the developer hint is now a muted footnote naming `ADMIN_EMAIL` /
`ADMIN_PASSWORD`; the button is a real `type="submit"`, so Enter in either field signs in; and a
failure renders as an inline `role="alert"` sentence from the API rather than a bare status code.

**The post-mortem workflow.** Generation stays on the incident, because it is an incident action
and it resolves the incident; everything afterwards is its own collection. The migration adds the
reviewed sections (`timeline_summary`, `root_cause`, `contributing_factors`, `impact`), a
`status` (`draft` → `in_review` → `published`), author fields, `published_at`, `updated_at`, and
a `post_mortem_actions` table with owner, due date, status and `position`.

- **Existing rows were backfilled to `published`, not `draft`.** They had been created through
  the API and readable by every role, so defaulting them to draft would have hidden documents
  people could already see. New rows still start as `draft`, and the original narrative was left
  untouched: the structured sections sit beside it and nothing is parsed out of prose.
- **Visibility is one predicate in one place** (`app/services/postmortems.py::visible_to`),
  because two routers ask the question and spreading it is how one of them leaks a draft.
  Operators and administrators see every draft — a reviewer who can only see their own drafts is
  not a reviewer — and a Viewer sees `published` only, getting `404` (not `403`) for a draft by
  id.
- **The export is a pure function** (`render_markdown`), which is why the exact bytes can be
  pinned. Empty sections render as `_Not recorded._` rather than being omitted, and overdue dates
  are marked at render time.
- **Action items replace wholesale on a `PATCH`**: an item returned with its `id` is the same
  row, one without `id` is created, anything missing is removed. Array order becomes `position`.
  `owner` is an email resolved to a user when one matches and kept as written when none does.
  Owners and due dates suggested by the model are dropped, because a fabricated deadline on
  somebody's list is worse than an empty one.
- **`GET /overview` carries the strip** that makes them actable: `{open, overdue, items[]}`, the
  earliest due date first, undated items last.
- **Verified in a browser** on the production build: generate → edit sections → move to
  `in_review` → publish (the timeline records each transition) → download the markdown.

## 16. The proxy's patience, the demo login, and two rules that had drifted

**A slow answer needs a patient proxy.** The worst case this service can take to answer is
arithmetic rather than a guess: `LLM_REQUEST_TIMEOUT × (LLM_MAX_RETRIES + 1)` plus the largest
backoff delays, or the stream's idle bound if that is longer — exposed as
`Settings.llm_patience_seconds`. `PROXY_READ_TIMEOUT_SECONDS` is what the deployment *tells* the
app its proxy will wait, and the startup guard compares the two, naming both numbers when they
disagree (with the defaults, 60 s against 91.5 s). Nothing is enforced from inside the app — the
proxy is the thing that gives up — but a proxy on nginx's own default turns an honest `503` and
its request id into a dead connection. `DEPLOYMENT.md` carries working nginx and Caddy examples.

**The shared demo login.** A stranger may sign in as a Viewer without being sent to ask for an
account. `DEMO_ACCOUNT_ENABLED` requires `DEMO_ACCOUNT_PASSWORD` — one without the other
provisions nothing, or publishes a credential the deployment believes is off — and the credential
is served by `GET /auth/demo` so the sign-in page can prefill it. The server keeps the only copy;
the browser bundle holds none. The account is re-asserted as a Viewer on every boot, so a
promotion made in the UI does not survive a restart, and turning the feature off refuses the
credential at sign-in rather than only hiding the button. It draws on one shared allowance
(`DEMO_ACCOUNT_RATE_LIMIT` per quota window) so a single visitor cannot spend the project's whole
budget, and startup logs the address it published — never the password.

**Administrator account management.** `GET/POST/PATCH/DELETE /users` and the
**Admin → Users** page replace the runbook's `psql DELETE`. Two refusals are pinned: the last
administrator cannot be removed, and neither can the account making the request; the
last-administrator answer is checked first because it names the loss, and the two overlap in
exactly one case. A 204 removes the row and that session's next request ends. The live-stack
script in `scripts/` exercises both guards against a real deployment and deletes the throwaway
administrator it created.

**Two UI rules had drifted from the routes they front, in opposite directions.** The Sandbox kept
its Send control disabled below Operator and printed "Requires the operator role or higher", while
`POST /sandbox/chat` and `/sandbox/stream` answer any signed-in role: asking spends provider
budget rather than state, and what bounds it is the project quota and the demo allowance. The
practical effect was that the shared demo login — the one account the README promises an AI
allowance — could not use the Sandbox at all. The control now waits for the session and enables
Send for anyone the API would answer. In the other direction, the edge guard listed five of seven
pages and `/admin/users` was served a rendered shell to a stranger; it is now one of the guarded
pages, and the middleware test pins the whole list — every page redirecting to
`/login?next=…`, encoded nested paths, `/login` reachable and bouncing a signed-in visitor home,
and the matcher still skipping `/api`, the Next asset paths and `favicon.ico`.

**The API schema stopped being public.** FastAPI mounts `/docs`, `/redoc` and `/openapi.json` by
default, so a production deployment published a map of every route and response shape to anyone
who asked. `EXPOSE_API_DOCS` follows the environment when unset — on outside production, off
inside it — and an explicit value wins either way. When it is off the paths answer `404` like any
other route nobody wrote; absence is the only answer that does not describe the deployment.

**What was verified for this round.** Backend: `ruff` clean, 325 tests passing. Frontend: 103
tests across 14 files, typecheck and production build clean. The two UI fixes were also checked
against the rebuilt stack: an anonymous request to `/admin/users` answers `307` to
`/login?next=%2Fadmin%2Fusers` while every other page still redirects, and the demo Viewer can
send a question through `POST /sandbox/stream` and receive a streamed answer.
