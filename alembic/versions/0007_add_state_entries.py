"""add state_entries table for multi-run /state snapshots

Revision ID: 0007
Revises: 0006
Create Date: 2026-07-01
"""

from alembic import op
import sqlalchemy as sa


revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "state_entries",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("ts", sa.DateTime(), nullable=False),
        sa.Column("mood", sa.Integer(), nullable=True),
        sa.Column("energy", sa.Integer(), nullable=True),
        sa.Column("appetite", sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_state_entries_day", "state_entries", ["day"])


def downgrade() -> None:
    op.drop_index("ix_state_entries_day", table_name="state_entries")
    op.drop_table("state_entries")
