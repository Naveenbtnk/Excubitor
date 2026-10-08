"""Building the rows that make up an incident's timeline.

Every write that changes an incident goes through here, so the events cannot disagree with
each other about what a status change looks like. The helper does not commit: the caller is
already inside a transaction that includes the change itself, and an event recorded in a
separate commit could survive a change that rolled back.
"""

from __future__ import annotations

from app.core.incidents import IncidentEventKind
from app.models import IncidentEvent


def incident_event(
    incident_id: int,
    kind: IncidentEventKind,
    actor: str,
    *,
    from_value: str | None = None,
    to_value: str | None = None,
    body: str = "",
) -> IncidentEvent:
    """One timeline entry. ``actor`` is the email of whoever caused it."""
    return IncidentEvent(
        incident_id=incident_id,
        kind=kind.value,
        actor=actor,
        # The columns are the diff; the body is the sentence a human reads. Both are stored
        # rather than derived, because the sentence is what makes the entry legible and the
        # columns are what a filter or an export can use later.
        from_value=from_value,
        to_value=to_value,
        body=body,
    )
