#!/usr/bin/env python3
"""Turn the three source CSVs into narrow files a GSQL loading job can read fast.

Three things force a prep step; none of them can be done inside a loading job.

1. transactions.csv has no card_id column. The case pack and the closed cases
   both key on card_id (C01234-K1), so it has to be reconstructed. The rule was
   recovered from the data, see derive_card_id below.
2. The NEXT edge needs each transaction's predecessor on the same card in ts
   order. A loading job sees one row at a time and cannot look backwards. One
   streaming pass does it for free because the source file is already sorted by
   ts (verified: zero out-of-order steps across all 590,742 rows).
3. 339 V columns are dead weight in memory. Dropping them here means the loader
   never parses them.

closed_cases_history.csv is loaded from its original path; only its pipe-packed
txn_ids and connected_card_ids columns are exploded into edge files here.
"""

import csv
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
SRC = os.path.join(REPO, "data")
OUT = os.path.join(REPO, "graph", "prepared")

TXN = os.path.join(SRC, "transactions.csv")
IDENTITY = os.path.join(SRC, "identity.csv")
CASES = os.path.join(SRC, "closed_cases_history.csv")

C_COLS = ["C%d" % i for i in range(1, 15)]
D_COLS = ["D%d" % i for i in range(1, 16)]
M_COLS = ["M%d" % i for i in range(1, 10)]

EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def to_epoch(ts):
    # ts is always "YYYY-MM-DD HH:MM:SS" in this file. Hand-slicing beats
    # strptime by roughly 5x over 590k rows and the format is fixed.
    y = int(ts[0:4]); mo = int(ts[5:7]); d = int(ts[8:10])
    h = int(ts[11:13]); mi = int(ts[14:16]); s = int(ts[17:19])
    dt = datetime(y, mo, d, h, mi, s, tzinfo=timezone.utc)
    return int((dt - EPOCH).total_seconds())


def pass1_card6_sets():
    """Collect each customer's distinct card6 values.

    The K index in a card_id is the 1-based position of that row's card6 value
    in the customer's sorted distinct card6 list, empty string sorting first.
    Validated against every labelled transaction in closed_cases_history.csv
    plus the 20 case-pack rows: 14975 of 14975 exact. No other column in the
    file reproduces the labels.
    """
    sets = defaultdict(set)
    with open(TXN, "r", encoding="utf-8", newline="") as f:
        header = f.readline().rstrip("\r\n").split(",")
        i_card6 = header.index("card6")
        i_cust = header.index("customer_id")
        n_cols = len(header)
        tail_from_end = n_cols - i_cust  # customer_id counted from the right
        for line in f:
            line = line.rstrip("\r\n")
            # card6 is column 10, so stop splitting there instead of building
            # 397 field objects per row.
            head = line.split(",", i_card6 + 1)
            card6 = head[i_card6]
            cust = line.rsplit(",", tail_from_end)[1]
            sets[cust].add(card6)
    return {c: sorted(v) for c, v in sets.items()}


def main():
    os.makedirs(OUT, exist_ok=True)

    sys.stderr.write("pass 1: recovering card6 sets per customer\n")
    card6_by_cust = pass1_card6_sets()
    sys.stderr.write("  customers: %d\n" % len(card6_by_cust))

    f_txn = open(os.path.join(OUT, "transactions_slim.csv"), "w", encoding="utf-8", newline="")
    f_card = open(os.path.join(OUT, "cards.csv"), "w", encoding="utf-8", newline="")
    f_cust = open(os.path.join(OUT, "customers.csv"), "w", encoding="utf-8", newline="")

    w_txn = csv.writer(f_txn, lineterminator="\n")
    w_card = csv.writer(f_card, lineterminator="\n")
    w_cust = csv.writer(f_cust, lineterminator="\n")

    w_txn.writerow(
        ["txn_id", "card_id", "customer_id", "ts", "ts_epoch", "amt", "product_cd",
         "channel", "risk_score", "addr1", "addr2", "dist1", "dist2",
         "p_email", "r_email", "prev_txn_id"] + C_COLS + D_COLS + M_COLS)
    w_card.writerow(["card_id", "customer_id", "card1", "card2", "card3", "card4",
                     "card5", "card6", "first_ts", "last_ts", "n_txns"])
    w_cust.writerow(["customer_id", "card1", "n_cards", "first_ts", "last_ts", "n_txns"])

    with open(TXN, "r", encoding="utf-8", newline="") as f:
        header = f.readline().rstrip("\r\n").split(",")
        ix = {h: i for i, h in enumerate(header)}
        i_c = [ix[c] for c in C_COLS]
        i_d = [ix[c] for c in D_COLS]
        i_m = [ix[c] for c in M_COLS]
        i_cust, i_ts, i_card6 = ix["customer_id"], ix["ts"], ix["card6"]

        prev_on_card = {}
        cards = {}
        custs = {}
        n = 0

        for line in f:
            p = line.rstrip("\r\n").split(",")
            cust = p[i_cust]
            card6 = p[i_card6]
            k = card6_by_cust[cust].index(card6) + 1
            card_id = "%s-K%d" % (cust, k)

            txn_id = p[0]
            ts = p[i_ts]
            prev = prev_on_card.get(card_id, "")
            prev_on_card[card_id] = txn_id

            w_txn.writerow(
                [txn_id, card_id, cust, ts, to_epoch(ts), p[ix["TransactionAmt"]],
                 p[ix["ProductCD"]], p[ix["channel"]], p[ix["risk_score"]],
                 p[ix["addr1"]], p[ix["addr2"]], p[ix["dist1"]], p[ix["dist2"]],
                 p[ix["P_emaildomain"]], p[ix["R_emaildomain"]], prev]
                + [p[i] for i in i_c] + [p[i] for i in i_d] + [p[i] for i in i_m])

            rec = cards.get(card_id)
            if rec is None:
                cards[card_id] = [card_id, cust, p[ix["card1"]], p[ix["card2"]],
                                  p[ix["card3"]], p[ix["card4"]], p[ix["card5"]],
                                  card6, ts, ts, 1]
            else:
                rec[9] = ts
                rec[10] += 1
                # card4/card5 are blank on a minority of rows; keep the first
                # non-blank so the Card vertex is not stamped with a gap.
                if not rec[5] and p[ix["card4"]]:
                    rec[5] = p[ix["card4"]]
                if not rec[6] and p[ix["card5"]]:
                    rec[6] = p[ix["card5"]]

            crec = custs.get(cust)
            if crec is None:
                custs[cust] = [cust, p[ix["card1"]], 0, ts, ts, 1]
            else:
                crec[4] = ts
                crec[5] += 1

            n += 1
            if n % 100000 == 0:
                sys.stderr.write("  %d rows\n" % n)

    for card_id, rec in cards.items():
        w_card.writerow(rec)
    for cust, crec in custs.items():
        crec[2] = len(card6_by_cust[cust])
        w_cust.writerow(crec)

    f_txn.close(); f_card.close(); f_cust.close()
    sys.stderr.write("pass 2 done: %d transactions, %d cards, %d customers\n"
                     % (n, len(cards), len(custs)))

    # Closed cases: explode the two pipe-packed columns into edge files. The
    # scalar columns load straight from the original CSV.
    n_ct = n_cc = n_case = 0
    with open(CASES, "r", encoding="utf-8", newline="") as f, \
         open(os.path.join(OUT, "case_txns.csv"), "w", encoding="utf-8", newline="") as ft, \
         open(os.path.join(OUT, "case_cards.csv"), "w", encoding="utf-8", newline="") as fc:
        wt = csv.writer(ft, lineterminator="\n")
        wc = csv.writer(fc, lineterminator="\n")
        wt.writerow(["case_id", "txn_id"])
        wc.writerow(["case_id", "card_id", "role"])
        for r in csv.DictReader(f):
            n_case += 1
            for t in r["txn_ids"].split("|"):
                t = t.strip()
                if t:
                    wt.writerow([r["case_id"], t]); n_ct += 1
            wc.writerow([r["case_id"], r["card_id"], "primary"]); n_cc += 1
            for c in r["connected_card_ids"].split("|"):
                c = c.strip()
                if c:
                    wc.writerow([r["case_id"], c, "connected"]); n_cc += 1
    sys.stderr.write("closed cases: %d rows, %d case-txn links, %d case-card links\n"
                     % (n_case, n_ct, n_cc))

    with open(os.path.join(OUT, "MANIFEST.txt"), "w", encoding="utf-8") as f:
        f.write("transactions_slim.csv rows=%d\ncards.csv rows=%d\ncustomers.csv rows=%d\n"
                "case_txns.csv rows=%d\ncase_cards.csv rows=%d\nclosed cases rows=%d\n"
                % (n, len(cards), len(custs), n_ct, n_cc, n_case))


if __name__ == "__main__":
    main()
