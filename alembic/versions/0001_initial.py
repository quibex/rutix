"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-05-14
"""
from alembic import op
import sqlalchemy as sa


revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "mood_entries",
        sa.Column("day", sa.Date(), primary_key=True),
        sa.Column("mood", sa.Integer(), nullable=True),
        sa.Column("anxiety", sa.Integer(), nullable=True),
        sa.Column("irritability", sa.Integer(), nullable=True),
        sa.Column("sleep_hours", sa.Float(), nullable=True),
        sa.Column("weight", sa.Float(), nullable=True),
        sa.Column("notes", sa.String(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            server_default=sa.func.current_timestamp(),
            nullable=False,
        ),
    )
    op.create_table(
        "medication_log",
        sa.Column("day", sa.Date(), primary_key=True),
        sa.Column("med_key", sa.String(), primary_key=True),
        sa.Column("taken", sa.Boolean(), nullable=False, server_default=sa.text("0")),
    )
    op.create_table(
        "meds_active",
        sa.Column("key", sa.String(), primary_key=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("column_label", sa.String(), nullable=False),
        sa.Column("current_dose", sa.String(), nullable=False),
        sa.Column("started_at", sa.Date(), nullable=False),
        sa.Column("archived_at", sa.Date(), nullable=True),
    )
    op.create_table(
        "flush_log",
        sa.Column("period_id", sa.String(), primary_key=True),
        sa.Column(
            "flushed_at",
            sa.DateTime(),
            server_default=sa.func.current_timestamp(),
            nullable=False,
        ),
        sa.Column("git_sha", sa.String(), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("flush_log")
    op.drop_table("meds_active")
    op.drop_table("medication_log")
    op.drop_table("mood_entries")
