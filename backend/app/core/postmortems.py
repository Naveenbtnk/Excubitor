"""The vocabulary a post-mortem is written and reviewed in.

Two closed sets, for the same reason :mod:`app.core.incidents` keeps theirs here: the migration
generates its check constraints from these values, and the schema refuses anything outside them,
so a document cannot reach a state the UI has no way to draw. Post-mortems started as a single
free-text body with one implicit state -- written -- and grew a review pipeline; a status column
without a vocabulary is how "publsihed" ends up in a database.
"""

from __future__ import annotations

from enum import StrEnum


class PostMortemStatus(StrEnum):
    """Where a document is in review.

    ``DRAFT`` is the state every new generation starts in, and the one a viewer may not see:
    an unreviewed document is a claim about an incident, not a record of one.
    """

    DRAFT = "draft"
    IN_REVIEW = "in_review"
    PUBLISHED = "published"


class ActionItemStatus(StrEnum):
    OPEN = "open"
    DONE = "done"


POST_MORTEM_STATUS_VALUES: tuple[str, ...] = tuple(status.value for status in PostMortemStatus)
ACTION_ITEM_STATUS_VALUES: tuple[str, ...] = tuple(status.value for status in ActionItemStatus)
