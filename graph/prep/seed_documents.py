#!/usr/bin/env python3
"""Cut data/README.md into the FraudPattern and PolicyChunk rows.

The policy, the pattern descriptions and the answer format all live in one
markdown file, and the agent has to cite them by rule number. Extracting them
mechanically rather than retyping them means a chunk's text is provably what
the graders read, and `ref` is a string the agent can put straight into
evidence[].ref.

Each policy rule R1 to R10 becomes its own chunk, because a rule number is the
unit an action's `reason` cites.
"""

import csv
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
README = os.path.join(REPO, "data", "README.md")
OUT = os.path.join(REPO, "graph", "prepared")

# Signals are my operationalisation, not the README's words. They are kept
# separate from `description` so an evidence claim can quote the README text
# and describe the detector separately.
PATTERNS = [
    ("card_testing", "Card testing",
     "A stolen card number is checked before use: three or more tiny online "
     "authorizations, often under $5, then a larger purchase. Confirmed by the "
     "sequence itself.",
     "Three or more online authorizations small relative to the card's own "
     "history inside one hour, followed within 24 hours by a purchase far above "
     "that run. The absolute $5 threshold in R5 fires on only 53 sequences in "
     "the whole file and matches confirmed fraud about one time in ten, so the "
     "matcher scores the run against the card's own amount distribution as well "
     "as against $5.",
     "R5"),
    ("card_not_present_fraud", "Card-not-present fraud",
     "The number is used online without the card. Amounts and products that "
     "don't fit the cardholder's history, often in a burst of two to four "
     "within 48 hours. On its own, one unusual online purchase is ambiguous: "
     "verify.",
     "Online channel, amount high against the customer baseline, product code "
     "or recipient email the card has not used, two to four transactions inside "
     "48 hours. A single unusual online purchase scores as ambiguous by design.",
     "R1 R2 R3 R4"),
    ("card_not_present_new_device", "Card-not-present fraud from a new device",
     "Same as card-not-present fraud, with the identity record marking the "
     "device as New for this account, sometimes behind a proxy. Stronger than "
     "the plain pattern, still not proof: people buy new phones.",
     "Everything card_not_present_fraud requires, plus id_15 = New on the "
     "flagged transaction and a device profile the card has not used before "
     "as of the investigation time. id_23 carrying ANONYMOUS or HIDDEN raises "
     "it further. Device sharing alone is not a signal: 49.4% of device "
     "profiles are shared by more than one card and the largest is shared by "
     "1,013, so the matcher weights how specific the profile is.",
     "R1 R2"),
    ("out_of_region_use", "Out-of-region use",
     "Card-present purchases in a billing region the cardholder has no history "
     "in, while their normal activity continues at home. Several days of "
     "purchases in one new region is a trip, not a clone.",
     "addr1 absent from the card's history before the investigation time, plus "
     "activity continuing in the home region inside the same window. Conditioned "
     "on how many regions the customer already uses: 61.9% use exactly one, and "
     "for a customer already spanning ten a new region means almost nothing. "
     "716 of the 900 cleared closed cases were cleared as cardholder travel, "
     "which is this pattern's false positive.",
     "R2 R3"),
    ("account_takeover", "Account takeover",
     "Mixed-channel activity inconsistent with the cardholder, often with "
     "device and match-flag anomalies, pointing to stolen credentials rather "
     "than a stolen number.",
     "Channel mix changing against the card's baseline, a new device profile, "
     "id_34 match status or M4/M7 flags shifting, and activity spread across "
     "product codes the card has not used. Distinguished from plain "
     "card-not-present fraud by the card-present leg continuing.",
     "R1 R2 R10"),
    ("undocumented", "Undocumented pattern",
     "Activity the evidence shows to be coordinated or repeated abuse but which "
     "fits none of the five documented patterns. Describe it in your own words "
     "and do not force it into a known category.",
     "Anything the five matchers score low on while ring_detect or velocity "
     "shows structure: a repeated amount band, a fixed cadence, one device "
     "across unrelated customers. Requires a written pattern_description.",
     "R9"),
    ("none", "No fraud pattern",
     "The alert is a false alarm. The activity matches the cardholder's own "
     "history or has an innocent explanation.",
     "All five matchers score low and the flagged transaction sits inside the "
     "card's normal amount, product, region and device behaviour.",
     "R3 R7"),
]


def read_readme():
    with open(README, "r", encoding="utf-8") as f:
        return f.read()


def policy_chunks(text):
    chunks = []

    def add(chunk_id, source, section, title, body, ref):
        body = body.strip()
        if body:
            chunks.append([chunk_id, source, section, title, body, ref])

    # The Fraud Policy runs from its own H1 to the Answer Format H1.
    policy = text.split("# Fraud Policy", 1)[1].split("# Answer Format", 1)[0]

    # R1 to R10 are the citable units. Each starts with **Rn. and runs to the
    # next one or to the end of section 3.
    rules_block = policy.split("### 3. Rules", 1)[1].split("### 3a.", 1)[0]
    parts = re.split(r"\n(?=\*\*R\d+\.)", rules_block)
    for part in parts:
        m = re.match(r"\*\*(R\d+)\.\s*([^*]+)\*\*", part.strip())
        if not m:
            continue
        rule, title = m.group(1), m.group(2).strip().rstrip(".")
        add("policy:%s" % rule, "fraud_policy", "3. Rules", "%s %s" % (rule, title),
            part, "policy:%s" % rule)

    # The remaining policy sections, each by its own heading.
    for heading, body in re.findall(r"### ([^\n]+)\n(.*?)(?=\n### |\Z)", policy, re.S):
        if heading.startswith("3. Rules"):
            continue
        slug = re.sub(r"[^a-z0-9]+", "_", heading.lower()).strip("_")
        add("policy:%s" % slug, "fraud_policy", heading, heading, body,
            "policy:%s" % slug)

    # The action table and the approval routes are what next_best_actions must
    # agree with, so they are retrievable on their own.
    patterns_block = text.split("## The five known fraud patterns", 1)[1].split("## Regulatory references", 1)[0]
    add("readme:known_patterns", "readme", "The five known fraud patterns",
        "The five known fraud patterns", patterns_block, "readme:known_patterns")

    for heading in ["Things to know", "Suggested graph schema", "The columns we added",
                    "`closed_cases_history.csv`", "The case pack", "Glossary"]:
        m = re.search(r"## %s\n(.*?)(?=\n## |\Z)" % re.escape(heading), text, re.S)
        if m:
            slug = re.sub(r"[^a-z0-9]+", "_", heading.lower()).strip("_")
            add("readme:%s" % slug, "readme", heading, heading, m.group(1),
                "readme:%s" % slug)

    answer = text.split("# Answer Format", 1)[1]
    for heading, body in re.findall(r"#### ([^\n]+)\n(.*?)(?=\n#### |\n### |\Z)", answer, re.S):
        slug = re.sub(r"[^a-z0-9]+", "_", heading.lower()).strip("_")
        add("answer:%s" % slug, "answer_format", heading, heading, body,
            "answer:%s" % slug)

    return chunks


def main():
    os.makedirs(OUT, exist_ok=True)
    text = read_readme()

    with open(os.path.join(OUT, "fraud_patterns.csv"), "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(["pattern", "title", "description", "signals", "policy_rules"])
        for row in PATTERNS:
            w.writerow(row)

    chunks = policy_chunks(text)
    with open(os.path.join(OUT, "policy_chunks.csv"), "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(["chunk_id", "source", "section", "title", "text", "ref"])
        for row in chunks:
            w.writerow(row)

    print("fraud_patterns.csv rows=%d" % len(PATTERNS))
    print("policy_chunks.csv rows=%d" % len(chunks))
    for c in chunks:
        print("  %-34s %6d chars  %s" % (c[0], len(c[4]), c[3][:50]))


if __name__ == "__main__":
    main()
