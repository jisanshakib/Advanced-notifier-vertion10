"""Terminal disconnects fail; intentional stops and startup cleanup remain safe."""
import asyncio
import os
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

os.environ.update(API_ID="1", API_HASH="x", BOT_TOKEN="1:x",
                  SQLITE_PATH=":memory:", GEMINI_API_KEY="", GROQ_API_KEY="", DATABASE_URL="")
import bot


class LifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        bot._stop = asyncio.Event()
        bot._bg_tasks.clear()

    async def test_unexpected_clean_disconnect_is_failure(self):
        task = asyncio.create_task(asyncio.sleep(0))
        with self.assertRaisesRegex(RuntimeError, "disconnected unexpectedly"):
            await bot.wait_for_clients([task])

    async def test_client_exception_is_failure(self):
        async def failed():
            raise OSError("offline")
        task = asyncio.create_task(failed())
        with self.assertRaises(RuntimeError) as caught:
            await bot.wait_for_clients([task])
        self.assertIsInstance(caught.exception.__cause__, OSError)

    async def test_intentional_stop_is_clean(self):
        bot._stop.set()
        await bot.wait_for_clients([asyncio.create_task(bot._stop.wait())])

    async def test_startup_failure_closes_health_server(self):
        server = MagicMock()
        server.wait_closed = AsyncMock()
        with patch.object(bot, "health_server", AsyncMock(return_value=server)), \
             patch.object(bot, "_USER_SESSION", "test"), \
             patch.object(bot.user, "connect", AsyncMock(side_effect=OSError("offline"))), \
             patch.object(bot.user, "disconnect", AsyncMock()), \
             patch.object(bot.bot, "disconnect", AsyncMock()), \
             patch.object(bot.voice_call, "shutdown", AsyncMock()), \
             patch.object(bot.db, "close"):
            with self.assertRaises(OSError):
                await bot.main()
        server.close.assert_called_once()
        server.wait_closed.assert_awaited_once()
        self.assertTrue(bot._stop.is_set())

    async def test_failed_voice_cleanup_still_disconnects_clients_and_db(self):
        user_disconnect, bot_disconnect = AsyncMock(), AsyncMock()
        with patch.object(bot.user, "disconnect", user_disconnect), \
             patch.object(bot.bot, "disconnect", bot_disconnect), \
             patch.object(bot.voice_call, "shutdown", AsyncMock(side_effect=RuntimeError("voice failed"))), \
             patch.object(bot.db, "close") as close:
            await bot.shutdown(None, [])
        user_disconnect.assert_awaited_once()
        bot_disconnect.assert_awaited_once()
        close.assert_called_once()


if __name__ == "__main__":
    unittest.main()