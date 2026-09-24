"""Builds a compact slice of the 708 MB transaction file for the mock tool layer.

Why a slice: the mock tools must return real dataset IDs, because made-up IDs
score zero in the answer files. Loading 590k x 393 columns per process is not
viable, so this runs once and caches only what the 20 benchmark cases can
possibly touch: every transaction of the 20 customers, plus every transaction
made from a device profile those customers used (that is the shared-device and
ring evidence R6 depends on).

Run: python -m backend.tools.mock.extract_slice
"""

from __future__ import annotations

import os
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[3]
DATA_DIR = REPO_ROOT / "data"
CACHE_DIR = REPO_ROOT / "backend" / "data_cache"

# Vesta keeps 393 columns. These are the ones an investigation can actually
# reason about: identity of the transaction, the card, where it was billed, who
# it was mailed to, plus the handful of named C/D/M features the README says are
# counts, day-deltas and match flags.
TXN_COLS = [
    "TransactionID",
    "TransactionDT",
    "TransactionAmt",
    "ProductCD",
    "card1",
    "card2",
    "card3",
    "card4",
    "card5",
    "card6",
    "addr1",
    "addr2",
    "dist1",
    "dist2",
    "P_emaildomain",
    "R_emaildomain",
    "C1",
    "C2",
    "C5",
    "C13",
    "C14",
    "D1",
    "D2",
    "D3",
    "D4",
    "D10",
    "D15",
    "M1",
    "M2",
    "M3",
    "M4",
    "M5",
    "M6",
    "M7",
    "M8",
    "M9",
    "customer_id",
    "ts",
    "channel",
    "risk_score",
]

IDENTITY_COLS = [
    "TransactionID",
    "id_01",
    "id_02",
    "id_05",
    "id_06",
    "id_11",
    "id_12",
    "id_15",
    "id_16",
    "id_17",
    "id_19",
    "id_20",
    "id_23",
    "id_28",
    "id_29",
    "id_30",
    "id_31",
    "id_32",
    "id_33",
    "id_34",
    "id_35",
    "id_36",
    "id_37",
    "id_38",
    "DeviceType",
    "DeviceInfo",
]

CHUNKSIZE = 200_000


def compose_device_profile(row: pd.Series) -> str:
    """`DeviceInfo | OS | browser | screen`, the exact string the answer format
    expects in connected_device_profiles."""
    parts = [row.get("DeviceInfo"), row.get("id_30"), row.get("id_31"), row.get("id_33")]
    cleaned = [str(p).strip() for p in parts if pd.notna(p) and str(p).strip() != ""]
    if not cleaned:
        return ""
    return " | ".join(cleaned)


def load_identity() -> pd.DataFrame:
    ident = pd.read_csv(DATA_DIR / "identity.csv", usecols=IDENTITY_COLS, low_memory=False)
    ident["TransactionID"] = ident["TransactionID"].astype("int64")
    ident["device_profile"] = ident.apply(compose_device_profile, axis=1)
    return ident


def scan_transactions(keep_customers: set[str], keep_txn_ids: set[int]) -> pd.DataFrame:
    kept: list[pd.DataFrame] = []
    reader = pd.read_csv(
        DATA_DIR / "transactions.csv",
        usecols=TXN_COLS,
        chunksize=CHUNKSIZE,
        low_memory=False,
    )
    for chunk in reader:
        mask = chunk["customer_id"].isin(keep_customers)
        if keep_txn_ids:
            mask = mask | chunk["TransactionID"].isin(keep_txn_ids)
        hit = chunk.loc[mask]
        if len(hit):
            kept.append(hit.copy())
    if not kept:
        return pd.DataFrame(columns=TXN_COLS)
    return pd.concat(kept, ignore_index=True)


def build() -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    case_pack = pd.read_csv(DATA_DIR / "case_pack.csv")
    customers = set(case_pack["customer_id"].astype(str))
    flagged = set(case_pack["flagged_txn_id"].astype("int64"))

    print(f"pass 1: scanning for {len(customers)} benchmark customers")
    first = scan_transactions(customers, flagged)
    print(f"  kept {len(first)} rows")

    ident = load_identity()
    ident_by_id = ident.set_index("TransactionID")

    # Device profiles the benchmark customers touched. Anything else that used
    # the same profile is the second-hop evidence R6 and ring_detect need.
    seen_ids = set(first["TransactionID"].astype("int64"))
    local_profiles = {
        p
        for p in ident_by_id.loc[ident_by_id.index.intersection(seen_ids), "device_profile"]
        if p
    }
    print(f"pass 2: {len(local_profiles)} device profiles touched by those customers")

    linked_ids = set(
        ident.loc[ident["device_profile"].isin(local_profiles), "TransactionID"].astype("int64")
    )
    linked_ids -= seen_ids
    print(f"  {len(linked_ids)} further transactions share one of those profiles")

    if linked_ids:
        print("pass 3: scanning for device-linked transactions")
        second = scan_transactions(set(), linked_ids)
        print(f"  kept {len(second)} rows")
        txns = pd.concat([first, second], ignore_index=True).drop_duplicates("TransactionID")
    else:
        txns = first

    txns = txns.sort_values("ts").reset_index(drop=True)

    # Card id is derivable only from the pairing the dataset already publishes in
    # the case pack and closed cases (C01234-K1). card2 distinguishes cards
    # within a customer, so rank it deterministically per customer.
    txns["card_key"] = txns["card1"].astype("Int64").astype(str) + "/" + txns["card2"].astype("Int64").astype(str)
    card_rank = (
        txns[["customer_id", "card_key"]]
        .drop_duplicates()
        .sort_values(["customer_id", "card_key"])
        .groupby("customer_id")
        .cumcount()
        + 1
    )
    ranked = txns[["customer_id", "card_key"]].drop_duplicates().sort_values(["customer_id", "card_key"]).copy()
    ranked["card_idx"] = card_rank.values
    txns = txns.merge(ranked, on=["customer_id", "card_key"], how="left")
    txns["card_id"] = txns["customer_id"].astype(str) + "-K" + txns["card_idx"].astype(str)

    ident_slice = ident.loc[ident["TransactionID"].isin(set(txns["TransactionID"].astype("int64")))]

    txns.to_parquet(CACHE_DIR / "txn_slice.parquet", index=False)
    ident_slice.to_parquet(CACHE_DIR / "identity_slice.parquet", index=False)
    print(f"wrote {len(txns)} transactions and {len(ident_slice)} identity rows to {CACHE_DIR}")


if __name__ == "__main__":
    build()
