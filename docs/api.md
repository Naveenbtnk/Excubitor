# API conventions

The dashboard only renders what these routes return, so the contracts here are the ones a
reviewer, an integration or a future client has to rely on. Public surface: `GET /health`,
`GET /health/llm`, `POST /auth/login` and the token-authenticated webhook. Everything else
needs the session cookie, and every write route declares a minimum role.

- [Pagination](#pagination)
- [Incident timeline](#incident-timeline)
- [Post-mortems](#post-mortems)
- [Alert ingestion](#alert-ingestion)

## Pagination

`GET /alerts`, `GET /incidents` and `GET /knowledge/runbooks` return a page envelope instead of a
bare array:

```json
{ "items": [], "total": 0, "limit": 50, "offset": 0, "has_more": false }
```

`limit` and `offset` are query parameters, bounded by `PAGINATION_DEFAULT_LIMIT` and
`PAGINATION_MAX_LIMIT`. Lists are ordered by `created_at` with an `id` tiebreaker so pages are
stable and never overlap.

## Incident timeline

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

## Post-mortems

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

## Alert ingestion

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
