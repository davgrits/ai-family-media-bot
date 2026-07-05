"""OpenTelemetry tracing setup. Initializes the SDK with an OTLP exporter when
`OTEL_EXPORTER_OTLP_ENDPOINT` is set; otherwise tracing is a safe no-op (spans
are created but go nowhere). Never raises — a misconfigured exporter must not
take down the service."""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_initialized = False


def setup_telemetry(service_name: str = "family-media-bot", otlp_endpoint: str = "") -> None:
    global _initialized
    if _initialized:
        return

    try:
        from opentelemetry import trace
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        provider = TracerProvider(resource=Resource.create({"service.name": service_name}))

        if otlp_endpoint:
            try:
                # The HTTP exporter reads OTEL_EXPORTER_OTLP_ENDPOINT from the
                # environment and appends /v1/traces per OTel convention.
                from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
                    OTLPSpanExporter,
                )

                provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
                logger.info("otel exporter enabled", extra={"otlp_endpoint": otlp_endpoint})
            except Exception as exc:  # exporter package missing / bad endpoint
                logger.warning(
                    "otel exporter unavailable; tracing stays local-only",
                    extra={"error": str(exc)},
                )
        else:
            logger.info("otel endpoint unset; tracing is a no-op")

        trace.set_tracer_provider(provider)
        _initialized = True
    except Exception as exc:  # SDK not installed, etc.
        logger.warning("otel sdk unavailable; continuing without tracing", extra={"error": str(exc)})


def get_tracer(name: str = "family_media_bot"):
    """Return a tracer. Falls back to a no-op tracer if the SDK can't be imported
    so call sites can always `with tracer.start_as_current_span(...)`."""
    try:
        from opentelemetry import trace

        return trace.get_tracer(name)
    except Exception:
        return _NoopTracer()


class _NoopSpan:
    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def set_attribute(self, *_a, **_k):
        pass

    def record_exception(self, *_a, **_k):
        pass

    def set_status(self, *_a, **_k):
        pass


class _NoopTracer:
    def start_as_current_span(self, *_a, **_k):
        return _NoopSpan()
