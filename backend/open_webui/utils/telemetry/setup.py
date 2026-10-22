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
    OTEL_EXPORTER_OTLP_TRACES_HEADERS,
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


def _build_resource_attributes(resource_attributes: str, service_name: str):
    attributes = {}
    for attribute in resource_attributes.split(','):
        key, separator, value = attribute.partition('=')
        if separator and key.strip():
            attributes[unquote(key.strip())] = unquote(value.strip())
    attributes[SERVICE_NAME] = service_name
    return attributes


def _build_trace_headers(
    basic_auth_username: str,
    basic_auth_password: str,
    generic_headers: str,
    trace_headers: str,
):
    headers = {}
    if basic_auth_username and basic_auth_password:
        auth_string = f'{basic_auth_username}:{basic_auth_password}'
        headers['authorization'] = f'Basic {b64encode(auth_string.encode()).decode()}'

    for header_source in (generic_headers, trace_headers):
        for header in header_source.split(','):
            key, separator, value = header.partition('=')
            if separator and key.strip():
                headers[unquote(key.strip()).lower()] = unquote(value.strip())
    return headers


def setup(app: FastAPI, db_engine: Engine):
    # set up trace
    resource_attributes = _build_resource_attributes(OTEL_RESOURCE_ATTRIBUTES, OTEL_SERVICE_NAME)
    resource = Resource.create(attributes=resource_attributes)
    if ENABLE_OTEL_TRACES:
        trace.set_tracer_provider(TracerProvider(resource=resource))

        headers = _build_trace_headers(
            OTEL_BASIC_AUTH_USERNAME,
            OTEL_BASIC_AUTH_PASSWORD,
            OTEL_EXPORTER_OTLP_HEADERS,
            OTEL_EXPORTER_OTLP_TRACES_HEADERS,
        )

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
