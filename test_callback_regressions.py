"""Offline regression tests using a read-only callback payload, like Telethon."""
import os
import tempfile
import unittest
from unittest.mock import AsyncMock, patch
from types import SimpleNamespace

_tmp = tempfile.TemporaryDirectory()
os.environ.update(API_ID="1", API_HASH="x", BOT_TOKEN="1:x", OWNER_ID="42",
                  SQLITE_PATH=os.path.join(_tmp.name, "tests.sqlite3"), DATABASE_URL="",
                  GEMINI_API_KEY="", USER_SESSION="")
import bot
import db


class ReadOnlyEvent:
    sender_id = 42
    chat_id = 42

    def __init__(self, data):
        self._data = data
        self.answer = AsyncMock()
        self.edit = AsyncMock()
        self.respond = AsyncMock()

    @property
    def data(self):
        return self._data


class CallbackTests(unittest.IsolatedAsyncioTestCase):
    async def test_priority_toggle_refreshes_without_setter(self):
        db.add_channel(101, "Test", None)
        event = ReadOnlyEvent(b"prt:101")
        await bot.on_cb(event)
        self.assertEqual(db.channel(101)["priority"], 1)
        self.assertEqual(event.data, b"prt:101")
        event.edit.assert_awaited_once()
        await bot.on_cb(event)
        self.assertEqual(db.channel(101)["priority"], 0)

    async def test_refresh_and_reset_preserve_keywords_and_channels(self):
        db.add_channel(102, "Keep", None)
        db.kw_add(bot.KW, "keep")
        db.hit_add(102, "Keep", ["keep"], [], "voice")
        refresh = ReadOnlyEvent(b"sum_r")
        await bot.on_cb(refresh)
        self.assertGreater(db.hit_totals()["total"], 0)
        refresh.edit.assert_awaited_once()
        reset = ReadOnlyEvent(b"sum_reset_ok")
        await bot.on_cb(reset)
        self.assertEqual(db.hit_totals()["total"], 0)
        self.assertIsNotNone(db.channel(102))
        self.assertTrue(any(r["word"] == "keep" for r in db.kw_list(bot.KW)))
        reset.edit.assert_awaited_once()

    async def test_normal_and_priority_keyword_delete_refresh(self):
        for prefix, table in (("kw", bot.KW), ("pk", bot.PKW)):
            with self.subTest(table=table):
                db.kw_add(table, "remove")
                row = next(r for r in db.kw_list(table) if r["word"] == "remove")
                event = ReadOnlyEvent(f"{prefix}d:{row['id']}".encode())
                await bot.on_cb(event)
                self.assertIsNone(db.kw_get(table, row["id"]))
                event.edit.assert_awaited_once()

    async def test_channel_delete_refresh(self):
        db.add_channel(103, "Delete", None)
        event = ReadOnlyEvent(b"chd:103")
        with patch.object(bot.user, "get_input_entity", AsyncMock(side_effect=RuntimeError("offline"))):
            await bot.on_cb(event)
        self.assertIsNone(db.channel(103))
        event.edit.assert_awaited_once()

    async def test_non_owner_cannot_toggle(self):
        db.add_channel(104, "Private", None)
        event = ReadOnlyEvent(b"prt:104")
        event.sender_id = 99
        await bot.on_cb(event)
        self.assertEqual(db.channel(104)["priority"], 0)
        event.edit.assert_not_awaited()

    async def test_voice_transcription_through_alert_and_summary(self):
        db.hits_reset()
        db.kw_add(bot.KW, "captionword")
        db.kw_add(bot.PKW, "spokenword")
        event = SimpleNamespace(message=object(), id=55, voice=True,
                                file=SimpleNamespace(duration=180, size=5,
                                                     mime_type="audio/ogg", name="voice.ogg", ext=".ogg"))
        channel = {"id": 105, "title": "Voice test", "username": None, "priority": 0}
        create = AsyncMock(return_value=SimpleNamespace(text="spokenword"))
        client = SimpleNamespace(audio=SimpleNamespace(transcriptions=SimpleNamespace(create=create)))
        send = AsyncMock()
        with patch.object(bot.transcribe, "AVAILABLE", True), \
             patch.object(bot.transcribe, "MODEL", "whisper-large-v3-turbo"), \
             patch.object(bot.transcribe, "_get_client", return_value=client), \
             patch.object(bot.user, "download_media", AsyncMock(return_value=b"audio")), \
             patch.object(bot.bot, "upload_file", AsyncMock(return_value="uploaded")), \
             patch.object(bot.bot, "send_file", send):
            await bot.on_voice_post(event, channel, "captionword")
        self.assertEqual(create.await_args.kwargs["model"], "whisper-large-v3-turbo")
        self.assertEqual(create.await_args.kwargs["file"], ("voice.ogg", b"audio"))
        send.assert_awaited_once()
        self.assertTrue(send.await_args.kwargs["voice_note"])
        self.assertIn("spokenword", send.await_args.kwargs["caption"])
        self.assertIn("captionword", send.await_args.kwargs["caption"])
        self.assertEqual(db.hit_totals(), {"total": 2, "prio": 0, "voice": 2})
        self.assertEqual({r["word"] for r in db.top_voice_keywords()}, {"spokenword", "captionword"})


if __name__ == "__main__":
    unittest.main()
