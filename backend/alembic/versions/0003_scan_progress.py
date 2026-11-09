"""scan progress: live phase / percent / per-source status for the dashboard's progress bar

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-30 18:00:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = '0003'
down_revision: str | None = '0002'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table('agent_runs') as batch_op:
        batch_op.add_column(sa.Column('progress', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'),
                                      nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('agent_runs') as batch_op:
        batch_op.drop_column('progress')
