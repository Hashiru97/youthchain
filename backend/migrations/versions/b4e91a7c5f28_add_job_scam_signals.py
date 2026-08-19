"""add job scam_signals

Revision ID: b4e91a7c5f28
Revises: d8c2f4b6a913
Create Date: 2026-08-19 16:30:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'b4e91a7c5f28'
down_revision = 'd8c2f4b6a913'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('job', schema=None) as batch_op:
        batch_op.add_column(sa.Column('scam_signals', sa.String(length=200), nullable=True))


def downgrade():
    with op.batch_alter_table('job', schema=None) as batch_op:
        batch_op.drop_column('scam_signals')
