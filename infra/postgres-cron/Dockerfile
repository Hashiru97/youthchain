# Postgres 16 with the pg_cron extension installed, for the job scanner's
# scheduling (see backend/migrations/versions/
# b6d3e81f5a97_enable_pg_cron_scan_scheduling.py for what actually uses
# it). Stock postgres:16-alpine — what every docker-compose*.yml in this
# repo used before this file existed — doesn't bundle pg_cron at all, and
# PGDG only publishes postgresql-16-cron as a .deb, not an Alpine package
# (an from-source Alpine build is unmaintained and not worth the
# fragility), hence FROM postgres:16 (Debian-based), not -alpine.
#
# The official postgres image already configures the PGDG apt repository
# for its own Postgres major version at build time, so this only needs
# one apt-get install — no new repo to add or trust here, same posture as
# every other apt-based install in this repo (backend/Dockerfile).
#
# shared_preload_libraries=pg_cron MUST be set at server start (it can't
# be changed by a migration/SQL statement after the fact) — set via
# `command:` in each compose file's postgres service, not baked in here,
# so cron.database_name stays overridable per compose file rather than
# hardcoded into the image.
FROM postgres:16

RUN apt-get update \
    && apt-get install -y --no-install-recommends postgresql-16-cron \
    && rm -rf /var/lib/apt/lists/*

# Baked in as CMD (not hardcoded via an orchestrator-level start-command
# override) so the base image's own ENTRYPOINT (docker-entrypoint.sh) stays
# in charge of the normal secure boot sequence -- initdb, then dropping to
# the unprivileged `postgres` user via gosu before actually exec'ing this
# command. An orchestrator that replaces the container's entrypoint outright
# to inject these flags (rather than only overriding CMD, the way
# docker-compose.yml's own `command:` does) skips that gosu step entirely
# and crash-loops on "root execution of the PostgreSQL server is not
# permitted" -- found via a real deploy, not assumed. docker-compose.yml's
# `command:` still overrides this identically for local/dev use; this CMD
# is only what actually runs where no such override is supplied.
CMD ["postgres", "-c", "shared_preload_libraries=pg_cron", "-c", "cron.database_name=youthchain"]
