"""add ncra_id to user

Revision ID: 8b93699c7f37
Revises: 1f587f46cd23
Create Date: 2026-08-12 15:04:26.277431

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '8b93699c7f37'
down_revision = '1f587f46cd23'
branch_labels = None
depends_on = None


def upgrade():
    # job_fts / job_fts_* are an FTS5 virtual table -- not ORM-tracked,
    # autogenerate always misclassifies it as "removed". Stripped, same
    # as every prior migration that has touched this file.
    with op.batch_alter_table('user', schema=None) as batch_op:
        batch_op.add_column(sa.Column('ncra_id', sa.String(length=50), nullable=True))


def downgrade():
    with op.batch_alter_table('user', schema=None) as batch_op:
        batch_op.drop_column('ncra_id')
