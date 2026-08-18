"""add employer active column

Revision ID: c6a9484f6134
Revises: 29c863dd4e20
Create Date: 2026-08-06 00:47:02.719718

Hand-edited after autogeneration: same recurring FTS5 false-positive as
every prior migration touching this database — stripped. Added
server_default=sa.true() for the new NOT NULL column so backfilling it
onto any database with pre-existing Employer rows doesn't fail outright
(the same S-02-adjacent gotcha already documented on the employer
verification and message-read-receipt migrations).
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'c6a9484f6134'
down_revision = '29c863dd4e20'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('employer', schema=None) as batch_op:
        batch_op.add_column(sa.Column('active', sa.Boolean(), nullable=False, server_default=sa.true()))


def downgrade():
    with op.batch_alter_table('employer', schema=None) as batch_op:
        batch_op.drop_column('active')
