#!/bin/sh
# Applies any pending Alembic migrations before the app starts serving
# (TD-04) — the container-native equivalent of an operator remembering to
# run `flask db upgrade` before restarting the service. db.create_all() in
# app.py still runs as a zero-config fallback, but a real deployment's
# schema history should come from migrations/, not that fallback.
set -e

export FLASK_APP=app.py
flask db upgrade

exec "$@"
