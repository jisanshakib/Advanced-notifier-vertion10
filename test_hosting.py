import asyncio
import os
import unittest
from unittest.mock import patch

import aiohttp
from aiohttp import web
import hosting


class HostingTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.server = None
        self.pinger = None

    async def asyncTearDown(self):
        if self.pinger:
            self.pinger.cancel()
            await asyncio.gather(self.pinger, return_exceptions=True)
        if self.server:
            await self.server.wait_closed()

    async def test_real_http_health_and_self_ping(self):
        with patch.dict(os.environ, {"PORT": "0"}):
            self.server = await hosting.start_health_server()
        address = self.server.runner.addresses[0]
        base = f"http://127.0.0.1:{address[1]}"
        async with aiohttp.ClientSession() as client:
            for path in ("/healthz", "/"):
                async with client.get(base + path) as response:
                    self.assertEqual(response.status, 200)
                    self.assertEqual(await response.text(), "OK")
            async with client.get(base + "/missing") as response:
                self.assertEqual(response.status, 404)
        stop = asyncio.Event()
        received = asyncio.Event()
        async def on_response(session, context, params):
            self.assertEqual(str(params.url), base + "/healthz")
            received.set()
        trace = aiohttp.TraceConfig()
        trace.on_request_end.append(on_response)
        original = aiohttp.ClientSession
        with patch.dict(os.environ, {"KEEP_ALIVE_URL": base, "KEEP_ALIVE_ENABLED": "true"}), \
             patch.object(hosting.aiohttp, "ClientSession",
                          side_effect=lambda **kw: original(trace_configs=[trace], **kw)):
            self.pinger = asyncio.create_task(hosting.keep_alive(stop))
            await asyncio.wait_for(received.wait(), 2)
            stop.set()
            await asyncio.wait_for(self.pinger, 1)

    async def test_slow_http_does_not_block_other_tasks_and_cancels(self):
        entered = asyncio.Event()
        release = asyncio.Event()
        async def slow(request):
            entered.set()
            await release.wait()
            return web.Response(text="OK")
        app = web.Application()
        app.router.add_get("/healthz", slow)
        runner = web.AppRunner(app)
        await runner.setup()
        await web.TCPSite(runner, "127.0.0.1", 0).start()
        base = f"http://127.0.0.1:{runner.addresses[0][1]}"
        try:
            with patch.dict(os.environ, {"KEEP_ALIVE_URL": base, "KEEP_ALIVE_ENABLED": "true"}):
                self.pinger = asyncio.create_task(hosting.keep_alive(asyncio.Event()))
                await asyncio.wait_for(entered.wait(), 2)
                heartbeat = asyncio.Event()
                asyncio.get_running_loop().call_later(0.02, heartbeat.set)
                await asyncio.wait_for(heartbeat.wait(), 0.2)
                self.assertFalse(self.pinger.done())
                self.pinger.cancel()
                await asyncio.wait_for(asyncio.gather(self.pinger, return_exceptions=True), 1)
        finally:
            release.set()
            await runner.cleanup()

    async def test_failed_ping_does_not_stop_task(self):
        stop = asyncio.Event()
        with patch.dict(os.environ, {"KEEP_ALIVE_URL": "http://127.0.0.1:1", "KEEP_ALIVE_ENABLED": "true"}):
            self.pinger = asyncio.create_task(hosting.keep_alive(stop))
            await asyncio.sleep(0.1)
            self.assertFalse(self.pinger.done())
            stop.set()
            await asyncio.wait_for(self.pinger, 1)

    async def test_default_interval_and_render_url(self):
        with patch.dict(os.environ, {"RENDER_EXTERNAL_URL": "https://example.onrender.com/"}, clear=True):
            self.assertEqual(hosting.ping_interval(), 300)
            self.assertEqual(hosting.ping_url(), "https://example.onrender.com/healthz")


if __name__ == "__main__":
    unittest.main()