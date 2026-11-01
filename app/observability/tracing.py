"""OpenTelemetry tracing.

A tracer provider is always installed, so every request has a trace id for
logs and responses. Spans are exported only when an OTLP endpoint is
configured (the standard OTEL_EXPORTER_OTLP_ENDPOINT / _HEADERS variables):
Jaeger locally (`docker compose --profile observability up`), or any OTLP
backend such as Langfuse or Grafana Tempo.

LLM spans follow the OpenTelemetry GenAI semantic conventions
(gen_ai.operation.name, gen_ai.request.model, gen_ai.usage.*), which LLM
observability tools understand.
"""

from collections.abc import Iterator
from contextlib import contextmanager

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SpanExporter
from opentelemetry.trace import Span, Status, StatusCode

from app.observability.usage import Kind, record

TRACER_NAME = "voc_copilot"
_provider: TracerProvider | None = None


def setup_tracing(
    service_name: str, *, otlp_endpoint: str | None, exporter: SpanExporter | None = None
) -> TracerProvider:
    """Install the global tracer provider once per process."""
    global _provider
    if _provider is None:
        _provider = TracerProvider(resource=Resource.create({"service.name": service_name}))
        trace.set_tracer_provider(_provider)
        if otlp_endpoint:
            _provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    if exporter is not None:  # tests and custom setups
        _provider.add_span_processor(BatchSpanProcessor(exporter))
    return _provider


def tracer() -> trace.Tracer:
    return trace.get_tracer(TRACER_NAME)


def current_trace_id() -> str | None:
    context = trace.get_current_span().get_span_context()
    return format(context.trace_id, "032x") if context.is_valid else None


@contextmanager
def llm_span(operation: Kind, *, provider: str, model: str) -> Iterator[Span]:
    name = "chat" if operation == "chat" else "embeddings"
    with tracer().start_as_current_span(f"{name} {model}") as span:
        span.set_attribute("gen_ai.operation.name", name)
        span.set_attribute("gen_ai.system", provider)
        span.set_attribute("gen_ai.request.model", model)
        try:
            yield span
        except Exception as exc:
            span.set_status(Status(StatusCode.ERROR, type(exc).__name__))
            raise


def record_usage(
    span: Span,
    *,
    kind: Kind,
    provider: str,
    model: str,
    response_model: str | None,
    input_tokens: int,
    output_tokens: int,
) -> None:
    """Put usage on the span and add it to the current request's meter."""
    span.set_attribute("gen_ai.response.model", response_model or model)
    span.set_attribute("gen_ai.usage.input_tokens", input_tokens)
    span.set_attribute("gen_ai.usage.output_tokens", output_tokens)
    record(
        provider=provider,
        model=response_model or model,
        kind=kind,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )
