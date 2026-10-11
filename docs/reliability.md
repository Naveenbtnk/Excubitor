# Reliability and provider behaviour

The parts of the system that decide what happens when the provider is slow, the quota is spent,
a Redis node disappears or an exception escapes a route. These are the guarantees the rest of the
code is allowed to lean on.

- [Rate limiting](#rate-limiting)
- [Provider budget](#provider-budget)
- [Provider resilience](#provider-resilience)
- [Error handling](#error-handling)
- [Observability](#observability)

## Rate limiting

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

## Provider budget

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

## Provider resilience

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

The worst case a proxy has to wait for is arithmetic rather than a guess — attempts times the
per-attempt deadline, plus the largest delays the backoff can insert. It is exposed as
`retry_budget_seconds` on `GET /health/llm`, and `PROXY_READ_TIMEOUT_SECONDS` states what the
proxy was actually configured with, so the startup guard can compare the two and name both
numbers when they disagree. Deployment examples size their read timeouts above that worst case
so a proxy can never give up before the service is done answering; see
[DEPLOYMENT.md → The proxy has to outwait the provider](../DEPLOYMENT.md#the-proxy-has-to-outwait-the-provider).

## Error handling

A failure that escapes a route is projected, never forwarded. The caller receives
`{"detail": "…", "error": {"code": "internal_error", "message": "…", "request_id": "…"}}` with a
`500`, and the traceback goes to the `cloudops.error` log line carrying the same request id — so a
user's screenshot is enough to find the stack, and the response itself holds nothing about the
schema, the path or the query.

On `POST /sandbox/stream` the `200` and the SSE headers are already committed when a token is
pulled, so the same failure is delivered as a terminal `error` event followed by `done`. The
partial answer is kept: an answer that got halfway is worth more than a reset connection.

## Observability

Logs are structured JSON on stdout. Every request emits one `http_request` line with
`request_id`, `method`, `path`, `status`, `duration_ms` and `user_id`; the same id is returned in
the `X-Request-ID` response header and honoured if the caller supplies one. `GET /health` checks
PostgreSQL connectivity and reports the counter backend as `counters` (`ok`, `unconfigured` when
no Redis is configured, or why it is unreachable), and `GET /health/llm` probes provider
reachability and reports the remaining provider budget (both are public, for orchestrator probes).
