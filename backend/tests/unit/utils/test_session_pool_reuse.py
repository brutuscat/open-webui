import asyncio
import unittest
from unittest.mock import patch

from aiohttp import web
from open_webui.utils import session_pool


class SessionPoolReuseTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await session_pool.close_session()
        self.transports = []
        self.connection_headers = []

        async def streamed(request):
            self.transports.append(request.transport)
            self.connection_headers.append(request.headers.get('Connection'))
            response = web.StreamResponse()
            await response.prepare(request)
            await response.write(b'data: first\n\n')
            await response.write(b'data: [DONE]\n\n')
            await response.write_eof()
            return response

        app = web.Application()
        app.router.add_get('/', streamed)
        self.runner = web.AppRunner(app)
        await self.runner.setup()
        site = web.TCPSite(self.runner, '127.0.0.1', 0)
        await site.start()
        self.url = f'http://127.0.0.1:{self.runner.addresses[0][1]}/'

    async def asyncTearDown(self):
        await session_pool.close_session()
        await self.runner.cleanup()

    async def check_reuse(self, timeout):
        with patch.object(session_pool, 'AIOHTTP_POOL_KEEPALIVE_TIMEOUT', timeout):
            session = await session_pool.get_session()
            for _ in range(2):
                async with session.get(self.url) as response:
                    self.assertEqual(await response.read(), b'data: first\n\ndata: [DONE]\n\n')
                # Ensure a measurable idle interval on Windows' coarse clock.
                await asyncio.sleep(0.03)
            self.assertIs(session, await session_pool.get_session())
        self.assertEqual(len(self.transports), 2)
        self.assertEqual(self.transports[0] is self.transports[1], timeout > 0)
        self.assertNotIn('close', self.connection_headers)

    async def test_default_reuses_connection_and_reads_complete_stream(self):
        await self.check_reuse(15)

    async def test_opt_out_uses_new_connections_and_reads_complete_stream(self):
        await self.check_reuse(0)
