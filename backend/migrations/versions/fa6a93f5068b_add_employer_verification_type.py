"""add employer verification_type

Revision ID: fa6a93f5068b
Revises: 3fdb04e1e185
Create Date: 2026-08-11 19:13:58.630050

Hand-edited after autogeneration: same recurring FTS5 false-positive as
every prior migration touching this database — stripped. server_default
'business' on the new NOT NULL column so backfilling it onto every
pre-existing Employer row is a real fact, not a guess: every employer
verified before this field existed was necessarily verified via the
business-document track (the only one that existed), so 'business' is
what actually happened for those rows, not an assumption.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'fa6a93f5068b'
down_revision = '3fdb04e1e185'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('employer', schema=None) as batch_op:
        batch_op.add_column(sa.Column('verification_type', sa.String(length=20), nullable=False, server_default='business'))


def downgrade():
    with op.batch_alter_table('employer', schema=None) as batch_op:
        batch_op.drop_column('verification_type')
