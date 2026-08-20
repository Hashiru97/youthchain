"""add user erased_at

Revision ID: 00ab5bd51837
Revises: b4e91a7c5f28
Create Date: 2026-08-19 23:40:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '00ab5bd51837'
down_revision = 'b4e91a7c5f28'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('user', schema=None) as batch_op:
        batch_op.add_column(sa.Column('erased_at', sa.DateTime(), nullable=True))


def downgrade():
    with op.batch_alter_table('user', schema=None) as batch_op:
        batch_op.drop_column('erased_at')
