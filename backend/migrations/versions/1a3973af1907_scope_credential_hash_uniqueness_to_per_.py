"""scope credential hash uniqueness to per-user

Revision ID: 1a3973af1907
Revises: 7c59f4c89917
Create Date: 2026-08-07 14:23:37.849329

Hand-edited after autogeneration: same recurring FTS5 false-positive as
every prior migration touching this database — stripped.

Also hand-edited beyond that (not just FTS stripping, and beyond two
earlier attempts that turned out wrong): autogenerate only picked up the
new index/constraint as ADDITIONS — it never detected that the OLD
single-column `UNIQUE (hash)` (created by the very first migration via a
bare `sa.UniqueConstraint('hash')` with no explicit name) needed to be
dropped. Worse, that constraint's real name is NOT portable across the two
database engines this app supports: SQLite stored it as an unnamed
autoindex (sqlite_autoindex_credential_1), while Postgres assigned its own
default name (credential_hash_key) at CREATE TABLE time — a hardcoded name
that worked on one engine failed on the other with "No such constraint"
(found by actually running this against a real Postgres instance, not
assumed). Fixed by looking the real name up dynamically at migration-run time
instead of hardcoding either dialect's name — the one approach that's
correct on both. A first attempt at "dynamic" used the plain inspector
(`inspect(bind).get_unique_constraints(...)`), which is correct for
Postgres (a real catalog name comes back) but wrong for SQLite: an
anonymous constraint reflects with `name: None`, and `if old_name:`
treated that the same as "not found," silently skipping the drop and
leaving SQLite exactly as broken as before. Fixed for real by reflecting
through a `MetaData` configured with a `naming_convention` instead — that
makes SQLAlchemy assign anonymous constraints the same deterministic name
a fresh model definition would get, while leaving Postgres's already-real
catalog name untouched — verified against both engines directly before
settling on this.

One more real issue found the same way (applying this to a real Postgres
instance, not assumed): `recreate="always"` unconditionally forces batch
mode's full copy-table-and-rename rebuild dance even on Postgres, which
supports `ALTER TABLE ... DROP/ADD CONSTRAINT` natively and never needed
that dance in the first place — SQLite is the one dialect that does,
since it can't ALTER a constraint in place. Forcing it on Postgres worked,
but left a cosmetically-leftover `_alembic_tmp_credential_id_seq` sequence
name behind (harmless functionally, confusing to a future reader).
`recreate="auto"` (the default) makes batch mode do the right thing per
dialect on its own: full rebuild only where SQLite actually requires it,
a clean direct ALTER everywhere else.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '1a3973af1907'
down_revision = '7c59f4c89917'
branch_labels = None
depends_on = None


_NAMING_CONVENTION = {"uq": "uq_%(table_name)s_%(column_0_name)s"}


def _find_hash_only_unique_constraint_name(bind):
    """The old single-column UNIQUE(hash) constraint's real name differs by
    dialect (see module docstring) — find it by column shape via a
    naming-convention-aware reflection instead of by a hardcoded name or
    the plain inspector (which returns name=None for SQLite's anonymous
    case, indistinguishable from "not found"). Returns None only if truly
    not present (e.g. re-running against an already-migrated db)."""
    meta = sa.MetaData(naming_convention=_NAMING_CONVENTION)
    meta.reflect(bind=bind, only=["credential"])
    for c in meta.tables["credential"].constraints:
        if isinstance(c, sa.UniqueConstraint) and [col.name for col in c.columns] == ["hash"]:
            return c.name
    return None


def upgrade():
    bind = op.get_bind()
    old_name = _find_hash_only_unique_constraint_name(bind)

    # naming_convention passed to batch_alter_table itself, not just to the
    # lookup above — real gap found by actually running this against
    # SQLite after the Postgres fix above: batch mode does its OWN internal
    # reflection to validate a drop_constraint target exists, using its own
    # (unconfigured) naming rules, which don't know about the name the
    # lookup above computed. Without this, batch mode reflects the same
    # constraint as anonymous all over again and rejects the very name
    # _find_hash_only_unique_constraint_name just returned with "No such
    # constraint" — confirmed by hitting that exact error before adding
    # this parameter.
    with op.batch_alter_table("credential", schema=None, naming_convention=_NAMING_CONVENTION) as batch_op:
        if old_name:
            batch_op.drop_constraint(old_name, type_="unique")
        batch_op.create_index(batch_op.f("ix_credential_hash"), ["hash"], unique=False)
        batch_op.create_unique_constraint("uq_credential_user_hash", ["user_id", "hash"])


def downgrade():
    with op.batch_alter_table("credential", schema=None, naming_convention=_NAMING_CONVENTION) as batch_op:
        batch_op.drop_constraint("uq_credential_user_hash", type_="unique")
        batch_op.drop_index(batch_op.f("ix_credential_hash"))
        batch_op.create_unique_constraint("uq_credential_hash", ["hash"])
