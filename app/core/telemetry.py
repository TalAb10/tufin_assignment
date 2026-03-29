from opentelemetry import trace, metrics
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource, SERVICE_NAME, SERVICE_VERSION

from app.core.config import settings

_SERVICE_NAME = "multi-tool-agent"
_tracer_provider: TracerProvider | None = None


def setup_telemetry() -> None:
    """Initialize TracerProvider and register it globally. Traces are exported
    via OTLP HTTP to Jaeger. Metrics use the default no-op provider (no metrics
    backend is configured in this setup)."""
    global _tracer_provider
    if _tracer_provider is not None:
        return

    resource = Resource.create({
        SERVICE_NAME: _SERVICE_NAME,
        SERVICE_VERSION: "1.0.0",
        "deployment.environment": "development" if settings.debug else "production",
    })

    otlp_trace_exporter = OTLPSpanExporter(
        endpoint=settings.otlp_endpoint + "/v1/traces",
    )
    _tracer_provider = TracerProvider(resource=resource)
    _tracer_provider.add_span_processor(BatchSpanProcessor(otlp_trace_exporter))
    trace.set_tracer_provider(_tracer_provider)


def shutdown_telemetry() -> None:
    """Flush and shut down exporters on app shutdown."""
    if _tracer_provider:
        _tracer_provider.shutdown()


def get_tracer() -> trace.Tracer:
    return trace.get_tracer(_SERVICE_NAME)


def get_meter() -> metrics.Meter:
    # Returns a no-op meter — metric recording in callbacks is silently discarded.
    # Add a MeterProvider + exporter here when a metrics backend (e.g. Prometheus) is available.
    return metrics.get_meter(_SERVICE_NAME)
