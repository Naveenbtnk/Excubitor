# CloudOps AI — Handoff Brief for Continued Development (DeepSeek)

> Read this entire file before writing any code. It tells you what this project is,
> exactly what has already been built and verified, what is intentionally left for you,
> and the constraints you must respect. Do not re-architect what already works — extend it.

## 1. What this project is

**CloudOps AI** is an AIOps (Cloud Operations) dashboard, modeled on tools like PagerDuty /
Datadog incident-response consoles. It simulates cloud infrastructure alerts, uses an LLM to
triage them (root cause + remediation), retrieves relevant runbooks via vector search (RAG),
and generates AI-written post-mortems. It is a two-service app:

- **`backend/`** — Python 3.12, FastAPI, async SQLAlchemy 2.1, PostgreSQL + pgvector, Alembic
  migrations, JWT cookie auth, Google Gemini via `google-genai`.
- **`frontend/`** — Next.js 16 (App Router, Turbopack), React 19, Tailwind CSS 4, SWR for data
  fetching, `react-markdown` for rendering AI output.
- **`compose.yaml`** at the repo root runs the whole stack (PostgreSQL/pgvector + backend +
  frontend) with Docker.

The product has five user-facing screens: **Overview**, **Alerts**, **Sandbox** (free-form chat
with the LLM), **Knowledge** (runbook upload + RAG query), **Post-Mortems**, plus a **Login**
page gating all of them.

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
- **Routes implemented** (all except `/health` require the session cookie):
  `GET /health`, `POST /auth/login`, `POST /auth/logout`, `GET /auth/me`, `GET /overview`,
  `GET /alerts`, `POST /alerts`, `POST /alerts/simulate`, `GET /alerts/scenarios`,
  `POST /alerts/{id}/ack`, `GET /knowledge/runbooks`, `POST /knowledge/runbooks`,
  `POST /knowledge/runbooks/upload`, `POST /knowledge/query`, `POST /sandbox/chat`,
  `POST /sandbox/stream` (SSE), `GET /incidents`, `POST /incidents/triage/{alert_id}`,
  `GET /incidents/{id}/post-mortem`, `POST /incidents/{id}/post-mortem`.
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
