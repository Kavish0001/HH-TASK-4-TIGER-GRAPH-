#!/usr/bin/env python3
"""Embed the case memory and the document corpus, and upsert both over REST.

Why REST and not a loading job for the documents: TigerGraph's file loader is
line based. A policy chunk is markdown and contains newlines, and a quoted
field spanning lines is silently dropped, not rejected. The first attempt
loaded 20 of 30 chunks with ERRORS=0, which is exactly the failure mode worth
avoiding. The REST payload is JSON, so newlines survive, and 37 documents is
not a bulk load by any reading.

Embeddings are BAAI/bge-small-en-v1.5, 384 dimensions, matching
EMBEDDING_MODEL in .env.example and the DIMENSION in graph/schema/schema.gsql.
bge wants an instruction prefix on the query side only, so the stored vectors
are plain passage embeddings and similar_cases is responsible for prefixing
its query text.

Run after graph/loading/load_all.gsql has loaded ClosedCase.
"""

import csv
import json
import os
import sys
import time

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
PREP = os.path.join(REPO, "graph", "prepared")
CASES = os.path.join(REPO, "data", "closed_cases_history.csv")

HOST = os.environ.get("TG_HOST", "http://localhost")
PORT = os.environ.get("TG_RESTPP_PORT", "14240")
GRAPH = os.environ.get("TG_GRAPH_NAME", "FraudInvestigation")
USER = os.environ.get("TG_USERNAME", "tigergraph")
PASSWORD = os.environ.get("TG_PASSWORD", "tigergraph")
MODEL = os.environ.get("EMBEDDING_MODEL", "BAAI/bge-small-en-v1.5")

URL = "%s:%s/restpp/graph/%s" % (HOST, PORT, GRAPH)
AUTH = (USER, PASSWORD)

# 5,565 cases times 384 floats is roughly 40 MB of JSON in one request, which
# RESTPP will refuse. 250 vertices per request keeps each body near 2 MB.
BATCH = 250


def post(payload):
    r = requests.post(URL, auth=AUTH, data=json.dumps(payload), timeout=300)
    r.raise_for_status()
    body = r.json()
    if body.get("error"):
        raise RuntimeError(body.get("message"))
    return body["results"][0]


def upsert(vtype, rows):
    """rows is a list of (primary_id, {attr: value})."""
    total = 0
    for i in range(0, len(rows), BATCH):
        chunk = rows[i:i + BATCH]
        payload = {"vertices": {vtype: {
            vid: {k: {"value": v} for k, v in attrs.items()} for vid, attrs in chunk}}}
        res = post(payload)
        total += res.get("accepted_vertices", 0)
        sys.stderr.write("  %s %d/%d\r" % (vtype, min(i + BATCH, len(rows)), len(rows)))
    sys.stderr.write("\n")
    return total


def closed_case_text(r):
    """What a closed case is retrieved *by*.

    The analyst note is the substance, but pattern and outcome carry most of
    the discriminating signal for "is this like my case", so they are stated in
    words rather than left for the note to imply. Exposure and card count go in
    because scale separates a one-transaction dispute from a ring.
    """
    return (
        "Fraud pattern: %s. Outcome: %s. Card %s, customer %s. "
        "%d transaction(s), exposure $%s. Actions taken: %s. "
        "Suspicious activity report filed: %s. Analyst notes: %s"
        % (r["pattern"] or "none", r["outcome"], r["card_id"], r["customer_id"],
           int(r["n_txns"] or 0), r["exposure_usd"] or "0",
           (r["actions_taken"] or "none").replace("|", ", "),
           r["report_filed"] or "No", r["analyst_notes"]))


def main():
    from sentence_transformers import SentenceTransformer

    t0 = time.time()
    model = SentenceTransformer(MODEL)
    dim = model.get_sentence_embedding_dimension()
    assert dim == 384, "schema declares DIMENSION=384, model gives %d" % dim
    sys.stderr.write("model %s loaded in %.1fs\n" % (MODEL, time.time() - t0))

    # ---- closed cases -------------------------------------------------------
    cases = list(csv.DictReader(open(CASES, encoding="utf-8")))
    texts = [closed_case_text(r) for r in cases]
    t0 = time.time()
    vecs = model.encode(texts, batch_size=64, normalize_embeddings=True,
                        show_progress_bar=False)
    sys.stderr.write("embedded %d closed cases in %.1fs\n" % (len(cases), time.time() - t0))
    n = upsert("ClosedCase", [(r["case_id"], {"emb": [float(x) for x in v]})
                              for r, v in zip(cases, vecs)])
    sys.stderr.write("ClosedCase embeddings upserted: %d\n" % n)

    # ---- fraud patterns -----------------------------------------------------
    pats = list(csv.DictReader(open(os.path.join(PREP, "fraud_patterns.csv"), encoding="utf-8")))
    ptexts = ["%s. %s Detectable signals: %s Policy rules: %s"
              % (r["title"], r["description"], r["signals"], r["policy_rules"]) for r in pats]
    pvecs = model.encode(ptexts, normalize_embeddings=True, show_progress_bar=False)
    n = upsert("FraudPattern", [
        (r["pattern"], {"title": r["title"], "description": r["description"],
                        "signals": r["signals"], "policy_rules": r["policy_rules"],
                        "emb": [float(x) for x in v]})
        for r, v in zip(pats, pvecs)])
    sys.stderr.write("FraudPattern upserted: %d\n" % n)

    # ---- policy and readme chunks ------------------------------------------
    chunks = list(csv.DictReader(open(os.path.join(PREP, "policy_chunks.csv"), encoding="utf-8")))
    ctexts = ["%s\n%s" % (r["title"], r["text"]) for r in chunks]
    cvecs = model.encode(ctexts, normalize_embeddings=True, show_progress_bar=False)
    n = upsert("PolicyChunk", [
        (r["chunk_id"], {"source": r["source"], "section": r["section"],
                         "title": r["title"], "text": r["text"], "ref": r["ref"],
                         "emb": [float(x) for x in v]})
        for r, v in zip(chunks, cvecs)])
    sys.stderr.write("PolicyChunk upserted: %d (of %d source rows)\n" % (n, len(chunks)))


if __name__ == "__main__":
    main()
