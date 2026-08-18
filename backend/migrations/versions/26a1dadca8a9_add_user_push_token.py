"""add user push token

Revision ID: 26a1dadca8a9
Revises: c0f4192abf29
Create Date: 2026-08-05 19:25:38.984650

Hand-edited after autogeneration: same recurring false-positive already
explained in the job_fts, message-read-receipts, and employer-verification
migrations — Alembic's autogenerate has no model for the hand-written
FTS5 virtual table and proposed dropping it and its SQLite-managed shadow
tables. Removed those; the only real change here is User.push_token.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '26a1dadca8a9'
down_revision = 'c0f4192abf29'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('user', schema=None) as batch_op:
        batch_op.add_column(sa.Column('push_token', sa.String(length=300), nullable=True))


def downgrade():
    with op.batch_alter_table('user', schema=None) as batch_op:
        batch_op.drop_column('push_token')
