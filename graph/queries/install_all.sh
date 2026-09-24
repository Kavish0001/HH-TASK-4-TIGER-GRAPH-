#!/usr/bin/env bash
# Create and install every FraudInvestigation query in the running container.
#
# Usage, from the repo root:   bash graph/queries/install_all.sh
#
# I copy the files in and run gsql inside the container, rather than use a
# host gsql client, because the container already has the matching 4.2.5
# client and the host usually has none. Files run in name order: 01 to 04 are
# the read tools, 05 the writers. CREATE OR REPLACE makes a rerun safe.
# INSTALL QUERY ALL takes 3 to 6 minutes on this box.

set -euo pipefail

CONTAINER="${TG_CONTAINER:-hhgoa-tigergraph}"
GSQL=/home/tigergraph/tigergraph/app/cmd/gsql
HERE="$(cd "$(dirname "$0")" && pwd)"

# Git Bash on Windows rewrites /home/... arguments into C:/Program Files/Git/...
export MSYS_NO_PATHCONV=1

src="$HERE"
if command -v cygpath >/dev/null 2>&1; then src="$(cygpath -w "$HERE")"; fi

docker exec "$CONTAINER" mkdir -p /tmp/fi_queries
for f in "$HERE"/0*.gsql; do
  base="$(basename "$f")"
  docker cp "$src/$base" "$CONTAINER:/tmp/fi_queries/$base" >/dev/null
  echo "== $base"
  docker exec -u tigergraph "$CONTAINER" "$GSQL" "/tmp/fi_queries/$base" | grep -v -i "warn\|comparison\|epsilon\|such\|numeric values" || true
done

# The analytics layer needs graph/schema/schema_analytics.gsql applied first
# (it adds SHARES_DEVICE and the Card result attributes). Without it these two
# files fail to create and the read tools above are unaffected.
ALG="$HERE/../algorithms"
alg_src="$ALG"
if command -v cygpath >/dev/null 2>&1; then alg_src="$(cygpath -w "$ALG")"; fi
for base in install_gds.gsql card_community.gsql; do
  docker cp "$alg_src/$base" "$CONTAINER:/tmp/fi_queries/$base" >/dev/null
  echo "== $base"
  docker exec -u tigergraph "$CONTAINER" "$GSQL" "/tmp/fi_queries/$base" | grep -i "created\|error" || true
done

echo "== INSTALL QUERY ALL"
docker exec -u tigergraph "$CONTAINER" "$GSQL" -g FraudInvestigation "INSTALL QUERY ALL"
docker exec -u tigergraph "$CONTAINER" "$GSQL" -g FraudInvestigation "SHOW QUERY *" | grep -c "CREATE QUERY" || true
