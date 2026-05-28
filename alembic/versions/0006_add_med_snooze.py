"""add med_snooze table for deferred reminders

Revision ID: 0006
Revises: 0005
Create Date: 2026-05-28
"""

from alembic import op
import sqlalchemy as sa


revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "med_snooze",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("fire_at", sa.DateTime(), nullable=False),
        sa.Column("med_keys", sa.String(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("med_snooze")
