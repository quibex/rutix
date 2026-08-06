"""add user_prefs table (runtime timezone)

Revision ID: 0008
Revises: 0007
Create Date: 2026-08-06
"""

from alembic import op
import sqlalchemy as sa


revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_prefs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tz", sa.String(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            server_default=sa.func.current_timestamp(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    # No seed row here: the app inserts one on first start using the TZ env var,
    # so a fresh deploy and an upgraded one converge on the same code path.


def downgrade() -> None:
    op.drop_table("user_prefs")
