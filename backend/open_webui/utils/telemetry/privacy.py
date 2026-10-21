"""Local redaction helpers for OpenTelemetry spans exported from Open WebUI."""

import asyncio
import json
import re
from urllib.parse import urlsplit, urlunsplit

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import Event, ReadableSpan
from opentelemetry.sdk.trace.export import SpanExporter
from opentelemetry.trace import Status, StatusCode

_QUOTED_SECRET_ASSIGNMENT = re.compile(
    r"""(?i)(?<![A-Za-z0-9_-])(?P<key_quote>["']?)(?P<key>(?:[A-Za-z0-9_-]*[_-])?(?:api[_ -]?key|access[_ -]?token|"""
    r"""refresh[_ -]?token|secret(?:[_ -]?access)?[_ -]?key|password|passwd|secret|token))"""
    r"""(?P=key_quote)(?P<separator>\s*[:=]\s*)(?P<quoted>"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*')"""
)
_SECRET_ASSIGNMENT = re.compile(
    r"""(?i)(?<![A-Za-z0-9_-])(?P<key_quote>["']?)(?P<key>(?:[A-Za-z0-9_-]*[_-])?(?:api[_ -]?key|access[_ -]?token|"""
    r"""refresh[_ -]?token|secret(?:[_ -]?access)?[_ -]?key|password|passwd|secret|token))"""
    r"""(?P=key_quote)(?P<separator>\s*[:=]\s*)(?P<value_quote>["']?)[^\s,;\]\}"']+"""
)
_BEARER = re.compile(r'(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+')
_JWT = re.compile(r'\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b')
_EMAIL = re.compile(r'(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b')
_CARD = re.compile(r'(?<!\d)(?:\d[ -]?){13,19}(?!\d)')
_PHONE = re.compile(r'(?<!\w)(?:\+?\d[\d(). -]{7,}\d)(?!\w)')
_SPANISH_ID = re.compile(r'(?i)\b(?:\d{8}[A-Z]|[XYZ]\d{7}[A-Z])\b')
_IPV4 = re.compile(r'(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])')
_WEBHOOK_PATH = re.compile(r'(/api/v1/channels/webhooks/)[^/?#\s]+/[^/?#\s]+')
_UUID_PATH_PARAMETER = re.compile(r'(?i)/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}(?=/|[?#\s]|$)')
_MAX_INSPECTED_STREAM_LINE_BYTES = 64 * 1024
_SENSITIVE_ATTRIBUTE = re.compile(
    r'(?i)(?:^client\.user\.|(?:^|\.)(?:user\.(?:id|email|name|role)|enduser\.id)'
    r'(?:$|\.)|(?:^|\.)(?:client\.address|net\.peer\.ip|net\.sock\.peer\.addr|network\.peer\.address)'
    r'(?:$|\.)|(?:^|\.)(?:authorization|cookie|set-cookie|(?:http\.)?(?:request|response)\.headers?)'
    r'(?:$|\.)|(?:^|\.)(?:db\.statement|db\.query\.text|url\.query)(?:$|\.))'
)


def redact_text(value: str) -> str:
    """Mask common credential and direct-identifier patterns before export."""
    if not isinstance(value, str):
        return value

    # Webhook authentication lives in the path, including in HTTP span names.
    value = _WEBHOOK_PATH.sub(r'\1{webhook_id}/{token}', value)
    value = _UUID_PATH_PARAMETER.sub('/{id}', value)
    value = _QUOTED_SECRET_ASSIGNMENT.sub(
        lambda match: (
            f'{match.group("key_quote")}{match.group("key")}{match.group("key_quote")}'
            f'{match.group("separator")}{match.group("quoted")[0]}[REDACTED]'
            f'{match.group("quoted")[-1]}'
        ),
        value,
    )
    value = _SECRET_ASSIGNMENT.sub(
        lambda match: (
            f'{match.group("key_quote")}{match.group("key")}{match.group("key_quote")}'
            f'{match.group("separator")}[REDACTED]'
        ),
        value,
    )
    value = _BEARER.sub('Bearer [REDACTED]', value)
    value = _JWT.sub('[REDACTED:JWT]', value)
    value = _EMAIL.sub('[REDACTED:EMAIL]', value)
    value = _CARD.sub('[REDACTED:NUMBER]', value)
    value = _PHONE.sub('[REDACTED:PHONE]', value)
    value = _SPANISH_ID.sub('[REDACTED:ID]', value)
    value = _IPV4.sub('[REDACTED:IP]', value)
    return value


def safe_url(value: str) -> str:
    """Keep request destination and path while dropping credentials and query data."""
    try:
        parsed = urlsplit(value)
        if not parsed.scheme or not parsed.hostname:
            return redact_text(value)
        host = parsed.hostname
        if ':' in host:
            host = f'[{host}]'
        if parsed.port is not None:
            host = f'{host}:{parsed.port}'
        return urlunsplit((parsed.scheme, host, redact_text(parsed.path), '', ''))
    except (TypeError, ValueError):
        return '[REDACTED:URL]'


def safe_http_target(value: str) -> str:
    """Keep a legacy HTTP request path while dropping query and fragment data."""
    try:
        parsed = urlsplit(value)
        if parsed.scheme and parsed.hostname:
            return safe_url(value)
        return redact_text(parsed.path)
    except (TypeError, ValueError):
        return '[REDACTED:URL]'


def get_chat_user_message(metadata, form_data):
    """Return the final direct user message after inlet filters."""
    metadata = metadata or {}
    form_data = form_data or {}
    if 'messages' in form_data:
        for message in reversed(form_data.get('messages') or []):
            if isinstance(message, dict) and message.get('role') == 'user':
                return message
        return None
    user_message = metadata.get('user_message')
    return user_message if isinstance(user_message, dict) else None


def safe_attributes(attributes):
    safe = {}
    for key, value in (attributes or {}).items():
        if _SENSITIVE_ATTRIBUTE.search(key):
            continue
        if isinstance(value, str):
            normalized_key = key.lower()
            value = (
                safe_http_target(value)
                if normalized_key == 'http.target'
                else safe_url(value)
                if normalized_key == 'url.full' or normalized_key.endswith(('.url', '.url.full'))
                else redact_text(value)
            )
        elif isinstance(value, (tuple, list)):
            normalized_key = key.lower()
            value = tuple(
                safe_http_target(item)
                if normalized_key == 'http.target' and isinstance(item, str)
                else redact_text(item)
                if isinstance(item, str)
                else item
                for item in value
            )
        safe[key] = value
    return safe


def _safe_event(event: Event) -> Event:
    attributes = safe_attributes(event.attributes)
    if event.name == 'exception':
        attributes = {key: value for key, value in attributes.items() if key == 'exception.type'}
    return Event(event.name, attributes, event.timestamp)


class RedactingSpanExporter(SpanExporter):
    """Sanitize completed spans immediately before delegating them to an exporter."""

    def __init__(self, exporter):
        self._exporter = exporter

    def export(self, spans):
        safe_spans = [
            ReadableSpan(
                name=redact_text(span.name),
                context=span.context,
                parent=span.parent,
                resource=Resource(attributes=safe_attributes(span.resource.attributes)),
                attributes=safe_attributes(span.attributes),
                events=tuple(_safe_event(event) for event in span.events),
                # Link attributes can contain propagated baggage; omit links rather than
                # risk exporting unredacted values through a less common path.
                links=(),
                kind=span.kind,
                instrumentation_scope=span.instrumentation_scope,
                status=Status(
                    span.status.status_code,
                    redact_text(span.status.description) if span.status.description else None,
                ),
                start_time=span.start_time,
                end_time=span.end_time,
            )
            for span in spans
        ]
        return self._exporter.export(safe_spans)

    def shutdown(self):
        return self._exporter.shutdown()

    def force_flush(self, timeout_millis=30000):
        force_flush = getattr(self._exporter, 'force_flush', None)
        return force_flush(timeout_millis) if force_flush else True


def capture_chat_span(span, model_id: str, user_message, has_files: bool):
    """Attach only the user's plain-text turn, never assembled context or tool data."""
    span.set_attribute('openinference.span.kind', 'CHAIN')
    span.set_attribute('llm.model_name', redact_text(str(model_id))[:256])
    if not has_files and isinstance(user_message, dict):
        content = user_message.get('content')
        if isinstance(content, str) and content:
            span.set_attribute('llm.input_messages.0.message.role', 'user')
            span.set_attribute('llm.input_messages.0.message.content', redact_text(content)[:12000])


def capture_chat_output(span, assistant_message):
    if not isinstance(assistant_message, dict):
        return
    content = assistant_message.get('content')
    if isinstance(content, str) and content:
        span.set_attribute('llm.output_messages.0.message.role', 'assistant')
        span.set_attribute('llm.output_messages.0.message.content', redact_text(content)[:12000])
    usage = assistant_message.get('usage')
    if isinstance(usage, dict):
        prompt = usage.get('prompt_tokens', usage.get('input_tokens'))
        completion = usage.get('completion_tokens', usage.get('output_tokens'))
        if isinstance(prompt, int) and prompt >= 0:
            span.set_attribute('llm.token_count.prompt', prompt)
        if isinstance(completion, int) and completion >= 0:
            span.set_attribute('llm.token_count.completion', completion)


def trace_streaming_chat_response(response, span, ctx):
    """Keep the chat span open and current until the client stream is consumed."""
    original_iterator = response.body_iterator

    async def traced_iterator():
        completed = False
        pending = bytearray()
        skipping_line = False

        def capture_stream_error(line):
            line = line.strip()
            if line.startswith(b'data:'):
                line = line[5:].strip()
            if not line or line == b'[DONE]':
                return
            try:
                event = json.loads(line)
            except (json.JSONDecodeError, UnicodeDecodeError):
                return
            if isinstance(event, dict) and event.get('error'):
                ctx['response_error'] = True

        try:
            with trace.use_span(span, end_on_exit=False):
                async for chunk in original_iterator:
                    # Scan only new data. Never copy or rescan the accumulated
                    # prefix, and discard oversized lines through their newline.
                    data = chunk if isinstance(chunk, (bytes, str)) else str(chunk)
                    newline = b'\n' if isinstance(data, bytes) else '\n'
                    offset = 0
                    while offset < len(data):
                        boundary = data.find(newline, offset)
                        end = boundary if boundary >= 0 else len(data)
                        if not skipping_line:
                            size = len(pending) + end - offset
                            if size <= _MAX_INSPECTED_STREAM_LINE_BYTES:
                                part = data[offset:end]
                                if isinstance(part, str):
                                    part = part.encode('utf-8')
                                size = len(pending) + len(part)
                            if size > _MAX_INSPECTED_STREAM_LINE_BYTES:
                                pending.clear()
                                skipping_line = True
                                span.set_attribute('openinference.stream.inspection_truncated', True)
                            else:
                                pending.extend(part)
                        if boundary < 0:
                            break
                        if not skipping_line:
                            capture_stream_error(pending)
                        pending.clear()
                        skipping_line = False
                        offset = end + 1
                    yield chunk
            if pending:
                capture_stream_error(pending)
            capture_chat_output(span, ctx.get('assistant_message'))
            span.set_status(Status(StatusCode.ERROR if ctx.get('response_error') else StatusCode.OK))
            completed = True
        except Exception as error:
            span.set_attribute('error.type', type(error).__name__)
            span.set_status(Status(StatusCode.ERROR))
            raise
        except asyncio.CancelledError:
            span.set_attribute('openinference.stream.cancelled', True)
            span.set_status(Status(StatusCode.ERROR))
            raise
        finally:
            if not completed and span.is_recording():
                span.set_attribute('openinference.stream.completed', False)
            span.end()

    response.body_iterator = traced_iterator()
    return response
