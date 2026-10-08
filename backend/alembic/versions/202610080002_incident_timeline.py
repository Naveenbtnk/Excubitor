"""add incidents.assignee and the incident_events timeline

An incident has had a single mutable ``status`` column since the beginning, which records
where it is now and nothing about how it got there. This adds the append-only table that
answers the second question, plus the assignee column the timeline needs something to record.

Revision ID: 202610080002
Revises: 202610080001
Create Date: 2026-10-08
"""

import sqlalchemy as sa
from alembic import op

revision = "202610080002"
down_revision = "202610080001"
branch_labels = None
depends_on = None

# Spelled out rather than imported from app.core.incidents: a migration describes the schema
# at this point in history and must not change meaning when the vocabulary does.
STATUS_LIST = "'triage', 'investigating', 'mitigated', 'resolved'"
KIND_LIST = "'created', 'status', 'assignee', 'comment', 'post_mortem'"


def upgrade() -> None:
    op.add_column("incidents", sa.Column("assignee", sa.String(length=180), nullable=True))

    # Anything outside the vocabulary was written by something that did not know the rules;
    # "triage" is the honest reading of it, since we cannot tell what it meant.
    op.execute(f"UPDATE incidents SET status = 'triage' WHERE status NOT IN ({STATUS_LIST})")
    op.create_check_constraint("ck_incidents_status", "incidents", f"status IN ({STATUS_LIST})")

    op.create_table(
        "incident_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "incident_id",
            sa.Integer(),
            sa.ForeignKey("incidents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(length=40), nullable=False),
        sa.Column("actor", sa.String(length=180), nullable=False),
        sa.Column("from_value", sa.String(length=180), nullable=True),
        sa.Column("to_value", sa.String(length=180), nullable=True),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(f"kind IN ({KIND_LIST})", name="ck_incident_events_kind"),
    )
    op.create_index("ix_incident_events_incident_id", "incident_events", ["incident_id"])
    op.create_index("ix_incident_events_kind", "incident_events", ["kind"])
    op.create_index("ix_incident_events_created_at", "incident_events", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_incident_events_created_at", table_name="incident_events")
    op.drop_index("ix_incident_events_kind", table_name="incident_events")
    op.drop_index("ix_incident_events_incident_id", table_name="incident_events")
    op.drop_table("incident_events")
    op.drop_constraint("ck_incidents_status", "incidents", type_="check")
    op.drop_column("incidents", "assignee")
