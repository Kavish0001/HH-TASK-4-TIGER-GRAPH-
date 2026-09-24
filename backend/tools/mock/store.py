"""Pandas store behind the mock tools.

Every row here came out of data/transactions.csv, data/identity.csv and
data/closed_cases_history.csv, so every ID the mock returns is a real dataset
ID. Nothing is invented. The slice is built by extract_slice.py.

The `as_of` cut is enforced here rather than in each tool, because leaking a row
later than a case's trigger time is the one mistake that silently inflates every
score.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd

from backend.config import get_settings

_LOCK = threading.Lock()


def _to_ts(value: str | datetime | pd.Timestamp) -> pd.Timestamp:
    return pd.Timestamp(value)


@dataclass
class Store:
    txns: pd.DataFrame
    identity: pd.DataFrame
    closed_cases: pd.DataFrame
    case_pack: pd.DataFrame

    # Lookup indexes built once.
    _by_txn: dict[int, int] = None  # type: ignore[assignment]
    _breadth_cache: dict[str, tuple[int, int]] = None  # type: ignore[assignment]
    _base_rate_cache: dict[str, float] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        self._by_txn = {int(t): i for i, t in enumerate(self.txns["TransactionID"].to_numpy())}
        self._breadth_cache = {}
        self._base_rate_cache = {}

    # ---- basic accessors -------------------------------------------------

    def txn(self, txn_id: str | int) -> pd.Series | None:
        pos = self._by_txn.get(int(txn_id))
        if pos is None:
            return None
        return self.txns.iloc[pos]

    def as_of_mask(self, df: pd.DataFrame, as_of: str | datetime) -> pd.Series:
        return df["ts"] <= _to_ts(as_of)

    def card_txns(self, card_id: str, as_of: str | datetime) -> pd.DataFrame:
        df = self.txns[self.txns["card_id"] == card_id]
        return df[self.as_of_mask(df, as_of)].sort_values("ts")

    def customer_txns(self, customer_id: str, as_of: str | datetime) -> pd.DataFrame:
        df = self.txns[self.txns["customer_id"] == customer_id]
        return df[self.as_of_mask(df, as_of)].sort_values("ts")

    def device_profile_for(self, txn_id: str | int) -> str:
        row = self.identity.loc[self.identity["TransactionID"] == int(txn_id)]
        if row.empty:
            return ""
        return str(row.iloc[0]["device_profile"] or "")

    def identity_for(self, txn_id: str | int) -> dict:
        row = self.identity.loc[self.identity["TransactionID"] == int(txn_id)]
        if row.empty:
            return {}
        rec = row.iloc[0].to_dict()
        return {k: (None if pd.isna(v) else v) for k, v in rec.items()}

    def txns_on_device(self, device_profile: str, as_of: str | datetime) -> pd.DataFrame:
        ids = set(
            self.identity.loc[
                self.identity["device_profile"] == device_profile, "TransactionID"
            ].astype("int64")
        )
        if not ids:
            return self.txns.iloc[0:0]
        df = self.txns[self.txns["TransactionID"].isin(ids)]
        return df[self.as_of_mask(df, as_of)].sort_values("ts")

    def device_profile_breadth(self, device_profile: str) -> tuple[int, int]:
        """(distinct cards, distinct customers) ever seen on this profile.

        Half the profiles in this dataset are shared by more than one card and
        the biggest is shared by 1,013, so sharing alone says nothing. Rarity is
        one of the two things that make a shared device meaningful.
        """
        cached = self._breadth_cache.get(device_profile)
        if cached is not None:
            return cached
        ids = set(
            self.identity.loc[
                self.identity["device_profile"] == device_profile, "TransactionID"
            ].astype("int64")
        )
        if not ids:
            result = (0, 0)
        else:
            df = self.txns[self.txns["TransactionID"].isin(ids)]
            result = (int(df["card_id"].nunique()), int(df["customer_id"].nunique()))
        self._breadth_cache[device_profile] = result
        return result

    def device_profile_signature(self, device_profile: str) -> dict:
        """What makes a shared profile evidence rather than a browser class.

        The scout's finding: the one profile in this dataset that really is a
        ring carries id_15=New on 100 percent of its rows and an anonymous proxy
        on 100 percent of them, across 52 cards. Generic profiles shared by
        hundreds of cards have neither concentration. So breadth alone is the
        wrong test; breadth plus novelty plus proxy concentration is the right
        one.
        """
        rows = self.identity[self.identity["device_profile"] == device_profile]
        n = len(rows)
        if n == 0:
            return {"n_rows": 0, "new_share": 0.0, "proxy_share": 0.0, "anonymous_share": 0.0, "parts_present": 0}
        status = rows["id_15"].fillna("")
        proxy = rows["id_23"].fillna("")
        parts = [p for p in device_profile.split(" | ")]
        return {
            "n_rows": n,
            "new_share": round(float((status == "New").mean()), 3),
            "proxy_share": round(float((proxy != "").mean()), 3),
            "anonymous_share": round(float(proxy.str.contains("ANONYMOUS").mean()), 3),
            "parts_present": sum(1 for p in parts if p and p.lower() != "unknown"),
        }

    def closed_cases_before(self, as_of: str | datetime) -> pd.DataFrame:
        cut = _to_ts(as_of)
        return self.closed_cases[self.closed_cases["closed_at_ts"] <= cut]

    def confirmed_fraud_card_base_rate(self, as_of: str | datetime) -> float:
        """Share of all cards in the bank carrying a confirmed-fraud closed case.

        Any "these cards have prior fraud" signal has to be read against this,
        or a device shared by 200 cards looks damning purely from volume.
        """
        key = str(_to_ts(as_of).date())
        cached = self._base_rate_cache.get(key)
        if cached is not None:
            return cached
        cc = self.closed_cases_before(as_of)
        confirmed = cc[cc["outcome"] == "confirmed_fraud"]
        total_cards = 14317  # distinct customer/card6 pairs in the full dataset
        rate = len(set(confirmed["card_id"])) / total_cards if total_cards else 0.0
        self._base_rate_cache[key] = rate
        return rate


NULL_SENTINEL = "\x00null"
UNKNOWN_PART = "unknown"


def compose_device_profile(frame: pd.DataFrame) -> pd.Series:
    """`DeviceInfo | OS | browser | screen`, space-pipe-space.

    id_30 and id_33 are null about half the time, so a single null token has to
    be chosen and kept: `unknown`. Dropping null parts instead would collapse
    two different devices onto the same string and would not match the format
    the answer file expects in connected_device_profiles.
    """
    parts = []
    for col in ("DeviceInfo", "id_30", "id_31", "id_33"):
        series = frame[col].astype("object").where(frame[col].notna(), UNKNOWN_PART)
        parts.append(series.astype(str).str.strip().replace({"": UNKNOWN_PART}))
    return parts[0] + " | " + parts[1] + " | " + parts[2] + " | " + parts[3]


def _assign_card_ids(txns: pd.DataFrame, card_index: pd.DataFrame) -> pd.DataFrame:
    """Derive `C01234-K1` style card ids.

    card_id is not a column. The rule is customer_id + "-K" + rank(card6), nulls
    ranked first then ascending lexicographic, which reproduces every label in
    closed_cases_history.csv and every card in case_pack.csv. The ranking has to
    come from the customer's whole history, so it arrives precomputed from
    build_card_index.py rather than being derived from this slice.
    """
    txns = txns.copy()
    txns = txns.drop(columns=[c for c in ("card_id", "card_idx", "card_key") if c in txns.columns])
    txns["card6_key"] = txns["card6"].fillna(NULL_SENTINEL).astype(str)
    merged = txns.merge(
        card_index.rename(columns={"card6": "card6_key"})[["customer_id", "card6_key", "card_id", "card_idx"]],
        on=["customer_id", "card6_key"],
        how="left",
    )
    # A customer never seen by the index cannot happen with the full-file build,
    # but fall back to a single card rather than emitting a null id.
    merged["card_id"] = merged["card_id"].fillna(merged["customer_id"].astype(str) + "-K1")
    merged["card_idx"] = merged["card_idx"].fillna(1).astype(int)
    return merged


def _load_closed_cases(path: Path) -> pd.DataFrame:
    cc = pd.read_csv(path)
    cc["opened_at_ts"] = pd.to_datetime(cc["opened_at"])
    cc["closed_at_ts"] = pd.to_datetime(cc["closed_at"])
    cc["txn_id_list"] = cc["txn_ids"].fillna("").apply(lambda s: [t for t in str(s).split("|") if t])
    cc["connected_list"] = (
        cc["connected_card_ids"].fillna("").apply(lambda s: [t for t in str(s).split("|") if t])
    )
    cc["actions_list"] = (
        cc["actions_taken"].fillna("").apply(lambda s: [t for t in str(s).split("|") if t])
    )
    return cc


@lru_cache(maxsize=1)
def get_store() -> Store:
    settings = get_settings()
    cache = settings.cache_dir
    txn_path = cache / "txn_slice.parquet"
    if not txn_path.exists():
        raise FileNotFoundError(
            f"{txn_path} missing. Run: python -m backend.tools.mock.extract_slice"
        )
    index_path = cache / "card_index.parquet"
    if not index_path.exists():
        raise FileNotFoundError(
            f"{index_path} missing. Run: python -m backend.tools.mock.build_card_index"
        )
    with _LOCK:
        txns = pd.read_parquet(txn_path)
        identity = pd.read_parquet(cache / "identity_slice.parquet")
        card_index = pd.read_parquet(index_path)
        case_pack = pd.read_csv(settings.data_dir / "case_pack.csv")
        closed = _load_closed_cases(settings.data_dir / "closed_cases_history.csv")

        txns["TransactionID"] = txns["TransactionID"].astype("int64")
        txns["ts"] = pd.to_datetime(txns["ts"])
        txns["TransactionAmt"] = txns["TransactionAmt"].astype(float)
        txns["risk_score"] = txns["risk_score"].astype(float)
        identity["TransactionID"] = identity["TransactionID"].astype("int64")
        # Recompose rather than trusting the cached column, so there is one
        # definition of the profile string in the codebase.
        identity["device_profile"] = compose_device_profile(identity)

        txns = _assign_card_ids(txns, card_index)
        txns = txns.sort_values("ts").reset_index(drop=True)
        return Store(txns=txns, identity=identity, closed_cases=closed, case_pack=case_pack)


def amount_stats(amounts: np.ndarray) -> dict[str, float]:
    if len(amounts) == 0:
        return {"count": 0, "mean": 0.0, "median": 0.0, "p95": 0.0, "max": 0.0, "std": 0.0}
    return {
        "count": int(len(amounts)),
        "mean": round(float(np.mean(amounts)), 2),
        "median": round(float(np.median(amounts)), 2),
        "p95": round(float(np.percentile(amounts, 95)), 2),
        "max": round(float(np.max(amounts)), 2),
        "std": round(float(np.std(amounts)), 2),
    }
