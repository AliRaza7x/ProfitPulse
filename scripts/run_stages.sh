#!/usr/bin/env bash
# Run the pipeline stages by hand, without Airflow (development / debugging).
# Airflow runs the same stage modules; see airflow/dags/.
#
#   scripts/run_stages.sh                      # full replay, no faults
#   scripts/run_stages.sh --corrupt-rate 0.01  # extra args go to the producer
#   SKIP_PUBLISH=1 scripts/run_stages.sh       # Kafka already holds the events
set -uo pipefail
cd "$(dirname "$0")/.."

run() { docker compose --env-file .env run --rm tools python -m profitpulse "$@"; }

# Run a stage, show filtered output, stop the whole script on failure.
timed() {
  local label="$1"; shift
  local t0 rc=0 out; t0=$(date +%s); out=$(mktemp)
  "$@" > "$out" 2>&1 || rc=$?
  grep -vE 'Container|Volume|WARN|log4j|Stage|setLogLevel|py4j|incubator' "$out" | cut -c1-400
  rm -f "$out"
  echo "   [$label: $(( $(date +%s) - t0 ))s, exit $rc]"
  [ "$rc" -eq 0 ] || { echo "STAGE FAILED: $label"; exit "$rc"; }
}

docker compose --env-file .env up -d postgres kafka
until [ "$(docker inspect -f '{{.State.Health.Status}}' profitpulse-kafka 2>/dev/null)" = "healthy" ]; do sleep 3; done
until [ "$(docker inspect -f '{{.State.Health.Status}}' profitpulse-postgres 2>/dev/null)" = "healthy" ]; do sleep 2; done

timed migrate           run migrate
timed topics            run topics ensure
[ -n "${SKIP_PUBLISH:-}" ] || timed publish run publish --record-run "$@"
timed ingest            run ingest
timed clean             run clean
timed transform         run transform
timed load              run load
timed features          run features
timed analytics         run analytics
timed publish-analytics run publish-analytics
