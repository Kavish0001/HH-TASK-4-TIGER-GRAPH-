"""Tool descriptions, one source for both MCP routes.

server.py registers them on its own tools, and set_query_descriptions.py writes
them onto the installed GSQL queries so the official tigergraph-mcp server
(tigergraph__get_query_description) shows the model the same text.
"""

TOOL_DESCRIPTIONS = {
    "customer_profile": (
        "Baseline for one customer (an issuer account, not a person) up to as_of: cards held, transaction "
        "count, amount mean/median/p95/max, billing regions (addr1) with counts, product codes, device "
        "profiles with counts, online vs in_person split, first and last transaction. Call this first on "
        "any case to know what normal looks like before judging the flagged transaction."
    ),
    "tx_context": (
        "One transaction in full: amount, time, product code, channel, risk score, billing region, email "
        "domains, device profile with its parts, device status id_15 (New/Found), proxy id_23, plus the "
        "previous and next transaction on the same card (next only if before as_of). Use it to read the "
        "flagged transaction or any transaction id another tool returned. Returns an error if the "
        "transaction is later than as_of."
    ),
    "card_window": (
        "Transactions on one card in the last `hours` (or `days`) before as_of, oldest first, with "
        "amount, product, channel, device profile, device status, proxy and risk score; also count, total "
        "and how many were under $5. Keeps the latest 120. Use it to see the sequence around an alert, "
        "for example the small-authorizations-then-large-purchase shape of card testing (policy R5)."
    ),
    "velocity": (
        "Per look-back window in hours (for example [1, 24, 168]) on one card: count, summed and max "
        "amount, count under $5, and which device profiles, billing regions, product codes and recipient "
        "email domains appear in the window but never before it on this card. Use it to test for a burst "
        "or for first-seen infrastructure."
    ),
    "behavior_shift": (
        "How far the flagged transaction departs from this card's own history (falls back to the customer "
        "when the card has under 8 prior rows): amount z-score and percentile, whether the product code, "
        "device profile, billing region or channel is new, and whether the hour of day is unusual. Use it "
        "to separate an out-of-pattern purchase from routine spend."
    ),
    "shared_device_profile": (
        "Who else used one device profile: all-time card and customer counts, the share of its rows "
        "marked New, behind any proxy and behind an anonymous proxy, the cards that used it up to as_of "
        "with first and last seen, and closed cases on those cards with outcomes. Half of all profiles "
        "are shared, so read the signature, not the count: a complete profile that is New and anonymous- "
        "proxied on every row is a ring signal (policy R6); a generic browser string shared by hundreds "
        "is not."
    ),
    "region_history": (
        "Whether the customer has history in one billing region (addr1, for example 264 or '264.0'): "
        "count and date range there, the home region and its share, and how many home-region transactions "
        "happened during the same span. reads_as_trip and reads_as_clone summarise it. Use for "
        "out_of_region_use (pattern 4). A transaction with no addr1 has no region data, which is not the "
        "same as no history."
    ),
    "ring_detect": (
        "Connected component from a seed card or device profile over cards and the device profiles they "
        "used up to as_of. A profile links cards only when it is rare, complete and New/proxied, or New "
        "behind an anonymous proxy on most rows, so generic browser strings do not glue unrelated cards. "
        "Returns members with their prior case outcomes, ring size, distinct customers, the shared "
        "profiles and is_ring. Use when a device or trigger suggests several cards are connected; its "
        "card ids feed connected_card_ids."
    ),
    "prior_cases_for_entities": (
        "Closed cases (July to October history) and earlier agent-written cases that touch any of the "
        "given entities: card ids, customer ids (includes their other cards) or device profiles. Each "
        "case has outcome (confirmed_fraud or cleared), pattern, exposure, actions taken, SAR filed, "
        "analyst notes and txn ids. Only cases closed before as_of. Use it to learn how this card or "
        "customer was handled before."
    ),
    "similar_cases": (
        "Top-k past cases most like the current one: vector similarity of query_text (describe the case "
        "in words: pattern, channel, amounts, device) against closed-case notes and agent case summaries, "
        "blended with overlap on the given entity ids. Returns case id, similarity, blended score, "
        "outcome, pattern, exposure, txn ids and why it matched. Only cases visible at as_of. Use its "
        "case ids for similar_prior_cases."
    ),
    "pattern_match": (
        "Score one fraud pattern against the flagged transaction: pattern is one of card_testing, "
        "card_not_present_fraud, card_not_present_new_device, out_of_region_use, account_takeover, or "
        "undocumented_structuring (several online purchases just under $500 in an hour). Returns a 0 to 1 "
        "score, signals present, absent and uncheckable (for example no identity row, no region), "
        "coverage, supporting transaction ids and detail. Use it to confirm or reject a hypothesis before "
        "recommending an action."
    ),
    "policy_lookup": (
        "Top-k passages from the fraud policy (rules R1 to R10, actions, approval routing, stopping), the "
        "pattern definitions and the dataset README, each with a citable ref such as policy:R5. Use it "
        "before recommending an action, to cite the rule that permits it and its approval route."
    ),
    "write_case": (
        "Write the finished case to the graph as an InvestigationCase vertex, linked to its card, "
        "affected transactions, connected cards, device profiles and pattern. Ids that are not in the "
        "dataset are not linked and come back in ids_not_in_dataset. Returns graph_case_id, which goes in "
        "the answer file. Call once per case after the decision, then append_evidence, record_decision "
        "and link_similar."
    ),
    "append_evidence": (
        "Append one evidence item (source, ref, claim) to a written case. Returns ok."
    ),
    "record_decision": (
        "Record an action decision on a written case: action name, route (auto, L1, L2), whether policy "
        "authorised it, the reason and the actor. Blocked or recommended-only actions are recorded too, "
        "with executed=false. Returns ok and the decision id."
    ),
    "link_similar": (
        "Link a written case to earlier cases (closed case ids like CC-3748 or earlier HHG case ids) with "
        "a similarity score. Unknown ids are reported in not_found and never created. Returns ok and "
        "linked ids."
    ),
}

# Installed query behind each tool. pattern_match fans out to one matcher per pattern.
QUERIES_FOR_TOOL = {
    "customer_profile": ["customer_profile"],
    "tx_context": ["tx_context"],
    "card_window": ["card_window"],
    "velocity": ["velocity"],
    "behavior_shift": ["behavior_shift"],
    "shared_device_profile": ["shared_device_profile"],
    "region_history": ["region_history"],
    "ring_detect": ["ring_detect"],
    "prior_cases_for_entities": ["prior_cases_for_entities"],
    "similar_cases": ["similar_cases"],
    "pattern_match": ["match_card_testing", "match_card_not_present", "match_out_of_region",
                      "match_account_takeover", "match_structuring"],
    "policy_lookup": ["policy_search"],
    "write_case": ["write_case"],
    "append_evidence": ["append_evidence"],
    "record_decision": ["record_decision"],
    "link_similar": ["link_similar"],
}
