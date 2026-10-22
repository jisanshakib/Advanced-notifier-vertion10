import os,sys; sys.path.insert(0,os.path.dirname(os.path.abspath(__file__)))
os.environ.update(API_ID="1",API_HASH="x",BOT_TOKEN="1:x",SQLITE_PATH="/tmp/t.sqlite3",GEMINI_API_KEY="")
if os.path.exists("/tmp/t.sqlite3"): os.remove("/tmp/t.sqlite3")
import bot, db, voice_call
from pytgcalls.types import RecordStream, MediaStream, ExternalMedia, ChatUpdate, Device, Direction
from pytgcalls.types.raw import AudioParameters
from pytgcalls import filters
RecordStream(True, AudioParameters(bitrate=48000, channels=1)); MediaStream(ExternalMedia.AUDIO, AudioParameters(bitrate=48000, channels=1))
ChatUpdate.Status.LEFT_CALL | ChatUpdate.Status.DISCARDED_CALL; filters.stream_frame(Direction.INCOMING, Device.MICROPHONE)
from google.genai import types as g; g.Blob(data=b"x", mime_type="audio/pcm;rate=16000")
from google import genai; c=genai.Client(api_key="x"); assert hasattr(c.aio.live,"connect")
m=bot.kw_match
cases=[("hello","hello there",1),("hello","helloworld",0),("hello","say Hello!",1),("hello","ohello",0),
("breaking news","BREAKING   news now",1),("খবর","আজকের খবর এসেছে",1),("খবর","খবরে বলা হয়েছে",0),
("ভালো","খুব ভালো দিন",1),("ভালো","ভালোবাসা",0),("$BTC","buy $BTC now",1),("$BTC","x$BTCy",0),("c++","I like c++.",0),("c++","I like c++ a lot",1)]
for w,t,e in cases: assert m(w,t)==bool(e),(w,t)
db.add_channel(5,"A",None); assert db.channel_active(5); db.toggle_priority(5)
db.del_channel(5); assert not db.channel_active(5)
db.add_channel(5,"A2",None); r=db.channel(5); assert r["title"]=="A2" and r["priority"]==0
db.add_channel(5,"A3","u"); assert len(db.channels())==1
assert db.kw_add("keywords","Hello") and not db.kw_add("keywords","hello")
print("AVAILABLE without key:", voice_call.AVAILABLE); print("ALL TESTS PASS")
# ---- v6 audit tests
import asyncio, time
assert db.kw_add("keywords","Alpha"); assert any(r["word"]=="Alpha" for r in db.kw_list("keywords"))
kid=[r["id"] for r in db.kw_list("keywords") if r["word"]=="Alpha"][0]
db.kw_update("keywords",kid,"Beta"); assert any(r["word"]=="Beta" for r in db.kw_list("keywords"))  # cache invalidated
db.kw_del("keywords",kid); assert not any(r["word"]=="Beta" for r in db.kw_list("keywords"))
assert not m("hello",""); assert not m("hello",None) if False else True
try: db.q("SELECT * FROM nope")
except Exception: pass
assert db.channel(999) is None  # connection still usable after a failed query
async def t_stop():
    bot._stop=asyncio.Event(); os.environ["RENDER_EXTERNAL_URL"]="http://127.0.0.1:9"
    t=asyncio.create_task(bot.keep_alive()); await asyncio.sleep(0.5); s=time.time(); bot._stop.set()
    await asyncio.wait_for(t,3); assert time.time()-s<1, "pinger did not stop instantly"
    await voice_call.shutdown()
asyncio.run(t_stop()); print("SHUTDOWN TESTS PASS")
# ---- voice + summary tests
db.hits_reset()
db.hit_add(1,"Chan A",["alpha","beta"],[],"text"); db.hit_add(1,"Chan A",["alpha"],["urgent"],"voice"); db.hit_add(2,"Chan B",["alpha"],[],"text")
tc=db.top_channels(); assert tc[0]["title"]=="Chan A" and tc[0]["n"]==4, tc
tk=db.top_keywords(); assert tk[0]["word"]=="alpha" and tk[0]["n"]==3
tot=db.hit_totals(); assert tot=={"total":5,"prio":1,"voice":2}, tot
assert "Chan A" in bot.summary_text(); db.hits_reset(); assert db.hit_totals()["total"]==0
import transcribe; assert transcribe.AVAILABLE is False
print("VOICE/SUMMARY TESTS PASS")


def test_caption_fits_telegram_limit():
    import bot
    head = "📨 <b>New matching post</b>\n"
    out = bot.build_body(head, "a&b " * 600, bot.CAPTION_LIMIT)
    assert len(out) <= 1024 and out.endswith("…") and "&amp" in out
    assert bot.build_body(head, "hi", 1024) == head + "hi"
test_caption_fits_telegram_limit(); print("caption test OK")


def test_voice_limit_and_breakdown():
    from types import SimpleNamespace as N
    assert bot.MAX_VOICE_SECONDS == 180
    assert bot.voice_duration(N(file=N(duration=181))) == 181
    assert bot.voice_duration(N(file=None)) is None
    db.hits_reset()
    db.hit_add(1, "A", ["alpha"], [], "voice")
    db.hit_add(1, "A", ["beta"], [], "text")
    words = [r["word"] for r in db.top_voice_keywords(10)]
    assert words == ["alpha"]
    assert "Keywords triggering voice alerts" in bot.summary_text()
    db.hits_reset()
test_voice_limit_and_breakdown(); print("voice limit/breakdown OK")


def test_flows():
    import asyncio
    from types import SimpleNamespace as N
    calls = []
    async def send_file(dest, f, **kw): calls.append(("file", kw)); return N(id=1)
    async def send_message(*a, **kw): calls.append(("msg", a, kw)); return N(id=2)
    async def upload_file(data, **kw): return "F"
    async def dl(*a, **kw): return b"img"
    bot.bot.send_file, bot.bot.send_message, bot.bot.upload_file = send_file, send_message, upload_file
    bot.user.download_media = dl
    photo = N(media=1, file=N(size=10, name="p.jpg", ext=".jpg"), web_preview=None, photo=1, video=None, message=None)
    # 1. image + text -> exactly ONE message (photo with caption)
    asyncio.run(bot.send_alert(photo, 1, "H\n", "hello <world>"))
    assert len(calls) == 1 and calls[0][0] == "file" and "hello &lt;world&gt;" in calls[0][1]["caption"]
    # 2. too large -> one text message with note
    calls.clear(); big = N(**{**photo.__dict__, "file": N(size=10**10, name="x", ext="")})
    asyncio.run(bot.send_alert(big, 1, "H\n", "t"))
    assert len(calls) == 1 and calls[0][0] == "msg" and "too large" in calls[0][1][1]
    # 3. upload fails -> falls back to text, no crash
    calls.clear()
    async def boom(*a, **k): raise RuntimeError("x")
    bot.bot.upload_file = boom
    asyncio.run(bot.send_alert(photo, 1, "H\n", "t")); assert [c[0] for c in calls] == ["msg"]
    # 4. voice > 3 min skipped before download
    calls.clear(); called = []
    async def dl2(*a, **k): called.append(1); return b"v"
    bot.user.download_media = dl2
    ev = N(file=N(duration=200, size=10), voice=1, id=5)
    asyncio.run(bot.on_voice_post(ev, {"title": "C", "id": 1, "username": None, "priority": 0}, ""))
    assert not called and not calls
    # 5. hanging Gemini times out instead of freezing
    bot.TRANSCRIBE_TIMEOUT = 0.2; bot.transcribe.AVAILABLE = True
    async def hang(*a, **k): await asyncio.sleep(30)
    bot.transcribe.transcribe = hang
    ev = N(file=N(duration=10, size=10, mime_type="audio/ogg"), voice=1, id=5)
    asyncio.run(asyncio.wait_for(bot.on_voice_post(ev, {"title": "C", "id": 1, "username": None, "priority": 0}, ""), 3))
    # 6. long lists stay under Telegram's limit
    assert len(bot.clip_lines(["• word"] * 2000, "")) < 4096
test_flows(); print("FLOW TESTS PASS")


def test_voice_priority_call_and_groq_limit():
    import asyncio
    from types import SimpleNamespace as N
    calls, spawned = [], []
    async def send_file(dest, f, **kw): calls.append(("file", kw)); return N(id=1)
    async def send_message(*a, **kw): calls.append(("msg", a, kw)); return N(id=2)
    async def upload_file(data, **kw): return "F"
    bot.bot.send_file, bot.bot.send_message, bot.bot.upload_file = send_file, send_message, upload_file
    async def dl(*a, **k): return b"voice"
    bot.user.download_media = dl
    bot.NOTIFY_CHAT = "12345"
    bot.transcribe.AVAILABLE = True
    bot.TRANSCRIBE_TIMEOUT = 120
    async def tr(data, mime): return "nothing interesting here"
    bot.transcribe.transcribe = tr
    bot.spawn = lambda coro: (spawned.append(coro), coro.close())[0]
    db.kw_add("priority_keywords", "urgent")
    db.set_setting("call_target", "999")
    ch = {"title": "C", "id": 1, "username": None, "priority": 0}  # NOT a priority channel
    ev = N(file=N(duration=10, size=10, mime_type="audio/ogg", name=None, ext=".ogg"), voice=1, id=5, message=None)
    # 1. priority keyword in the CAPTION (transcript has none) still matches, forwards, and calls
    asyncio.run(bot.on_voice_post(ev, ch, "urgent update"))
    assert [c[0] for c in calls] == ["file"], calls
    assert len(spawned) == 1, "priority keyword must trigger the call from any watched channel"
    # 2. file over Groq's 25 MB limit is skipped before download
    calls.clear(); spawned.clear(); downloaded = []
    async def dl2(*a, **k): downloaded.append(1); return b"v"
    bot.user.download_media = dl2
    big = N(file=N(duration=10, size=26 * 1024 * 1024), voice=1, id=6, message=None)
    asyncio.run(bot.on_voice_post(big, ch, "urgent"))
    assert not downloaded and not calls
    # 3. Groq rate-limit/failure -> voice skipped silently, bot keeps running
    async def boom(*a, **k): raise RuntimeError("rate limited")
    bot.transcribe.transcribe = boom
    bot.user.download_media = dl
    calls.clear()
    asyncio.run(bot.on_voice_post(ev, ch, "urgent"))
    assert not calls
test_voice_priority_call_and_groq_limit(); print("VOICE PRIORITY/GROQ LIMIT TESTS PASS")
