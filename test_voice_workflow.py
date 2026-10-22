import os, sys; sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.update(API_ID="1", API_HASH="x", BOT_TOKEN="1:x", SQLITE_PATH="/tmp/tv.sqlite3", GEMINI_API_KEY="")
if os.path.exists("/tmp/tv.sqlite3"): os.remove("/tmp/tv.sqlite3")
import asyncio
from types import SimpleNamespace as N
import bot, db

calls, spawned, downloaded = [], [], []
async def send_file(dest, f, **kw): calls.append(("file", kw)); return N(id=1)
async def send_message(*a, **kw): calls.append(("msg", a, kw)); return N(id=2)
async def upload_file(data, **kw): return "F"
bot.bot.send_file, bot.bot.send_message, bot.bot.upload_file = send_file, send_message, upload_file
async def dl(*a, **k): downloaded.append(1); return b"voice"
bot.user.download_media = dl
bot.NOTIFY_CHAT = "12345"
bot.transcribe.AVAILABLE = True
bot.TRANSCRIBE_TIMEOUT = 120
async def tr(data, mime): return "nothing interesting here"
bot.transcribe.transcribe = tr
bot.spawn = lambda coro: (spawned.append(coro), coro.close())[0]
db.kw_add("priority_keywords", "urgent")
db.kw_add("keywords", "alpha")
db.set_setting("call_target", "999")
ch = {"title": "C", "id": 1, "username": None, "priority": 0}  # NOT a priority channel
ev = N(file=N(duration=10, size=10, mime_type="audio/ogg", name=None, ext=".ogg"), voice=1, id=5, message=None)

# 1. priority keyword in the CAPTION (transcript has none) -> forwarded + call, any channel
asyncio.run(bot.on_voice_post(ev, ch, "urgent update"))
assert [c[0] for c in calls] == ["file"], calls
assert len(spawned) == 1, "priority keyword must trigger the call from any watched channel"

# 2. normal keyword in the TRANSCRIPT (caption empty) -> forwarded, NO call
calls.clear(); spawned.clear()
asyncio.run(bot.on_voice_post(ev, ch, "")) if False else None
async def tr2(data, mime): return "alpha bravo"
bot.transcribe.transcribe = tr2
asyncio.run(bot.on_voice_post(ev, ch, ""))
assert [c[0] for c in calls] == ["file"] and not spawned, (calls, spawned)

# 3. no keyword anywhere -> silently skipped
calls.clear(); spawned.clear()
async def tr3(data, mime): return "nothing interesting here"
bot.transcribe.transcribe = tr3
asyncio.run(bot.on_voice_post(ev, ch, ""))
assert not calls and not spawned

# 4. file over Groq's 25 MB limit -> skipped before download
downloaded.clear(); calls.clear()
big = N(file=N(duration=10, size=26 * 1024 * 1024), voice=1, id=6, message=None)
asyncio.run(bot.on_voice_post(big, ch, "urgent"))
assert not downloaded and not calls

# 5. over 3 minutes -> skipped before download
downloaded.clear()
long_ev = N(file=N(duration=200, size=10), voice=1, id=7, message=None)
asyncio.run(bot.on_voice_post(long_ev, ch, "urgent"))
assert not downloaded

# 6. Groq rate-limit/failure -> skipped silently, no crash
async def boom(*a, **k): raise RuntimeError("rate limited")
bot.transcribe.transcribe = boom
calls.clear()
asyncio.run(bot.on_voice_post(ev, ch, "urgent"))
assert not calls
print("VOICE WORKFLOW TESTS PASS")
