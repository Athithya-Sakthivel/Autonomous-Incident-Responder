"""
Centralised configuration for the Agent Service.
All settings are read from environment variables – no .env file dependency.
"""

from __future__ import annotations

import dspy
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables only."""

    model_config = SettingsConfigDict(
        env_file=None,
        case_sensitive=False,
        extra="ignore",
    )

    # ── LLM ────────────────────────────────────────────────────────
    llm_api_key: str = ""
    llm_safeguard_model: str = "groq/openai/gpt-oss-safeguard-20b"
    llm_resolver_model: str = "groq/llama-3.1-8b-instant"

    llm_safeguard_temperature: float = 0.0
    llm_safeguard_max_tokens: int = 1024

    llm_resolver_temperature: float = 0.2
    llm_resolver_max_tokens: int = 4096

    # ── Agent behaviour ────────────────────────────────────────────
    urgency_escalate_threshold: int = 8
    max_auto_resolve_amount: float = 10000.0
    max_wallet_credit_amount: float = 500.0

    # ── MCP Server ─────────────────────────────────────────────────
    mcp_server_url: str = "http://mcp-server-svc.inference.svc.cluster.local:8001/mcp"

    # ── PostgreSQL ─────────────────────────────────────────────────
    database_url: str = (
        "postgresql://app:password@postgres-pooler.default.svc.cluster.local:5432/agents_state"
    )
    pool_min_size: int = 5
    pool_max_size: int = 25

    # ── OpenTelemetry ──────────────────────────────────────────────
    otel_service_name: str = "agent-service"
    otel_exporter_otlp_endpoint: str = (
        "http://signoz-otel-collector.signoz.svc.cluster.local:4317"
    )
    otel_exporter_otlp_insecure: bool = True
    otel_metric_export_interval_ms: int = 60_000
    otel_metric_export_timeout_ms: int = 30_000
    deployment_environment: str = "production"
    service_version: str = "0.1.0"

    # ── Server ─────────────────────────────────────────────────────
    host: str = "0.0.0.0"
    port: int = 8000
    log_level: str = "INFO"


settings = Settings()


def create_safeguard_lm() -> dspy.LM:
    """LM for guardrail + classification (GPT-OSS-Safeguard 20B)."""
    return dspy.LM(
        model=settings.llm_safeguard_model,
        api_key=settings.llm_api_key,
        temperature=settings.llm_safeguard_temperature,
        max_tokens=settings.llm_safeguard_max_tokens,
    )


def create_resolver_lm() -> dspy.LM:
    """LM for the agentic resolver (GPT-OSS 120B)."""
    return dspy.LM(
        model=settings.llm_resolver_model,
        api_key=settings.llm_api_key,
        temperature=settings.llm_resolver_temperature,
        max_tokens=settings.llm_resolver_max_tokens,
    )