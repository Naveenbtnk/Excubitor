# Excubitor — Handoff Brief for Continued Development (DeepSeek)

> Read this entire file before writing any code. It tells you what this project is,
> exactly what has already been built and verified, what is intentionally left for you,
> and the constraints you must respect. Do not re-architect what already works — extend it.

## 1. What this project is

**Excubitor** (CloudOps AI) is an AIOps (Cloud Operations) dashboard, modeled on tools like PagerDuty /
Datadog incident-response consoles. It simulates cloud infrastructure alerts, uses an LLM to
triage them (root cause + remediation), retrieves relevant runbooks via vector search (RAG),
and generates AI-written post-mortems. It is a two-service app:

- **`backend/`** — Python 3.12, FastAPI, async SQLAlchemy 2.1, PostgreSQL + pgvector, Alembic
  migrations, JWT cookie auth, Google Gemini via `google-genai`.
- **`frontend/`** — Next.js 16 (App Router, Turbopack), React 19, Tailwind CSS 4, SWR for data
  fetching, `react-markdown` for rendering AI output.
- **`compose.yaml`** at the repo root runs the whole stack (PostgreSQL/pgvector + backend +
  frontend) with Docker.

The product has six user-facing screens: **Overview**, **Alerts**, **Sandbox** (free-form chat
with the LLM), **Knowledge** (runbook upload + RAG query), **Post-Mortems**, and an incident's own
**Timeline** page, plus a **Login** page gating all of them. Three roles — Admin, Operator and
Viewer — are enforced on every route, not in the UI.

## 2. Current status — everything below is DONE and verified

Do not redo this work. It has been built, linted, type-checked, tested, and the Docker images
build and run healthy.

### Backend (`backend/`)
- Layered structure: `app/core` (config, security), `app/db` (session/base), `app/models`
  (SQLAlchemy ORM: `User`, `Alert`, `Incident`, `PostMortem`, `RunbookChunk` with a pgvector
  `Vector(768)` column), `app/schemas` (Pydantic request/response models), `app/api/routes`
  (FastAPI routers), `app/services` (LLM providers, RAG, triage, simulator).
- **Auth**: JWT in an httpOnly cookie (`app/core/security.py`, `app/api/routes/auth.py`,
  `app/api/deps.py::get_current_user`). Single admin seeded from `ADMIN_EMAIL` /
  `ADMIN_PASSWORD` env vars at startup (`app/db/session.py::seed_admin`, uses an
  `INSERT ... ON CONFLICT DO NOTHING` to be safe under multiple Gunicorn workers). **There is
  no public signup route by design** — this is an internal ops tool.
- **LLM layer** is provider-swappable (`app/services/llm/base.py::LLMProvider`). Two
  implementations exist: `gemini.py` (default, uses `google-genai` SDK, model
  `gemini-3.8-flash`, embeddings `gemini-embedding-001` at 768 dims, supports structured JSON
  output via Pydantic `response_model` and token streaming) and `ollama.py` (local fallback).
  Switch via `LLM_PROVIDER` env var.
- **RAG**: `app/services/rag.py` chunks runbook text (900 chars, 120 overlap), embeds with
  Gemini, stores in `runbook_chunks` (pgvector), queries via cosine distance.
- **Triage**: `app/services/triage.py` calls Gemini with a Pydantic `TriageResult` schema
  (summary, root_cause, remediation_steps, confidence) — this is **structured output**, not
  free text parsing.
- **Alembic**: one migration (`backend/alembic/versions/202609290001_initial_schema.py`)
  creates all tables plus `CREATE EXTENSION IF NOT EXISTS vector` and an HNSW index on
  `runbook_chunks.embedding`.
- **Routes implemented.** Everything requires the session cookie and a role except the two
  health probes, `POST /auth/login` and the webhook:
  - public: `GET /health`, `GET /health/llm`, `POST /auth/login`, `POST /webhooks/alertmanager`
    (shared token, not a session).
  - session: `POST /auth/logout`, `GET /auth/me`, `GET /overview`, `GET /alerts`, `POST /alerts`,
    `POST /alerts/simulate`, `GET /alerts/scenarios`, `POST /alerts/{id}/ack`,
    `GET /knowledge/runbooks`, `POST /knowledge/runbooks`, `POST /knowledge/runbooks/upload`,
    `POST /knowledge/query`, `POST /sandbox/chat`, `POST /sandbox/stream` (SSE),
    `GET /incidents`, `GET /incidents/{id}`, `PATCH /incidents/{id}`,
    `GET /incidents/{id}/activity`, `POST /incidents/{id}/comments`,
    `POST /incidents/triage/{alert_id}`, `GET /incidents/{id}/post-mortem`,
    `POST /incidents/{id}/post-mortem`.
  - the document's own collection (see §15): `GET /post-mortems`, `GET /post-mortems/{id}`,
    `PATCH /post-mortems/{id}`, `PATCH /post-mortems/{id}/actions/{id}`,
    `GET /post-mortems/{id}/export.md`.
  - admin only: `GET /users`, `POST /users`, `PATCH /users/{id}`, `DELETE /users/{id}`.
    Deletion refuses the caller's own account and the last administrator; scripts/
    verify_live_stack.py (see docs/development.md) exercises both live.
- **Verified**: `ruff check app tests` passes clean. `pytest` passes (4 tests: password
  hashing/JWT round-trip, RAG chunking). `docker build` succeeds. Container boots, runs Alembic
  migration, seeds admin, passes its healthcheck.
- Legacy scaffold modules (`app/llm/`, `app/rag/`, `app/routers/`, `app/simulator/`,
  `app/db/models.py`) are **dead code left over from the first prototype**. They are excluded
  from `ruff` in `pyproject.toml` and are not imported by `app/main.py`. You should delete them
  once you confirm nothing references them — they were superseded by `app/services/llm`,
  `app/services/rag.py`, `app/api/routes`, `app/models/`.

### Frontend (`frontend/`)
- Next.js 16 App Router, React 19, Tailwind **v4** (migrated from v3 to clear a high-severity
  `npm audit` finding in a transitive dependency — don't downgrade back to v3).
- `proxy.ts` (Next 16's replacement for `middleware.ts`) protects all routes except `/login`
  and redirects unauthenticated users, using the `cloudops_session` cookie as the signal.
- `lib/api.ts` is the single fetch wrapper — always sends `credentials: "include"`, parses
  FastAPI's `{"detail": "..."}` error shape, throws `ApiError`.
- All five pages (`app/page.tsx`, `app/alerts/page.tsx`, `app/sandbox/page.tsx`,
  `app/knowledge/page.tsx`, `app/post-mortems/page.tsx`) and `app/login/page.tsx` use SWR for
  data fetching and are mobile-responsive (the original issue — an oversized, wrapping desktop
  nav — was fixed in `components/Nav.tsx` with a hamburger menu under `lg` breakpoint).
- `next.config.js` rewrites `/api/*` to `BACKEND_URL` **at build time** — this is a Next 16
  quirk, which is why the Docker build passes `BACKEND_URL` as a build ARG (see
  `frontend/Dockerfile` and `compose.yaml`).
- **Verified**: `npm run typecheck` clean, `npm run build` succeeds (all routes prerender),
  `npm audit --omit=dev` → 0 vulnerabilities, `docker build` succeeds.

### Docker / Infra
- `compose.yaml` at repo root defines `db` (pgvector/pgvector:pg16, host port 5434),
  `backend` (host port 8010), `frontend` (host port 3000). Host ports are overridable via
  `DB_PORT` / `BACKEND_PORT` / `FRONTEND_PORT` env vars to avoid clashing with services already
  running on the developer's machine.
- Both Dockerfiles are multi-stage, run as a non-root `app` user, and have healthchecks.
- Verified end-to-end on alternate ports (`5435`/`8011`/`3002`): all three containers reported
  healthy, `GET /health` returned `{"status":"ok","provider":"gemini","database":"ok"}`.
- **Known fix already applied**: running Gunicorn with 2 workers caused a race where both
  workers tried to insert the seed admin simultaneously → `UniqueViolationError` → container
  marked unhealthy. Fixed with `INSERT ... ON CONFLICT DO NOTHING`. If you add more startup
  seeding logic, make it idempotent/concurrency-safe the same way.

## 3. What is explicitly NOT done — this is your scope

1. **Browser/UX verification was not completed in this session.** Typecheck, build, lint,
   tests, and Docker health all pass, but nobody clicked through the running app in a browser
   at both desktop and mobile widths. Do that first — it's the fastest way to find anything
   broken before adding features.
2. **Delete the dead scaffold code** listed above (`app/llm/`, `app/rag/`, `app/routers/`,
   `app/simulator/`, `app/db/models.py`) once you've confirmed `app/main.py` and its imports
   don't touch them.
3. **No automated frontend tests** exist (no Playwright/Vitest). No backend integration tests
   against a real Postgres (only unit tests for pure functions). Add these.
4. **No CI pipeline** (GitHub Actions or similar) wired up to run lint/typecheck/test/build on
   every push.
5. **No rate limiting** on `/sandbox/chat`, `/sandbox/stream`, or `/knowledge/query` — these
   call paid Gemini APIs and are only gated by login. Add per-user rate limiting before any
   real deployment.
6. **No pagination** on `/alerts`, `/incidents`, `/knowledge/runbooks` — they return full
   tables. Fine for a demo, not for production data volumes.
7. **No password reset / account management UI** — by design there's one seeded admin. If the
   user wants more than one operator account, you'll need to design that (and decide if public
   signup should stay disabled — recommend keeping it disabled unless told otherwise).
8. **Observability**: no structured logging, no request tracing, no metrics endpoint. The
   `/health` route only checks DB connectivity, not Gemini reachability.
9. **Secrets**: `backend/.env` currently exists locally with a real Gemini key. **It must never
   be committed.** Confirm `.gitignore` still excludes it before any `git add`. If you ever see
   a real API key appear in a command output, tool log, or screenshot, tell the user to rotate
   it immediately — this happened once already during development.

## 4. How to run it right now

```bash
# Backend (local, no Docker)
cd backend
python -m venv .venv
.venv\Scripts\Activate.ps1          # Windows
pip install -e ".[dev]"
# requires PostgreSQL+pgvector reachable at DATABASE_URL in backend/.env
alembic upgrade head
uvicorn app.main:app --reload --port 8010

# Frontend (local, no Docker)
cd frontend
npm install
npm run dev          # expects frontend/.env.local with BACKEND_URL=http://127.0.0.1:8010

# Full stack with Docker (recommended for parity with production)
docker compose up --build
# dashboard: http://localhost:3000 | api: http://localhost:8010 | postgres: localhost:5434
```

Required `backend/.env` keys (see `backend/.env.example` for the full template):
`GEMINI_API_KEY`, `JWT_SECRET`, `ADMIN_EMAIL`, `ADMIN_PASSWORD`, `DATABASE_URL`.

## 5. Hard constraints — do not violate these

- **Never expose the Gemini API key to the browser.** All LLM calls go through FastAPI.
- **Never commit `backend/.env`** or print its contents (including the API key) in logs,
  terminal output shared back to the user, or committed files.
- **Keep the LLM provider swappable.** Don't hardcode Gemini calls outside
  `app/services/llm/gemini.py`. If you add a new capability (e.g. function calling), add it to
  `LLMProvider` base class first so Ollama has a defined (even if degraded) behavior too.
- **Keep auth required on every data route.** Every router except `/health` and `/auth/login`
  must depend on `get_current_user`.
- **Don't downgrade Tailwind back to v3** — the v4 migration was done specifically to resolve a
  high-severity transitive `npm audit` finding (`braces` via `chokidar`/`tailwindcss@3`).
- **Keep `BACKEND_URL` as a frontend Docker build ARG** — Next.js 16 evaluates `rewrites()` at
  build time, not runtime, so this is not optional.
- Follow the existing code style: no comments explaining *what* code does, only non-obvious
  *why*; small layered modules; Pydantic schemas for every API boundary; async everywhere in
  the backend.

## 6. Suggested order of work for DeepSeek

1. Run the app (Docker or local) and click through all five pages + login at both a desktop
   and a ~390px mobile viewport. Fix anything visually broken.
2. Delete the dead scaffold modules (see §3.2) and confirm `ruff check app` and
   `uvicorn app.main:app` still work.
3. Add backend integration tests (spin up Postgres via `docker compose up -d db`, run Alembic,
   hit routes with `httpx.AsyncClient` + `ASGITransport`).
4. Add a GitHub Actions workflow: lint + test backend, typecheck + build frontend, build both
   Docker images.
5. Add rate limiting to the three LLM-calling routes.
6. Add pagination to list endpoints.
7. Revisit observability (structured logs at minimum; a `/health/llm` check that pings Gemini
   is a good addition).

Everything above this point reflects verified, working state as of this handoff. Build on it,
don't rebuild it.

## 7. Session update — browser verification + the remaining scope (done)

§§1–6 above were accurate about the codebase, but the browser click-through in §6.1 found three
real defects that typecheck/build/lint could not catch. All three are fixed and re-verified.

### Bugs found by clicking through the running app

1. **Login was impossible with the documented default admin.** `admin@cloudops.local` was
declared as pydantic's `EmailStr`, and `email-validator` 2.3+ rejects reserved/special-use TLDs
outright, so `POST /auth/login` returned `422` and `GET /auth/me` would also have failed
response validation. Fixed by adding a lenient `Email` type in `app/schemas/common.py` that
falls back to a shape check for reserved domains while still normalising and validating real
addresses.
2. **FastAPI validation errors rendered as a bare status code.** `lib/api.ts` only understood
`{"detail": "..."}`; 422s arrive as an array of objects. It now flattens those into a readable
message.
3. **The mobile nav was still broken.** `components/Nav.tsx` used `hidden lg:flex`, but the
custom `.card` / `.pill-nav` / `.input` / `.btn-*` classes in `app/globals.css` were written
outside any `@layer`. Unlayered CSS beats Tailwind v4's `@layer utilities` regardless of source
order, so `.pill-nav`'s `display` overrode `hidden` and the desktop nav overflowed the viewport
at 390px. The component classes now live in `@layer components`. This also silently repaired
elsewhere (e.g. `bg-brand-rose` on the AI Triage button and `px-0` on the sandbox send button).

Verified in a real browser at 1440x900 and 390x844: login, Overview, Alerts, Knowledge,
Sandbox and Post-Mortems all render, the hamburger menu works, and no page overflows
horizontally (`scrollWidth == clientWidth`).

### Scope completed in this session

- **§3.2 dead scaffold deleted** — `app/llm/`, `app/rag/`, `app/routers/`, `app/simulator/` and
  `app/db/models.py` are gone, and the matching `exclude` list was removed from
  `[tool.ruff]`. `app/config.py` is now an orphan (nothing imports it) but was **not** deleted
  because it is not on the §3.2 list — see "Still open" below.
- **§3.3 integration tests** — `backend/tests/conftest.py` creates and migrates an isolated
  `cloudops_ai_test` database on the host in `DATABASE_URL` (override with `TEST_DATABASE_URL`),
  truncates between tests, seeds the admin, and swaps in an offline `FakeLLM`. 78 tests cover
  auth, the full authz sweep over every data route, alerts, pagination, triage, post-mortems,
  RAG indexing/query, SSE streaming and rate limiting.
- **§3.4 CI** — `.github/workflows/ci.yml`: backend lint + tests against a
  `pgvector/pgvector:pg16` service container, frontend typecheck + `npm audit --omit=dev` +
  build, then Docker image builds for both services.
- **§3.5 rate limiting** — `app/services/ratelimit.py` provides a per-user, per-scope sliding
  window exposed as a dependency. Applied to `sandbox/chat`, `sandbox/stream`,
  `knowledge/query`, `incidents/triage`, `incidents/post-mortem` (`LLM_RATE_LIMIT`) and runbook
  indexing (`EMBEDDING_RATE_LIMIT`). Over-budget requests get `429` plus `Retry-After`. Limits are
  read from settings per request, so they are tunable without a redeploy.
- **§3.6 pagination** — `GET /alerts`, `GET /incidents` and `GET /knowledge/runbooks` now return
  `{items, total, limit, offset, has_more}` with stable `created_at, id` ordering. **This is a
  breaking change to the response shape**; all four frontend consumers were updated and use
  `useSWRInfinite` via `lib/paged.ts` with a shared `components/LoadMore.tsx` control.
- **§3.7 observability** — `app/core/observability.py` installs a JSON formatter and an ASGI
  middleware that tags each request with a request id, echoes it as `X-Request-ID`, and logs one
  `http_request` line with status, duration and user id. Redundant gunicorn/uvicorn access logs
  are suppressed, and health probes are demoted to DEBUG. `GET /health/llm` probes the provider
  through a new `LLMProvider.health()` method (Gemini uses a no-token model metadata read,
  Ollama uses `/api/tags`), so the capability stays provider-swappable.

### Still open (deliberately not done)

- `backend/app/config.py` is dead (a leftover shim re-exporting `app.core.config`). Left in
  place pending an explicit decision, since it is not on the §3.2 list.
- Multi-operator account management (§3.7) — still one seeded admin, no signup.
- The rate limiter is per-process, so with the 2 Gunicorn workers the effective ceiling is
  `2 x limit`. A shared store is the production path.
- No frontend component/E2E tests; the UI was verified by hand in a browser.
- The `npm audit --omit=dev` → "0 vulnerabilities" claim in §2 is stale: advisories published
  since this handoff flagged `source-map-js` (high, fixed by a non-breaking bump to 1.2.2) and
  `postcss-selector-parser` nested under `@tailwindcss/typography` (moderate, 2 findings). The
  latter's only available "fix" is `npm audit fix --force`, which downgrades
  `@tailwindcss/typography` to 0.5.4. That downgrade is deliberately not taken, so CI gates on
  `--audit-level=high` instead. Revisit when the plugin ships a patched release.
- Transient upstream failures from `gemini-3.8-flash` (`503 UNAVAILABLE`) were observed under
  load (roughly one in three calls) and used to be surfaced verbatim as a `502`. Bounded retries
  were added in the next session — see §8.

## 8. Session update — provider retry/backoff

The provider layer now survives the transient upstreams that were visible in §7. Nothing about
it is Gemini-specific: the contract grew in `LLMProvider`'s module, and any provider that
classifies its own errors inherits the behaviour.

- **`LLMTransientError`** (`app/services/llm/base.py`) marks a failure a later attempt can
  recover from. It carries `replayable`: a dropped socket or an overloaded 5xx clears in
  milliseconds and is replayed here, while a quota window (`429 RESOURCE_EXHAUSTED`) outlives
  the request, so replaying it would only burn more quota and it is handed to the caller
  instead. Plain `LLMError` still means permanent (bad model, auth, malformed request).
- **Retry layer** (`app/services/llm/retry.py`) — `RetryPolicy` is exponential backoff with
  **full jitter** (`random.uniform(0, base * 2**attempt)`, capped at `LLM_RETRY_MAX_DELAY`) and
  `RetryingProvider` wraps any provider: `chat` and `embed` are replayed, `stream` is replayed
  only before the first token (a mid-stream failure cannot be replayed without duplicating
  text), and `health` is never retried because a probe must answer promptly.
  `LLM_MAX_RETRIES` counts attempts *after* the first, so the default 2 means at most 3 upstream
  calls per request; `0` disables the wrapper entirely. `get_llm()` returns the wrapped provider,
  so every route gets it without changes.
- **HTTP contract** — exhausted and retryable → `503` with `Retry-After: LLM_RETRY_MAX_DELAY`;
  a quota `429` → `503` with `Retry-After: RATE_LIMIT_WINDOW_SECONDS`; a permanent failure stays
  `502`. Both handlers hang off the existing `LLMError` handler, and Starlette resolves the more
  specific class first.
- **Tests** — 21 new tests in `backend/tests/test_llm_retry.py` (99 collected at the time, up from
  78; an intermediate run showed 95 after the first 17 were added, and §9 takes the suite to 113):
  backoff
  growth/jitter/caps, settings mapping, replay vs. no-replay, stream-after-token safety, health
  passthrough, HTTP classification, and the real `503`/`429`/`400` Gemini payload shapes.
- **Verified against the live provider.** `429 RESOURCE_EXHAUSTED` → `503 Retry-After: 60` with
  **no** `llm_retry` line (no quota burn); `503 UNAVAILABLE` → jittered `llm_retry` lines
  (`delay_s` 0.03 / 0.271 / 0.382) and `503 Retry-After: 8` once they were spent; ordinary
  requests stayed `200`.

Known gap this exposed: a single upstream call can take 18–70 s under load (one `http_request`
line measured `duration_ms: 70218`), which holds a worker and a rate-limit slot for that whole
time. §9 closes that gap with a deadline per attempt.

## 9. Session update — per-attempt provider timeouts

§8 bounded how many times a call is replayed; it did not bound how long a single attempt may
hang. A slow provider could still hold a Gunicorn worker for over a minute. Each attempt now
has a deadline, enforced in the same provider-agnostic place as the retries.

- **Settings** (`app/core/config.py`, documented in `backend/.env.example`) —
  `LLM_REQUEST_TIMEOUT=30` bounds one whole non-streaming attempt, `LLM_STREAM_IDLE_TIMEOUT=60`
  bounds the silence *between* streamed chunks, and `LLM_CONNECT_TIMEOUT=5` bounds connection
  setup. `0` disables a bound, and the two wrapper bounds map to `None` rather than an
  instantly-expiring timeout.
- **Enforcement** — `AttemptTimeouts` + `RetryingProvider` in `app/services/llm/retry.py` wrap the
  call with `asyncio.timeout`, so the deadline is per attempt for both providers and all four
  methods (`chat`, `embed`, a stream's idle gap, and the bounded-but-never-retried `health`).
  A deadline that fires raises `LLMTransientError(replayable=True)`, which means it inherits the
  existing bounded policy and the existing `503` + `Retry-After` contract; nothing about the
  route layer changed.
- **Streams** — the deadline applies to each `__anext__()`, not to the whole response, so a slow
  but actively producing generation is never cut off. It is replayed only while no token has
  been emitted; once output has been delivered the failure is surfaced instead of duplicated.
  `contextlib.aclosing` closes the upstream generator when a consumer stops early (an SSE client
  disconnecting), which previously waited for garbage collection.
- **Provider clients** — neither SDK is the retry authority any more. `google-genai`'s
  `retry_options` is deliberately left unset (verified: its default is no retries), so our
  bounded policy is the only source of extra requests — the constraint that no retry may create
  a duplicate paid request. Its client-level `timeout` uses the *laxer* of the two wrapper bounds
  (`socket_timeout_ms`) purely so a dead socket does not linger. Ollama's hardcoded
  `timeout=10/60/None` literals are now the same settings.
- **Tests** — 14 for the deadlines plus 5 for the retry hint in
  `backend/tests/test_llm_retry.py`, **118 collected in total** (40 in that file, and
  78 + 21 + 14 + 5 = 118): settings mapping
  and the disable path, the socket-scalar rule, both clients' timeouts, slow-attempt replay,
  exhaustion, disabled deadlines, the bounded health probe, a stall before the first token
  (replayed) versus after output (never replayed, exactly one upstream call), a chatty stream
  surviving a short idle deadline, early-exit closure, delay parsing in every shape observed
  (`"60"`, `"56023.632260421s"`, an HTTP header, garbage, a `bool`), and three route-level checks
  over real HTTP. No test calls a paid API; the wrapper-level ones use instant backoff so nothing
  sleeps for real.
- **Provider-supplied waits are now honoured** — `Retry-After` was a local guess, which is wrong
  whenever the real window is not ours to guess. `LLMTransientError.retry_after` carries whatever
  the provider asked for, parsed from `RetryInfo.retryDelay` (or an httpx `Retry-After` header),
  and `main.py` prefers it over the configured fallback. Verified live: the header went from a
  hardcoded `60` to `55316`, counting down correctly from `56023` captured ~12 minutes earlier.
- **A bug the tests caught** — `google-genai`'s `APIError` accepts the body as the keyword
  `response_json` but exposes it as `details`, so the first version of the hint lookup silently
  returned `None` on the real path. `error_body()` in `app/services/llm/gemini.py` checks both
  names, and `test_the_error_body_is_found_whichever_attribute_the_sdk_uses` pins it, so an SDK
  rename surfaces as a failure instead of a silently missing header.
- **Operational finding worth acting on** — the `429`s seen in §7/§8 are not a per-minute limit
  gone wrong: the free tier caps `gemini-3.8-flash` at **20 requests per day**
  (`GenerateRequestsPerDayPerProjectPerModel-FreeTier`), and the day's budget can be spent in a
  single minute, because `LLM_RATE_LIMIT` defaults to 20 per **minute** per user. Options: lower
  `LLM_RATE_LIMIT`, point `GEMINI_MODEL` at a model with a larger quota, or enable billing. Until
  then the demo will return `503` with an honest multi-hour `Retry-After` once the budget is gone.
- **`.gitignore` audit** — `git check-ignore` confirmed `backend/.env` and `frontend/.env.local`
  were already covered, but five generated or local paths were not: `*.egg-info/`,
  `.pytest_cache/`, `.ruff_cache/`, `*.tsbuildinfo` and `.freebuff/`. All five exist on disk
  today, so the first `git add -A` would have committed them. Added to `.gitignore`, then
  re-checked that no source file is caught by the new rules (`.env.example`, `compose.yaml`,
  `backend/app/config.py`, `backend/pyproject.toml` and the CI workflow all stay committable).
- **Unchanged on purpose** — `backend/app/config.py` is still there, per an explicit instruction
  not to delete it, and no Git state was touched.

## 10. Session update — provider budget (the ceiling §9 asked for)

§9 ended on a finding rather than a fix: `LLM_RATE_LIMIT` is per **user** and per **minute**, while
the quota it needs to protect is per **project** and per **day**. Twenty requests from one user
inside a minute spent the whole day's allowance, and every later caller got `503` with a
`Retry-After` counted in hours. That ceiling now exists.

- **The unit of account is the upstream attempt, not the HTTP request.** This is the part that
  matters. One request can fan out into several provider calls — `add_runbook` embeds once per
  900-character chunk, so a long runbook is a dozen calls — and every replayed attempt is another
  request as far as the provider is concerned. Counting requests would have under-reported both.
  So the budget is charged inside the retry wrapper, per attempt.
- **`QuotaGuardedProvider` sits *inside* `RetryingProvider`** (see `build_provider` in
  `app/services/llm/retry.py`). Wrapping the other way round would charge once per logical call and
  let replays slip past the ceiling; `test_the_budget_is_charged_inside_the_retry_wrapper` pins the
  ordering, and `test_replayed_attempts_are_charged_too` pins the consequence.
- **Refusals reuse the existing contract instead of inventing a second one.** A spent budget raises
  `LLMTransientError(replayable=False, retry_after=…)` — exactly what an upstream `429` becomes in
  `gemini.py` — so callers still get `503` + `Retry-After`, with one difference worth having: ours
  fires *before* the request is sent, so the wait it advertises is the wait that is really left
  rather than the provider's after-the-fact hint. No new exception type, no new handler, and one
  documented meaning for "no allowance left" whichever side reported it.
- **Provider-aware default** (`app/core/config.py`) — `LLM_QUOTA_LIMIT` unset means 20 for Gemini,
  matching the free tier's 20 `generateContent` requests/day/project/model, and **unlimited for
  Ollama**, which has no quota to protect and should not be capped at 20 local calls a day by a
  default aimed at someone else's billing. An explicit value always wins, including `0` to disable.
  Window is a day via `LLM_QUOTA_WINDOW_SECONDS`.
- **One shared budget for chat, streams and embeddings.** Deliberately conservative: Gemini meters
  those methods separately, and refusing a call we might have got away with is much cheaper than
  discovering the ceiling mid-demo. Splitting it later is a key change in `quota.py`, not a refactor.
- **`GET /health/llm` reports the allowance** as `quota: {limit, used, remaining, window_seconds,
  resets_in}`, and is omitted when the budget is disabled. The reachability probe itself is exempt
  from the budget — a monitoring loop polls that endpoint far more often than 20 times a day and
  must not be able to spend the allowance users need.
- **A crash the probe caught.** Writing the stream test surfaced that `LLMTransientError` raised
  while an SSE response is being iterated cannot become a `503`: the `200` and the SSE headers are
  already on the wire, Starlette refuses to rewrite a started response and raises
  `RuntimeError: Caught handled exception, but response already started` instead, aborting the
  connection with a traceback. This was pre-existing, but a spent budget triggers it exactly when
  someone opens the sandbox stream, so `/sandbox/stream` now wraps its generator and emits a
  terminal `error` event followed by `done`
  (`test_a_stream_refused_mid_response_ends_with_an_error_event`, plus
  `test_a_stream_that_breaks_after_output_ends_with_an_error_event` for the general upstream case,
  which also proves the partial token is delivered exactly once). The frontend has no SSE consumer
  yet, so nothing downstream had to change.
- **`SlidingWindowLimiter` moved to `app/core/window.py`** (still re-exported from
  `app/services/ratelimit.py`). The move was not cosmetic: the provider budget lives in the LLM
  layer, and reaching back into a module that imports the API auth dependencies would have inverted
  the layering the swappable-provider constraint depends on. It also gained a read-only `state()`
  for the health snapshot, and a documented rule that **one instance serves one window length** —
  `check()` prunes every key against the caller's window, so a single instance holding both a 60s
  limit and a day-long budget would silently erase the daily counter. The budget therefore has its
  own instance.
- **Tests** — `backend/tests/test_llm_quota.py` adds **16**, taking the suite from 118 to **136
  passing**, ruff clean. Coverage: the provider-aware default and its overrides, window arithmetic
  against a patched clock (no real waiting), the snapshot, the disabled path, the local refusal
  (asserting the refused call never reached the provider and that the message names the knob that
  fixes it), health exemption, an unconsumed stream claiming nothing, per-attempt charging under
  replay, a spent budget stopping a retry mid-call, a replayed stream claiming two units, the
  `build_provider` wiring, and four route-level checks over real HTTP — including that a runbook
  long enough to need many embeds is refused part-way *and leaves nothing behind*.
- **Two existing tests had to change, and both changes are contract updates rather than
  relaxations** — `test_build_provider_wraps_unless_every_layer_is_switched_off` (a plain provider
  now only goes out unwrapped when the budget is off too) and
  `test_llm_health_is_public_and_reports_provider`, which asserts the `/health/llm` payload exactly
  and therefore needed the new `quota` block spelled out. `conftest.py` also resets the budget
  alongside the per-user limiter: without it, the 40 tests in `test_llm_retry.py` would exhaust a
  20/day budget mid-session and start failing for the wrong reason.
- **What is still true** — the budget limits *provider spend*, not request rate: `LLM_RATE_LIMIT`
  still allows 20 requests per minute per user, and one user can still consume the whole shared
  allowance quickly (they just cannot exceed it now, and the failure is a `503` that names the
  setting instead of a 15-hour provider outage). The counter is in-process, like the rate limiter
  (`workers x limit`), so it needs a shared store before running multiple workers. Natural next
  steps, none of them started: per-method budgets once the real per-method quotas are verified
  rather than assumed, a shared budget in Redis/Postgres, and surfacing `quota.remaining` in the
  dashboard so an operator sees the ceiling coming.

## 11. Session update — the repo, and shared counters (Redis)

Two things this session: the project finally became a Git repository of its own, and the
ceilings §10 added stopped being per-worker.

**A repository at last.** The project had no history of its own — it sat untracked inside the
home-directory repo at `C:/Users/navee`, which has zero commits and thousands of unrelated
staged paths. `git init -b main` here, then one commit of 96 files (10,164 insertions). The
parent repo was left untouched and that was *verified*, not assumed: `md5sum` of
`C:/Users/navee/.git/index` (`b547609e…`) and `.git/HEAD` (`4cf2d64e…`) is identical before and
after, and it still reports no commits. Nothing was pushed; there is no remote. The nine
sensitive-or-generated paths stay excluded (`backend/.env`, `frontend/.env.local`, `.venv`,
`node_modules`, caches), so the first commit contains only the three `.env.example` templates.
Note for whoever runs `git add` in the home directory later: git will now warn about a nested
repository here, and the fix is to leave it alone rather than add it as a gitlink.

**Counters moved behind a store** (`app/core/counter_store.py`). Both ceilings — the per-user
rate limit in `app/services/ratelimit.py` and the provider budget in
`app/services/llm/quota.py` — now go through one interface with two backends:

- `MemoryCounterStore` keeps the old behaviour exactly (per window length, one
  `SlidingWindowLimiter` each) and remains the default when `REDIS_URL` is empty. Single worker,
  local runs and the default test suite are unaffected.
- `RedisCounterStore` puts the window in Redis: a Lua script prunes a sorted set, counts it and
  claims a slot in one atomic step, using the *server's* `TIME` so worker clock skew cannot
  stretch or shorten a window. Atomicity is the whole point — two workers reading "19 used" and
  both writing 20 is how a shared 20/day budget quietly becomes 40/day.
- **An unreachable Redis degrades instead of failing open.** The store falls back to its own
  process-local counters and logs one throttled warning a minute, so the ceiling narrows to the
  worker that noticed rather than disappearing. `test_an_unreachable_redis_degrades_to_this_worker`
  pins it: still limited, just locally.
- `async` punctuation: `reserve()`, `snapshot()` and the limiter dependency are now awaited.
  `QuotaGuardedProvider._claim` became async with them; no behaviour changed.
- **Health visibility** — `GET /health` gained `counters` (`ok`, `unconfigured` with no Redis, or
  the reason it is unreachable) and the `/health/llm` quota block gained `store`, which is the
  field that tells you whether those numbers mean anything deployment-wide. The Redis probe runs
  on every `/health` call, so an orchestrator healthcheck also notices a lost Redis.
- **Enforced at the interface, not just in unit tests.** Docker runs with
  `REDIS_URL=redis://redis:6379/0`. A *separate process* inside the container claimed three calls,
  and the API server — a different process — then reported `used: 3, remaining: 17, store: redis`,
  with the sorted set visible in `redis-cli`. That is the multi-worker claim, demonstrated on the
  real stack rather than argued.
- **Tests** — `backend/tests/test_counter_store.py` adds 10, taking the suite to **146 passing**
  with ruff clean. They cover the in-memory path (including the window-per-instance rule that
  would otherwise erase the day-long budget), the limitation Redis exists to remove (two memory
  stores cannot share a ceiling — pinned as a deliberate contrast so the contrast cannot rot), two
  workers sharing one ceiling, **exactly 5 of 30 concurrent claims granted across three stores**,
  window expiry on the server clock, the degraded path, a route drawing on a budget spent by
  another worker, and both health fields including the unreachable case. They skip with a reason
  when no Redis answers rather than reporting an unearned pass; CI runs a `redis:7-alpine` service
  so they run there, and `docker compose up -d redis` provides one locally.
- **Compose and CI** — a `redis` service (no persistence: these are windows, not data) with a
  healthcheck the backend waits on, `REDIS_URL` on the backend, and `REDIS_PORT`/`REDIS_*`
  documented in `.env.example`. `redis>=6,<7` was added to `backend/pyproject.toml` — it was not a
  dependency before, so the venv, the image and CI all needed it.

## 12. Session update — the Sandbox streams, and the bug that only a browser could show

The Sandbox page used the blocking `POST /sandbox/chat`. It now streams from
`POST /sandbox/stream`, which already existed and had no consumer.

- **The client** (`frontend/lib/stream.ts`) reads the response body as bytes and splits it into
  SSE frames. `EventSource` cannot be used: it only issues GET requests and this endpoint takes a
  JSON body. Handled explicitly: `CRLF` frame endings, `:` keep-alive comments, multi-line `data`
  fields, a multi-byte character split across two network chunks, and the distinction between a
  refusal that arrives with a status code (rate limit before the first byte) and one that arrives
  as an `error` event (after it). The `done` frame is tracked, so a stream that ends without one is
  reported instead of being shown as a finished answer.
- **The page** (`frontend/app/sandbox/page.tsx`) appends tokens as they arrive, shows a caret while
  the answer is live, and offers **Stop** in place of Send while streaming. Stopping aborts the
  fetch: the partial answer is kept, and the two cases are distinguished — "Stopped before any
  output." when nothing had arrived yet, and the partial text plus a note when it had. Quota
  refusals and provider failures render as an amber note naming the setting to change, rather than
  as a generic error or a silent truncation.
- **A real bug, visible only in a browser.** The stream worked in `next dev` and in every curl
  probe, and arrived in one lump in the production build. The Next.js server gzips responses by
  default, and a gzip encoder buffers until it has a block to emit, so all tokens were held until
  the generation finished. Browsers always send `Accept-Encoding: gzip`; curl does not — which is
  why the probes kept passing. The response now sets `Cache-Control: no-store, no-transform` (the
  spec's instruction to leave the body alone, and what Next's compression honours), verified at
  the header level and again in the browser. `test_stream_forbids_transforming_proxies` pins it.
  Worth remembering: this class of bug cannot be caught by testing the API, only by testing the
  thing the user actually looks at.
- **Verified in the browser, through the production Next server** (`npm start`, not just dev), with
  a local stub provider standing in for a working model — the Gemini free tier is still exhausted,
  and no test may spend real quota. Confirmed: partial text on screen while the answer was still
  being generated; both stop branches; the `done` path; and the refusal note once the shared budget
  was spent. The stub lives outside the repository, in the OS temp directory, and is not part of
  the project.
- **Tests** — one added for the header, taking the suite to **147 passing** with ruff clean.
- **Two things this changed for callers** — `frontend/lib/api.ts` grew `toApiError()` so the stream
  client and the JSON client explain failures the same way (the API's `detail` beats "503 Service
  Unavailable"), and the Sandbox no longer calls `/sandbox/chat`. That endpoint stays: it is the
  documented non-streaming interface and it is still covered by its tests.

## 13. Session update — the provider's words stop at the server

The Sandbox still showed a raw Gemini 429 in its error note: about a kilobyte of JSON naming the
quota metric, the quota id, the model, the project and a docs URL. Useful to an operator, noise in
a chat bubble, and project metadata to anyone else. The provider's own text now stops at the
server, and what reaches the browser is a sentence we wrote.

- **One projection point** (`backend/app/services/llm/errors.py`). `public_llm_error()` logs the
  failure in full and returns the only part that may travel: a code, our own sentence, the provider
  *name* (from our configuration, never from the payload), an optional wait and a request id.
  Splitting log-and-project into two calls would let one of them be forgotten.
- **A closed vocabulary** — `LLMErrorKind`: `quota_exhausted`, `budget_exhausted`, `rate_limited`,
  `timeout`, `unavailable`, `provider_error`. `LLMError`/`LLMTransientError` now carry a `kind`
  alongside the internal (log-only) message, and `gemini.py`/`retry.py` classify 429s with it.
- **A quota is told from a rate limit by the wait, not the payload.** `kind_for_wait()` splits them
  at one hour: per-minute limits ask for ~60s, the free-tier daily cap asked for ~56000s. Reading
  the quota metadata instead would mean parsing exactly the material that must not travel.
- **The sentence** is composed from our own configuration and the wait, and states the wait only
  when the provider gave one — a countdown that was guessed would be worse than none. On the
  Sandbox it reads: *"Gemini's daily quota has been reached. Sandbox responses will be available
  again in approximately 15 hours 33 minutes. Switch to the local Ollama provider to keep working
  in the meantime."* The switch is only offered when Ollama is actually an alternative, and the
  local budget names itself rather than quoting `LLM_QUOTA_LIMIT`.
- **Wire shape** — SSE `error` frames carry `data: {"code", "message", "provider",
  "retry_after"?, "request_id"?}` (serialised with `json.dumps`, because `sse_starlette`'s encoder
  calls `str()` on a dict and would emit an unparseable Python repr); HTTP 503/502 bodies carry the
  sentence as `detail` and the same object as `error`. `retry_after` is absent when unknown, so a
  client can tell "wait this long" from "no idea". 503 keeps its `Retry-After` header, and the
  `request_id` in the payload is the same one already echoed as `x-request-id`.
- **Frontend** — `parsePublicError()` accepts only that exact field set, so a provider payload that
  happens to be valid JSON is still discarded; anything else is replaced with a sentence of ours.
  The page renders the server's sentence verbatim (one source of truth for the countdown), keeps
  the error code as a `data-error-code` attribute, and offers the request id as a small **Copy
  diagnostic ID** action instead of printing it. A body that is not JSON is no longer echoed at
  all — the status line stands in for it.
- **A bug the new tests found** — `alembic/env.py` called `fileConfig()` with the default
  `disable_existing_loggers=True`, which switches off every logger that already exists in the
  process. Harmless for a standalone `alembic upgrade`, but the test harness migrates in-process, so
  `cloudops.*` was silenced for the rest of the run and the "the raw detail is in the logs"
  assertion saw zero records. Fixed at the source (`disable_existing_loggers=False`).
- **Tests** — `backend/tests/test_llm_errors.py` (27) drives a verbatim copy of the live 429 through
  the projection and the HTTP surface, asserting on leak markers (`generativelanguage.googleapis.com`,
  `quotaMetric`, `ai.google.dev`, `@type`, `RetryInfo`, …) in both directions: nothing in the
  response, the full text in the structured log line. Backend suite: **174 passing**, ruff clean.
  Frontend moved to Vitest + React Testing Library (`frontend/lib/stream.test.ts`,
  `frontend/app/sandbox/page.test.tsx`, **18 passing**): SSE framing (CRLF, comments, multi-line
  data, a multi-byte character split across chunks), progressive rendering, both Stop branches, the
  friendly quota note with and without a wait, the copy action, and a raw payload pushed through the
  real parser and page asserting none of it appears on screen. `npm test` joined typecheck and build
  in CI.
- **Verified in a browser** against a stub provider outside the repository: the note rendered with
  the countdown, the copy action reported "Copied", `data-error-code="quota_exhausted"` was set,
  and no leak marker was anywhere in the DOM. The live Gemini free tier is still capped, so the
  real quota path could not be exercised end to end; the raw payload used in the tests is a verbatim
  capture from the 429 that prompted this. The CI audit step is currently red on pre-existing
  advisories in `next@16.3.6` and `sharp@0.35.4` (both unchanged by this work; `npm audit fix`
  covers them) and should be handled in its own commit.

## 14. Session update — roles, an incident's history, and real webhooks

Three things that took the app from "one admin and a status column" to something a team runs with.
They landed before the rebrand below and were not written up at the time; this section and §15
close that gap.

- **Roles are enforced per route** (`app/core/rbac.py`, `app/api/deps.py::require_role`).
  `Role` is `admin` / `operator` / `viewer`, and privilege is expressed as a *rank* rather than a
  permission matrix, so "may this caller do X" stays one comparison and a route cannot end up with
  a list that drifts out of step with the others. `rank()` fails **closed** (an unrecognised stored
  value ranks below Viewer and grants nothing), because the alternative turns a typo into an
  administrator. `ROLE_VALUES` also generates the migration's check constraint, so the database and
  the module cannot disagree about what a valid role is. The role is read from the user row on
  every request, so a demotion takes effect on the next call rather than at the next login — which
  is what revoking access during an incident has to mean. Hiding a button is a courtesy; a Viewer
  calling a write route directly gets `403`.
- **An incident has an append-only history** (`app/models/incident_event.py`,
  `app/services/timeline.py::incident_event`, `app/core/incidents.py::IncidentEventKind`). Nothing
  updates or deletes an entry, `actor` is the email of whoever acted so the record survives a
  rename, and `from_value`/`to_value` hold the diff next to the sentence in `body` rather than
  making a reader parse it. The helper deliberately does **not** commit: the caller is already in
  the transaction that changes the incident, and an event in its own commit could outlive a change
  that rolled back. Two behaviours are pinned by tests: re-saving the same status writes nothing
  (a log people read should not fill up with no-ops), and `PATCH` treats an absent `assignee` as
  "leave it" while `null` means "unassign" — the two have to be distinguishable or changing a
  status would unassign somebody. `GET /incidents/{id}/activity` is the one list that reads
  oldest-first, because a timeline is read the way it happened.
- **Real Alertmanager ingestion** (`app/api/routes/webhooks.py`,
  `app/services/alertmanager.py`, `app/schemas/webhooks.py`). The caller is Prometheus, which has no
  cookie and no role, so `X-API-Key` compared with `compare_digest` is its whole identity —
  constant time, because a check that returns early on the first wrong byte is a token oracle. The
  check is a FastAPI **dependency**, which matters: body validation runs before the endpoint, so an
  unauthenticated caller would otherwise get a schema error back, telling someone with a wrong key
  the shape of a private API. An **unset** token answers `503` ("not configured") rather than
  accepting anything, so a deployment that never set it up is never an open write route; a missing
  or wrong key answers `401`. Each entry of `alerts[]` becomes one alert row — the **per-alert**
  status is read, not the group's, because a group is `firing` as a whole even when one of its
  alerts has just recovered. The payload models are lenient on purpose (`extra="ignore"`):
  Alertmanager owns that shape and we are not in a position to make it validate anything. The
  response is a receipt — `{accepted, opened, resolved, alert_ids}` — so its logs and ours line up.
  Deduplication is deliberately absent and covered in the README's "Known limitations".
- **Migrations** — `202610080001_rbac_roles.py` (adds `users.role`, backfilled to `admin` for the
  seeded account, plus the check constraint) and `202610080002_incident_timeline.py` (creates
  `incident_events`, backfilling one `created` entry per existing incident so old incidents are not
  blank).
- **Tests** — `backend/tests/test_rbac.py` (a full authz sweep: every data route is called by
  every role and the expected allow/deny is asserted, so a new route added without a role
  dependency fails the sweep), `test_api_incidents.py` (the timeline routes and the no-op rule) and
  `test_webhooks.py` (token missing/wrong/unset, the mapping table, resolved-vs-firing, an empty
  group, and a repeated delivery creating the alerts again — the known limitation, pinned rather
  than hidden).

## 15. Session update — the name, the front door, and the post-mortem workflow

Three commits, in order: the product got its real name, the sign-in page became the front door, and
the post-mortem stopped being one blob and became something a team signs off.

**The name.** The project is **Excubitor** (still "CloudOps AI" as the subtitle). What was
renamed is presentation only, and that was a constraint rather than an accident: `APP_NAME` — the
*value*, not the variable — the triage system prompt, the config default and the package
description. No environment variable *name*, no database name or DSN, no image name and no Python
package name moved, so nothing a deployment stores or addresses changed and no migration was
needed. The mark is hand-written SVG — a watchfire inside a shield, one path with two subpaths
filled `evenodd` so the flame is a real hole and the whole thing takes a single `currentColor`,
which is what lets the same drawing sit on the dark nav tile, on the brand panel and in the
favicon. `components/Logo.tsx` exports the path data and `app/icon.svg` +
`public/apple-touch-icon.svg` are built from that one constant, so the tab icon cannot drift from
the nav; the geometry is intentionally coarse because it has to survive 16px.

**The sign-in page** (`app/login/page.tsx`, `components/BrandPanel.tsx`) is now a two-panel
layout — the mark and tagline on one side, the form on the other, collapsing to a header plus form
below `lg`. Three decisions worth keeping: the fields are **empty on purpose** (the old page
pre-filled the seeded admin's credentials, which turns a sign-in page into a button anyone who can
reach it may press — the developer hint is now a muted footnote naming `ADMIN_EMAIL` /
`ADMIN_PASSWORD` instead), the button is a real `type="submit"` so Enter in either field signs in,
and a failure renders as an inline `role="alert"` sentence from the API rather than a bare status
code. Nothing about the exchange changed: same `POST /auth/login`, same httpOnly cookie, same
`next` destination.

**The post-mortem workflow.** Generation still lives on the incident, because it is an incident
action and it still resolves the incident; everything afterwards is its own collection. The
migration `202610090001_post_mortem_workflow.py` adds the reviewed sections
(`timeline_summary`, `root_cause`, `contributing_factors`, `impact`), a `status`
(`draft` → `in_review` → `published`), `author`/`author_id`, `published_at`, `updated_at`, and a
`post_mortem_actions` table with owner, due date, status and `position`.

- **Existing rows were backfilled to `published`, not `draft`.** They were created by an operator
  through the API and have been readable by every role since, so defaulting them to draft would
  have hidden documents people can already see. New rows still start as `draft`. The narrative in
  `content` was left untouched: the structured sections sit *beside* it, and nothing is parsed out
  of prose.
- **Visibility is one predicate in one place** (`app/services/postmortems.py::visible_to`), because
  two routers ask the question — the collection and the incident-scoped generator — and spreading
  it is how one of them ends up leaking a draft. Operators and Admins see every draft, since a
  reviewer who can only see their own drafts is not a reviewer; a Viewer sees `published` only and
  gets a `404` (not `403`) for a draft by id.
- **The export is a pure function** (`render_markdown`) of the document, which is why the exact
  bytes a download produces can be pinned. Empty sections render as `_Not recorded._` rather than
  being omitted, and overdue dates are marked at render time.
- **Action items replace wholesale on a `PATCH`**, row by row: an item returned with its `id` is
  the same row, an item without one is created, and anything missing from the list is removed —
  a form that edits a set of rows has no other way to say "this one is gone". Array order becomes
  `position`. `owner` is an email resolved to a user when one matches and kept as written when none
  does; owners and due dates suggested by the model are dropped, because a fabricated deadline on
  somebody's list is worse than an empty one. `GET /overview` gained the strip that makes them
  actable: `{open, overdue, items[]}`, earliest due date first with undated items last.
- **Tests** — `backend/tests/test_post_mortem_workflow.py` (the pipeline and its timeline entries,
  the replace semantics, the export bytes, the strip's ordering and counts, and the role
  visibility rules), plus new route cases in `test_api_auth.py`'s `PROTECTED_ROUTES` and
  `test_rbac.py`'s `READS`, so the new collection is covered by the authz sweep rather than beside
  it. Frontend cases cover the workspace and the Overview strip in `app/post-mortems/page.test.tsx`
  and `app/page.test.tsx`.
- **Verified in a browser**, end to end on the production build: generate → edit the sections →
  move to `in_review` → publish (the timeline records each transition) → download the markdown.
- **One thing that is still a proxy question, not an app question** — a slow generation can spend
  longer than a proxy's default read timeout, and the retry budget makes that worse: see §16,
  which is the next session.
