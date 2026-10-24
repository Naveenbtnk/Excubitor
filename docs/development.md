# Development

Running Excubitor locally, proving it works, and what CI does on every push.

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
# Windows PowerShell: .venv\Scripts\Activate.ps1
source .venv/bin/activate
ruff check app tests
pytest -q

cd ../frontend
npm run typecheck
npm test
npm run build
npm audit --omit=dev --audit-level=high
```

Bare `pytest -q` is the canonical form, not `python -m pytest`: the `-m` form puts the working
directory on `sys.path`, which can hide an import that fails once the package is installed the way
CI installs it.

The backend suite is a mix of unit tests and integration tests. The integration tests need a
reachable PostgreSQL/pgvector server: they create and migrate an isolated `cloudops_ai_test`
database on the same host as `DATABASE_URL` and never touch the development database. Override
the target with `TEST_DATABASE_URL` when PostgreSQL is not on the default port:

```bash
cd backend
docker compose -f ../compose.yaml up -d db
# PowerShell
$env:TEST_DATABASE_URL = "postgresql+asyncpg://cloudops:cloudops@localhost:5434/cloudops_ai_test"
pytest -q
```

The suite replaces the LLM provider with a deterministic offline fake, so tests never call a
paid API.

The tests that prove the rate limit and the provider budget hold *across workers* need a real
Redis, because that is the only thing that can prove it. They look for one at `TEST_REDIS_URL`
(default `redis://localhost:6379/15`) and skip with a reason when none answers, rather than
reporting a pass they did not earn. `docker compose up -d redis` provides one; CI runs a service
container so the shared-ceiling guarantee is exercised on every push.

### Verifying a running stack

The suite above imports the app; the paths that only exist once a deployment is built -- the
environment seeding the first administrator, the shared demo login published at `GET /auth/demo`,
and the role guards as they behave through a real session cookie -- are checked by one script that
talks to the compose stack over HTTP:

```bash
python scripts/verify_live_stack.py
```

It reads `ADMIN_EMAIL` and `ADMIN_PASSWORD` from `backend/.env`, calls no provider route (so it
spends nothing), and leaves the stack as it found it: a throwaway administrator is created,
exercised through the demotion and deletion guards, and deleted again. The demo half skips with a
reason unless `DEMO_ACCOUNT_ENABLED=true` and `DEMO_ACCOUNT_PASSWORD` are set. Point it at another
origin with `EXCUBITOR_BASE_URL`.

## Continuous integration

`.github/workflows/ci.yml` runs on every push and pull request, with a `pgvector/pgvector:pg16`
and a `redis:7-alpine` service container, and cancels superseded runs on the same ref.

| Job | What it runs |
| --- | --- |
| Backend | `ruff check app tests`, then `pytest -q` against the service containers |
| Frontend | `npm ci`, `tsc --noEmit`, `vitest run`, `npm audit --omit=dev --audit-level=high`, then `next build` with `BACKEND_URL` set (Next evaluates `rewrites()` at build time) |
| Docker | Builds both images with buildx and the GitHub Actions layer cache, and publishes neither |

CI runs on Ubuntu only, and that is a documented limitation rather than an oversight. A second
backend job on a Windows runner was considered: the project is developed on Windows and verified
on Linux, and a matrix is the check that catches the difference. GitHub-hosted Windows runners
cannot run service containers — GitHub requires an Ubuntu runner for `services:`, or a self-hosted
Linux runner with Docker — so the pgvector and Redis the suite needs are not available to a
Windows job. A Windows job without them would skip the integration tests that distinguish the
platforms and report a green build it had not earned, so the Windows side is covered instead by
running the same `pytest -q` against the compose services on a Windows workstation.

The audit step blocks on high and critical findings only. Two moderate findings remain in
`@tailwindcss/typography -> postcss-selector-parser`, whose only "fix" is a forced downgrade of
the plugin to 0.5.4 — the same kind of downgrade the project already rejected when it moved to
Tailwind v4.
