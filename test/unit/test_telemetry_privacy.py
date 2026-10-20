import asyncio
import unittest

from open_webui.utils.telemetry.privacy import (
    RedactingSpanExporter,
    capture_chat_output,
    capture_chat_span,
    get_chat_user_message,
    redact_text,
    safe_attributes,
    safe_url,
    trace_streaming_chat_response,
)
from open_webui.utils.telemetry.setup import _build_resource_attributes, _build_trace_headers
from opentelemetry.sdk.trace import Event, ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import Status, StatusCode
from starlette.responses import StreamingResponse


class TelemetryPrivacyTests(unittest.TestCase):
    def test_redacts_credentials_and_direct_identifiers(self):
        result = redact_text(
            'email=alice@example.com Bearer abc.def api_key=secret-value '
            'DNI 12345678Z phone +34 600 123 456'
        )
        self.assertNotIn('alice@example.com', result)
        self.assertNotIn('abc.def', result)
        self.assertNotIn('secret-value', result)
        self.assertNotIn('12345678Z', result)
        self.assertNotIn('+34 600 123 456', result)
        self.assertIn('[REDACTED:EMAIL]', result)

    def test_redacts_environment_variable_secret_names(self):
        result = redact_text(
            'OPENAI_API_KEY=sk-openai DATABASE_PASSWORD="correct horse battery staple" '
            'AWS_SECRET_ACCESS_KEY=aws-secret GITHUB_TOKEN=github-secret'
        )
        for secret in ('sk-openai', 'correct horse battery staple', 'aws-secret', 'github-secret'):
            self.assertNotIn(secret, result)

    def test_redacts_escaped_quotes_inside_quoted_secrets(self):
        result = redact_text(r'password="alpha\"bravo charlie"')
        self.assertNotIn('alpha', result)
        self.assertNotIn('bravo', result)
        self.assertNotIn('charlie', result)
        self.assertIn('[REDACTED]', result)

    def test_redacts_secrets_under_quoted_json_keys(self):
        result = redact_text('{"api_key":"live-secret", "password":"correct horse"}')
        self.assertNotIn('live-secret', result)
        self.assertNotIn('correct horse', result)

    def test_drops_legacy_http_targets_and_client_ip_attributes(self):
        attributes = safe_attributes(
            {
                'http.target': '/oauth/callback?code=opaque&state=private#fragment',
                'client.address': '2001:db8::1234',
                'net.peer.ip': '2001:db8::abcd',
            }
        )
        self.assertEqual(attributes['http.target'], '/oauth/callback')
        self.assertNotIn('client.address', attributes)
        self.assertNotIn('net.peer.ip', attributes)

    def test_selects_post_inlet_user_message_for_trace(self):
        filtered = {'role': 'user', 'content': 'redacted prompt'}
        metadata = {'user_message': {'role': 'user', 'content': 'original prompt'}}
        form_data = {'messages': [{'role': 'system', 'content': 'system'}, filtered]}
        self.assertIs(get_chat_user_message(metadata, form_data), filtered)

    def test_does_not_fall_back_to_filtered_out_user_message(self):
        metadata = {'user_message': {'role': 'user', 'content': 'original prompt'}}
        self.assertIsNone(get_chat_user_message(metadata, {'messages': []}))

    def test_resource_attributes_keep_explicit_service_name_precedence(self):
        attributes = _build_resource_attributes('service.name=generic,deployment.environment=prod', 'traces')
        self.assertEqual(attributes['service.name'], 'traces')
        self.assertEqual(attributes['deployment.environment'], 'prod')

    def test_trace_headers_override_generic_and_basic_auth(self):
        headers = _build_trace_headers(
            'user',
            'password',
            'authorization=generic,api-key=generic-key',
            'authorization=trace,api-key=trace-key',
        )
        self.assertEqual(headers['authorization'], 'trace')
        self.assertEqual(headers['api-key'], 'trace-key')

    def test_url_drops_credentials_query_and_fragment(self):
        self.assertEqual(
            safe_url('https://user:password@example.test/path?api_key=secret#fragment'),
            'https://example.test/path',
        )

    def test_url_preserves_ipv6_authority_syntax(self):
        self.assertEqual(
            safe_url('https://[2001:db8::1]:8443/path?token=secret'),
            'https://[2001:db8::1]:8443/path',
        )
        self.assertEqual(safe_url('https://[2001:db8::1]/path'), 'https://[2001:db8::1]/path')

    def test_exporter_removes_sensitive_attributes_and_exception_text(self):
        downstream = InMemorySpanExporter()
        exporter = RedactingSpanExporter(downstream)
        source = ReadableSpan(
            name='request',
            attributes={
                'client.user.email': 'alice@example.com',
                'http.url': 'https://example.test/path?token=secret',
                'http.request.header.x_api_key': ['live-api-key'],
                'http.response.header.set_cookie': ['session=opaque-token'],
                'db.statement': 'select private_value',
                'input.value': 'hello alice@example.com api_key=secret-value',
            },
            events=(
                Event(
                    'exception',
                    {'exception.type': 'ValueError', 'exception.message': 'Bearer secret-token'},
                ),
            ),
        )

        exporter.export((source,))
        exported = downstream.get_finished_spans()[0]
        self.assertNotIn('client.user.email', exported.attributes)
        self.assertNotIn('db.statement', exported.attributes)
        self.assertNotIn('http.request.header.x_api_key', exported.attributes)
        self.assertNotIn('http.response.header.set_cookie', exported.attributes)
        self.assertEqual(exported.attributes['http.url'], 'https://example.test/path')
        self.assertNotIn('alice@example.com', exported.attributes['input.value'])
        self.assertNotIn('secret-value', exported.attributes['input.value'])
        self.assertEqual(exported.events[0].attributes, {'exception.type': 'ValueError'})

    def test_chat_span_captures_redacted_user_and_assistant_text_only(self):
        provider = TracerProvider()
        downstream = InMemorySpanExporter()
        provider.add_span_processor(SimpleSpanProcessor(RedactingSpanExporter(downstream)))
        span = provider.get_tracer('test').start_span('chat', attributes={})
        capture_chat_span(
            span,
            'gpt-oss-20b',
            {'content': 'email alice@example.com api_key=super-secret'},
            has_files=False,
        )
        capture_chat_output(span, {'content': 'Call me at +34 600 123 456', 'output': [{'type': 'function_call'}]})
        span.set_status(Status(StatusCode.OK))
        span.end()

        exported = downstream.get_finished_spans()[0]
        self.assertIn('llm.input_messages.0.message.content', exported.attributes)
        self.assertNotIn('alice@example.com', exported.attributes['llm.input_messages.0.message.content'])
        self.assertNotIn('super-secret', exported.attributes['llm.input_messages.0.message.content'])
        self.assertNotIn('+34 600 123 456', exported.attributes['llm.output_messages.0.message.content'])
        self.assertNotIn('function_call', str(exported.attributes))

    def test_files_suppress_input_capture(self):
        provider = TracerProvider()
        downstream = InMemorySpanExporter()
        provider.add_span_processor(SimpleSpanProcessor(RedactingSpanExporter(downstream)))
        span = provider.get_tracer('test').start_span('chat')
        capture_chat_span(span, 'model', {'content': 'private attachment text'}, has_files=True)
        span.end()
        self.assertNotIn('llm.input_messages.0.message.content', downstream.get_finished_spans()[0].attributes)

    def test_stream_span_lives_until_consumed_and_captures_final_text(self):
        provider = TracerProvider()
        downstream = InMemorySpanExporter()
        provider.add_span_processor(SimpleSpanProcessor(RedactingSpanExporter(downstream)))
        span = provider.get_tracer('test').start_span('chat')

        async def body():
            yield b'data: chunk\n\n'

        response = StreamingResponse(body())
        ctx = {'assistant_message': {'content': 'answer for alice@example.com'}}
        trace_streaming_chat_response(response, span, ctx)

        async def consume():
            return [chunk async for chunk in response.body_iterator]

        self.assertEqual(asyncio.run(consume()), [b'data: chunk\n\n'])
        exported = downstream.get_finished_spans()[0]
        self.assertIn('llm.output_messages.0.message.content', exported.attributes)
        self.assertNotIn('alice@example.com', exported.attributes['llm.output_messages.0.message.content'])

    def test_stream_error_ends_span_without_exporting_exception_text(self):
        provider = TracerProvider()
        downstream = InMemorySpanExporter()
        provider.add_span_processor(SimpleSpanProcessor(RedactingSpanExporter(downstream)))
        span = provider.get_tracer('test').start_span('chat')

        async def body():
            raise RuntimeError('alice@example.com api_key=secret-value')
            yield b''

        response = StreamingResponse(body())
        trace_streaming_chat_response(response, span, {})

        async def consume():
            with self.assertRaises(RuntimeError):
                async for _ in response.body_iterator:
                    pass

        asyncio.run(consume())
        exported = downstream.get_finished_spans()[0]
        self.assertEqual(exported.status.status_code, StatusCode.ERROR)
        self.assertEqual(exported.attributes['error.type'], 'RuntimeError')
        self.assertNotIn('alice@example.com', str(exported))
        self.assertNotIn('secret-value', str(exported))

    def test_stream_provider_error_marks_span_as_error(self):
        provider = TracerProvider()
        downstream = InMemorySpanExporter()
        provider.add_span_processor(SimpleSpanProcessor(RedactingSpanExporter(downstream)))
        span = provider.get_tracer('test').start_span('chat')

        async def body():
            yield b'data: {"error":{"message":"provider failed"}}\n\n'

        response = StreamingResponse(body())
        trace_streaming_chat_response(response, span, {})

        async def consume():
            return [chunk async for chunk in response.body_iterator]

        self.assertEqual(asyncio.run(consume()), [b'data: {"error":{"message":"provider failed"}}\n\n'])
        exported = downstream.get_finished_spans()[0]
        self.assertEqual(exported.status.status_code, StatusCode.ERROR)

    def test_stream_cancellation_marks_and_ends_span(self):
        provider = TracerProvider()
        downstream = InMemorySpanExporter()
        provider.add_span_processor(SimpleSpanProcessor(RedactingSpanExporter(downstream)))
        span = provider.get_tracer('test').start_span('chat')

        async def body():
            raise asyncio.CancelledError()
            yield b''

        response = StreamingResponse(body())
        trace_streaming_chat_response(response, span, {})

        async def consume():
            with self.assertRaises(asyncio.CancelledError):
                async for _ in response.body_iterator:
                    pass

        asyncio.run(consume())
        exported = downstream.get_finished_spans()[0]
        self.assertEqual(exported.status.status_code, StatusCode.ERROR)
        self.assertTrue(exported.attributes['openinference.stream.cancelled'])


if __name__ == '__main__':
    unittest.main()
