"""add purpose to otp_code

Revision ID: 1f587f46cd23
Revises: ca76305b2894
Create Date: 2026-08-12 14:16:37.697998

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '1f587f46cd23'
down_revision = 'ca76305b2894'
branch_labels = None
depends_on = None


def upgrade():
    # job_fts / job_fts_* are an FTS5 virtual table -- not ORM-tracked,
    # autogenerate always misclassifies it as "removed". Stripped, same
    # as every prior migration that has touched this file.
    with op.batch_alter_table('otp_code', schema=None) as batch_op:
        batch_op.add_column(sa.Column('purpose', sa.String(length=10), nullable=False, server_default='register'))
    with op.batch_alter_table('otp_code', schema=None) as batch_op:
        batch_op.alter_column('purpose', server_default=None)


def downgrade():
    with op.batch_alter_table('otp_code', schema=None) as batch_op:
        batch_op.drop_column('purpose')
