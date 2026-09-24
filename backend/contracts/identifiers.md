# Identifier derivation

Verified by `data-scout` against the real data. These are not conventions to pick;
they are facts about the dataset, and every ID in an answer file depends on them.
Made-up IDs score zero, and a wrong derivation produces IDs that look real.

## `card_id` is not a column

`transactions.csv` has no card identifier. It must be derived:

```
card_id = customer_id + "-K" + rank(card6)
```

where `rank` orders the distinct `card6` values held by that customer, nulls
ranked first, then ascending lexicographic, starting at 1.

Validated at 100 percent against all 5,565 closed cases, all 20 case-pack rows,
and all 92 `connected_card_ids` references. If a derived ID fails to match a known
closed case, the derivation is wrong, not the data.

## `customer_id`

A one to one relabeling of `card1`. 13,553 customers hold 14,317 cards, and 94.4
percent hold exactly one.

A customer is not a person. Transaction counts per customer reach 14,891 over six
months, which no individual cardholder produces. Treat `Customer` as an issuer
grouping. This matters when writing a SAR narrative: do not describe a customer
vertex as an individual.

## `ts`

```
ts = 2016-07-02 00:00:00 + TransactionDT seconds
```

Exact for all 590,742 rows. Use the arithmetic rather than parsing the string, and
use `TransactionDT` directly as the ordering key for the `NEXT` edge.

## `device_profile`

The composed string `DeviceInfo | id_30 | id_31 | id_33`, which is the exact shape
`connected_device_profiles` expects in the answer file.

`id_30` is 46.2 percent null and `id_33` is 49.1 percent null, so about half of all
profiles are partial. The composition rule for nulls must be decided once and
applied identically in the loader, the queries and the answer writer, or the same
device will get two different profile strings and the join will silently fail.

## Coverage facts that change joins

- 6,640 online transactions (4.4 percent) have no identity record. Online implies a
  device record 95.6 percent of the time, not always. HHG-002's flagged
  transaction is one of the exceptions.
- All 900 cleared closed cases have `first_fraud_txn_id` null, exposure 0.00 and a
  single transaction. Join outcomes on `txn_ids`.
- `addr1` is missing on 94.8 percent of `ProductCD=C` rows. Five case-pack cases
  therefore have no billing region data. "No region data" and "no history in this
  region" are different answers and must not share a code path.

## Excluded from evidence

The nine undocumented closed cases have transactions at `second == 0` at a rate of
100 percent against a 1.65 percent base rate, and two case-pack rows share it.
This is an artifact of how the dataset was generated.

It may be used as an internal sanity check. It must never appear in `evidence`, in
`pattern_description`, in `summary`, or in a SAR narrative. Citing a generation
artifact as grounds for a regulatory filing is indefensible.
