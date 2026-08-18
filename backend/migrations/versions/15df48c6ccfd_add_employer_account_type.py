"""add employer account_type

Revision ID: 15df48c6ccfd
Revises: fa6a93f5068b
Create Date: 2026-08-11 20:05:00.000000

Hand-edited after autogeneration: same recurring FTS5 false-positive as
every prior migration touching this database — stripped. server_default
'business' for the same reason verification_type's migration uses it:
every account created before this field existed signed up under a flow
that never asked, so 'business' is the neutral, already-established
default, not a guess about who any specific existing employer is.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '15df48c6ccfd'
down_revision = 'fa6a93f5068b'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('employer', schema=None) as batch_op:
        batch_op.add_column(sa.Column('account_type', sa.String(length=20), nullable=False, server_default='business'))


def downgrade():
    with op.batch_alter_table('employer', schema=None) as batch_op:
        batch_op.drop_column('account_type')
