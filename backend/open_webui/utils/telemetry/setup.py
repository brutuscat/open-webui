from base64 import b64encode
from urllib.parse import unquote

from fastapi import FastAPI
from open_webui.env import (
    ENABLE_OTEL_METRICS,
    ENABLE_OTEL_TRACES,
    OTEL_BASIC_AUTH_PASSWORD,
    OTEL_BASIC_AUTH_USERNAME,
    OTEL_EXPORTER_OTLP_ENDPOINT,
    OTEL_EXPORTER_OTLP_HEADERS,
    OTEL_EXPORTER_OTLP_INSECURE,
    OTEL_OTLP_SPAN_EXPORTER,
    OTEL_RESOURCE_ATTRIBUTES,
    OTEL_SERVICE_NAME,
)
from open_webui.utils.telemetry.instrumentors import Instrumentor
from open_webui.utils.telemetry.metrics import setup_metrics
from open_webui.utils.telemetry.privacy import RedactingSpanExporter
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
    OTLPSpanExporter as HttpOTLPSpanExporter,
)
from opentelemetry.sdk.resources import SERVICE_NAME, Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from sqlalchemy import Engine


def setup(app: FastAPI, db_engine: Engine):
    # set up trace
    resource_attributes = {}
    for attribute in OTEL_RESOURCE_ATTRIBUTES.split(','):
        key, separator, value = attribute.partition('=')
        if separator and key.strip():
            resource_attributes[unquote(key.strip())] = unquote(value.strip())
    resource_attributes[SERVICE_NAME] = OTEL_SERVICE_NAME
    resource = Resource.create(attributes=resource_attributes)
    if ENABLE_OTEL_TRACES:
        trace.set_tracer_provider(TracerProvider(resource=resource))

        # Add basic auth header only if both username and password are not empty
        headers = {}
        if OTEL_BASIC_AUTH_USERNAME and OTEL_BASIC_AUTH_PASSWORD:
            auth_string = f'{OTEL_BASIC_AUTH_USERNAME}:{OTEL_BASIC_AUTH_PASSWORD}'
            auth_header = b64encode(auth_string.encode()).decode()
            headers['authorization'] = f'Basic {auth_header}'

        for header in OTEL_EXPORTER_OTLP_HEADERS.split(','):
            key, separator, value = header.partition('=')
            if separator and key.strip():
                headers[unquote(key.strip())] = unquote(value.strip())

        # otlp export
        if OTEL_OTLP_SPAN_EXPORTER == 'http':
            exporter = HttpOTLPSpanExporter(
                endpoint=OTEL_EXPORTER_OTLP_ENDPOINT,
                headers=headers,
            )
        else:
            exporter = OTLPSpanExporter(
                endpoint=OTEL_EXPORTER_OTLP_ENDPOINT,
                insecure=OTEL_EXPORTER_OTLP_INSECURE,
                headers=headers,
            )
        trace.get_tracer_provider().add_span_processor(BatchSpanProcessor(RedactingSpanExporter(exporter)))
        Instrumentor(app=app, db_engine=db_engine).instrument()

    # set up metrics only if enabled
    if ENABLE_OTEL_METRICS:
        setup_metrics(app, resource, db_engine)
