#!/usr/bin/env bash
# Build the card projection and run the GDS algorithms on it.
#
# Usage, from the repo root:  bash graph/algorithms/run_analytics.sh
# Prerequisites: graph/schema/schema_analytics.gsql has been run once, and
# install_gds.gsql and card_community.gsql are created and installed
# (INSTALL QUERY ALL). build_card_links.gsql runs interpreted, see its header.
#
# Results land on Card attributes wcc_component, louvain_community and
# device_pagerank. card_community(card_id) reads them back.

set -euo pipefail
R="${TG_HOST:-http://localhost}:${TG_RESTPP_PORT:-14240}/restpp/query/FraudInvestigation"
A="-u ${TG_USERNAME:-tigergraph}:${TG_PASSWORD:-tigergraph}"

export MSYS_NO_PATHCONV=1
HERE="$(cd "$(dirname "$0")" && pwd)"
src="$HERE"; if command -v cygpath >/dev/null 2>&1; then src="$(cygpath -w "$HERE")"; fi
echo "== build_card_links (history only, before 2016-11-01)"
docker cp "$src/build_card_links.gsql" "${TG_CONTAINER:-hhgoa-tigergraph}:/tmp/build_card_links.gsql" >/dev/null
docker exec -u tigergraph "${TG_CONTAINER:-hhgoa-tigergraph}" /home/tigergraph/tigergraph/app/cmd/gsql /tmp/build_card_links.gsql | tail -8

echo "== tg_wcc on Card via SHARES_DEVICE"
curl -s $A "$R/tg_wcc?v_type_set=Card&e_type_set=SHARES_DEVICE&print_limit=0&print_results=false&result_attribute=wcc_component" | head -c 400; echo

echo "== tg_louvain on Card via SHARES_DEVICE"
curl -s $A "$R/tg_louvain?v_type_set=Card&e_type_set=SHARES_DEVICE&weight_attribute=weight&result_attribute=louvain_community&print_stats=true" | head -c 600; echo

echo "== tg_pagerank on Card via SHARES_DEVICE"
curl -s $A "$R/tg_pagerank?v_type=Card&e_type=SHARES_DEVICE&top_k=10&print_results=true&result_attribute=device_pagerank" | head -c 1200; echo
