#!/bin/bash
# Runs once, the first time the database volume is created.
# Creates a second, empty database that the tests use.
set -e
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
  -c "CREATE DATABASE fx_explorer_test OWNER \"$POSTGRES_USER\";"
