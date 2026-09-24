"""Global customer -> card6 -> card index map.

card_id is not a column in the dataset. The rule that reproduces all 5,565
closed-case labels and all 20 case-pack labels is:

    card_id = customer_id + "-K" + rank(card6)

with nulls ranked first and the rest ascending lexicographic. Ranking has to be
computed over a customer's whole history, not over the slice, or a customer we
only partly observe gets the wrong index and the connected_card_ids we emit are
wrong. So this reads the full file with four columns.

Run: python -m backend.tools.mock.build_card_index
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[3]
NULL_SENTINEL = "\x00null"


def build() -> None:
    data_dir = REPO_ROOT / "data"
    cache_dir = REPO_ROOT / "backend" / "data_cache"
    cache_dir.mkdir(parents=True, exist_ok=True)

    pairs: set[tuple[str, str]] = set()
    reader = pd.read_csv(
        data_dir / "transactions.csv",
        usecols=["customer_id", "card6"],
        chunksize=500_000,
        low_memory=False,
    )
    for chunk in reader:
        chunk["card6"] = chunk["card6"].fillna(NULL_SENTINEL).astype(str)
        pairs.update(map(tuple, chunk[["customer_id", "card6"]].drop_duplicates().to_numpy()))

    df = pd.DataFrame(sorted(pairs), columns=["customer_id", "card6"])
    # NULL_SENTINEL sorts before every printable value, which is exactly the
    # "nulls first, then ascending lexicographic" rule.
    df = df.sort_values(["customer_id", "card6"])
    df["card_idx"] = df.groupby("customer_id").cumcount() + 1
    df["card_id"] = df["customer_id"] + "-K" + df["card_idx"].astype(str)
    df.to_parquet(cache_dir / "card_index.parquet", index=False)
    print(f"wrote {len(df)} customer/card6 pairs for {df.customer_id.nunique()} customers")


if __name__ == "__main__":
    build()
