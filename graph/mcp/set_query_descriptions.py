#!/usr/bin/env python3
"""Write the tool descriptions onto the installed GSQL queries.

The official tigergraph-mcp server lists installed queries and shows their
stored description (tigergraph__get_query_description). Without this step a
model on that route sees bare query names. Run after install_all.sh.
"""

from __future__ import annotations

import os
import sys

from pyTigerGraph import TigerGraphConnection

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from descriptions import QUERIES_FOR_TOOL, TOOL_DESCRIPTIONS  # noqa: E402

PATTERN_NOTE = {
    "match_card_testing": "Matcher for pattern card_testing (policy R5). ",
    "match_card_not_present": "Matcher for card_not_present_fraud; set require_new_device=true for "
    "card_not_present_new_device. ",
    "match_out_of_region": "Matcher for out_of_region_use. ",
    "match_account_takeover": "Matcher for account_takeover. ",
    "match_structuring": "Matcher for the undocumented structuring pattern (several online purchases just "
    "under $500 in an hour). ",
}


def main() -> None:
    conn = TigerGraphConnection(
        host=os.environ.get("TG_HOST", "http://localhost"),
        graphname=os.environ.get("TG_GRAPH_NAME", "FraudInvestigation"),
        username=os.environ.get("TG_USERNAME", "tigergraph"),
        password=os.environ.get("TG_PASSWORD") or "tigergraph",
        restppPort=os.environ.get("TG_RESTPP_PORT", "14240"),
        gsPort=os.environ.get("TG_GSQL_PORT", "14240"),
    )
    for tool, queries in QUERIES_FOR_TOOL.items():
        for q in queries:
            text = PATTERN_NOTE.get(q, "") + TOOL_DESCRIPTIONS[tool]
            try:
                conn.updateQueryDescription(q, text, {})
                print(f"ok   {q}")
            except Exception as exc:  # keep going; one missing query should not hide the rest
                print(f"FAIL {q}: {exc}")


if __name__ == "__main__":
    main()
