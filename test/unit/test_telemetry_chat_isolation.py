"""Run production chat coroutines with service stubs, without booting the app/DB."""

import ast
import asyncio
import copy
import logging
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from open_webui.utils.telemetry.privacy import (
    capture_chat_output,
    capture_chat_span,
    get_chat_user_message,
    trace_streaming_chat_response,
)
from opentelemetry import trace
from opentelemetry.sdk.trace.sampling import ALWAYS_ON
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import SpanKind, Status, StatusCode
from starlette.responses import JSONResponse, StreamingResponse

ROOT = Path(__file__).resolve().parents[2]


def load_coroutine(path, name, dependencies):
    # Compile the complete production function, including its awaits and return
    # contract. Only external services are stubbed; no application imports run.
    tree = ast.parse((ROOT / path).read_text())
    function = next(node for node in ast.walk(tree) if isinstance(node, ast.AsyncFunctionDef) and node.name == name)
    exec(compile(ast.Module(body=[function], type_ignores=[]), str(path), 'exec'), dependencies)
    return dependencies[name]


class TelemetryChatIsolationTests(unittest.TestCase):
    def test_concurrent_models_export_their_own_post_inlet_snapshot_before_rag(self):
        self.check_fanout()

    def test_request_filters_redact_or_remove_input_without_exposing_rag(self):
        for rag in (False, True):
            for action in ('redact', 'remove', 'drop_last', 'drop_duplicate', 'unchanged'):
                with self.subTest(rag=rag, action=action):
                    self.check_fanout(rag=rag, request_action=action)

    def check_fanout(self, rag=True, request_action=None):
        async def run():
            downstream = InMemorySpanExporter()
            provider = TracerProvider(sampler=ALWAYS_ON)
            provider.add_span_processor(SimpleSpanProcessor(downstream))
            arrived = set()
            snapshots_ready = asyncio.Event()
            models = {name: {'id': name} for name in ('dlp', 'removed', 'raw')}
            request = SimpleNamespace(
                state=SimpleNamespace(), app=SimpleNamespace(state=SimpleNamespace(MODELS=models))
            )

            async def inlet(_request, form_data, _user, _models):
                model_id = form_data['model']
                content = 'filtered by DLP' if model_id == 'dlp' else 'private unfiltered prompt'
                messages = [] if model_id == 'removed' else [{'role': 'user', 'content': content}]
                if messages and request_action in ('drop_last', 'drop_duplicate'):
                    messages.insert(
                        0,
                        {
                            'role': 'user',
                            'content': content
                            if request_action == 'drop_duplicate'
                            else 'private earlier conversation',
                        },
                    )
                return {**form_data, 'messages': messages}

            async def files(_request, form_data, _extra, _user):
                # All snapshots must have been taken before any model returns.
                arrived.add(form_data['model'])
                if len(arrived) == len(models):
                    snapshots_ready.set()
                await snapshots_ready.wait()
                return form_data, {'sources': [{'source': {'name': 'retrieved document'}}] if rag else []}

            async def inject_rag(_request, messages, _sources, _prompt):
                messages[-1]['content'] = 'private retrieved document body'
                return messages

            def last_user(messages):
                return next((message for message in reversed(messages) if message.get('role') == 'user'), None)

            async def filters(**kwargs):
                form_data = kwargs['form_data']
                if kwargs['filter_type'] == 'request':
                    if rag and last_user(form_data['messages']):
                        self.assertEqual(last_user(form_data['messages'])['content'], 'private retrieved document body')
                    if request_action == 'remove':
                        form_data['messages'] = []
                    elif request_action in ('drop_last', 'drop_duplicate') and form_data['messages']:
                        form_data['messages'].pop()
                    elif request_action == 'redact' and last_user(form_data['messages']):
                        last_user(form_data['messages'])['content'] = 'request filtered for ' + form_data['model']
                return form_data, {}

            dependencies = {
                'copy': copy,
                'log': logging.getLogger(__name__),
                'ENABLE_PLUGINS': request_action is not None,
                'get_filter_context': lambda request: None,
                'get_filter_functions': AsyncMock(return_value=['test-filter']),
                'process_filter_functions': filters,
                'UserModel': type('UserModel', (), {}),
                'Config': SimpleNamespace(get=AsyncMock(return_value=None)),
                'apply_params_to_form_data': lambda body, model: body,
                'is_saved_chat_id': lambda chat_id: False,
                'process_messages_with_output': lambda messages, **kwargs: messages,
                'get_reasoning_format': lambda model: None,
                'sanitize_tool_pairs': lambda messages: messages,
                'get_system_message': lambda messages: None,
                'convert_url_images_to_base64': AsyncMock(side_effect=lambda body, **kwargs: body),
                'get_event_emitter': AsyncMock(return_value=None),
                'get_event_call': AsyncMock(return_value=None),
                'get_system_oauth_token': AsyncMock(return_value=None),
                'get_task_model_id': lambda model_id, *args: model_id,
                'get_last_user_message': lambda messages: (last_user(messages) or {}).get('content'),
                'get_last_user_message_item': last_user,
                'process_pipeline_inlet_filter': inlet,
                'extract_skill_ids_from_messages': lambda messages: set(),
                'strip_skill_mentions': lambda messages: None,
                'chat_completion_files_handler': files,
                'resolve_system_prompt': AsyncMock(return_value=None),
                'apply_source_context_to_messages': inject_rag,
                'normalize_messages_for_model': lambda body: body,
            }
            payload = load_coroutine('backend/open_webui/utils/middleware.py', 'process_chat_payload', dependencies)
            chat_dependencies = {
                'asyncio': asyncio,
                'log': logging.getLogger(__name__),
                'process_chat_payload': payload,
                'drain_approved_tool_calls': AsyncMock(return_value=False),
                'ENABLE_OTEL': True,
                'ENABLE_OTEL_TRACES': True,
                'trace': SimpleNamespace(get_tracer=provider.get_tracer, use_span=trace.use_span),
                'SpanKind': SpanKind,
                'Status': Status,
                'StatusCode': StatusCode,
                'capture_chat_span': capture_chat_span,
                'capture_chat_output': capture_chat_output,
                'get_chat_user_message': get_chat_user_message,
                'trace_streaming_chat_response': trace_streaming_chat_response,
                'StreamingResponse': StreamingResponse,
                'JSONResponse': JSONResponse,
                'chat_completion_handler': AsyncMock(return_value={'choices': []}),
                'build_chat_response_context': AsyncMock(return_value={'assistant_message': {'content': 'answer'}}),
                'process_chat_response': AsyncMock(side_effect=lambda response, ctx: response),
            }
            process_chat = load_coroutine('backend/open_webui/main.py', 'process_chat', chat_dependencies)
            # The same request and initial messages are shared, as in UI fan-out.
            body = {'messages': [{'role': 'user', 'content': 'private unfiltered prompt'}], 'tools': []}
            results = await asyncio.wait_for(
                asyncio.gather(
                    *(
                        process_chat(request, {**body, 'model': model_id}, None, {}, model)
                        for model_id, model in models.items()
                    )
                ),
                timeout=5,
            )
            self.assertEqual(results, [{'choices': []}] * len(models))
            spans = {span.attributes['llm.model_name']: span for span in downstream.get_finished_spans()}
            self.assertEqual(set(spans), set(models))
            input_key = 'llm.input_messages.0.message.content'
            if request_action in ('remove', 'drop_last', 'drop_duplicate') or (request_action == 'redact' and rag):
                for span in spans.values():
                    self.assertNotIn(input_key, span.attributes)
            elif request_action == 'redact':
                self.assertEqual(spans['dlp'].attributes[input_key], 'request filtered for dlp')
                self.assertEqual(spans['raw'].attributes[input_key], 'request filtered for raw')
            else:
                self.assertEqual(spans['dlp'].attributes[input_key], 'filtered by DLP')
                self.assertEqual(spans['raw'].attributes[input_key], 'private unfiltered prompt')
            self.assertNotIn(input_key, spans['removed'].attributes)
            for span in spans.values():
                self.assertEqual(span.status.status_code, StatusCode.OK)
                self.assertNotIn('private retrieved document body', str(span.attributes))

        asyncio.run(run())
