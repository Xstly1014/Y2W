"""Project-level configuration based on pydantic-settings.

All settings are loaded from environment variables / .env file. Importing
`settings` from this module gives every sub-package a single source of truth.
"""
from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ----- LLM -----
    openai_api_key: str = Field(default="", alias="OPENAI_API_KEY")
    openai_api_base: str = Field(
        default="https://api.openai.com/v1", alias="OPENAI_API_BASE"
    )
    llm_model_name: str = Field(default="gpt-4o-mini", alias="LLM_MODEL_NAME")
    llm_temperature: float = Field(default=0.0, alias="LLM_TEMPERATURE")

    # ----- Embedding -----
    embedding_model_name: str = Field(
        default="text-embedding-3-small", alias="EMBEDDING_MODEL_NAME"
    )
    embedding_provider: Literal["openai", "local"] = Field(
        default="openai", alias="EMBEDDING_PROVIDER"
    )

    # ----- RAG -----
    vector_store_dir: Path = Field(
        default=PROJECT_ROOT / "data" / "vectorstore", alias="VECTOR_STORE_DIR"
    )
    chunk_size: int = Field(default=500, alias="CHUNK_SIZE")
    chunk_overlap: int = Field(default=50, alias="CHUNK_OVERLAP")
    retrieval_top_k: int = Field(default=3, alias="RETRIEVAL_TOP_K")
    # Vector store backend selection.
    #   faiss     — original FAISS index files on disk (default, zero infra)
    #   pg_python — PostgreSQL-backed, similarity computed in Python (numpy)
    #               Works without pgvector extension; data lives in PG table
    #               `vector_store`. Enterprise-grade: ACID, backup, multi-tenant.
    #   pgvector  — PostgreSQL + pgvector extension (requires `CREATE EXTENSION
    #               vector;`). Uses HNSW/IVFFlat indexes for sub-linear search.
    #               Needs the extension compiled & installed (Windows: needs
    #               Visual Studio + Windows SDK; see scripts/build_pgvector.ps1).
    vector_store_backend: Literal["faiss", "pg_python", "pgvector"] = Field(
        default="faiss", alias="VECTOR_STORE_BACKEND"
    )
    # PG connection for the pg_python / pgvector backends. Defaults to the same
    # PG instance as the e-commerce DB (postgres user, localhost:5432) but a
    # separate database `agent_vectors` so agent vectors don't mix with
    # e-commerce business tables.
    pg_vector_database_url: str = Field(
        default="postgresql+psycopg2://postgres:postgres@127.0.0.1:5432/agent_vectors",
        alias="PG_VECTOR_DATABASE_URL",
    )

    # ----- Memory -----
    short_term_memory_max_messages: int = Field(
        default=20, alias="SHORT_TERM_MEMORY_MAX_MESSAGES"
    )
    long_term_memory_collection: str = Field(
        default="long_term_memory", alias="LONG_TERM_MEMORY_COLLECTION"
    )
    # ----- Long-term memory enhancements -----
    long_term_memory_half_life_days: float = Field(
        default=7.0,
        alias="LTM_HALF_LIFE_DAYS",
        description=(
            "Ebbinghaus forgetting curve half-life in days. "
            "High-importance memories have longer effective half-life."
        ),
    )
    long_term_memory_decay_threshold: float = Field(
        default=0.05,
        alias="LTM_DECAY_THRESHOLD",
        description=(
            "Memories with decay_score below this are candidates for deletion."
        ),
    )
    long_term_memory_extract_facts: bool = Field(
        default=True,
        alias="LTM_EXTRACT_FACTS",
        description=(
            "When True, remember_extracted() uses LLM to extract facts "
            "(subject/predicate/object triples) for precise recall. "
            "When False, falls back to plain remember() (raw text only). "
            "Default True since the save_memory tool is called infrequently "
            "(only when the agent decides to persist a user fact), so the "
            "extra LLM call is acceptable. See "
            "`optimization_logs/2026-07-20/issues-and-fixes.md` P2-5."
        ),
    )

    # ----- MCP -----
    mcp_server_url: str = Field(default="", alias="MCP_SERVER_URL")
    # Multi-server MCP config. When set, `MCPServerRegistry.load_from_yaml`
    # reads this file at startup and registers every server listed in it.
    # A missing file is tolerated (empty registry) so the agent still boots.
    mcp_servers_config_path: Path | None = Field(
        default=None,
        alias="MCP_SERVERS_CONFIG_PATH",
        description=(
            "Path to a YAML file defining multiple MCP servers. "
            "If set, MCPServerRegistry loads from it at startup."
        ),
    )

    # ----- Evaluation -----
    # Default points to the seed fixture shipped inside the package; override
    # with EVAL_DATASET_PATH=.env to use a custom dataset under data/.
    eval_dataset_path: Path = Field(
        default=PROJECT_ROOT / "evaluation" / "fixtures" / "eval_cases.yaml",
        alias="EVAL_DATASET_PATH",
    )
    eval_output_dir: Path = Field(
        default=PROJECT_ROOT / "data" / "eval" / "results", alias="EVAL_OUTPUT_DIR"
    )

    # ----- Data Flywheel -----
    badcase_store_path: Path = Field(
        default=PROJECT_ROOT / "data" / "flywheel" / "badcases.jsonl",
        alias="BADCASE_STORE_PATH",
    )
    goodcase_store_path: Path = Field(
        default=PROJECT_ROOT / "data" / "flywheel" / "goodcases.jsonl",
        alias="GOODCASE_STORE_PATH",
    )

    # ----- Post Training -----
    post_train_output_dir: Path = Field(
        default=PROJECT_ROOT / "data" / "post_training",
        alias="POST_TRAIN_OUTPUT_DIR",
    )

    # ----- Business Platform (cross-border e-commerce SaaS) -----
    # Mock e-commerce platform (Shopify/Shopee stand-in) for the demo.
    mock_platform_base_url: str = Field(
        default="http://127.0.0.1:8001", alias="MOCK_PLATFORM_BASE_URL"
    )
    # Default tenant id used when no tenant header is provided (demo only).
    default_tenant_id: str = Field(default="demo-tenant", alias="DEFAULT_TENANT_ID")
    # Tenant-aware RAG collection name prefix. Real collection = f"{prefix}_{tenant_id}".
    kb_collection_prefix: str = Field(default="kb", alias="KB_COLLECTION_PREFIX")
    # FastAPI service port.
    api_port: int = Field(default=8000, alias="API_PORT")
    mock_platform_port: int = Field(default=8001, alias="MOCK_PLATFORM_PORT")

    # ----- E-commerce platform (independent service on port 8002) -----
    # The full Vue 3 SPA + PostgreSQL-backed catalog/orders/payments.
    # See ecommerce/ package. Connection params live in ecommerce/config.py
    # under ECOMMERCE_* env vars.
    ecommerce_port: int = Field(default=8002, alias="ECOMMERCE_PORT")

    # ----- Inference acceleration -----
    llm_prompt_cache_enabled: bool = Field(
        default=False, alias="LLM_PROMPT_CACHE_ENABLED",
        description="Enable LLM response cache for deterministic (temperature=0) calls.",
    )
    llm_prompt_cache_max_size: int = Field(
        default=256, alias="LLM_PROMPT_CACHE_MAX_SIZE",
        description="Max entries in the in-memory LRU cache.",
    )
    llm_prompt_cache_disk_path: Path | None = Field(
        default=None, alias="LLM_PROMPT_CACHE_DISK_PATH",
        description="Optional disk cache path. None = memory only.",
    )
    llm_batch_max_workers: int = Field(
        default=4, alias="LLM_BATCH_MAX_WORKERS",
        description="Max parallel workers for batch_invoke.",
    )

    # ----- LangSmith / LangFuse observability export -----
    # When langchain_api_key is set AND langchain_tracing_v2=true, LangChain
    # automatically uploads every LLM/tool call to LangSmith (no code change
    # needed — just `pip install langsmith` and set these env vars).
    # See `optimization_logs/2026-07-20/issues-and-fixes.md` P1-3.
    langchain_api_key: str = Field(default="", alias="LANGCHAIN_API_KEY")
    langchain_tracing_v2: bool = Field(
        default=False, alias="LANGCHAIN_TRACING_V2",
        description="Enable LangChain auto-tracing (uploads to LangSmith).",
    )
    langchain_project: str = Field(
        default="0719agent", alias="LANGCHAIN_PROJECT",
        description="LangSmith project name (groups traces in the UI).",
    )
    langchain_endpoint: str = Field(
        default="https://api.smith.langchain.com", alias="LANGCHAIN_ENDPOINT",
    )

    def ensure_dirs(self) -> None:
        """Create all runtime directories used by the project."""
        for path in [
            self.vector_store_dir,
            self.eval_dataset_path.parent,
            self.eval_output_dir,
            self.badcase_store_path.parent,
            self.goodcase_store_path.parent,
            self.post_train_output_dir,
        ]:
            Path(path).mkdir(parents=True, exist_ok=True)


settings = Settings()
settings.ensure_dirs()
