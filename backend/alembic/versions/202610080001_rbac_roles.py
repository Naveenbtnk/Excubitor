"""constrain and default users.role

The column has existed since the initial schema but was never constrained or defaulted, and
nothing read it. Now that roles decide what a caller may do, the database should refuse a
value the application has no meaning for, and a row created without an explicit role should
get the least privileged one rather than an implicit administrator.

Revision ID: 202610080001
Revises: 202609290001
Create Date: 2026-10-08
"""

import sqlalchemy as sa
from alembic import op

revision = "202610080001"
down_revision = "202609290001"
branch_labels = None
depends_on = None

# Spelled out rather than imported from app.core.rbac: a migration describes the schema at
# this point in history, and must not change meaning when the vocabulary does.
ROLE_LIST = "'admin', 'operator', 'viewer'"


def upgrade() -> None:
    # Any value outside the vocabulary was written by something that did not know the rules.
    # Viewer is the only safe reading of it: the alternative silently grants access.
    op.execute(f"UPDATE users SET role = 'viewer' WHERE role NOT IN ({ROLE_LIST})")
    op.alter_column(
        "users",
        "role",
        existing_type=sa.String(length=40),
        existing_nullable=False,
        server_default="viewer",
    )
    op.create_check_constraint("ck_users_role", "users", f"role IN ({ROLE_LIST})")


def downgrade() -> None:
    op.drop_constraint("ck_users_role", "users", type_="check")
    op.alter_column(
        "users",
        "role",
        existing_type=sa.String(length=40),
        existing_nullable=False,
        server_default=None,
    )
