"""add user consent accepted at

Revision ID: 65e3837cd2b1
Revises: 26a1dadca8a9
Create Date: 2026-08-05 19:58:28.089699

Hand-edited after autogeneration: same recurring FTS5 false-positive as
every prior migration touching this database — stripped. The only real
change is User.consent_accepted_at, nullable (existing rows predate the
consent flow and have no consent timestamp to backfill).
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '65e3837cd2b1'
down_revision = '26a1dadca8a9'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('user', schema=None) as batch_op:
        batch_op.add_column(sa.Column('consent_accepted_at', sa.DateTime(), nullable=True))


def downgrade():
    with op.batch_alter_table('user', schema=None) as batch_op:
        batch_op.drop_column('consent_accepted_at')
