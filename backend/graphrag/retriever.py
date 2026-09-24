"""Retrieval: vector top-k plus graph expansion, deduplicated and budgeted.

Two halves, as the contract describes. The vector half finds text that reads
like this case. The graph half finds cases that touch the same card, customer or
device profile, which is usually the one that actually solves a case. They are
blended, deduplicated by ref and trimmed to a token budget before anything
reaches a prompt.

Raw rows never enter the context block. Every line carries a `ref` so the final
explanation can cite it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from backend.config import get_settings
from backend.graphrag.index import Hit, get_index
from backend.memory.case_memory import get_case_memory

# How much a shared entity is worth against pure text similarity. A prior case
# on the same card is more useful than a prior case that merely reads alike.
ENTITY_BONUS = 0.45
MAX_ENTITY_BONUS = 0.9


@dataclass
class ContextBlock:
    text: str
    refs: list[str]
    chunks: list[dict[str, Any]]
    truncated: bool


def retrieve_policy(query: str, k: int = 4) -> list[dict[str, Any]]:
    hits = get_index().search(query, k=k, kinds=("policy", "pattern"))
    return [{"ref": h.ref, "text": h.text, "score": h.score, "kind": h.kind} for h in hits]


def retrieve_similar_cases(
    query_text: str, entity_ids: list[str], k: int = 5, as_of: str | None = None
) -> list[dict[str, Any]]:
    """Vector top-k over closed-case text blended with entity overlap.

    `as_of` is enforced twice: a closed case is only visible once it closed, and
    a case this agent wrote is only visible once it was written. Both are the
    same rule, that an investigation cannot read the future.
    """
    index = get_index()
    wanted = set(entity_ids)
    # Over-fetch, because the entity blend reorders and a case that matters may
    # sit outside the pure-text top k.
    raw: list[Hit] = index.search(query_text, k=max(k * 6, 30), kinds=("closed_case",))

    scored: list[dict[str, Any]] = []
    for hit in raw:
        meta = hit.metadata
        if as_of and str(meta.get("closed_at", "")) > str(as_of):
            continue
        overlap = wanted & set(meta.get("entities", []))
        bonus = min(ENTITY_BONUS * len(overlap), MAX_ENTITY_BONUS)
        scored.append(
            {
                "case_id": meta.get("case_id"),
                "similarity": round(hit.score, 4),
                "blended_score": round(hit.score + bonus, 4),
                "outcome": meta.get("outcome"),
                "pattern": meta.get("pattern"),
                "exposure_usd": meta.get("exposure_usd"),
                "closed_at": meta.get("closed_at"),
                "connected_card_ids": meta.get("connected_card_ids", []),
                "txn_ids": meta.get("txn_ids", []),
                "why_matched": (
                    f"shares {len(overlap)} entity/entities: {', '.join(sorted(overlap)[:4])}"
                    if overlap
                    else "text similarity to the analyst notes"
                ),
                "ref": hit.ref,
                "text": hit.text,
                "source": "closed_case",
            }
        )

    # Cases this agent has already closed become memory for later ones.
    for record in get_case_memory().for_entities(entity_ids, as_of or "9999"):
        scored.append(
            {
                "case_id": record.case_id,
                "similarity": 0.0,
                "blended_score": round(MAX_ENTITY_BONUS, 4),
                "outcome": record.outcome,
                "pattern": record.pattern,
                "exposure_usd": record.exposure_usd,
                "closed_at": record.closed_at,
                "connected_card_ids": record.connected_card_ids,
                "txn_ids": record.txn_ids,
                "why_matched": "a case this agent closed earlier on a shared entity",
                "ref": f"case:{record.graph_case_id}",
                "text": record.as_text(),
                "source": "agent_memory",
            }
        )

    scored.sort(key=lambda d: -d["blended_score"])
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for item in scored:
        if item["ref"] in seen:
            continue
        seen.add(item["ref"])
        out.append(item)
        if len(out) >= k:
            break
    return out


def build_context_block(
    policy_chunks: list[dict[str, Any]],
    case_chunks: list[dict[str, Any]],
    token_budget: int | None = None,
) -> ContextBlock:
    """One deduplicated, cited, budgeted block.

    Budget is counted in characters at four per token, which is close enough and
    does not need a tokenizer for a provider we may swap.
    """
    budget = (token_budget or get_settings().graphrag_token_budget) * 4
    lines: list[str] = []
    refs: list[str] = []
    kept: list[dict[str, Any]] = []
    used = 0
    truncated = False

    ordered = [("policy", c) for c in policy_chunks] + [("case", c) for c in case_chunks]
    seen: set[str] = set()
    for kind, chunk in ordered:
        ref = chunk["ref"]
        if ref in seen:
            continue
        seen.add(ref)
        body = " ".join(str(chunk.get("text", "")).split())
        entry = f"[{ref}] {body}"
        if used + len(entry) > budget:
            room = budget - used
            if room < 200:
                truncated = True
                break
            entry = entry[:room] + " ..."
            truncated = True
        lines.append(entry)
        refs.append(ref)
        kept.append({"ref": ref, "kind": kind, "score": chunk.get("score") or chunk.get("blended_score", 0.0)})
        used += len(entry)
        if truncated:
            break

    return ContextBlock(text="\n\n".join(lines), refs=refs, chunks=kept, truncated=truncated)
