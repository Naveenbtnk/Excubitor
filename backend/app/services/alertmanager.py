"""Map a Prometheus Alertmanager webhook group onto our alert rows.

Alertmanager speaks a shape we do not own, so the translation lives here rather than in the
route: one function from payload to alert fields, which is the whole of the mapping and can
be read (and tested) without a database or an HTTP client.

One alert per ``alerts[]`` entry, not one per delivery: a group carrying two firing alerts
and one resolved alert becomes three rows with three statuses, because that is what the
alert list has always shown and collapsing them would hide which one recovered.
"""

from __future__ import annotations

from typing import Any

from app.schemas.webhooks import AlertmanagerAlert, AlertmanagerWebhook

DEFAULT_SEVERITY = "warning"
DEFAULT_TITLE = "Alertmanager alert"
DEFAULT_SOURCE = "alertmanager"

# The column widths in app.models.alert. Alertmanager labels are free text with no length
# contract, and a 300-character alertname must not become a 500 from the database.
TITLE_LIMIT = 180
SEVERITY_LIMIT = 30
SOURCE_LIMIT = 80

OPEN = "open"
RESOLVED = "resolved"


def _first(*values: str | None) -> str | None:
    """The first value that is present and non-empty."""
    for value in values:
        if value:
            return value
    return None


def _title(group: AlertmanagerWebhook, alert: AlertmanagerAlert) -> str:
    # ``instance`` is what makes a group of otherwise identical alerts readable: three
    # alerts named "HighCPU" are only useful with "on node-1/node-2/node-3" attached.
    name = _first(
        alert.labels.get("alertname"), group.common_labels.get("alertname")
    ) or DEFAULT_TITLE
    instance = alert.labels.get("instance")
    return (f"{name} on {instance}" if instance else name)[:TITLE_LIMIT]


def _severity(group: AlertmanagerWebhook, alert: AlertmanagerAlert) -> str:
    raw = _first(alert.labels.get("severity"), group.common_labels.get("severity"))
    return (raw or DEFAULT_SEVERITY).lower()[:SEVERITY_LIMIT]


def _source(group: AlertmanagerWebhook, alert: AlertmanagerAlert) -> str:
    raw = _first(
        alert.labels.get("job"),
        alert.labels.get("cluster"),
        group.common_labels.get("job"),
        group.common_labels.get("cluster"),
    )
    return (raw or DEFAULT_SOURCE)[:SOURCE_LIMIT]


def _status(group: AlertmanagerWebhook, alert: AlertmanagerAlert) -> str:
    # The per-alert status wins over the group's: a group is "firing" as a whole even when
    # one of its alerts has just recovered, and that one is the interesting row.
    state = _first(alert.status, group.status) or ""
    return RESOLVED if state.lower() == RESOLVED else OPEN


def _detail(group: AlertmanagerWebhook, alert: AlertmanagerAlert) -> str:
    """Everything a responder needs that has no column of its own."""
    summary = _first(
        alert.annotations.get("summary"), group.common_annotations.get("summary")
    )
    description = _first(
        alert.annotations.get("description"), group.common_annotations.get("description")
    )
    lines = [summary] if summary else []
    if description and description != summary:
        # Groups often repeat the same sentence in both annotations; printing it twice makes
        # the detail look like two events.
        lines.append(description)
    if alert.labels:
        rendered = ", ".join(f"{key}={value}" for key, value in sorted(alert.labels.items()))
        lines.append(f"labels: {rendered}")
    if alert.starts_at:
        lines.append(f"startsAt: {alert.starts_at}")
    if alert.generator_url:
        lines.append(f"generatorURL: {alert.generator_url}")
    if group.external_url:
        lines.append(f"alertmanager: {group.external_url}")
    return "\n".join(lines)


def to_alert_rows(payload: AlertmanagerWebhook) -> list[dict[str, Any]]:
    """One row per alert in the group, in the shape ``Alert(**row)`` expects."""
    return [
        {
            "title": _title(payload, alert),
            "severity": _severity(payload, alert),
            "source": _source(payload, alert),
            "status": _status(payload, alert),
            "detail": _detail(payload, alert),
        }
        for alert in payload.alerts
    ]
