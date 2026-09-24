"""Pattern matchers, in plain Python.

The LLM does not compute these. Each matcher returns a score, the signals it
found, the signals it looked for and did not find, and the signals it could not
check at all. That last category matters: `addr1` is null on 94.8 percent of
ProductCD C rows, so "no region data" and "no history in this region" are
different facts and must not collapse into one branch.

Calibration notes, from the measured base rates in plans/findings/dataset.md:

- R5's literal "under $5" fires 53 times in six months and only 9.4 percent of
  those touch confirmed fraud, while the 16 labelled card_testing cases have a
  median amount of $28.49 and only 8.2 percent under $5. So the policy text is
  cited as written, but the detector is card-relative and is suppressed on cards
  that produce such runs habitually.
- Half of all device profiles are shared by more than one card and the largest
  is shared by 1,013, so breadth alone is not evidence. Novelty and proxy
  concentration on the profile are what separate a ring from a browser class.
- 716 of the 900 cleared cases were a travelling cardholder and 158 were a new
  phone. A matcher that treats a new region or a New device as proof fails on
  the ambiguous half of the pack by construction.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from backend.models.enums import Pattern
from backend.tools.mock.store import Store

# Literal R5 thresholds, kept so the policy citation is honest.
R5_SMALL_USD = 5.0
R5_LARGE_USD = 25.0
R5_BLOCK_USD = 100.0
R5_WINDOW_HOURS = 1
R5_FOLLOW_HOURS = 24

# Episode window: how far back from the flagged transaction the agent treats
# activity as part of the same episode.
EPISODE_HOURS = 48

# Structuring signature from the five undocumented closed cases.
STRUCTURING_MIN = 400.0
STRUCTURING_MAX = 500.0
STRUCTURING_N = 4
STRUCTURING_WINDOW_MIN = 60

UNCHECKABLE = "uncheckable"


@dataclass
class PatternScore:
    pattern: str
    score: float
    present_signals: list[str] = field(default_factory=list)
    absent_signals: list[str] = field(default_factory=list)
    uncheckable_signals: list[str] = field(default_factory=list)
    supporting_txn_ids: list[str] = field(default_factory=list)
    detail: dict[str, Any] = field(default_factory=dict)
    narrative: str = ""

    @property
    def required_signals(self) -> list[str]:
        return self.present_signals + self.absent_signals + self.uncheckable_signals

    @property
    def coverage(self) -> float:
        """Share of this pattern's required signals the agent could actually check."""
        total = len(self.required_signals)
        if total == 0:
            return 0.0
        return round((total - len(self.uncheckable_signals)) / total, 3)

    def to_dict(self) -> dict[str, Any]:
        return {
            "pattern": self.pattern,
            "score": round(self.score, 3),
            "present_signals": self.present_signals,
            "absent_signals": self.absent_signals,
            "uncheckable_signals": self.uncheckable_signals,
            "coverage": self.coverage,
            "supporting_txn_ids": self.supporting_txn_ids,
            "detail": self.detail,
            "narrative": self.narrative,
        }


# ---------------------------------------------------------------------------
# helpers


def _episode_window(hist: pd.DataFrame, flagged_ts: pd.Timestamp, hours: int = EPISODE_HOURS) -> pd.DataFrame:
    return hist[(hist["ts"] >= flagged_ts - pd.Timedelta(hours=hours)) & (hist["ts"] <= flagged_ts)]


def _ids(df: pd.DataFrame) -> list[str]:
    return [str(int(t)) for t in df["TransactionID"]]


def _find_testing_sequences(
    online: pd.DataFrame, small_thr: float, large_thr: float
) -> list[dict[str, Any]]:
    """Runs of three or more small online authorizations inside an hour that are
    followed by a larger purchase on the same card."""
    if online.empty:
        return []
    small = online[online["TransactionAmt"] < small_thr].sort_values("ts")
    out: list[dict[str, Any]] = []
    times = small["ts"].to_numpy()
    i = 0
    while i < len(small):
        end = times[i] + np.timedelta64(R5_WINDOW_HOURS, "h")
        j = i
        while j < len(small) and times[j] <= end:
            j += 1
        if j - i >= 3:
            run = small.iloc[i:j]
            after = online[
                (online["ts"] > run["ts"].iloc[-1])
                & (online["ts"] <= run["ts"].iloc[-1] + pd.Timedelta(hours=R5_FOLLOW_HOURS))
                & (online["TransactionAmt"] >= large_thr)
            ]
            if not after.empty:
                follow = after.iloc[0]
                out.append(
                    {
                        "run_txn_ids": _ids(run),
                        "run_amounts": [round(float(a), 2) for a in run["TransactionAmt"]],
                        "run_start": str(run["ts"].iloc[0]),
                        "follow_txn_id": str(int(follow["TransactionID"])),
                        "follow_amount": round(float(follow["TransactionAmt"]), 2),
                        "follow_ts": str(follow["ts"]),
                        "end_ts": follow["ts"],
                    }
                )
            i = j
        else:
            i += 1
    return out


# ---------------------------------------------------------------------------
# matchers


def match_card_testing(store: Store, card_id: str, txn_id: str, as_of: str) -> PatternScore:
    row = store.txn(txn_id)
    hist = store.card_txns(card_id, as_of)
    flagged_ts = pd.Timestamp(row["ts"])
    online = hist[hist["channel"] == "online"]

    present: list[str] = []
    absent: list[str] = []

    if online.empty:
        return PatternScore(
            pattern=Pattern.CARD_TESTING.value,
            score=0.0,
            absent_signals=["online_authorizations", "small_auth_run", "escalation_purchase", "run_is_unusual_for_card"],
            narrative="No online activity on this card, so the R5 sequence cannot exist.",
        )
    present.append("online_authorizations")

    # Card-relative "small". The labelled cases used a run of amounts small for
    # that card, not an absolute $5 cut.
    p10 = float(np.percentile(online["TransactionAmt"], 10))
    relative_small = max(R5_SMALL_USD, min(p10, 30.0))
    literal = _find_testing_sequences(online, R5_SMALL_USD, R5_LARGE_USD)
    relative = _find_testing_sequences(online, relative_small, max(relative_small * 4, R5_LARGE_USD))

    recent = [s for s in relative if flagged_ts - pd.Timedelta(hours=EPISODE_HOURS) <= s["end_ts"] <= flagged_ts]
    older = [s for s in relative if s["end_ts"] < flagged_ts - pd.Timedelta(hours=EPISODE_HOURS)]

    if recent:
        present.append("small_auth_run")
        present.append("escalation_purchase")
    else:
        absent.append("small_auth_run")
        absent.append("escalation_purchase")

    # The trap: a card that has produced this shape repeatedly for months is
    # showing its own normal behaviour, not a stolen number being tested.
    habitual = len(older) >= 2
    if recent and not habitual:
        present.append("run_is_unusual_for_card")
    else:
        absent.append("run_is_unusual_for_card")

    score = 0.0
    supporting: list[str] = []
    if recent:
        seq = recent[-1]
        supporting = seq["run_txn_ids"] + [seq["follow_txn_id"]]
        score = 0.62
        if any(s in literal for s in recent) or literal:
            score += 0.10  # the literal R5 shape is present too
        if seq["follow_amount"] >= R5_BLOCK_USD:
            score += 0.08
        if habitual:
            # Eleven such sequences across six months on one card is that card's
            # baseline. Suppress hard rather than firing on a routine week.
            score = min(score, 0.18)
    detail = {
        "relative_small_threshold": round(relative_small, 2),
        "literal_r5_sequences": len(literal),
        "relative_sequences_total": len(relative),
        "sequences_in_episode_window": len(recent),
        "sequences_before_episode": len(older),
        "habitual_for_this_card": habitual,
        "card_online_txn_count": int(len(online)),
        "latest_sequence": {k: v for k, v in (recent[-1].items() if recent else [])if k != "end_ts"},
        "cleared_purchase_over_100": bool(recent and recent[-1]["follow_amount"] >= R5_BLOCK_USD),
    }
    narrative = (
        f"{len(recent)} run(s) of three or more small online authorizations inside an hour followed by a "
        f"larger purchase in the 48 hours to the alert"
        if recent
        else "No run of three or more small online authorizations followed by a larger purchase."
    )
    if habitual and recent:
        narrative += (
            f"; this card produced {len(older)} earlier runs of the same shape, so the sequence is its "
            "normal behaviour rather than a testing episode"
        )
    return PatternScore(
        pattern=Pattern.CARD_TESTING.value,
        score=min(score, 0.95),
        present_signals=present,
        absent_signals=absent,
        supporting_txn_ids=supporting,
        detail=detail,
        narrative=narrative,
    )


def match_cnp(store: Store, card_id: str, txn_id: str, as_of: str, require_new_device: bool = False) -> PatternScore:
    row = store.txn(txn_id)
    hist = store.card_txns(card_id, as_of)
    prior = hist[hist["TransactionID"] != int(txn_id)]
    flagged_ts = pd.Timestamp(row["ts"])

    present: list[str] = []
    absent: list[str] = []
    uncheckable: list[str] = []
    pattern = Pattern.CARD_NOT_PRESENT_NEW_DEVICE if require_new_device else Pattern.CARD_NOT_PRESENT_FRAUD

    online = str(row["channel"]) == "online"
    if online:
        present.append("online_channel")
    else:
        absent.append("online_channel")

    amt = float(row["TransactionAmt"])
    base = prior["TransactionAmt"].to_numpy()
    pct = float((base < amt).mean()) if len(base) >= 5 else None
    if pct is None:
        uncheckable.append("amount_out_of_baseline")
    elif pct >= 0.95:
        present.append("amount_out_of_baseline")
    else:
        absent.append("amount_out_of_baseline")

    new_product = bool(row["ProductCD"] not in set(prior["ProductCD"].dropna()))
    if new_product:
        present.append("new_product_code")
    else:
        absent.append("new_product_code")

    window = _episode_window(hist, flagged_ts)
    burst = window[window["channel"] == "online"]
    if 2 <= len(burst) <= 6:
        present.append("burst_within_48h")
    else:
        absent.append("burst_within_48h")

    profile = store.device_profile_for(txn_id)
    identity = store.identity_for(txn_id)
    device_status = str(identity.get("id_15") or "")
    if not identity:
        uncheckable.append("device_marked_new")
        uncheckable.append("device_new_for_card")
        device_new_for_card = False
        marked_new = False
    else:
        ident_prior = store.identity[store.identity["TransactionID"].isin(set(prior["TransactionID"]))]
        known = {p for p in ident_prior["device_profile"] if p}
        device_new_for_card = bool(profile and profile not in known)
        marked_new = device_status == "New"
        (present if marked_new else absent).append("device_marked_new")
        (present if device_new_for_card else absent).append("device_new_for_card")

    proxy = str(identity.get("id_23") or "")
    if identity:
        (present if proxy else absent).append("proxy_flag")
    else:
        uncheckable.append("proxy_flag")

    # Weights. A single unusual online purchase is explicitly ambiguous in the
    # README, so no one signal can carry this above the R1 threshold on its own.
    score = 0.0
    if online:
        score += 0.18
    if "amount_out_of_baseline" in present:
        score += 0.20
    if new_product:
        score += 0.12
    if "burst_within_48h" in present:
        score += 0.16
    if require_new_device:
        if marked_new:
            score += 0.14
        if device_new_for_card:
            score += 0.12
        if "ANONYMOUS" in proxy:
            score += 0.12
        elif proxy:
            score += 0.05
        if not (marked_new or device_new_for_card):
            # Without a new device this is plain CNP, not pattern 3.
            score = min(score, 0.25)
    else:
        if marked_new or device_new_for_card:
            score += 0.05

    supporting = _ids(burst) if len(burst) >= 2 else [str(int(row["TransactionID"]))]
    detail = {
        "amount": round(amt, 2),
        "amount_percentile_on_card": round(pct, 3) if pct is not None else None,
        "prior_txns_on_card": int(len(prior)),
        "new_product_code": new_product,
        "burst_size_48h": int(len(burst)),
        "device_profile": profile,
        "device_status_id_15": device_status or None,
        "proxy_id_23": proxy or None,
        "device_new_for_card": device_new_for_card,
        # 158 of the 900 cleared cases were exactly this: a cardholder buying
        # from a new phone. Recorded so the scorer can hold the line.
        "new_device_is_common_false_alarm": bool(marked_new or device_new_for_card),
    }
    narrative = (
        f"Online purchase of ${amt:,.2f}"
        + (f" at the {pct:.0%} percentile of this card's history" if pct is not None else "")
        + (f", {len(burst)} online transactions in the preceding 48 hours" if len(burst) >= 2 else "")
        + (f", from a device profile new to this card" if device_new_for_card else "")
    )
    return PatternScore(
        pattern=pattern.value,
        score=min(score, 0.95),
        present_signals=present,
        absent_signals=absent,
        uncheckable_signals=uncheckable,
        supporting_txn_ids=supporting,
        detail=detail,
        narrative=narrative,
    )


def match_out_of_region(store: Store, card_id: str, txn_id: str, as_of: str) -> PatternScore:
    row = store.txn(txn_id)
    hist = store.card_txns(card_id, as_of)
    prior = hist[hist["TransactionID"] != int(txn_id)]

    present: list[str] = []
    absent: list[str] = []
    uncheckable: list[str] = []

    card_present = str(row["channel"]) == "in_person"
    (present if card_present else absent).append("card_present_channel")

    addr = row["addr1"]
    if pd.isna(addr):
        # Not the same as "no history in this region". 94.8 percent of
        # ProductCD C rows have no addr1 at all, and five pack cases are in that
        # position. Region reasoning is unavailable, not negative.
        uncheckable.extend(["region_new_for_customer", "home_activity_continued", "sustained_multi_day_presence"])
        return PatternScore(
            pattern=Pattern.OUT_OF_REGION_USE.value,
            score=0.0,
            present_signals=present,
            absent_signals=absent,
            uncheckable_signals=uncheckable,
            detail={"addr1": None, "reason": "no billing region recorded on this transaction"},
            narrative="No billing region is recorded on this transaction, so out-of-region use cannot be assessed.",
        )

    target = float(addr)
    cust_hist = store.customer_txns(str(row["customer_id"]), as_of)
    cust_prior = cust_hist[cust_hist["TransactionID"] != int(txn_id)]
    regions = cust_prior["addr1"].dropna().astype(float)
    distinct_regions = int(regions.nunique())
    seen_here = int((regions == target).sum())

    if seen_here == 0:
        present.append("region_new_for_customer")
    else:
        absent.append("region_new_for_customer")

    in_region = hist[hist["addr1"].notna() & (hist["addr1"].astype(float) == target)]
    span_days = 0.0
    if len(in_region) > 1:
        span_days = (in_region["ts"].max() - in_region["ts"].min()).total_seconds() / 86400

    home = float(regions.value_counts().index[0]) if len(regions) else None
    home_during = 0
    if home is not None and len(in_region):
        lo, hi = in_region["ts"].min(), in_region["ts"].max()
        same = cust_hist[(cust_hist["ts"] >= lo) & (cust_hist["ts"] <= hi) & cust_hist["addr1"].notna()]
        home_during = int((same["addr1"].astype(float) == home).sum())

    (present if home_during > 0 else absent).append("home_activity_continued")
    (present if span_days >= 2 else absent).append("sustained_multi_day_presence")

    score = 0.0
    if card_present:
        score += 0.16
    if seen_here == 0:
        # 61.9 percent of customers use exactly one region, so a new region means
        # something there and almost nothing for a customer already spanning ten.
        if distinct_regions <= 2:
            score += 0.30
        elif distinct_regions <= 5:
            score += 0.16
        elif distinct_regions <= 10:
            score += 0.07
        else:
            score += 0.02
    if home_during > 0 and seen_here == 0:
        score += 0.28  # spending in two places at once reads as a clone
    if span_days >= 2 and home_during == 0:
        # 716 of the 900 cleared cases were a travelling cardholder.
        score = min(score, 0.22)

    detail = {
        "addr1": str(target),
        "customer_distinct_regions": distinct_regions,
        "prior_txns_in_this_region": seen_here,
        "home_region": str(home) if home is not None else None,
        "home_activity_during_window": home_during,
        "span_days_in_region": round(span_days, 2),
        "reads_as_trip": bool(span_days >= 2 and home_during == 0),
        "reads_as_clone": bool(home_during > 0 and seen_here == 0),
    }
    narrative = (
        f"Card-present use in billing region {target:.0f}"
        + (" the cardholder has no history in" if seen_here == 0 else f" seen {seen_here} times before")
        + (f", while {home_during} transactions continued in the home region {home:.0f} over the same window" if home_during else "")
    )
    return PatternScore(
        pattern=Pattern.OUT_OF_REGION_USE.value,
        score=min(score, 0.95),
        present_signals=present,
        absent_signals=absent,
        uncheckable_signals=uncheckable,
        supporting_txn_ids=_ids(in_region) if len(in_region) else [str(int(row["TransactionID"]))],
        detail=detail,
        narrative=narrative,
    )


def match_account_takeover(store: Store, card_id: str, txn_id: str, as_of: str) -> PatternScore:
    row = store.txn(txn_id)
    hist = store.card_txns(card_id, as_of)
    prior = hist[hist["TransactionID"] != int(txn_id)]
    flagged_ts = pd.Timestamp(row["ts"])

    present: list[str] = []
    absent: list[str] = []
    uncheckable: list[str] = []

    window = _episode_window(hist, flagged_ts)
    channels = set(window["channel"].dropna())
    (present if len(channels) >= 2 else absent).append("mixed_channel_activity")

    prior_channels = prior["channel"].value_counts(normalize=True).to_dict()
    this_channel = str(row["channel"])
    channel_share = float(prior_channels.get(this_channel, 0.0))
    (present if channel_share < 0.10 and len(prior) >= 20 else absent).append("channel_inconsistent_with_history")

    identity = store.identity_for(txn_id)
    if identity:
        ident_prior = store.identity[store.identity["TransactionID"].isin(set(prior["TransactionID"]))]
        known = {p for p in ident_prior["device_profile"] if p}
        profile = store.device_profile_for(txn_id)
        device_anomaly = bool((profile and profile not in known) or str(identity.get("id_15") or "") == "New")
        (present if device_anomaly else absent).append("device_anomaly")
    else:
        uncheckable.append("device_anomaly")
        device_anomaly = False

    # M1..M9 are the README's match flags, for example whether the name on the
    # card matches the address. Individual definitions are not published, so
    # this is reported as "match flags disagree with the card's history", not as
    # a claim about any specific flag.
    mism = 0
    checked = 0
    for i in range(1, 10):
        col = f"M{i}"
        val = row.get(col)
        if pd.isna(val) or prior.empty:
            continue
        modal = prior[col].dropna()
        if modal.empty:
            continue
        checked += 1
        if modal.mode().iloc[0] != val:
            mism += 1
    if checked == 0:
        uncheckable.append("match_flag_anomaly")
    else:
        (present if mism >= 3 else absent).append("match_flag_anomaly")

    score = 0.0
    if len(channels) >= 2:
        score += 0.22
    if "channel_inconsistent_with_history" in present:
        score += 0.22
    if device_anomaly:
        score += 0.16
    if "match_flag_anomaly" in present:
        score += 0.18
    if len(channels) < 2:
        # The README defines this pattern as mixed-channel. Without that it is
        # some other pattern.
        score = min(score, 0.25)

    detail = {
        "channels_in_48h": sorted(channels),
        "flagged_channel": this_channel,
        "flagged_channel_share_in_history": round(channel_share, 3),
        "match_flags_checked": checked,
        "match_flags_disagreeing_with_card_history": mism,
        "match_flag_note": "M1 to M9 are Vesta match flags with no published individual definitions",
        "device_anomaly": device_anomaly,
    }
    narrative = (
        f"Activity in {len(channels)} channel(s) within 48 hours"
        + (f"; the flagged channel accounts for {channel_share:.1%} of this card's history" if len(prior) >= 20 else "")
        + (f"; {mism} of {checked} match flags disagree with the card's usual values" if checked else "")
    )
    return PatternScore(
        pattern=Pattern.ACCOUNT_TAKEOVER.value,
        score=min(score, 0.95),
        present_signals=present,
        absent_signals=absent,
        uncheckable_signals=uncheckable,
        supporting_txn_ids=_ids(window) if len(window) > 1 else [str(int(row["TransactionID"]))],
        detail=detail,
        narrative=narrative,
    )


# --- undocumented detectors -------------------------------------------------


def match_structuring(store: Store, card_id: str, txn_id: str, as_of: str) -> PatternScore:
    """Several online purchases inside an hour, each just below a round
    authorization ceiling.

    Not one of the five documented patterns. Five confirmed-fraud closed cases
    describe exactly this and were labelled `undocumented`, which is why the
    matcher exists and why R9 is the rule it points at.
    """
    row = store.txn(txn_id)
    hist = store.card_txns(card_id, as_of)
    flagged_ts = pd.Timestamp(row["ts"])
    window = hist[
        (hist["ts"] >= flagged_ts - pd.Timedelta(minutes=STRUCTURING_WINDOW_MIN))
        & (hist["ts"] <= flagged_ts + pd.Timedelta(minutes=STRUCTURING_WINDOW_MIN))
        & (hist["ts"] <= pd.Timestamp(as_of))
    ]
    band = window[
        (window["channel"] == "online")
        & (window["TransactionAmt"] >= STRUCTURING_MIN)
        & (window["TransactionAmt"] < STRUCTURING_MAX)
    ].sort_values("ts")

    present: list[str] = []
    absent: list[str] = []
    hit = False
    supporting: list[str] = []
    total = 0.0
    if len(band) >= STRUCTURING_N:
        span = (band["ts"].max() - band["ts"].min()).total_seconds() / 60
        if span <= STRUCTURING_WINDOW_MIN:
            hit = True
            supporting = _ids(band)
            total = float(band["TransactionAmt"].sum())
    (present if hit else absent).append("four_or_more_online_just_under_500_in_one_hour")

    # Does the card do this routinely? If so it is a merchant behaviour.
    earlier = hist[hist["ts"] < flagged_ts - pd.Timedelta(hours=EPISODE_HOURS)]
    earlier_band = earlier[
        (earlier["channel"] == "online")
        & (earlier["TransactionAmt"] >= STRUCTURING_MIN)
        & (earlier["TransactionAmt"] < STRUCTURING_MAX)
    ]
    habitual = len(earlier_band) >= 12
    (absent if habitual else present).append("not_routine_for_this_card")

    score = 0.0
    if hit:
        score = 0.66
        if 1500 <= total <= 2500:
            score += 0.12
        if habitual:
            score = min(score, 0.25)

    detail = {
        "band_usd": [STRUCTURING_MIN, STRUCTURING_MAX],
        "n_in_band": int(len(band)),
        "total_usd": round(total, 2),
        "window_minutes": STRUCTURING_WINDOW_MIN,
        "amounts": [round(float(a), 2) for a in band["TransactionAmt"]] if hit else [],
        "routine_for_card": habitual,
    }
    narrative = (
        f"{len(band)} online purchases of ${STRUCTURING_MIN:.0f} to ${STRUCTURING_MAX:.0f} on this card inside "
        f"{STRUCTURING_WINDOW_MIN} minutes, totalling ${total:,.2f}, each amount stopping just short of a round "
        "authorization ceiling"
        if hit
        else "No run of several online purchases just below a round authorization ceiling."
    )
    return PatternScore(
        pattern="undocumented_structuring",
        score=min(score, 0.92),
        present_signals=present,
        absent_signals=absent,
        supporting_txn_ids=supporting,
        detail=detail,
        narrative=narrative,
    )


def match_proxy_device_ring(store: Store, card_id: str, txn_id: str, as_of: str) -> PatternScore:
    """One specific device, marked new on every account it touches, behind an
    anonymous proxy, spread across many unrelated cardholders.

    Also not one of the five. Four confirmed-fraud closed cases describe it and
    were labelled `undocumented`.
    """
    profile = store.device_profile_for(txn_id)
    present: list[str] = []
    absent: list[str] = []
    uncheckable: list[str] = []

    if not profile:
        return PatternScore(
            pattern="undocumented_proxy_device_ring",
            score=0.0,
            uncheckable_signals=["specific_device_profile", "new_on_every_account", "anonymous_proxy", "many_cardholders"],
            detail={"reason": "no identity record on the flagged transaction"},
            narrative="No device record on the flagged transaction, so a shared-device ring cannot be assessed.",
        )

    sig = store.device_profile_signature(profile)
    n_cards, n_customers = store.device_profile_breadth(profile)

    # All four parts present is what separates a real handset fingerprint from a
    # generic browser class shared by hundreds of unrelated cards.
    specific = sig["parts_present"] == 4
    (present if specific else absent).append("specific_device_profile")
    (present if sig["new_share"] >= 0.80 else absent).append("new_on_every_account")
    (present if sig["anonymous_share"] >= 0.50 else absent).append("anonymous_proxy")
    (present if n_customers >= 5 else absent).append("many_cardholders")

    df = store.txns_on_device(profile, as_of)
    recent = df[df["ts"] >= pd.Timestamp(as_of) - pd.Timedelta(days=45)]
    device_cards = set(df["card_id"].dropna())
    cc = store.closed_cases_before(as_of)
    linked = cc[cc["card_id"].isin(device_cards)]
    confirmed = linked[linked["outcome"] == "confirmed_fraud"]
    # A raw count of prior cases is not a signal: roughly a third of all cards
    # in this dataset carry a confirmed-fraud closed case, so any profile with a
    # hundred cards collects hundreds of them. What matters is whether this
    # profile's cards are hit more often than cards in general.
    fraud_card_share = (
        len(set(confirmed["card_id"])) / len(device_cards) if device_cards else 0.0
    )
    baseline = store.confirmed_fraud_card_base_rate(as_of)
    elevated = bool(device_cards and fraud_card_share >= max(baseline * 1.4, 0.10))
    (present if elevated else absent).append("prior_confirmed_fraud_above_base_rate")

    score = 0.0
    if specific:
        score += 0.12
    if sig["new_share"] >= 0.80:
        score += 0.20
    if sig["anonymous_share"] >= 0.50:
        score += 0.28
    elif sig["proxy_share"] >= 0.50:
        score += 0.12
    if 5 <= n_customers <= 150:
        score += 0.12
    if elevated:
        score += 0.18
    if sig["anonymous_share"] < 0.25 and sig["proxy_share"] < 0.25:
        # Without a proxy concentration this is a popular handset, not a ring.
        score = min(score, 0.20)
    if not specific or n_customers < 3:
        score = min(score, 0.15)

    detail = {
        "device_profile": profile,
        "distinct_cards": n_cards,
        "distinct_customers": n_customers,
        "share_marked_new": sig["new_share"],
        "share_behind_proxy": sig["proxy_share"],
        "share_anonymous_proxy": sig["anonymous_share"],
        "parts_present": sig["parts_present"],
        "cards_in_last_45_days": int(recent["card_id"].nunique()),
        "txns_in_last_45_days": int(len(recent)),
        "prior_confirmed_fraud_cases": confirmed["case_id"].tolist()[:10],
        "cards_with_prior_confirmed_fraud_share": round(fraud_card_share, 3),
        "dataset_base_rate": round(baseline, 3),
        "elevated_vs_base_rate": elevated,
    }
    narrative = (
        f"Device profile {profile} appears on {n_cards} cards across {n_customers} cardholders, marked new on "
        f"{sig['new_share']:.0%} of its transactions and behind an anonymous proxy on {sig['anonymous_share']:.0%}"
        + (f"; {len(confirmed)} prior confirmed-fraud cases touch cards on this device" if len(confirmed) else "")
    )
    return PatternScore(
        pattern="undocumented_proxy_device_ring",
        score=min(score, 0.95),
        present_signals=present,
        absent_signals=absent,
        uncheckable_signals=uncheckable,
        supporting_txn_ids=_ids(recent[recent["card_id"] == card_id]) or [txn_id],
        detail=detail,
        narrative=narrative,
    )


MATCHERS = {
    Pattern.CARD_TESTING.value: match_card_testing,
    Pattern.CARD_NOT_PRESENT_FRAUD.value: lambda s, c, t, a: match_cnp(s, c, t, a, require_new_device=False),
    Pattern.CARD_NOT_PRESENT_NEW_DEVICE.value: lambda s, c, t, a: match_cnp(s, c, t, a, require_new_device=True),
    Pattern.OUT_OF_REGION_USE.value: match_out_of_region,
    Pattern.ACCOUNT_TAKEOVER.value: match_account_takeover,
    "undocumented_structuring": match_structuring,
    "undocumented_proxy_device_ring": match_proxy_device_ring,
}


def score_pattern(store: Store, pattern: str, card_id: str, txn_id: str, as_of: str) -> dict[str, Any]:
    matcher = MATCHERS.get(pattern)
    if matcher is None:
        return {"pattern": pattern, "score": 0.0, "error": f"unknown pattern {pattern}"}
    return matcher(store, card_id, txn_id, as_of).to_dict()


def score_all_patterns(store: Store, card_id: str, txn_id: str, as_of: str) -> list[PatternScore]:
    return sorted(
        (m(store, card_id, txn_id, as_of) for m in MATCHERS.values()),
        key=lambda p: -p.score,
    )
