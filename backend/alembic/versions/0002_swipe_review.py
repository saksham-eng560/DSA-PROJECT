"""swipe review: keep / skip decisions and auto-submit for kept jobs

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-30 10:00:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0002'
down_revision: str | None = '0001'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table('applications') as batch_op:
        batch_op.add_column(sa.Column('review_decision', sa.String(length=16), nullable=True))
        batch_op.add_column(sa.Column('reviewed_at', sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column('auto_submit', sa.Boolean(), server_default=sa.false(), nullable=False))


def downgrade() -> None:
    with op.batch_alter_table('applications') as batch_op:
        batch_op.drop_column('auto_submit')
        batch_op.drop_column('reviewed_at')
        batch_op.drop_column('review_decision')
