"""add employer erased_at

Revision ID: c4d9aded8bfa
Revises: 00ab5bd51837
Create Date: 2026-08-19 23:55:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'c4d9aded8bfa'
down_revision = '00ab5bd51837'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('employer', schema=None) as batch_op:
        batch_op.add_column(sa.Column('erased_at', sa.DateTime(), nullable=True))


def downgrade():
    with op.batch_alter_table('employer', schema=None) as batch_op:
        batch_op.drop_column('erased_at')
