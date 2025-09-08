"""
OpenTelemetry instrumentation setup for changedetection.io Flask application.

This module provides comprehensive observability with traces, metrics, and logs
using OTLP exporters. It's designed to work with the existing loguru logging
and preserve the application's simplicity.
"""

import logging
import os

from flask import Flask
from opentelemetry import metrics, trace
# Try importing from public API first, fallback to private API
try:
    from opentelemetry.logs import set_logger_provider
except ImportError:
    from opentelemetry._logs import set_logger_provider
# Try importing from public API first, fallback to private API
try:
    from opentelemetry.exporter.otlp.proto.grpc.logs_exporter import OTLPLogExporter
except ImportError:
    try:
        from opentelemetry.exporter.otlp.proto.grpc._log_exporter import OTLPLogExporter
    except ImportError:
        # If both fail, set to None and handle gracefully
        OTLPLogExporter = None
from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.flask import FlaskInstrumentor
from opentelemetry.instrumentation.logging import LoggingInstrumentor
# Try importing from public API first, fallback to private API
try:
    from opentelemetry.sdk.logs import LoggerProvider, LoggingHandler
except ImportError:
    from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
# Try importing from public API first, fallback to private API
try:
    from opentelemetry.sdk.logs.export import BatchLogRecordProcessor
except ImportError:
    from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import SERVICE_NAME, SERVICE_VERSION, Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor


def get_resource(service_name: str, service_version: str = "unknown") -> Resource:
    """
    Create OpenTelemetry resource with service attributes.

    Args:
        service_name: Logical service name for resource attributes.
        service_version: Service version for resource attributes.

    Returns:
        OpenTelemetry Resource instance.
    """
    return Resource(attributes={
        SERVICE_NAME: service_name,
        SERVICE_VERSION: service_version,
        "service.instance.id": os.environ.get("HOSTNAME", "unknown"),
        "deployment.environment": os.environ.get("ENVIRONMENT", "development"),
    })


def get_otlp_endpoint() -> str:
    """
    Get OTLP endpoint from environment variables.

    Returns:
        OTLP endpoint URL.
    """
    return os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4317")


def get_otlp_headers() -> dict | None:
    """
    Get OTLP headers from environment variables.

    Returns:
        Dictionary of headers or None if no bearer token is set.
    """
    bearer_token = os.environ.get("OTEL_EXPORTER_OTLP_BEARER_TOKEN")
    if bearer_token:
        return {"Authorization": f"Bearer {bearer_token}"}
    return None


def setup_tracing(resource: Resource, otlp_endpoint: str, headers: dict | None = None) -> trace.Tracer:
    """
    Set up OpenTelemetry tracing.

    Args:
        resource: OpenTelemetry resource with service attributes.
        otlp_endpoint: Endpoint for the OTLP trace exporter.
        headers: Optional headers for OTLP exporter.

    Returns:
        An OpenTelemetry Tracer instance.
    """
    trace_provider = TracerProvider(resource=resource)

    # Create exporter with optional headers
    exporter_kwargs = {"endpoint": otlp_endpoint}
    if headers:
        exporter_kwargs["headers"] = headers

    otlp_exporter = OTLPSpanExporter(**exporter_kwargs)
    otlp_processor = BatchSpanProcessor(otlp_exporter)
    trace_provider.add_span_processor(otlp_processor)
    trace.set_tracer_provider(trace_provider)
    return trace.get_tracer(__name__)


def setup_metrics(resource: Resource, otlp_endpoint: str, headers: dict | None = None) -> metrics.Meter:
    """
    Set up OpenTelemetry metrics.

    Args:
        resource: OpenTelemetry resource with service attributes.
        otlp_endpoint: Endpoint for the OTLP metric exporter.
        headers: Optional headers for OTLP exporter.

    Returns:
        An OpenTelemetry Meter instance.
    """
    # Create exporter with optional headers
    exporter_kwargs = {"endpoint": otlp_endpoint}
    if headers:
        exporter_kwargs["headers"] = headers

    metric_reader = PeriodicExportingMetricReader(
        OTLPMetricExporter(**exporter_kwargs),
        export_interval_millis=30000  # Export every 30 seconds
    )
    metrics.set_meter_provider(MeterProvider(resource=resource, metric_readers=[metric_reader]))
    return metrics.get_meter(__name__)


def setup_logging(resource: Resource, otlp_endpoint: str, headers: dict | None = None) -> logging.Logger:
    """
    Set up OpenTelemetry logging.

    This sets up OTLP log export while preserving the existing loguru setup.
    The application can continue using loguru for local logging while also
    sending structured logs to the OTLP endpoint.

    Args:
        resource: OpenTelemetry resource with service attributes.
        otlp_endpoint: Endpoint for the OTLP log exporter.
        headers: Optional headers for OTLP exporter.

    Returns:
        A configured logger for OpenTelemetry.
    """
    logger_provider = LoggerProvider(resource=resource)
    set_logger_provider(logger_provider)

    # Create exporter with optional headers if available
    if OTLPLogExporter is not None:
        exporter_kwargs = {"endpoint": otlp_endpoint}
        if headers:
            exporter_kwargs["headers"] = headers

        logger_provider.add_log_record_processor(
            BatchLogRecordProcessor(OTLPLogExporter(**exporter_kwargs))
        )
    else:
        # Log exporter not available, skip OTLP logging setup
        print("Warning: OTLPLogExporter not available, skipping OTLP log export")

    # Create a handler for OpenTelemetry logs
    handler = LoggingHandler(level=logging.INFO, logger_provider=logger_provider)

    # Create a separate logger for OpenTelemetry that doesn't interfere with loguru
    otel_logger = logging.getLogger("changedetection.otel")
    otel_logger.addHandler(handler)
    otel_logger.setLevel(logging.INFO)
    otel_logger.propagate = False  # Don't propagate to root logger to avoid conflicts

    # Instrument standard logging for trace correlation
    LoggingInstrumentor().instrument(set_logging_format=True)

    return otel_logger


def setup_instrumentation(app: Flask, service_name: str, service_version: str = "unknown") -> tuple[logging.Logger, trace.Tracer, metrics.Meter]:
    """
    Instrument a Flask application with OpenTelemetry.

    Args:
        app: The Flask application instance to instrument.
        service_name: Logical service name for resource attributes.
        service_version: Service version for resource attributes.

    Returns:
        Tuple containing (logger, tracer, meter) instances.
    """
    # Instrument Flask application
    FlaskInstrumentor().instrument_app(app)

    # Get configuration
    resource = get_resource(service_name, service_version)
    otlp_endpoint = get_otlp_endpoint()
    headers = get_otlp_headers()

    # Setup telemetry components
    tracer = setup_tracing(resource, otlp_endpoint, headers)
    meter = setup_metrics(resource, otlp_endpoint, headers)
    logger = setup_logging(resource, otlp_endpoint, headers)

    return logger, tracer, meter


def create_custom_metrics(meter: metrics.Meter) -> dict:
    """
    Create custom metrics for changedetection.io application.

    Args:
        meter: OpenTelemetry Meter instance.

    Returns:
        Dictionary of metric instruments.
    """
    return {
        # HTTP metrics
        "http_requests_total": meter.create_counter(
            "http_requests_total",
            description="Total number of HTTP requests",
            unit="1"
        ),
        "http_request_duration": meter.create_histogram(
            "http_request_duration_seconds",
            description="HTTP request duration in seconds",
            unit="s"
        ),

        # Application-specific metrics
        "watch_checks_total": meter.create_counter(
            "watch_checks_total",
            description="Total number of website checks performed",
            unit="1"
        ),
        "watch_changes_detected": meter.create_counter(
            "watch_changes_detected_total",
            description="Total number of changes detected",
            unit="1"
        ),
        "notification_sent_total": meter.create_counter(
            "notifications_sent_total",
            description="Total number of notifications sent",
            unit="1"
        ),
        "queue_size": meter.create_up_down_counter(
            "queue_size",
            description="Current size of processing queues",
            unit="1"
        ),
        "active_watches": meter.create_up_down_counter(
            "active_watches",
            description="Number of active watches",
            unit="1"
        ),
        "worker_processing_time": meter.create_histogram(
            "worker_processing_time_seconds",
            description="Time spent processing in worker threads",
            unit="s"
        ),
    }


def setup_loguru_trace_correlation():
    """
    Setup loguru to include OpenTelemetry trace and span IDs in log records.
    This enhances the existing loguru setup with trace correlation.
    """

    from loguru import logger
    from opentelemetry import trace

    def trace_correlation_filter(record):
        """Add OpenTelemetry trace and span IDs to loguru records"""
        # Get current span context
        current_span = trace.get_current_span()
        if current_span and current_span.is_recording():
            span_context = current_span.get_span_context()
            if span_context.is_valid:
                # Add trace and span IDs to the record
                record["extra"]["trace_id"] = format(span_context.trace_id, "032x")
                record["extra"]["span_id"] = format(span_context.span_id, "016x")
                record["extra"]["trace_flags"] = span_context.trace_flags
        return record

    # Add the filter to existing loguru configuration
    # This will enhance all log messages with trace correlation
    logger.configure(patcher=trace_correlation_filter)

    return logger


def get_trace_context():
    """
    Get current OpenTelemetry trace context for manual logging.

    Returns:
        Dictionary with trace_id and span_id, or empty dict if no active span.
    """
    from opentelemetry import trace

    current_span = trace.get_current_span()
    if current_span and current_span.is_recording():
        span_context = current_span.get_span_context()
        if span_context.is_valid:
            return {
                "trace_id": format(span_context.trace_id, "032x"),
                "span_id": format(span_context.span_id, "016x"),
                "trace_flags": span_context.trace_flags
            }
    return {}
