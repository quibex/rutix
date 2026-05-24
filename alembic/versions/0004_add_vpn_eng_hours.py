"""add vpn_hours and eng_hours columns to mood_entries

Revision ID: 0004
Revises: 0003
Create Date: 2026-05-24
"""

from alembic import op
import sqlalchemy as sa


revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("mood_entries", sa.Column("vpn_hours", sa.Float(), nullable=True))
    op.add_column("mood_entries", sa.Column("eng_hours", sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column("mood_entries", "eng_hours")
    op.drop_column("mood_entries", "vpn_hours")
