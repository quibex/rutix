"""add reminder_time column to meds_active

Revision ID: 0003
Revises: 0002
Create Date: 2026-05-23
"""

from alembic import op
import sqlalchemy as sa


revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("meds_active", sa.Column("reminder_time", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("meds_active", "reminder_time")
