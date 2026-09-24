# Social posts

Replace `[LINK]` with the blog or demo video URL before posting.

## X

Built a fraud investigation agent on @TigerGraphDB for the @247pmstudio HHGOA challenge.

It walks from flagged cards to shared devices and past cases, scores risk and confidence apart, asks for evidence when unsure, and sends blocks and SARs to a human.

[LINK]

## LinkedIn

We built an agent that investigates card fraud alerts on TigerGraph, for the
@TigerGraphDB HHGOA agentic fraud investigation challenge by @247pmstudio.

Given an alert, it opens a case at the trigger time, runs GSQL queries through a
TigerGraph MCP server (customer history, shared device profiles, ring detection,
pattern checks, similar prior cases), and scores the case in code: a fraud
probability, a separate confidence, and a list of what it still does not know.
When the evidence is not enough, it asks for more before acting. Actions follow
the bank's written policy: low-impact steps run automatically, card blocks and
regulatory reports wait for an analyst. When a report is required it drafts one,
and each finished case is written back to the graph so later cases can find it.

A few things the data taught us:

- The bank's risk score is a reason to look, not an answer. One of the clearest
  fraud cases scored 0.05.
- Half of all device profiles are shared by more than one card, so "shared
  device" alone means little. One specific phone profile on 52 cardholders,
  always new and always behind an anonymous proxy, meant a lot.
- Most false alarms were travel or a new phone. An agent that treats every new
  region or device as fraud blocks the wrong people.

What it does not do: customer replies are simulated (the dataset has none), and
the actions are stubs, not real bank systems. The language model only writes the
explanations; the scores, thresholds and routing are code.

Stack: TigerGraph 4.2.5 in Docker, GSQL, graph algorithms (WCC, Louvain,
PageRank), TigerGraph vectors, TigerGraph MCP, LangGraph, FastAPI, Next.js.

Write-up and demo: [LINK]

#TigerGraph #GraphDatabase #FraudDetection
