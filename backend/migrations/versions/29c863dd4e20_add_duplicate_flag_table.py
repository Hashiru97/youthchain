"""add duplicate flag table

Revision ID: 29c863dd4e20
Revises: 65e3837cd2b1
Create Date: 2026-08-05 20:08:05.624495

Hand-edited after autogeneration: same recurring FTS5 false-positive as
every prior migration touching this database — stripped. The only real
change is the new duplicate_flag table (see DuplicateFlag in app.py).
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '29c863dd4e20'
down_revision = '65e3837cd2b1'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('duplicate_flag',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('matched_user_id', sa.Integer(), nullable=False),
    sa.Column('reason', sa.String(length=300), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=True),
    sa.Column('resolved', sa.Boolean(), nullable=False),
    sa.Column('resolved_at', sa.DateTime(), nullable=True),
    sa.Column('resolved_by_admin_id', sa.Integer(), nullable=True),
    sa.ForeignKeyConstraint(['matched_user_id'], ['user.id'], ),
    sa.ForeignKeyConstraint(['resolved_by_admin_id'], ['admin.id'], ),
    sa.ForeignKeyConstraint(['user_id'], ['user.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('duplicate_flag', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_duplicate_flag_created_at'), ['created_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_duplicate_flag_matched_user_id'), ['matched_user_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_duplicate_flag_user_id'), ['user_id'], unique=False)


def downgrade():
    with op.batch_alter_table('duplicate_flag', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_duplicate_flag_user_id'))
        batch_op.drop_index(batch_op.f('ix_duplicate_flag_matched_user_id'))
        batch_op.drop_index(batch_op.f('ix_duplicate_flag_created_at'))

    op.drop_table('duplicate_flag')
