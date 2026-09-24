# Graph algorithms

The GDS library ships in the container at
`/home/tigergraph/gsql-graph-algorithms`. I create its queries on
FraudInvestigation unchanged (`install_gds.gsql`): `tg_wcc`, `tg_louvain`,
`tg_pagerank`, `tg_jaccard_nbor_ss`, `tg_cosine_nbor_ss`.

## Why a projection

WCC, Louvain and PageRank in the library run over one vertex type. The identity
graph is bipartite (Transaction to DeviceProfile), so `build_card_links.gsql`
projects it to Card to Card `SHARES_DEVICE` edges, keeping only specific
profiles (the same rules `ring_detect` uses). It only reads transactions before
2016-11-01, before any benchmark case opens, so the results are history and
cannot leak exam-window outcomes.

## Run

```
# once: adds SHARES_DEVICE and Card.wcc_component / louvain_community / device_pagerank
bash -c 'MSYS_NO_PATHCONV=1 docker cp graph/schema/schema_analytics.gsql hhgoa-tigergraph:/tmp/ && MSYS_NO_PATHCONV=1 docker exec -u tigergraph hhgoa-tigergraph /home/tigergraph/tigergraph/app/cmd/gsql /tmp/schema_analytics.gsql'
bash graph/queries/install_all.sh          # also creates install_gds.gsql and card_community.gsql
bash graph/algorithms/run_analytics.sh
```

## What it found (cutoff 2016-11-01)

- 283 profiles qualify and write 1,452 card pairs (1,411 distinct edges) over 476 cards.
- WCC glues the SM-G935F ring cards into a component of over 100 cards,
  because some ring cards also share a rare complete profile with others.
- Louvain separates them: the community of C11468-K2 (an August SM-G935F card)
  has 22 cards, including C12033-K1, C07762-K1 and C04311-K1, all on that profile.
- PageRank top cards: C06224-K2, C05704-K2, C02716-K2, C01155-K2, C11000-K2.

`card_community(card_id)` reads a card's WCC and Louvain mates back, ranked by
PageRank. Per-case ring detection at a case's own `as_of` stays in
`ring_detect`, which a precomputed global component cannot replace.

Case similarity uses TigerVector cosine (HNSW) over case embeddings in
`similar_cases`. `tg_jaccard_nbor_ss` is installed for neighbourhood overlap,
for example `source` an InvestigationCase, `e_type=CONNECTED_TO`,
`reverse_e_type=CONNECTED_FROM`, to find closed cases naming the same cards.
