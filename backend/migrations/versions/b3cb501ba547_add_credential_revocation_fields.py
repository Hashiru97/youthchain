"""add credential revocation fields

Revision ID: b3cb501ba547
Revises: 1a3973af1907
Create Date: 2026-08-09 13:49:41.558386

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'b3cb501ba547'
down_revision = '1a3973af1907'
branch_labels = None
depends_on = None


def upgrade():
    # Autogenerate also proposed dropping job_fts and its SQLite FTS5
    # shadow tables (job_fts_idx/_docsize/_config/_data) — a known false
    # positive, not a real schema change: those are hand-written virtual
    # tables (see bc0adf755ad8's own migration) that Alembic's reflection
    # can't recognize as intentional, so it reads them as "not in the
    # model, must have been removed." Stripped here, same as every other
    # migration in this chain has had to do around that table.
    with op.batch_alter_table('credential', schema=None) as batch_op:
        batch_op.add_column(sa.Column('revoked_at', sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column('revoked_by_admin_id', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('revoke_onchain_tx', sa.String(length=80), nullable=True))
        batch_op.create_foreign_key(
            'fk_credential_revoked_by_admin_id_admin', 'admin', ['revoked_by_admin_id'], ['id']
        )


def downgrade():
    with op.batch_alter_table('credential', schema=None) as batch_op:
        batch_op.drop_constraint('fk_credential_revoked_by_admin_id_admin', type_='foreignkey')
        batch_op.drop_column('revoke_onchain_tx')
        batch_op.drop_column('revoked_by_admin_id')
        batch_op.drop_column('revoked_at')
