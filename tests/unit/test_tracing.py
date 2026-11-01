from collections.abc import Iterator

import pytest
import structlog
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from app.core.logging import add_trace_context
from app.llm.base import Message
from app.llm.fake import FakeEmbeddingProvider, FakeLLMProvider
from app.observability.tracing import current_trace_id, setup_tracing, tracer
from app.observability.usage import metered


@pytest.fixture
def spans() -> Iterator[InMemorySpanExporter]:
    exporter = InMemorySpanExporter()
    provider = setup_tracing("test", otlp_endpoint=None)
    processor = SimpleSpanProcessor(exporter)
    provider.add_span_processor(processor)
    yield exporter
    processor.shutdown()


async def test_llm_calls_emit_genai_spans_and_meter_usage(spans: InMemorySpanExporter) -> None:
    with metered() as meter:
        await FakeLLMProvider(["hello there"]).complete([Message(role="user", content="hi you")])
        await FakeEmbeddingProvider().embed(["one two three"])

    chat, embed = spans.get_finished_spans()[-2:]
    assert chat.name == "chat fake-llm"
    assert chat.attributes is not None
    assert chat.attributes["gen_ai.operation.name"] == "chat"
    assert chat.attributes["gen_ai.system"] == "fake"
    assert (
        chat.attributes["gen_ai.usage.input_tokens"],
        chat.attributes["gen_ai.usage.output_tokens"],
    ) == (2, 2)
    assert embed.name == "embeddings fake-embedding"
    assert {(u.kind, u.input_tokens) for u in meter.by_model.values()} == {
        ("chat", 2),
        ("embedding", 3),
    }


def test_trace_id_in_logs_and_helper(spans: InMemorySpanExporter) -> None:
    assert current_trace_id() is None
    with tracer().start_as_current_span("request"):
        trace_id = current_trace_id()
        event = add_trace_context(None, "info", {"event": "x"})
    assert trace_id is not None
    assert len(trace_id) == 32
    assert event["trace_id"] == trace_id
    assert len(event["span_id"]) == 16
    assert "trace_id" not in add_trace_context(None, "info", {"event": "y"})


def test_logging_pipeline_includes_trace_processor() -> None:
    from app.core.logging import configure_logging

    configure_logging("INFO", json=True)
    processors = structlog.get_config()["processors"]
    # Before the renderer, so the ids end up in the rendered line.
    assert processors.index(add_trace_context) < len(processors) - 1
