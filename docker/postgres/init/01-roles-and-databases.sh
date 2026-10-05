#!/bin/bash
# Runs once on first container start (empty data volume). Creates the pipeline
# and read-only roles, plus the separate Airflow metadata database.
# All names and passwords come from environment variables (see .env.example).
set -euo pipefail

psql -v ON_ERROR_STOP=1 \
     --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
     -v app_user="$PP_APP_USER"       -v app_pw="$PP_APP_PASSWORD" \
     -v reader_user="$PP_READER_USER" -v reader_pw="$PP_READER_PASSWORD" \
     -v af_user="$AIRFLOW_DB_USER"    -v af_pw="$AIRFLOW_DB_PASSWORD" \
     -v af_db="$AIRFLOW_DB_NAME"      -v main_db="$POSTGRES_DB" <<'EOSQL'

SELECT format('CREATE ROLE %I LOGIN PASSWORD %L', :'app_user', :'app_pw') \gexec
SELECT format('CREATE ROLE %I LOGIN PASSWORD %L', :'reader_user', :'reader_pw') \gexec
SELECT format('CREATE ROLE %I LOGIN PASSWORD %L', :'af_user', :'af_pw') \gexec

-- The pipeline role creates and owns the analytical schemas.
SELECT format('GRANT CONNECT, CREATE ON DATABASE %I TO %I', :'main_db', :'app_user') \gexec
SELECT format('GRANT CONNECT ON DATABASE %I TO %I', :'main_db', :'reader_user') \gexec

SELECT format('CREATE DATABASE %I OWNER %I', :'af_db', :'af_user') \gexec
EOSQL
