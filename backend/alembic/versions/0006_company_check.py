"""company check: verdict, tier and reasons per job

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-01 16:30:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = '0006'
down_revision: str | None = '0005'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table('jobs') as batch_op:
        batch_op.add_column(sa.Column('company_verdict', sa.String(length=16), nullable=True))
        batch_op.add_column(sa.Column('company_tier', sa.String(length=32), nullable=True))
        batch_op.add_column(sa.Column('company_check', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()),
                                                                              'postgresql'), nullable=True))
        batch_op.create_index('idx_jobs_company_tier', ['company_tier'], unique=False)
        batch_op.create_index('idx_jobs_company_verdict', ['company_verdict'], unique=False)


def downgrade() -> None:
    with op.batch_alter_table('jobs') as batch_op:
        batch_op.drop_index('idx_jobs_company_verdict')
        batch_op.drop_index('idx_jobs_company_tier')
        batch_op.drop_column('company_check')
        batch_op.drop_column('company_tier')
        batch_op.drop_column('company_verdict')
