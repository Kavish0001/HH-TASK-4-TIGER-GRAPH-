#!/usr/bin/env bash
# Starts everything the dashboard needs, in dependency order: TigerGraph, the
# MCP server that fronts it, the API that drives the agent over MCP, then the
# Next.js dashboard. I run them from one script because a demo that dies on a
# forgotten server looks like a broken agent.
set -euo pipefail
cd "$(dirname "$0")/.."

docker compose -f infra/docker-compose.yml up -d
until curl -s -m 2 http://localhost:14240/api/ping >/dev/null; do
  echo "waiting for TigerGraph..."; sleep 5
done

python graph/mcp/server.py --port 8765 > mcp.log 2>&1 &
until curl -s -m 2 -o /dev/null http://localhost:8765/sse --max-time 2; do sleep 2; done

# DRY_RUN=true keeps the demo deterministic; set it to false with a
# GOOGLE_API_KEY in .env to get LLM-written narratives.
PYTHONPATH=. TOOL_BACKEND=mcp DRY_RUN="${DRY_RUN:-true}" \
  python -m uvicorn backend.api.main:app --port 8000 > api.log 2>&1 &
until curl -s -m 2 http://localhost:8000/health >/dev/null; do sleep 2; done

echo "MCP on :8765, API on :8000. Starting the dashboard on :3000."
cd frontend && npm run dev
