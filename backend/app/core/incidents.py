"""The vocabulary an incident's timeline is written in.

Two closed sets. ``IncidentStatus`` is where an incident can be, and ``IncidentEventKind`` is
what happened to it. Both live here rather than beside the routes that write them so the
migration, the schemas and the timeline UI cannot drift apart: a status the API accepts but
the database rejects would be a 500, and a kind the UI cannot draw would be a blank row in
the middle of somebody's incident.
"""

from __future__ import annotations

from enum import StrEnum


class IncidentStatus(StrEnum):
    # Where triage leaves a new incident: an AI has read the alert and said what it thinks.
    TRIAGE = "triage"
    INVESTIGATING = "investigating"
    MITIGATED = "mitigated"
    RESOLVED = "resolved"


class IncidentEventKind(StrEnum):
    CREATED = "created"
    STATUS = "status"
    ASSIGNEE = "assignee"
    COMMENT = "comment"
    POST_MORTEM = "post_mortem"


INCIDENT_STATUS_VALUES: tuple[str, ...] = tuple(status.value for status in IncidentStatus)
INCIDENT_EVENT_KIND_VALUES: tuple[str, ...] = tuple(kind.value for kind in IncidentEventKind)
