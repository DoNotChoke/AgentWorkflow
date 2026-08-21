"""Khởi tạo OpenTelemetry"""

from __future__ import annotations

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter

from app.core.config import Settings
from app.core.context import current_context


def configure_tracing(settings: Settings, *, component: str) -> None:
    """Cấu hình tracer provider. Gọi một lần khi khởi động process."""
    if not settings.otel_enabled:
        return

    resource = Resource.create(
        {
            "service.name": f"{settings.service_name}-{component}",
            "service.namespace": "agentworkflow",
            "service.version": "0.0.1",
            "deployment.environment": settings.environment,
            "aw.component": component,
        }
    )

    provider = TracerProvider(resource=resource)

    if settings.environment == "local" and settings.debug:
        provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))

    provider.add_span_processor(
        BatchSpanProcessor(
            OTLPSpanExporter(endpoint=settings.otel_exporter_endpoint, insecure=True)
        )
    )

    trace.set_tracer_provider(provider)


def instrument_fastapi(app: object) -> None:
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

    FastAPIInstrumentor.instrument_app(app)  # type: ignore[arg-type]


def instrument_clients() -> None:
    """Instrument HTTP client, SQLAlchemy và Redis."""
    from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
    from opentelemetry.instrumentation.redis import RedisInstrumentor

    HTTPXClientInstrumentor().instrument()
    RedisInstrumentor().instrument()


def instrument_sqlalchemy(engine: object) -> None:
    from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor

    SQLAlchemyInstrumentor().instrument(engine=getattr(engine, "sync_engine", engine))


def bind_context_attributes() -> None:
    """Đính định danh của §36 vào span hiện tại.

    Nhờ đó có thể đi từ một span chậm trong Jaeger về đúng tenant, đúng
    execution, đúng Execution Trace ở tầng sản phẩm.
    """
    ctx = current_context()
    if ctx is None:
        return
    span = trace.get_current_span()
    if not span.get_span_context().is_valid:
        return
    for key, value in ctx.as_log_fields().items():
        span.set_attribute(f"aw.{key}", value)


def current_otel_ids() -> tuple[str | None, str | None]:
    """Trả về ``(trace_id, span_id)`` dạng hex để lưu vào Execution Trace (§9.1)."""
    span_ctx = trace.get_current_span().get_span_context()
    if not span_ctx.is_valid:
        return None, None
    return format(span_ctx.trace_id, "032x"), format(span_ctx.span_id, "016x")


def get_tracer(name: str) -> trace.Tracer:
    return trace.get_tracer(name)
