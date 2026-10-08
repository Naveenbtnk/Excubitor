"""Roles and their ordering.

Three roles, and privilege only ever grows along them: a Viewer reads, an Operator also
changes things, an Admin also manages people. Expressing that as a rank rather than a
permission matrix keeps "can this caller do X" to one comparison, so a route cannot end up
with a list that drifts out of step with the others.

The values here are the ones stored in ``users.role``; the check constraint added by the
RBAC migration is generated from :data:`ROLE_VALUES`, so the database and this module cannot
disagree about what a valid role is.
"""

from __future__ import annotations

from enum import StrEnum


class Role(StrEnum):
    ADMIN = "admin"
    OPERATOR = "operator"
    VIEWER = "viewer"


_RANK: dict[Role, int] = {Role.VIEWER: 0, Role.OPERATOR: 1, Role.ADMIN: 2}

ROLE_VALUES: tuple[str, ...] = tuple(role.value for role in Role)


def rank(role: str) -> int:
    """The privilege of a stored role.

    Anything unrecognised ranks below Viewer, so it grants nothing. A value this code does
    not know about is either a mistake or an attempt, and failing closed is the only safe
    reading of it -- the opposite choice would turn a typo into an administrator.
    """
    try:
        return _RANK[Role(role)]
    except ValueError:
        return -1


def at_least(role: str, minimum: Role) -> bool:
    """Whether ``role`` may perform something that requires ``minimum``."""
    return rank(role) >= _RANK[minimum]
