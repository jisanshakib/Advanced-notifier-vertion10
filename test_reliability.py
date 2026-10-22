"""Missed-post fixes: dedupe/queue, album grouping, poller recovery, FloodWait retry."""
import os, sys; sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.update(API_ID="1", API_HASH="x", BOT_TOKEN="1:x", SQLITE_PATH="/tmp/trel.sqlite3", GEMINI_API_KEY="")
if os.path.exists("/tmp/trel.sqlite3"): os.remove("/tmp/trel.sqlite3")
import asyncio, unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as N
from unittest.mock import AsyncMock, patch
from telethon.errors import FloodWaitError
import bot, db

OLD = datetime.now(timezone.utc) - timedelta(minutes=1)


def msg(i, text="", photo=False, grouped=None, size=100):
    f = N(size=size, name=None, ext=".jpg") if photo else None
    return N(id=i, raw_text=text, voice=None, audio=None, photo=photo, video=None, web_preview=None,
             media=object() if photo else None, file=f, grouped_id=grouped, date=OLD,
             peer_id=N(channel_id=500))


class Reliability(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        bot._seen.clear(); bot._last_id.clear(); bot._queue = None
        db.add_channel(500, "Chan", None)
        db.kw_add(bot.KW, "breaking")
        bot.NOTIFY_CHAT = "1"

    async def test_same_post_from_event_and_poller_is_queued_once(self):
        self.assertTrue(bot.enqueue(500, [msg(1, "breaking")]))
        self.assertFalse(bot.enqueue(500, [msg(1, "breaking")], "poll"))
        self.assertEqual(bot.get_queue().qsize(), 1)

    async def test_burst_of_simultaneous_posts_all_processed(self):
        handled = []
        async def fake(ch, msgs): handled.append(msgs[0].id)
        for i in range(1, 51):
            bot.enqueue(500, [msg(i, "breaking")])
        with patch.object(bot, "process_post", fake):
            workers = [asyncio.create_task(bot.alert_worker(n)) for n in range(3)]
            await asyncio.wait_for(bot.get_queue().join(), 5)
            for w in workers: w.cancel()
        self.assertEqual(sorted(handled), list(range(1, 51)))

    async def test_one_failing_post_does_not_stop_the_worker(self):
        handled = []
        async def fake(ch, msgs):
            if msgs[0].id == 1: raise RuntimeError("boom")
            handled.append(msgs[0].id)
        bot.enqueue(500, [msg(1, "breaking")]); bot.enqueue(500, [msg(2, "breaking")])
        with patch.object(bot, "process_post", fake):
            w = asyncio.create_task(bot.alert_worker(0))
            await asyncio.wait_for(bot.get_queue().join(), 5); w.cancel()
        self.assertEqual(handled, [2])

    async def test_album_forwarded_as_one_grouped_post_with_caption(self):
        album = [msg(10, "breaking news", photo=True, grouped=7), msg(11, photo=True, grouped=7),
                 msg(12, photo=True, grouped=7)]
        send = AsyncMock(return_value=[N(id=1)])
        with patch.object(bot.user, "download_media", AsyncMock(return_value=b"img")), \
             patch.object(bot.bot, "upload_file", AsyncMock(side_effect=lambda d, **k: k["file_name"])), \
             patch.object(bot.bot, "send_file", send):
            await bot.process_post(db.channel(500), album)
        send.assert_awaited_once()
        files, caps = send.await_args.args[1], send.await_args.kwargs["caption"]
        self.assertEqual(len(files), 3)
        self.assertIn("breaking news", caps[0])
        self.assertEqual(caps[1:], ["", ""])

    async def test_poller_recovers_missed_posts(self):
        bot._last_id[500] = 20
        missed = [msg(22, "breaking"), msg(21, "breaking")]
        with patch.object(bot.user, "get_messages", AsyncMock(return_value=missed)), \
             patch.object(bot.asyncio, "sleep", AsyncMock()):
            await bot.poll_once()
        q = bot.get_queue()
        ids = [q.get_nowait()[1][0].id for _ in range(q.qsize())]
        self.assertEqual(ids, [21, 22])

    async def test_floodwait_is_waited_out_and_retried(self):
        fn = AsyncMock(side_effect=[FloodWaitError(request=None, capture=3), "ok"])
        with patch.object(bot.asyncio, "sleep", AsyncMock()) as sl:
            self.assertEqual(await bot.tg_retry(fn), "ok")
        sl.assert_awaited_with(4)

    async def test_text_priority_needs_priority_channel(self):
        db.kw_add(bot.PKW, "fire")
        db.set_setting("call_target", "9")
        spawned = []
        with patch.object(bot, "spawn", lambda c: (spawned.append(1), c.close())), \
             patch.object(bot, "send_alert", AsyncMock()):
            await bot.process_post(dict(db.channel(500), priority=0), [msg(30, "fire breaking")])
            self.assertEqual(spawned, [])
            await bot.process_post(dict(db.channel(500), priority=1), [msg(31, "fire")])
            self.assertEqual(spawned, [1])


if __name__ == "__main__":
    unittest.main()
