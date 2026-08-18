"""rename otpcode email to identifier, add channel

Revision ID: ca76305b2894
Revises: 7f0271479978
Create Date: 2026-08-12 12:09:35.489582

FTS5 false-positive diff stripped per this chain's standing convention.
identifier/channel added with a server_default so this doesn't fail on a
non-empty table, then backfilled from the old email column (still email
for every existing row -- channel="sms" only starts appearing once
portal_register()'s new flow issues one) before email is dropped, so any
still-valid (unexpired, unused) OTP code at migration time keeps working
instead of being silently orphaned.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'ca76305b2894'
down_revision = '7f0271479978'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('otp_code', schema=None) as batch_op:
        batch_op.add_column(sa.Column('identifier', sa.String(length=200), nullable=False, server_default=''))
        batch_op.add_column(sa.Column('channel', sa.String(length=10), nullable=False, server_default='email'))

    op.execute("UPDATE otp_code SET identifier = email")

    with op.batch_alter_table('otp_code', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_otp_code_email'))
        batch_op.create_index(batch_op.f('ix_otp_code_identifier'), ['identifier'], unique=False)
        batch_op.drop_column('email')
        batch_op.alter_column('identifier', server_default=None)
        batch_op.alter_column('channel', server_default=None)


def downgrade():
    with op.batch_alter_table('otp_code', schema=None) as batch_op:
        batch_op.add_column(sa.Column('email', sa.VARCHAR(length=200), nullable=False, server_default=''))

    op.execute("UPDATE otp_code SET email = identifier")

    with op.batch_alter_table('otp_code', schema=None) as batch_op:
        batch_op.alter_column('email', server_default=None)
        batch_op.drop_index(batch_op.f('ix_otp_code_identifier'))
        batch_op.create_index(batch_op.f('ix_otp_code_email'), ['email'], unique=False)
        batch_op.drop_column('channel')
        batch_op.drop_column('identifier')
