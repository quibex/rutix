"""add energy column to mood_entries

Revision ID: 0002
Revises: 0001
Create Date: 2026-05-18
"""
from alembic import op
import sqlalchemy as sa


revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("mood_entries", sa.Column("energy", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("mood_entries", "energy")
