"""OpenTelemetry wiring, pinned to a known GenAI semantic-convention revision.

Two decisions worth stating, both from PRD §13.1:

*   **The semconv version is pinned.** The GenAI conventions are still at
    Development stability, so attribute names move between releases. Every
    attribute this module emits goes through :func:`gen_ai_attributes`, which is
    the single place a rename has to be absorbed. Dashboards keep working.
*   **Content capture is off by default.** Span attributes are always indexed,
    size-limited, and visible to anyone with backend access. Prompts and
    completions go to span *events*, capped, behind ``AUTOBENCH_CAPTURE_CONTENT``.
    The full, uncapped trajectory lives in object storage — the trace is for
    latency and shape, the artifact is for content.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from autobench.core.config import Settings, get_settings
from autobench.core.security import redact

#: The revision these attribute names were taken from. Bump deliberately.
GENAI_SEMCONV_VERSION = "1.27.0"

logger = logging.getLogger(__name__)

_TRACER: Any = None
_CONFIGURED = False


def configure_telemetry(settings: Settings | None = None) -> None:
    """Idempotent. A no-op when no OTLP endpoint is configured."""
    global _TRACER, _CONFIGURED
    if _CONFIGURED:
        return
    _CONFIGURED = True
    settings = settings or get_settings()

    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )

    if not settings.otlp_endpoint:
        logger.debug("no OTLP endpoint configured; traces are no-ops")
        return

    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        resource = Resource.create(
            {
                "service.name": settings.service_name,
                "service.version": _version(),
                "deployment.environment": settings.environment,
                "autobench.semconv.genai": GENAI_SEMCONV_VERSION,
            }
        )
        provider = TracerProvider(resource=resource)
        provider.add_span_processor(
            BatchSpanProcessor(OTLPSpanExporter(endpoint=settings.otlp_endpoint))
        )
        trace.set_tracer_provider(provider)
        _TRACER = trace.get_tracer("autobench", _version())
        logger.info(
            "telemetry -> %s (genai semconv %s)", settings.otlp_endpoint, GENAI_SEMCONV_VERSION
        )
    except Exception as exc:  # pragma: no cover - never fail a run over telemetry
        logger.warning("telemetry disabled: %s", exc)


def _version() -> str:
    from autobench import __version__

    return __version__


@contextmanager
def span(name: str, **attributes: Any) -> Iterator[Any]:
    """Start a span, or yield ``None`` when tracing is not configured.

    Callers must tolerate ``None`` — that is the price of telemetry never being
    load-bearing.
    """
    if _TRACER is None:
        yield None
        return
    with _TRACER.start_as_current_span(name) as active:
        for key, value in attributes.items():
            if value is not None:
                active.set_attribute(key, value)
        try:
            yield active
        except Exception as exc:
            active.record_exception(exc)
            from opentelemetry.trace import Status, StatusCode

            active.set_status(Status(StatusCode.ERROR, str(exc)))
            raise


def gen_ai_attributes(
    *,
    system: str,
    model: str,
    operation: str = "chat",
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    cached_tokens: int | None = None,
    temperature: float | None = None,
    agent_name: str | None = None,
    tool_name: str | None = None,
) -> dict[str, Any]:
    """Normalise to the pinned GenAI conventions at the single point of export."""
    attrs: dict[str, Any] = {
        "gen_ai.system": system,
        "gen_ai.operation.name": operation,
        "gen_ai.request.model": model,
    }
    if input_tokens is not None:
        attrs["gen_ai.usage.input_tokens"] = input_tokens
    if output_tokens is not None:
        attrs["gen_ai.usage.output_tokens"] = output_tokens
    if cached_tokens is not None:
        attrs["gen_ai.usage.cached_input_tokens"] = cached_tokens
    if temperature is not None:
        attrs["gen_ai.request.temperature"] = temperature
    if agent_name is not None:
        attrs["gen_ai.agent.name"] = agent_name
    if tool_name is not None:
        attrs["gen_ai.tool.name"] = tool_name
    return {k: v for k, v in attrs.items() if v is not None}


def record_content_event(active_span: Any, event_name: str, content: str) -> None:
    """Attach prompt/completion text as a capped, redacted span *event*.

    Never an attribute: attributes are always indexed and size-limited, and they
    are the wrong place for user content.
    """
    settings = get_settings()
    if active_span is None or not settings.capture_content:
        return
    body = redact(content)[: settings.capture_content_chars]
    active_span.add_event(
        event_name, {"gen_ai.content": body, "truncated": len(content) > len(body)}
    )
