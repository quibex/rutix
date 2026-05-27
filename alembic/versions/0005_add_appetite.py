"""add appetite column to mood_entries

Revision ID: 0005
Revises: 0004
Create Date: 2026-05-27
"""

from alembic import op
import sqlalchemy as sa


revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("mood_entries", sa.Column("appetite", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("mood_entries", "appetite")
