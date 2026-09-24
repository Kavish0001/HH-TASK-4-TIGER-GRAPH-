#!/usr/bin/env python3
"""Turn the three source CSVs into narrow files a GSQL loading job can read fast.

Four things force a prep step; none can be done inside a loading job.

1. transactions.csv has no card_id column. The case pack and the closed cases
   both key on card_id (C01234-K1), so it has to be reconstructed. The rule was
   recovered from the data and is documented on derive_card_id below. Every
   card_id in an answer file depends on it.
2. The NEXT edge needs each transaction's predecessor on the same card in ts
   order. A loading job sees one row at a time and cannot look backwards. One
   streaming pass does it for free because the source file is already sorted by
   ts (verified: zero out-of-order steps across all 590,742 rows).
3. 339 V columns are dead weight. The host has 7.6 GB of RAM and TigerGraph
   already holds 3 GB of it, so only the shortlist from
   plans/findings/dataset.md section 8 is carried. Dropping the rest here means
   the loader never parses them.
4. The device profile string has to substitute a null token for the four parts,
   and roughly half of all identity rows have at least one part missing. Doing
   it here rather than with gsql_concat keeps one definition of the token.

closed_cases_history.csv is loaded from its original path; only its pipe-packed
txn_ids and connected_card_ids columns are exploded into edge files here.
"""

import csv
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
SRC = os.path.join(REPO, "data")
OUT = os.path.join(REPO, "graph", "prepared")

TXN = os.path.join(SRC, "transactions.csv")
IDENTITY = os.path.join(SRC, "identity.csv")
CASES = os.path.join(SRC, "closed_cases_history.csv")

# A TigerGraph FLOAT attribute cannot be null, and 0 is a real value for most
# of these columns, so a missing reading is written as this sentinel and the
# queries render it back to JSON null. Every D and V column here is
# non-negative in the source file, so no true value can collide with it.
NULL_NUM = -999999.0

# Null token for the four device-profile parts. The scout report used the same
# token throughout and half of all profiles are partial, so it shows up often.
NULL_TOK = "unknown"

# Shortlist from plans/findings/dataset.md section 8, risk-matched correlation.
# C1..C14 are dropped wholesale (max |r| 0.105, the weakest group in the file).
# M1, M2, M3, M5, M6, M8, M9 dropped as weak and mostly null.
D_COLS = ["D1", "D2", "D3", "D5", "D7", "D11"]
M_COLS = ["M4", "M7"]
V_COLS = ["V10", "V48", "V70", "V95", "V96", "V97", "V100", "V101", "V102",
          "V103", "V139", "V140", "V146", "V147", "V279", "V283", "V284",
          "V285", "V287", "V288", "V289", "V312"]

# ts = 2016-07-02 00:00:00 + TransactionDT seconds, exactly, for every row.
# Verified by the scout over all 590,742 rows: one distinct offset. Deriving ts
# this way is about four times cheaper than parsing the ts string.
DATASET_START = datetime(2016, 7, 2, 0, 0, 0, tzinfo=timezone.utc)
START_EPOCH = int((DATASET_START - datetime(1970, 1, 1, tzinfo=timezone.utc)).total_seconds())


def num(s):
    return s if s else repr(NULL_NUM)


def pass1_card6_sets():
    """Collect each customer's distinct card6 values.

    The K index in a card_id is the 1-based position of that row's card6 value
    in the customer's sorted distinct card6 list, empty string sorting first.
    Validated against every labelled transaction in closed_cases_history.csv
    plus the 20 case-pack rows: 14,975 of 14,975 exact, and the scout confirmed
    it independently against all 92 connected_card_ids references. No other
    column in the file reproduces the labels.
    """
    sets = defaultdict(set)
    with open(TXN, "r", encoding="utf-8", newline="") as f:
        header = f.readline().rstrip("\r\n").split(",")
        i_card6 = header.index("card6")
        i_cust = header.index("customer_id")
        tail_from_end = len(header) - i_cust  # customer_id counted from the right
        for line in f:
            line = line.rstrip("\r\n")
            # card6 is column 10, so stop splitting there instead of building
            # 397 field objects per row.
            card6 = line.split(",", i_card6 + 1)[i_card6]
            cust = line.rsplit(",", tail_from_end)[1]
            sets[cust].add(card6)
    return {c: sorted(v) for c, v in sets.items()}


def write_transactions(card6_by_cust, identity):
    f_txn = open(os.path.join(OUT, "transactions_slim.csv"), "w", encoding="utf-8", newline="")
    f_card = open(os.path.join(OUT, "cards.csv"), "w", encoding="utf-8", newline="")
    f_cust = open(os.path.join(OUT, "customers.csv"), "w", encoding="utf-8", newline="")
    w_txn = csv.writer(f_txn, lineterminator="\n")
    w_card = csv.writer(f_card, lineterminator="\n")
    w_cust = csv.writer(f_cust, lineterminator="\n")

    # Column order is the Transaction vertex attribute order in
    # graph/schema/schema.gsql, with prev_txn_id parked last. That lets the
    # loading job write VALUES($0, $1, ... $52) instead of threading fifty
    # placeholders past a mid-row skip, which is where this kind of load
    # silently puts the wrong column in the wrong attribute.
    w_txn.writerow(
        ["txn_id", "card_id", "customer_id", "ts", "ts_epoch", "hour_of_day", "amt",
         "product_cd", "channel", "risk_score", "addr1", "addr2", "dist1", "dist2",
         "p_email", "r_email", "has_identity", "device_profile", "device_type",
         "id_12", "id_15", "id_23", "id_34"]
        + D_COLS + M_COLS + V_COLS + ["prev_txn_id"])
    w_card.writerow(["card_id", "customer_id", "card1", "card2", "card3", "card4",
                     "card5", "card6", "first_ts", "last_ts", "n_txns"])
    w_cust.writerow(["customer_id", "card1", "n_cards", "first_ts", "last_ts", "n_txns"])

    with open(TXN, "r", encoding="utf-8", newline="") as f:
        header = f.readline().rstrip("\r\n").split(",")
        ix = {h: i for i, h in enumerate(header)}
        i_d = [ix[c] for c in D_COLS]
        i_m = [ix[c] for c in M_COLS]
        i_v = [ix[c] for c in V_COLS]
        i_cust, i_card6, i_dt = ix["customer_id"], ix["card6"], ix["TransactionDT"]
        i_amt, i_prod, i_ch, i_risk = ix["TransactionAmt"], ix["ProductCD"], ix["channel"], ix["risk_score"]
        i_a1, i_a2, i_d1, i_d2 = ix["addr1"], ix["addr2"], ix["dist1"], ix["dist2"]
        i_pe, i_re = ix["P_emaildomain"], ix["R_emaildomain"]
        i_c1, i_c2, i_c3, i_c4, i_c5 = ix["card1"], ix["card2"], ix["card3"], ix["card4"], ix["card5"]

        prev_on_card = {}
        cards, custs = {}, {}
        n = 0
        n_matched = 0
        for line in f:
            p = line.rstrip("\r\n").split(",")
            cust = p[i_cust]
            card6 = p[i_card6]
            card_id = "%s-K%d" % (cust, card6_by_cust[cust].index(card6) + 1)

            txn_id = p[0]
            epoch = START_EPOCH + int(p[i_dt])
            dt = DATASET_START + timedelta(seconds=int(p[i_dt]))
            ts = dt.strftime("%Y-%m-%d %H:%M:%S")

            prev = prev_on_card.get(card_id, "")
            prev_on_card[card_id] = txn_id

            # 6,640 online transactions have no identity row, HHG-002's
            # flagged transaction among them, so this is a left join.
            ident = identity.get(txn_id)
            if ident is None:
                dev = ["false", "", "", "", "", "", ""]
            else:
                dev = ["true"] + ident
                n_matched += 1

            w_txn.writerow(
                [txn_id, card_id, cust, ts, epoch, dt.hour, p[i_amt], p[i_prod],
                 p[i_ch], p[i_risk], p[i_a1], p[i_a2], num(p[i_d1]), num(p[i_d2]),
                 p[i_pe], p[i_re]] + dev
                + [num(p[i]) for i in i_d] + [p[i] for i in i_m]
                + [num(p[i]) for i in i_v] + [prev])

            rec = cards.get(card_id)
            if rec is None:
                cards[card_id] = [card_id, cust, p[i_c1], p[i_c2], p[i_c3],
                                  p[i_c4], p[i_c5], card6, ts, ts, 1]
            else:
                rec[9] = ts
                rec[10] += 1
                # card4 and card5 are blank on a minority of rows; keep the
                # first non-blank so the Card vertex is not stamped with a gap.
                if not rec[5] and p[i_c4]:
                    rec[5] = p[i_c4]
                if not rec[6] and p[i_c5]:
                    rec[6] = p[i_c5]

            crec = custs.get(cust)
            if crec is None:
                custs[cust] = [cust, p[i_c1], 0, ts, ts, 1]
            else:
                crec[4] = ts
                crec[5] += 1

            n += 1
            if n % 100000 == 0:
                sys.stderr.write("  %d rows\n" % n)

    for rec in cards.values():
        w_card.writerow(rec)
    for cust, crec in custs.items():
        crec[2] = len(card6_by_cust[cust])
        w_cust.writerow(crec)

    f_txn.close(); f_card.close(); f_cust.close()
    return n, len(cards), len(custs), n_matched


def read_identity():
    """Compose the device profile and keep only the identity fields that reason.

    Returns txn_id -> [device_profile, device_type, id_12, id_15, id_23, id_34]
    so the transaction pass can left join it in memory. 144,432 rows is small
    enough to hold, and merging here means the loading job writes Transaction
    once instead of writing it and then updating it, which with 53 attributes
    is where a positional loader goes wrong.
    """
    profiles = {}
    byid = {}
    with open(IDENTITY, "r", encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            parts = [(row["DeviceInfo"] or "").strip() or NULL_TOK,
                     (row["id_30"] or "").strip() or NULL_TOK,
                     (row["id_31"] or "").strip() or NULL_TOK,
                     (row["id_33"] or "").strip() or NULL_TOK]
            profile = " | ".join(parts)
            profiles[profile] = parts
            byid[row["TransactionID"]] = [
                profile,
                (row["DeviceType"] or "").strip() or NULL_TOK,
                (row["id_12"] or "").strip() or NULL_TOK,
                (row["id_15"] or "").strip() or NULL_TOK,
                (row["id_23"] or "").strip() or NULL_TOK,
                (row["id_34"] or "").strip() or NULL_TOK]

    with open(os.path.join(OUT, "device_profiles.csv"), "w", encoding="utf-8", newline="") as out:
        w = csv.writer(out, lineterminator="\n")
        w.writerow(["device_profile", "device_info", "os", "browser", "screen"])
        for profile, parts in profiles.items():
            w.writerow([profile] + parts)
    return byid, len(profiles)


def write_case_edges():
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
            # All 900 cleared cases have first_fraud_txn_id null, so txn_ids is
            # the only column that links a cleared case to its transaction.
            for t in r["txn_ids"].split("|"):
                t = t.strip()
                if t:
                    wt.writerow([r["case_id"], t]); n_ct += 1
            wc.writerow([r["case_id"], r["card_id"], "primary"]); n_cc += 1
            for c in r["connected_card_ids"].split("|"):
                c = c.strip()
                if c:
                    wc.writerow([r["case_id"], c, "connected"]); n_cc += 1
    return n_case, n_ct, n_cc


def main():
    os.makedirs(OUT, exist_ok=True)

    sys.stderr.write("pass 1: recovering card6 sets per customer\n")
    card6_by_cust = pass1_card6_sets()
    sys.stderr.write("  customers: %d\n" % len(card6_by_cust))

    identity, n_prof = read_identity()
    sys.stderr.write("identity: %d rows, %d distinct device profiles\n"
                     % (len(identity), n_prof))

    sys.stderr.write("pass 2: transactions, cards, customers\n")
    n_txn, n_card, n_cust, n_matched = write_transactions(card6_by_cust, identity)
    sys.stderr.write("  %d transactions, %d cards, %d customers, %d with identity\n"
                     % (n_txn, n_card, n_cust, n_matched))
    n_id = len(identity)

    n_case, n_ct, n_cc = write_case_edges()
    sys.stderr.write("closed cases: %d rows, %d case-txn links, %d case-card links\n"
                     % (n_case, n_ct, n_cc))

    with open(os.path.join(OUT, "MANIFEST.txt"), "w", encoding="utf-8") as f:
        for k, v in [("transactions_slim.csv", n_txn), ("cards.csv", n_card),
                     ("customers.csv", n_cust), ("identity rows merged", n_id),
                     ("device_profiles.csv", n_prof), ("case_txns.csv", n_ct),
                     ("case_cards.csv", n_cc), ("closed_cases_history.csv", n_case)]:
            f.write("%s rows=%d\n" % (k, v))


if __name__ == "__main__":
    main()
