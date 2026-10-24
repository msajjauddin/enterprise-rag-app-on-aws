"""
Metrics and tracing.

Metrics are written to stdout in CloudWatch Embedded Metric Format; the awslogs
driver ships them and CloudWatch extracts the metrics (no agent needed).
Traces go over OTLP to the ADOT collector sidecar, which exports to X-Ray.
"""
import json
import logging
import sys
import time
from typing import Dict, Optional

from .config import settings

logger = logging.getLogger(__name__)

UNITS = {
    "QueryLatencyMs": "Milliseconds",
    "TimeToFirstTokenMs": "Milliseconds",
    "RetrievalLatencyMs": "Milliseconds",
    "InputTokens": "Count",
    "OutputTokens": "Count",
    "EstimatedCostUSD": "None",
    "CacheHit": "None",
    "WebSearchFallback": "None",
    "GuardrailIntervened": "Count",
    "RateLimited": "Count",
    "EvalFaithfulness": "None",
    "EvalAnswerRelevancy": "None",
    "UserFeedback": "None",
    "QueryErrors": "Count",
}


def emit_metrics(metrics: Dict[str, float], properties: Optional[Dict] = None) -> None:
    """Write one EMF record. Properties are searchable in Logs Insights but are not dimensions."""
    if not metrics:
        return
    record = {
        "_aws": {
            "Timestamp": int(time.time() * 1000),
            "CloudWatchMetrics": [{
                "Namespace": settings.metrics_namespace,
                "Dimensions": [["Service"]],
                "Metrics": [{"Name": name, "Unit": UNITS.get(name, "None")} for name in metrics],
            }],
        },
        "Service": "query-api",
        **(properties or {}),
        **metrics,
    }
    sys.stdout.write(json.dumps(record, default=str) + "\n")
    sys.stdout.flush()


def setup_tracing(app) -> None:
    """Instrument FastAPI, boto3 and psycopg when an OTLP endpoint is configured."""
    if not settings.otel_endpoint:
        return
    try:
        from opentelemetry import propagate, trace
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
        from opentelemetry.instrumentation.botocore import BotocoreInstrumentor
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        from opentelemetry.instrumentation.psycopg import PsycopgInstrumentor
        from opentelemetry.propagators.aws import AwsXRayPropagator
        from opentelemetry.sdk.extension.aws.trace import AwsXRayIdGenerator
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        provider = TracerProvider(
            resource=Resource.create({"service.name": settings.service_name, "deployment.environment": settings.stage}),
            id_generator=AwsXRayIdGenerator(),
        )
        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=settings.otel_endpoint, insecure=True)))
        trace.set_tracer_provider(provider)
        propagate.set_global_textmap(AwsXRayPropagator())

        FastAPIInstrumentor.instrument_app(app, excluded_urls="health")
        BotocoreInstrumentor().instrument()
        PsycopgInstrumentor().instrument()
        logger.info("OpenTelemetry tracing enabled")
    except Exception as e:  # tracing must never take the service down
        logger.warning(f"Tracing not enabled: {e}")


def tracer():
    from opentelemetry import trace
    return trace.get_tracer("rag.query")
