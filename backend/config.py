"""Single place env turns into typed settings.

The one switch that matters for the parallel lanes is TOOL_BACKEND: `mock`
today, `mcp` the moment graph-engineer's TigerGraph MCP server is up. Nothing
else in the agent knows which one it is talking to.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[1]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(REPO_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Tool layer
    tool_backend: Literal["mock", "mcp"] = "mock"
    mcp_server_url: str = "http://localhost:8765/sse"

    # TigerGraph
    tg_host: str = "http://localhost"
    tg_restpp_port: int = 14240
    tg_username: str = "tigergraph"
    tg_password: str = ""
    tg_graph_name: str = "FraudInvestigation"

    # LLM. No key is expected yet, so every consumer must construct lazily.
    llm_provider: str = "anthropic"
    llm_model: str = "claude-opus-5"
    anthropic_api_key: str = ""
    llm_max_tokens: int = 2048
    dry_run: bool = True

    # Embeddings
    embedding_provider: Literal["local", "hashing"] = "local"
    embedding_model: str = "BAAI/bge-small-en-v1.5"

    # Agent
    agent_max_steps: int = 24
    graphrag_token_budget: int = 3000

    # Backend
    backend_host: str = "0.0.0.0"
    backend_port: int = 8000

    @property
    def repo_root(self) -> Path:
        return REPO_ROOT

    @property
    def data_dir(self) -> Path:
        return REPO_ROOT / "data"

    @property
    def cache_dir(self) -> Path:
        return REPO_ROOT / "backend" / "data_cache"

    @property
    def cases_dir(self) -> Path:
        return REPO_ROOT / "cases"

    @property
    def contracts_dir(self) -> Path:
        return REPO_ROOT / "backend" / "contracts"

    @property
    def llm_enabled(self) -> bool:
        # A key can appear later without a code change; until then every
        # decision node falls back to its deterministic path.
        return bool(self.anthropic_api_key) and not self.dry_run


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
