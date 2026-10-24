"""
Runtime configuration for the query service, read once from environment variables
(set by Terraform in the ECS task definition).
"""
import os
from dataclasses import dataclass, field


def _float(name: str, default: str) -> float:
    return float(os.environ.get(name, default))


def _int(name: str, default: str) -> int:
    return int(os.environ.get(name, default))


@dataclass(frozen=True)
class Settings:
    stage: str = os.environ.get("STAGE", "local")
    aws_region: str = os.environ.get("AWS_REGION", "us-east-1")

    # Database (RDS Proxy read-only endpoint in prod, cluster reader endpoint elsewhere)
    db_read_host: str = os.environ.get("DB_READ_HOST", "localhost")
    db_name: str = os.environ.get("DB_NAME", "ragapp")
    db_secret_arn: str = os.environ.get("DB_SECRET_ARN", "")
    db_pool_max: int = _int("DB_POOL_MAX", "20")

    # Cache
    redis_host: str = os.environ.get("REDIS_HOST", "")
    redis_tls: bool = os.environ.get("REDIS_TLS", "true").lower() == "true"
    rate_limit_per_minute: int = _int("RATE_LIMIT_PER_MINUTE", "20")
    semantic_cache_threshold: float = _float("SEMANTIC_CACHE_THRESHOLD", "0.95")
    semantic_cache_max_entries: int = _int("SEMANTIC_CACHE_MAX_ENTRIES", "200")
    cache_ttl_seconds: int = _int("CACHE_TTL_SECONDS", "3600")

    # Auth
    cognito_user_pool_id: str = os.environ.get("COGNITO_USER_POOL_ID", "")
    cognito_app_client_id: str = os.environ.get("COGNITO_APP_CLIENT_ID", "")
    cognito_eval_client_id: str = os.environ.get("COGNITO_EVAL_CLIENT_ID", "")
    eval_groups: tuple = tuple(g for g in os.environ.get("EVAL_GROUPS", "eval").split(",") if g)

    # Storage
    chat_history_table: str = os.environ.get("CHAT_HISTORY_TABLE", "")
    metadata_table: str = os.environ.get("METADATA_TABLE", "")
    documents_bucket: str = os.environ.get("DOCUMENTS_BUCKET", "")
    max_upload_bytes: int = _int("MAX_UPLOAD_BYTES", str(50 * 1024 * 1024))
    history_ttl_days: int = _int("HISTORY_TTL_DAYS", "90")

    # Models
    embedding_model_id: str = os.environ.get("EMBEDDING_MODEL_ID", "amazon.titan-embed-text-v2:0")
    embedding_dimensions: int = _int("EMBEDDING_DIMENSIONS", "1024")
    rerank_model_id: str = os.environ.get("RERANK_MODEL_ID", "cohere.rerank-v3-5:0")
    generation_model_id: str = os.environ.get("GENERATION_MODEL_ID", "anthropic.claude-opus-5-5")
    fallback_model_id: str = os.environ.get("FALLBACK_MODEL_ID", "anthropic.claude-opus-4-8")
    judge_model_id: str = os.environ.get("JUDGE_MODEL_ID", "anthropic.claude-haiku-5-5")
    generation_effort: str = os.environ.get("GENERATION_EFFORT", "low")
    generation_max_tokens: int = _int("GENERATION_MAX_TOKENS", "16000")
    # USD per million tokens, for the cost/query metric. Set to your Bedrock prices.
    price_input_per_mtok: float = _float("PRICE_INPUT_PER_MTOK", "4.00")
    price_output_per_mtok: float = _float("PRICE_OUTPUT_PER_MTOK", "20.00")

    guardrail_id: str = os.environ.get("GUARDRAIL_ID", "")
    guardrail_version: str = os.environ.get("GUARDRAIL_VERSION", "DRAFT")
    guardrail_check_chars: int = _int("GUARDRAIL_CHECK_CHARS", "1500")

    # Retrieval
    candidate_k: int = _int("CANDIDATE_K", "20")
    final_k: int = _int("FINAL_K", "5")
    rag_confidence_threshold: float = _float("RAG_CONFIDENCE_THRESHOLD", "0.35")

    # Web search fallback
    mcp_server_url: str = os.environ.get("MCP_SERVER_URL", "")
    mcp_timeout: float = _float("MCP_TIMEOUT", "20")

    # Online evaluation
    eval_sample_rate: float = _float("EVAL_SAMPLE_RATE", "0.05")

    # Observability
    metrics_namespace: str = os.environ.get("METRICS_NAMESPACE", "RAGApp/local")
    otel_endpoint: str = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "")
    service_name: str = os.environ.get("OTEL_SERVICE_NAME", "query-api")

    cors_origins: list = field(default_factory=lambda: [
        o for o in os.environ.get("CORS_ORIGINS", "").split(",") if o
    ])

    @property
    def cognito_issuer(self) -> str:
        return f"https://cognito-idp.{self.aws_region}.amazonaws.com/{self.cognito_user_pool_id}"


settings = Settings()
