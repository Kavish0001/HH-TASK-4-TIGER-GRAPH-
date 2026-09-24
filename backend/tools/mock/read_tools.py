"""Mock implementations of the twelve read tools in backend/contracts/tools.md.

Shapes match the contract so swapping in graph-engineer's MCP tools is a config
change. All data comes from the real CSVs, so every ID returned is real. Every
tool honours `as_of`.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from backend.tools.base import ToolResult
from backend.tools.mock.store import Store, amount_stats, get_store

# A device profile is only evidence when few people use it. Beyond this many
# distinct customers the string is a browser fingerprint class, not a device.
RARE_DEVICE_MAX_CUSTOMERS = 8
RING_MAX_MEMBERS = 40


def _clean(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, float) and np.isnan(value):
        return None
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return round(float(value), 4)
    if isinstance(value, pd.Timestamp):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    if pd.isna(value) if not isinstance(value, (list, dict, str)) else False:
        return None
    return value


def _txn_row(store: Store, row: pd.Series, with_device: bool = True) -> dict[str, Any]:
    out = {
        "txn_id": str(int(row["TransactionID"])),
        "ts": _clean(row["ts"]),
        "amount": round(float(row["TransactionAmt"]), 2),
        "product_cd": _clean(row["ProductCD"]),
        "channel": _clean(row["channel"]),
        "risk_score": round(float(row["risk_score"]), 3),
        "card_id": _clean(row["card_id"]),
        "customer_id": _clean(row["customer_id"]),
        "addr1": _clean(row["addr1"]),
        "addr2": _clean(row["addr2"]),
        "p_email": _clean(row["P_emaildomain"]),
        "r_email": _clean(row["R_emaildomain"]),
    }
    if with_device:
        out["device_profile"] = store.device_profile_for(row["TransactionID"])
    return out


# ---------------------------------------------------------------------------


def customer_profile(store: Store, customer_id: str, as_of: str, **_: Any) -> ToolResult:
    ref = f"query:customer_profile(customer_id={customer_id}, as_of={as_of})"
    df = store.customer_txns(customer_id, as_of)
    if df.empty:
        return ToolResult(ok=True, ref=ref, data={"customer_id": customer_id, "found": False})

    addr_counts = df["addr1"].dropna().astype(float).value_counts().head(12)
    ident = store.identity[store.identity["TransactionID"].isin(set(df["TransactionID"]))]
    profiles = [p for p in ident["device_profile"].value_counts().head(15).items() if p[0]]

    data = {
        "customer_id": customer_id,
        "found": True,
        "cards": sorted(df["card_id"].dropna().unique().tolist()),
        "txn_count": int(len(df)),
        "amount_stats": amount_stats(df["TransactionAmt"].to_numpy()),
        "billing_regions": [{"addr1": str(k), "n": int(v)} for k, v in addr_counts.items()],
        "product_codes": df["ProductCD"].value_counts().to_dict(),
        "device_profiles": [{"profile": p, "n": int(n)} for p, n in profiles],
        "channel_split": df["channel"].value_counts().to_dict(),
        "first_txn": _clean(df["ts"].min()),
        "last_txn": _clean(df["ts"].max()),
        "mean_risk_score": round(float(df["risk_score"].mean()), 3),
    }
    return ToolResult(ok=True, ref=ref, data=data)


def tx_context(store: Store, txn_id: str, as_of: str, **_: Any) -> ToolResult:
    ref = f"query:tx_context(txn_id={txn_id}, as_of={as_of})"
    row = store.txn(txn_id)
    if row is None:
        return ToolResult(ok=False, ref=ref, error=f"transaction {txn_id} not in slice")

    card_hist = store.card_txns(str(row["card_id"]), as_of)
    ids = card_hist["TransactionID"].tolist()
    prev_txn = next_txn = None
    if int(row["TransactionID"]) in ids:
        pos = ids.index(int(row["TransactionID"]))
        if pos > 0:
            prev_txn = _txn_row(store, card_hist.iloc[pos - 1])
        if pos + 1 < len(ids):
            next_txn = _txn_row(store, card_hist.iloc[pos + 1])

    identity = store.identity_for(txn_id)
    data = {
        "transaction": _txn_row(store, row),
        "identity": {
            "device_type": identity.get("DeviceType"),
            "device_info": identity.get("DeviceInfo"),
            "os": identity.get("id_30"),
            "browser": identity.get("id_31"),
            "screen": identity.get("id_33"),
            # id_15 is the README's device New / Found field, the pattern 3 signal.
            "device_status": identity.get("id_15"),
            "proxy": identity.get("id_23"),
            "match_status": identity.get("id_34"),
            "device_rating": _clean(identity.get("id_01")),
        }
        if identity
        else {},
        "match_flags": {f"M{i}": _clean(row.get(f"M{i}")) for i in range(1, 10)},
        "day_deltas": {k: _clean(row.get(k)) for k in ("D1", "D2", "D3", "D4", "D10", "D15")},
        "counts": {k: _clean(row.get(k)) for k in ("C1", "C2", "C5", "C13", "C14")},
        "prev_txn": prev_txn,
        "next_txn": next_txn,
    }
    return ToolResult(ok=True, ref=ref, data=data)


def card_window(
    store: Store, card_id: str, as_of: str, hours: int | None = None, days: int | None = None, **_: Any
) -> ToolResult:
    span = f"hours={hours}" if hours else f"days={days or 30}"
    ref = f"query:card_window(card_id={card_id}, {span})"
    delta = pd.Timedelta(hours=hours) if hours else pd.Timedelta(days=days or 30)
    df = store.card_txns(card_id, as_of)
    cutoff = pd.Timestamp(as_of) - delta
    df = df[df["ts"] >= cutoff]
    rows = [_txn_row(store, r) for _, r in df.iterrows()]
    data = {
        "card_id": card_id,
        "window": span,
        "n": len(rows),
        "total_amount": round(float(df["TransactionAmt"].sum()), 2) if len(df) else 0.0,
        "transactions": rows[:120],
        "truncated": len(rows) > 120,
    }
    return ToolResult(ok=True, ref=ref, data=data)


def velocity(store: Store, card_id: str, windows: list[int], as_of: str, **_: Any) -> ToolResult:
    ref = f"query:velocity(card_id={card_id}, windows={windows})"
    hist = store.card_txns(card_id, as_of)
    now = pd.Timestamp(as_of)
    out = []
    for h in windows:
        cutoff = now - pd.Timedelta(hours=h)
        win = hist[hist["ts"] >= cutoff]
        before = hist[hist["ts"] < cutoff]
        ident_win = store.identity[store.identity["TransactionID"].isin(set(win["TransactionID"]))]
        ident_before = store.identity[
            store.identity["TransactionID"].isin(set(before["TransactionID"]))
        ]
        win_profiles = {p for p in ident_win["device_profile"] if p}
        old_profiles = {p for p in ident_before["device_profile"] if p}
        out.append(
            {
                "window_hours": h,
                "n": int(len(win)),
                "sum_amount": round(float(win["TransactionAmt"].sum()), 2),
                "n_small_under_5": int((win["TransactionAmt"] < 5).sum()),
                "max_amount": round(float(win["TransactionAmt"].max()), 2) if len(win) else 0.0,
                "first_seen_device_profile": sorted(win_profiles - old_profiles)[:5],
                "first_seen_billing_region": sorted(
                    {str(a) for a in win["addr1"].dropna()} - {str(a) for a in before["addr1"].dropna()}
                )[:5],
                "first_seen_product_cd": sorted(
                    set(win["ProductCD"].dropna()) - set(before["ProductCD"].dropna())
                ),
                "first_seen_recipient_email": sorted(
                    set(win["R_emaildomain"].dropna()) - set(before["R_emaildomain"].dropna())
                )[:5],
            }
        )
    return ToolResult(ok=True, ref=ref, data={"card_id": card_id, "windows": out, "history_n": int(len(hist))})


def behavior_shift(store: Store, customer_id: str, txn_id: str, as_of: str, **_: Any) -> ToolResult:
    ref = f"query:behavior_shift(customer_id={customer_id}, txn_id={txn_id})"
    row = store.txn(txn_id)
    if row is None:
        return ToolResult(ok=False, ref=ref, error=f"transaction {txn_id} not in slice")

    # Baseline is everything on the card before the flagged transaction. Some
    # `customer_id` values in this dataset hold ten thousand transactions, which
    # makes a customer-level baseline meaningless, so card level comes first and
    # the tool says which one it used.
    card_hist = store.card_txns(str(row["card_id"]), as_of)
    card_hist = card_hist[card_hist["TransactionID"] != int(row["TransactionID"])]
    scope = "card"
    if len(card_hist) < 8:
        card_hist = store.customer_txns(customer_id, as_of)
        card_hist = card_hist[card_hist["TransactionID"] != int(row["TransactionID"])]
        scope = "customer"

    amt = float(row["TransactionAmt"])
    if len(card_hist) >= 3:
        base = card_hist["TransactionAmt"].to_numpy()
        mean, std = float(np.mean(base)), float(np.std(base))
        z = (amt - mean) / std if std > 1e-6 else 0.0
        pct = float((base < amt).mean())
    else:
        mean = std = z = 0.0
        pct = 0.5

    ident_hist = store.identity[store.identity["TransactionID"].isin(set(card_hist["TransactionID"]))]
    known_profiles = {p for p in ident_hist["device_profile"] if p}
    this_profile = store.device_profile_for(txn_id)

    hours = card_hist["ts"].dt.hour.to_numpy() if len(card_hist) else np.array([])
    this_hour = int(pd.Timestamp(row["ts"]).hour)
    hour_share = float((hours == this_hour).mean()) if len(hours) else 0.0

    data = {
        "baseline_scope": scope,
        "baseline_n": int(len(card_hist)),
        "amount": round(amt, 2),
        "baseline_mean": round(mean, 2),
        "baseline_std": round(std, 2),
        "amount_z": round(z, 2),
        "amount_percentile": round(pct, 3),
        "new_product_cd": bool(row["ProductCD"] not in set(card_hist["ProductCD"].dropna())),
        "new_device_profile": bool(this_profile and this_profile not in known_profiles),
        "device_profile": this_profile,
        "new_billing_region": bool(
            pd.notna(row["addr1"])
            and str(float(row["addr1"])) not in {str(float(a)) for a in card_hist["addr1"].dropna()}
        ),
        "hour_of_day": this_hour,
        "hour_share_in_history": round(hour_share, 3),
        "unusual_hour": bool(hour_share < 0.02 and len(hours) >= 20),
        "new_channel": bool(row["channel"] not in set(card_hist["channel"].dropna())),
    }
    return ToolResult(ok=True, ref=ref, data=data)


def shared_device_profile(store: Store, device_profile: str, as_of: str, **_: Any) -> ToolResult:
    ref = f"query:shared_device_profile(device_profile={device_profile})"
    if not device_profile:
        return ToolResult(ok=True, ref=ref, data={"device_profile": "", "found": False})

    n_cards_all, n_cust_all = store.device_profile_breadth(device_profile)
    df = store.txns_on_device(device_profile, as_of)
    per_card = (
        df.groupby(["card_id", "customer_id"])
        .agg(n=("TransactionID", "size"), first_seen=("ts", "min"), last_seen=("ts", "max"))
        .reset_index()
        .sort_values("first_seen")
    )
    cards = [
        {
            "card_id": r["card_id"],
            "customer_id": r["customer_id"],
            "n": int(r["n"]),
            "first_seen": _clean(r["first_seen"]),
            "last_seen": _clean(r["last_seen"]),
        }
        for _, r in per_card.head(60).iterrows()
    ]
    entity_ids = [c["card_id"] for c in cards] + [c["customer_id"] for c in cards]
    prior = _prior_cases(store, entity_ids, as_of)

    data = {
        "device_profile": device_profile,
        "found": True,
        "n_cards": n_cards_all,
        "n_customers": n_cust_all,
        # The contract's shared-origin signal only means something if the
        # profile is rare. A generic browser string is not a shared device.
        "is_rare": bool(0 < n_cust_all <= RARE_DEVICE_MAX_CUSTOMERS),
        "rarity_threshold_customers": RARE_DEVICE_MAX_CUSTOMERS,
        "cards": cards,
        "prior_cases": prior,
        "confirmed_fraud_prior_cases": [p["case_id"] for p in prior if p["outcome"] == "confirmed_fraud"],
    }
    return ToolResult(ok=True, ref=ref, data=data)


def region_history(store: Store, customer_id: str, addr1: str, as_of: str, **_: Any) -> ToolResult:
    ref = f"query:region_history(customer_id={customer_id}, addr1={addr1})"
    hist = store.customer_txns(customer_id, as_of)
    if hist.empty or addr1 in (None, "", "nan"):
        return ToolResult(ok=True, ref=ref, data={"customer_id": customer_id, "addr1": addr1, "found": False})

    target = float(addr1)
    in_region = hist[hist["addr1"].astype(float) == target]
    counts = hist["addr1"].dropna().astype(float).value_counts()
    home = float(counts.index[0]) if len(counts) else None

    home_during = 0
    if home is not None and len(in_region):
        lo, hi = in_region["ts"].min(), in_region["ts"].max()
        same_window = hist[(hist["ts"] >= lo) & (hist["ts"] <= hi)]
        home_during = int((same_window["addr1"].astype(float) == home).sum())

    span_days = 0.0
    if len(in_region) > 1:
        span_days = round((in_region["ts"].max() - in_region["ts"].min()).total_seconds() / 86400, 2)

    data = {
        "customer_id": customer_id,
        "addr1": str(target),
        "found": True,
        "has_history": bool(len(in_region) > 0),
        "n_in_region": int(len(in_region)),
        "first_in_region": _clean(in_region["ts"].min()) if len(in_region) else None,
        "last_in_region": _clean(in_region["ts"].max()) if len(in_region) else None,
        "span_days": span_days,
        "home_region": str(home) if home is not None else None,
        "home_share": round(float(counts.iloc[0] / counts.sum()), 3) if len(counts) else 0.0,
        "home_activity_during_window": home_during,
        # Several days in one new region while home goes quiet reads as a trip.
        # Same-day spending in two regions reads as a clone.
        "reads_as_trip": bool(span_days >= 2 and home_during == 0 and len(in_region) >= 2),
        "reads_as_clone": bool(home_during > 0 and len(in_region) >= 1 and span_days < 2),
    }
    return ToolResult(ok=True, ref=ref, data=data)


def ring_detect(
    store: Store, as_of: str, card_id: str | None = None, device_profile: str | None = None, **_: Any
) -> ToolResult:
    seed = card_id or device_profile or ""
    ref = f"query:ring_detect(seed={seed})"

    # Bounded expansion rather than a component over the whole graph: edges are
    # only followed through rare shared elements, because the common ones
    # connect hundreds of unrelated customers and would return a useless blob.
    profiles: set[str] = set()
    if device_profile:
        profiles.add(device_profile)
    if card_id:
        hist = store.card_txns(card_id, as_of)
        ident = store.identity[store.identity["TransactionID"].isin(set(hist["TransactionID"]))]
        profiles |= {p for p in ident["device_profile"] if p}

    rare_profiles = [p for p in profiles if 0 < store.device_profile_breadth(p)[1] <= RARE_DEVICE_MAX_CUSTOMERS]

    members: dict[str, dict[str, Any]] = {}
    shared_elements: list[dict[str, Any]] = []
    for prof in rare_profiles:
        df = store.txns_on_device(prof, as_of)
        if df.empty:
            continue
        member_cards = sorted(df["card_id"].dropna().unique().tolist())
        shared_elements.append({"type": "device_profile", "value": prof, "cards": member_cards})
        for _, r in df.iterrows():
            cid = str(r["card_id"])
            entry = members.setdefault(
                cid, {"card_id": cid, "customer_id": str(r["customer_id"]), "shared": [], "n": 0}
            )
            entry["n"] += 1
            if prof not in entry["shared"]:
                entry["shared"].append(prof)

    if card_id and card_id not in members:
        hist = store.card_txns(card_id, as_of)
        if not hist.empty:
            members[card_id] = {
                "card_id": card_id,
                "customer_id": str(hist.iloc[0]["customer_id"]),
                "shared": [],
                "n": int(len(hist)),
            }

    member_list = sorted(members.values(), key=lambda m: -m["n"])[:RING_MAX_MEMBERS]
    entity_ids = [m["card_id"] for m in member_list] + [m["customer_id"] for m in member_list]
    prior = _prior_cases(store, entity_ids, as_of)
    for m in member_list:
        m["prior_case_outcomes"] = [
            p["outcome"] for p in prior if p["card_id"] == m["card_id"] or p["customer_id"] == m["customer_id"]
        ]

    distinct_customers = len({m["customer_id"] for m in member_list})
    data = {
        "seed": seed,
        "ring_size": len(member_list),
        "distinct_customers": distinct_customers,
        "members": member_list,
        "shared_elements": shared_elements,
        "prior_cases": prior,
        "confirmed_fraud_members": sum(1 for m in member_list if "confirmed_fraud" in m["prior_case_outcomes"]),
        # One card on a rare device is not a ring.
        "is_ring": bool(distinct_customers >= 2 and len(member_list) >= 2),
    }
    return ToolResult(ok=True, ref=ref, data=data)


def _prior_cases(store: Store, entity_ids: list[str], as_of: str) -> list[dict[str, Any]]:
    cc = store.closed_cases_before(as_of)
    if cc.empty or not entity_ids:
        return []
    wanted = set(entity_ids)
    mask = cc["card_id"].isin(wanted) | cc["customer_id"].isin(wanted)
    conn = cc["connected_list"].apply(lambda lst: any(c in wanted for c in lst))
    hits = cc[mask | conn]
    out = []
    for _, r in hits.head(25).iterrows():
        out.append(
            {
                "case_id": r["case_id"],
                "customer_id": r["customer_id"],
                "card_id": r["card_id"],
                "outcome": r["outcome"],
                "pattern": r["pattern"],
                "exposure_usd": float(r["exposure_usd"]),
                "n_txns": int(r["n_txns"]),
                "closed_at": str(r["closed_at"]),
                "actions_taken": r["actions_list"],
                "report_filed": r["report_filed"],
                "analyst_notes": str(r["analyst_notes"])[:600],
            }
        )
    return out


def prior_cases_for_entities(store: Store, entity_ids: list[str], as_of: str, **_: Any) -> ToolResult:
    ref = f"query:prior_cases_for_entities(n_entities={len(entity_ids)}, as_of={as_of})"
    cases = _prior_cases(store, entity_ids, as_of)
    confirmed = [c for c in cases if c["outcome"] == "confirmed_fraud"]
    data = {
        "n": len(cases),
        "cases": cases,
        "confirmed_fraud_rate": round(len(confirmed) / len(cases), 3) if cases else 0.0,
        "patterns": sorted({c["pattern"] for c in confirmed}),
    }
    return ToolResult(ok=True, ref=ref, data=data)


def pattern_match(store: Store, pattern: str, card_id: str, txn_id: str, as_of: str, **_: Any) -> ToolResult:
    """Scores one of the five documented patterns against the card's history.

    Returns which required signals were present and which were absent, because
    the sufficiency checklist in backend/scoring drives whether the agent may
    act or must first request evidence.
    """
    from backend.scoring.patterns import score_pattern  # local import avoids a cycle

    ref = f"query:pattern_match(pattern={pattern}, card_id={card_id}, txn_id={txn_id})"
    row = store.txn(txn_id)
    if row is None:
        return ToolResult(ok=False, ref=ref, error=f"transaction {txn_id} not in slice")
    result = score_pattern(store, pattern, card_id, txn_id, as_of)
    return ToolResult(ok=True, ref=ref, data=result)


def similar_cases(
    store: Store, query_text: str, entity_ids: list[str], k: int, as_of: str, **_: Any
) -> ToolResult:
    from backend.graphrag.retriever import retrieve_similar_cases

    ref = f"query:similar_cases(k={k}, as_of={as_of})"
    hits = retrieve_similar_cases(query_text=query_text, entity_ids=entity_ids, k=k, as_of=as_of)
    return ToolResult(ok=True, ref=ref, data={"n": len(hits), "cases": hits})


def policy_lookup(query: str, k: int = 4, **_: Any) -> ToolResult:
    from backend.graphrag.retriever import retrieve_policy

    ref = f"doc:policy_lookup(query={query[:60]}, k={k})"
    chunks = retrieve_policy(query, k)
    return ToolResult(ok=True, ref=ref, data={"n": len(chunks), "chunks": chunks})


READ_DISPATCH = {
    "customer_profile": customer_profile,
    "tx_context": tx_context,
    "card_window": card_window,
    "velocity": velocity,
    "behavior_shift": behavior_shift,
    "shared_device_profile": shared_device_profile,
    "region_history": region_history,
    "ring_detect": ring_detect,
    "prior_cases_for_entities": prior_cases_for_entities,
    "pattern_match": pattern_match,
    "similar_cases": similar_cases,
}
