# TigerGraph MCP for the fraud investigation graph

Two MCP servers sit on the same installed queries. Use the first for the agent;
the second is the official TigerGraph server and is there for inspection and ad
hoc work.

| Server | What the model sees | Why |
|---|---|---|
| `fraud-investigation-graph` (`graph/mcp/server.py`) | 16 tools named for investigation steps (`customer_profile`, `ring_detect`, `pattern_match`, `write_case`, ...), each with a description saying what it returns and when to use it | This is what `backend/tools/factory.py` `McpBackend` connects to. Tool names match `backend/tools/base.py` exactly, so the agent code does not change |
| `tigergraph` ([tigergraph/tigergraph-mcp](https://github.com/tigergraph/tigergraph-mcp)) | Generic tools: `tigergraph__run_installed_query`, `tigergraph__show_query`, `tigergraph__get_query_description`, schema and statistics tools | The official route. I write the same descriptions onto every installed query (step 4), so `get_query_description` shows the model the same text |

I wrote the first server because the official one exposes generic tools. A model
given `run_installed_query` has to know our query names, parameter types and
output layout, and the same server can also run arbitrary GSQL, which an
investigating agent has no business doing. Both servers go through RESTPP to the
same installed queries.

## 1. Prerequisites

- The container is up and the graph is loaded (see `infra/README.md`). Check:
  ```
  curl -s -u tigergraph:tigergraph -X POST http://localhost:14240/restpp/builtins/FraudInvestigation -d '{"function":"stat_vertex_number","type":"*"}'
  ```
  Expect Transaction 590742, Card 14317, Customer 13553, ClosedCase 5565,
  DeviceProfile 9706, PolicyChunk 30, FraudPattern 7.
- Vectors are in: `python graph/prep/embed_and_upsert.py` has been run once.
- Python 3.10 or later.

## 2. Install the queries

From the repo root, in Git Bash:

```
bash graph/queries/install_all.sh
```

This creates every query in `graph/queries/0*.gsql` and runs
`INSTALL QUERY ALL` (3 to 6 minutes). For the analytics layer (optional, see
`graph/algorithms/README.md`):

```
bash graph/algorithms/run_analytics.sh
```

## 3. Python packages

```
pip install tigergraph-mcp uvicorn starlette sentence-transformers requests python-dotenv
pip install langchain-mcp-adapters      # only for the agent's McpBackend
```

`tigergraph-mcp` pulls `pyTigerGraph>=2.0.4` and the newest `mcp` (2.x). The
backend pins `langchain-mcp-adapters==0.1.7`, which fails to import against
`mcp` 2.x (`streamablehttp_client` is gone), so pin it back:

```
pip install "mcp>=1.9,<2"
```

`server.py` runs on either generation (`MCPServer` on 2.x, `FastMCP` on 1.x),
and `tigergraph-mcp` 1.0.3 supports both.

## 4. Put the descriptions on the installed queries

```
python graph/mcp/set_query_descriptions.py
```

Descriptions live in `graph/mcp/descriptions.py`, one source for both servers.

## 5. Run the agent-facing server

```
python graph/mcp/server.py                     # SSE on http://127.0.0.1:8765/sse
python graph/mcp/server.py --transport stdio   # for Claude Desktop, Cursor, Claude Code
```

It reads `TG_HOST`, `TG_RESTPP_PORT`, `TG_GRAPH_NAME`, `TG_USERNAME`,
`TG_PASSWORD` from `.env`. An empty `TG_PASSWORD` falls back to the container
default `tigergraph`.

The first `similar_cases` or `policy_lookup` call loads `BAAI/bge-small-en-v1.5`
(about 15 seconds). After that each call is well under a second.

## 6. Point the agent at it

In `.env`:

```
TOOL_BACKEND=mcp
MCP_SERVER_URL=http://localhost:8765/sse
```

`McpBackend` loads the tools through `langchain-mcp-adapters` and reads `ref`
from each result, which every tool here returns.

One fix is needed in `backend/tools/factory.py` (not in this lane): MCP tools
from `langchain-mcp-adapters` are async only, so `tool.invoke(...)` raises
`StructuredTool does not support sync invocation`. The call that works, verified
against this server:

```python
payload = asyncio.run(tool.ainvoke({k: v for k, v in args.items() if v is not None}))
```

Each result is JSON text with an `ok` field; a failed query comes back as
`{"ok": false, "ref": ..., "error": ...}` rather than an exception, so the
backend should map `ok` onto `ToolResult.ok`.

The same tools are also available in-process without MCP, through
`backend.tools.tg.backend.TigerGraphBackend`, which implements `ToolBackend`
directly. `factory.py` does not offer it as a `TOOL_BACKEND` value yet; the MCP
route needs no code change.

## 7. The official server

`graph/mcp/mcp_config.json` has both servers in the `mcpServers` layout that
Claude Desktop, Claude Code and Cursor read. Note that `tigergraph-mcp` reads
`TG_GRAPHNAME` (no underscore) while this repo's `.env` uses `TG_GRAPH_NAME`, so
the config sets it explicitly. I limit it with `TG_ALLOWED_TOOLS` to read-only
tools so a model on that route cannot drop or rewrite queries.

HTTP instead of stdio:

```
TG_HOST=http://localhost TG_RESTPP_PORT=14240 TG_GS_PORT=14240 TG_GRAPHNAME=FraudInvestigation \
TG_USERNAME=tigergraph TG_PASSWORD=tigergraph \
tigergraph-mcp --transport streamable-http --port 8766
```

Then a call looks like
`tigergraph__run_installed_query(query_name="ring_detect", params={"card_id": "C13487-K1", "as_of": "2016-11-22 22:11:00"})`.
On this route VERTEX parameters (`customer_id`, `card_id`, `txn_id`,
`device_profile` on `shared_device_profile`) are passed as plain id strings.
pyTigerGraph 2.0.4 logs a deprecation warning for that form and retries over
GET, which works; I verified `get_query_description` and
`run_installed_query(behavior_shift ...)` through this server over stdio.

## Tool to query map

| Tool | Installed query | File |
|---|---|---|
| customer_profile | customer_profile | 01_entity.gsql |
| tx_context | tx_context | 01_entity.gsql |
| card_window | card_window | 01_entity.gsql |
| velocity | velocity | 01_entity.gsql |
| behavior_shift | behavior_shift | 01_entity.gsql |
| region_history | region_history | 01_entity.gsql |
| shared_device_profile | shared_device_profile | 02_network.gsql |
| ring_detect | ring_detect | 02_network.gsql |
| prior_cases_for_entities | prior_cases_for_entities | 03_memory.gsql |
| similar_cases | similar_cases (HNSW cosine over ClosedCase.emb and InvestigationCase.emb, blended with entity overlap) | 03_memory.gsql |
| policy_lookup | policy_search (HNSW cosine over PolicyChunk.emb and FraudPattern.emb) | 03_memory.gsql |
| pattern_match | match_card_testing, match_card_not_present (require_new_device for pattern 3), match_out_of_region, match_account_takeover, match_structuring | 04_patterns.gsql |
| write_case, append_evidence, record_decision, link_similar | same names | 05_writers.gsql |

## Rules every tool keeps

- `as_of` is required on every read and nothing later than it is returned.
  Agent-written cases become visible to later reads once their own `opened_at`
  is earlier than `as_of`.
- Every result carries `ref`, for example
  `query:ring_detect(seed=C13487-K1)`, to copy into `evidence[].ref`.
- Every id returned is a real vertex id. `write_case` resolves ids against the
  graph before it stores them and reports anything it could not find in
  `ids_not_in_dataset`; `link_similar` reports unknown case ids in `not_found`.
  Neither creates a vertex it was not asked to.
