"""Bounded, asynchronous hosting probes; never access Telegram or the database."""
import asyncio
import logging
import os
from urllib.parse import urlsplit

import aiohttp
from aiohttp import web

log = logging.getLogger("hosting")


def ping_url():
    base = (os.getenv("KEEP_ALIVE_URL") or os.getenv("RENDER_EXTERNAL_URL", "")).strip()
    if not base:
        return None
    parsed = urlsplit(base)
    if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("Keep-alive URL must be an HTTP(S) URL without credentials")
    return base.rstrip("/") + "/healthz"


def ping_interval():
    try:
        return max(30, min(600, int(os.getenv("KEEP_ALIVE_INTERVAL", "300"))))
    except ValueError:
        log.warning("Invalid KEEP_ALIVE_INTERVAL; using 300 seconds")
        return 300


async def health(request):
    # Liveness only: no SQL queries, Telegram calls, audio work, or secret details.
    return web.Response(text="OK", headers={"Cache-Control": "no-store"})


class HealthServer:
    def __init__(self, runner):
        self.runner = runner
        self._cleanup = None

    def close(self):
        if self._cleanup is None:
            self._cleanup = asyncio.create_task(self.runner.cleanup())

    async def wait_closed(self):
        self.close()
        await self._cleanup


async def start_health_server():
    port = os.getenv("PORT")
    if not port:
        return None  # Background workers/local runs do not need a web listener.
    app = web.Application(client_max_size=1024)
    app.router.add_get("/", health)
    app.router.add_get("/healthz", health)
    runner = web.AppRunner(app, access_log=None, shutdown_timeout=2)
    await runner.setup()
    try:
        await web.TCPSite(runner, "0.0.0.0", int(port)).start()
    except BaseException:
        await runner.cleanup()
        raise  # A web service without a listener is a deployment error.
    log.info("Health check listening on :%s (/healthz)", port)
    return HealthServer(runner)


async def keep_alive(stop):
    """Best-effort self-ping, not an uptime guarantee on a sleeping free service."""
    if os.getenv("KEEP_ALIVE_ENABLED", "true").lower() in ("0", "false", "no"):
        return
    try:
        url = ping_url()
    except ValueError:
        log.warning("Invalid keep-alive URL; self-ping disabled")
        return
    if not url:
        log.info("No public URL configured; self-ping disabled")
        return
    interval = ping_interval()
    # Native asynchronous I/O: no worker threads shared with media/audio processing.
    timeout = aiohttp.ClientTimeout(total=10, connect=5)
    async with aiohttp.ClientSession(timeout=timeout, connector=aiohttp.TCPConnector(limit=1)) as session:
        while not stop.is_set():
            try:
                async with session.get(url, allow_redirects=False,
                                       headers={"User-Agent": "bot-health-probe"}) as response:
                    response.raise_for_status()
                    await response.content.read(64)  # Never download an unbounded response.
                    log.debug("Keep-alive HTTP %s", response.status)
            except (aiohttp.ClientError, asyncio.TimeoutError, OSError):
                log.warning("Keep-alive request failed; bot continues")
            try:
                await asyncio.wait_for(stop.wait(), interval)
            except asyncio.TimeoutError:
                pass