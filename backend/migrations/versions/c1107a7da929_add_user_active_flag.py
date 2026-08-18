"""add user active flag

Revision ID: c1107a7da929
Revises: 4862a1c653b3
Create Date: 2026-08-11 23:32:53.044105

FTS5 false-positive diff stripped per this chain's standing convention
(job_fts isn't ORM-tracked, so autogenerate always sees it as a phantom
"removed table"). Do NOT ever let those op.drop_table('job_fts*') lines
reach upgrade() -- a prior migration in this chain briefly did, and
because SQLite DDL auto-commits per-statement (non-transactional), it
partially executed: the shadow tables (job_fts_config/data/docsize/idx)
were dropped before the final drop_table('job_fts') failed with "vtable
constructor failed", leaving the main virtual table registered in
sqlite_master with no backing storage. Recovered via
`DELETE FROM sqlite_master WHERE name='job_fts'` under
`PRAGMA writable_schema=ON`, then VACUUM, then letting app.py's own
CREATE VIRTUAL TABLE IF NOT EXISTS bootstrap recreate it clean.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'c1107a7da929'
down_revision = '4862a1c653b3'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('user', schema=None) as batch_op:
        batch_op.add_column(sa.Column('active', sa.Boolean(), server_default='1', nullable=False))


def downgrade():
    with op.batch_alter_table('user', schema=None) as batch_op:
        batch_op.drop_column('active')
