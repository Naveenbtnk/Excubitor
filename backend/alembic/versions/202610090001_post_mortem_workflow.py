"""give post-mortems structured sections, a review state and action items

A post-mortem was one markdown blob with no state: whoever generated it wrote the whole record,
and everyone could read it the moment it existed. That is a document, not a workflow. This adds
the sections the document is reviewed on (timeline summary, root cause, contributing factors,
impact), the state it moves through (draft, in review, published), who wrote it, and the follow-up
items with owners and due dates that make it something a team acts on.

Three choices worth stating, because a migration is where they become permanent:

* Existing rows become ``published``, not ``draft``. They were created by an operator through the
  API and have been readable by every role since; defaulting them to draft would hide documents
  people can already see. New rows still start as ``draft``, which is the server default.
* ``content`` is untouched. The narrative stays where it was and the new sections sit beside it --
  nothing is parsed out of prose, because a parser deciding what a paragraph meant is how a
  post-mortem loses a detail nobody notices until an audit.
* ``author`` and the action items' ``owner`` store an email next to the user reference. A
  document outlives an account; the reference is for the app, the address is for a human.

Revision ID: 202610090001
Revises: 202610080002
Create Date: 2026-10-09
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "202610090001"
down_revision = "202610080002"
branch_labels = None
depends_on = None

# Spelled out rather than imported from app.core.postmortems: a migration describes the schema at
# this point in history and must not change meaning when the vocabulary does.
STATUS_LIST = "'draft', 'in_review', 'published'"
ACTION_STATUS_LIST = "'open', 'done'"


def upgrade() -> None:
    op.add_column(
        "post_mortems",
        sa.Column("timeline_summary", sa.Text(), nullable=False, server_default=""),
    )
    op.add_column(
        "post_mortems", sa.Column("root_cause", sa.Text(), nullable=False, server_default="")
    )
    op.add_column(
        "post_mortems",
        sa.Column(
            "contributing_factors",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    op.add_column("post_mortems", sa.Column("impact", sa.Text(), nullable=False, server_default=""))
    op.add_column(
        "post_mortems",
        sa.Column("status", sa.String(length=20), nullable=False, server_default="draft"),
    )
    op.add_column(
        "post_mortems", sa.Column("author", sa.String(length=180), nullable=False, server_default="")
    )
    op.add_column(
        "post_mortems",
        sa.Column(
            "author_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column(
        "post_mortems", sa.Column("published_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "post_mortems",
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )

    # See the note above: what people can already read stays readable.
    op.execute("UPDATE post_mortems SET status = 'published', published_at = created_at")
    op.create_check_constraint("ck_post_mortems_status", "post_mortems", f"status IN ({STATUS_LIST})")
    op.create_index("ix_post_mortems_status", "post_mortems", ["status"])

    op.create_table(
        "post_mortem_actions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "post_mortem_id",
            sa.Integer(),
            sa.ForeignKey("post_mortems.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column(
            "owner_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("owner", sa.String(length=180), nullable=False, server_default=""),
        sa.Column("due_date", sa.Date(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="open"),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(f"status IN ({ACTION_STATUS_LIST})", name="ck_post_mortem_actions_status"),
    )
    op.create_index(
        "ix_post_mortem_actions_post_mortem_id", "post_mortem_actions", ["post_mortem_id"]
    )
    op.create_index("ix_post_mortem_actions_due_date", "post_mortem_actions", ["due_date"])
    op.create_index("ix_post_mortem_actions_status", "post_mortem_actions", ["status"])


def downgrade() -> None:
    op.drop_index("ix_post_mortem_actions_status", table_name="post_mortem_actions")
    op.drop_index("ix_post_mortem_actions_due_date", table_name="post_mortem_actions")
    op.drop_index("ix_post_mortem_actions_post_mortem_id", table_name="post_mortem_actions")
    op.drop_table("post_mortem_actions")

    op.drop_index("ix_post_mortems_status", table_name="post_mortems")
    op.drop_constraint("ck_post_mortems_status", "post_mortems", type_="check")
    for column in (
        "updated_at",
        "published_at",
        "author_id",
        "author",
        "status",
        "impact",
        "contributing_factors",
        "root_cause",
        "timeline_summary",
    ):
        op.drop_column("post_mortems", column)
