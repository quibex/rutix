"""add job_prefs table (per-cron on/off + time)

Revision ID: 0009
Revises: 0008
Create Date: 2026-08-15
"""

from alembic import op
import sqlalchemy as sa


revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "job_prefs",
        sa.Column("job_id", sa.String(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("hour", sa.String(), nullable=True),
        sa.Column("minute", sa.String(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            server_default=sa.func.current_timestamp(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("job_id"),
    )
    # No seed rows: an absent row means "run this job on the schedule the code
    # ships with", so existing installs keep their current behaviour and new
    # jobs need no backfill.


def downgrade() -> None:
    op.drop_table("job_prefs")
