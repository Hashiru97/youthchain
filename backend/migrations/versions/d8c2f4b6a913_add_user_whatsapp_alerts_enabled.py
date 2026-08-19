"""add user whatsapp_alerts_enabled

Revision ID: d8c2f4b6a913
Revises: a4b2dd171dc9
Create Date: 2026-08-19 09:30:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'd8c2f4b6a913'
down_revision = 'a4b2dd171dc9'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('user', schema=None) as batch_op:
        batch_op.add_column(sa.Column('whatsapp_alerts_enabled', sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade():
    with op.batch_alter_table('user', schema=None) as batch_op:
        batch_op.drop_column('whatsapp_alerts_enabled')
