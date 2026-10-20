import asyncio
import unittest

from open_webui.utils.telemetry.privacy import (
    RedactingSpanExporter,
    capture_chat_output,
    capture_chat_span,
    redact_text,
    safe_url,
    trace_streaming_chat_response,
)
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

    def test_url_drops_credentials_query_and_fragment(self):
        self.assertEqual(
            safe_url('https://user:password@example.test/path?api_key=secret#fragment'),
            'https://example.test/path',
        )

    def test_exporter_removes_sensitive_attributes_and_exception_text(self):
        downstream = InMemorySpanExporter()
        exporter = RedactingSpanExporter(downstream)
        source = ReadableSpan(
            name='request',
            attributes={
                'client.user.email': 'alice@example.com',
                'http.url': 'https://example.test/path?token=secret',
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
