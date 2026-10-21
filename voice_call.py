"""Gemini Live voice calls over a real Telegram private call.

Flow for a PRIORITY alert:
  * Call #1 rings the target (same double-call rules as caller.py):
      - declined / hung up while ringing -> STOP
      - ignored (no answer in RING_SECONDS) -> place call #2
      - answered -> Gemini Live starts talking
  * On answer, Gemini speaks first: greeting "আসসালামু আলাইকুম জিসান ভাই", then explains
    the core topic + summary of the post, then keeps a two-way live audio stream open so
    the user can ask follow-up questions in real time.

Audio path:  Telegram call (48 kHz PCM16 mono, via py-tgcalls)  <->  Gemini Live
             (16 kHz PCM16 in / 24 kHz PCM16 out, via google-genai).

If GEMINI_API_KEY or py-tgcalls is unavailable, bot.py falls back to caller.py (ring only).
"""
import asyncio
import logging
import os

import numpy as np

log = logging.getLogger("voice_call")

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
GEMINI_LIVE_MODEL = os.getenv("GEMINI_LIVE_MODEL", "gemini-2.5-flash-native-audio-preview-09-2025").strip()
GEMINI_VOICE = os.getenv("GEMINI_VOICE", "Puck").strip()
RING_SECONDS = 45
SILENCE_TIMEOUT = 40  # no audio frames from Telegram for this long = network dropped -> end call
try:
    MAX_CALL_SECONDS = int(os.getenv("MAX_CALL_SECONDS", "900") or 900)  # 15 minutes
except ValueError:
    MAX_CALL_SECONDS = 900
GREETING = "আসসালামু আলাইকুম জিসান ভাই"

TG_RATE = 48000
FRAME_MS = 10
FRAME_BYTES = TG_RATE * FRAME_MS // 1000 * 2  # 960 bytes = 10 ms mono PCM16

try:
    from google import genai
    from google.genai import types as gtypes
    from pytgcalls import PyTgCalls, filters
    from pytgcalls.types import (ChatUpdate, Device, Direction, ExternalMedia,
                                 MediaStream, RecordStream, StreamFrames)
    from pytgcalls.types.raw import AudioParameters
    AVAILABLE = bool(GEMINI_API_KEY)
    if not GEMINI_API_KEY:
        log.warning("GEMINI_API_KEY not set – priority calls will ring only (no AI voice).")
except Exception as e:  # library missing / incompatible
    AVAILABLE = False
    log.warning("Gemini live calls disabled (%s) – priority calls will ring only.", e)

_calls = None            # PyTgCalls instance
_active: dict[int, "LiveCall"] = {}
_lock = asyncio.Lock()   # never two calls at once


# ------------------------------------------------------------------ audio helpers
def _resample(pcm: bytes, src: int, dst: int) -> bytes:
    if src == dst or not pcm:
        return pcm
    a = np.frombuffer(pcm, dtype=np.int16).astype(np.float32)
    n = int(len(a) * dst / src)
    if n <= 0:
        return b""
    out = np.interp(np.linspace(0, len(a) - 1, n), np.arange(len(a)), a)
    return np.clip(out, -32768, 32767).astype(np.int16).tobytes()


def _system_prompt(channel: str, post: str) -> str:
    return (
        "You are a friendly personal news assistant speaking on a live phone call with Zisan (জিসান ভাই). "
        "Speak naturally in Bangla (Bengali) unless he speaks another language, then follow his language. "
        "Keep answers short and conversational, like a phone call. "
        "Only use facts from the post below; if something isn't in it, say you don't know.\n\n"
        f"PRIORITY POST from channel “{channel}”:\n\"\"\"\n{post[:6000]}\n\"\"\""
    )


OPENING = (
    f"The call has just been answered. Speak first, right now. Start exactly with the greeting "
    f"“{GREETING}”, then in a few sentences explain the core topic and a clear summary of the "
    "priority post. Finish by asking if he has any questions about it."
)


# ------------------------------------------------------------------ one live session
class LiveCall:
    def __init__(self, chat_id: int, channel: str, post: str):
        self.chat_id = chat_id
        self.channel = channel
        self.post = post
        self.out_q: asyncio.Queue[bytes] = asyncio.Queue()
        self.in_q: asyncio.Queue[bytes] = asyncio.Queue(maxsize=500)
        self.ended = asyncio.Event()
        self.answered = asyncio.Event()
        self.last_frame = None  # loop time of last incoming audio frame

    async def run(self):
        client = genai.Client(api_key=GEMINI_API_KEY)
        try:
            await self._run(client)
        finally:
            aclose = getattr(getattr(client, "aio", None), "aclose", None)
            if aclose:  # release the HTTP session (newer google-genai)
                try:
                    await aclose()
                except Exception:
                    pass

    async def _run(self, client):
        config = {
            "response_modalities": ["AUDIO"],
            "system_instruction": _system_prompt(self.channel, self.post),
            "speech_config": {"voice_config": {"prebuilt_voice_config": {"voice_name": GEMINI_VOICE}}},
        }
        async with client.aio.live.connect(model=GEMINI_LIVE_MODEL, config=config) as session:
            # Gemini speaks first
            await session.send_client_content(
                turns=gtypes.Content(role="user", parts=[gtypes.Part(text=OPENING)]), turn_complete=True)
            tasks = [asyncio.create_task(t) for t in (
                self._gemini_to_tg(session), self._tg_to_gemini(session), self._play_out(),
                self._watchdog())]
            waiter = asyncio.create_task(self.ended.wait())
            try:
                # stop on: hang-up, 15-min limit, or any audio task crashing (e.g. Gemini closed)
                done, _ = await asyncio.wait(tasks + [waiter], timeout=MAX_CALL_SECONDS,
                                             return_when=asyncio.FIRST_COMPLETED)
                if not done:
                    log.info("max call length (%ss) reached", MAX_CALL_SECONDS)
                for t in done:
                    if t is not waiter and not t.cancelled() and t.exception():
                        log.warning("live audio task stopped: %s", t.exception())
            finally:
                self.ended.set()
                for t in tasks + [waiter]:
                    t.cancel()
                await asyncio.gather(*tasks, waiter, return_exceptions=True)

    async def _watchdog(self):
        """Ends the call if Telegram audio stops arriving (network loss without a hang-up event)."""
        loop = asyncio.get_running_loop()
        start = loop.time()
        while not self.ended.is_set():
            await asyncio.sleep(2)
            last = self.last_frame or start
            if loop.time() - last > SILENCE_TIMEOUT:
                log.info("no call audio for %ss – assuming disconnect", SILENCE_TIMEOUT)
                return

    async def _gemini_to_tg(self, session):
        buf = b""
        while not self.ended.is_set():
            got = False
            async for msg in session.receive():
                got = True
                sc = getattr(msg, "server_content", None)
                if sc and getattr(sc, "interrupted", False):  # user talked over Gemini
                    while not self.out_q.empty():
                        self.out_q.get_nowait()
                    buf = b""
                data = getattr(msg, "data", None)
                if data:
                    buf += _resample(data, 24000, TG_RATE)
                    while len(buf) >= FRAME_BYTES:
                        await self.out_q.put(buf[:FRAME_BYTES])
                        buf = buf[FRAME_BYTES:]
            if not got:  # stream closed by Gemini -> stop instead of busy-looping
                return

    async def _tg_to_gemini(self, session):
        while not self.ended.is_set():
            chunk = await self.in_q.get()
            pcm16k = _resample(chunk, TG_RATE, 16000)
            if pcm16k:
                await session.send_realtime_input(
                    audio=gtypes.Blob(data=pcm16k, mime_type="audio/pcm;rate=16000"))

    async def _play_out(self):
        silence = b"\x00" * FRAME_BYTES
        loop = asyncio.get_running_loop()
        nxt = loop.time()
        while not self.ended.is_set():
            try:
                frame = self.out_q.get_nowait()
            except asyncio.QueueEmpty:
                frame = silence
            try:
                await _calls.send_frame(self.chat_id, Device.MICROPHONE, frame)
            except Exception as e:
                log.debug("send_frame: %s", e)
            nxt += FRAME_MS / 1000
            now = loop.time()
            if nxt < now - 0.2:  # fell behind (CPU hiccup): resync instead of bursting audio
                nxt = now
            await asyncio.sleep(max(0, nxt - now))


# ------------------------------------------------------------------ setup
async def install(user_client):
    """Start py-tgcalls on the userbot. Call after the userbot is connected."""
    global _calls, AVAILABLE
    if not AVAILABLE:
        return
    try:
        _calls = PyTgCalls(user_client)

        @_calls.on_update(filters.stream_frame(Direction.INCOMING, Device.MICROPHONE))
        async def _incoming(_, update: StreamFrames):
            lc = _active.get(update.chat_id)
            if not lc or lc.ended.is_set():
                return
            lc.last_frame = asyncio.get_running_loop().time()
            for f in update.frames:
                try:
                    lc.in_q.put_nowait(f.frame)
                except asyncio.QueueFull:
                    pass

        @_calls.on_update(filters.chat_update(ChatUpdate.Status.LEFT_CALL | ChatUpdate.Status.DISCARDED_CALL))
        async def _ended(_, update):
            lc = _active.get(update.chat_id)
            if lc:
                lc.ended.set()

        await _calls.start()
        log.info("Gemini live calls ready (model %s).", GEMINI_LIVE_MODEL)
    except Exception as e:
        AVAILABLE = False
        log.exception("py-tgcalls failed to start, falling back to ring-only calls: %s", e)


async def shutdown():
    """Called on bot exit: end live sessions and hang up so no call/websocket is left open."""
    for lc in list(_active.values()):
        lc.ended.set()
    for cid in list(_active):
        await _hangup(cid)


async def _hangup(chat_id):
    if _calls is None:
        return
    try:
        await asyncio.wait_for(_calls.leave_call(chat_id), 10)
    except Exception:
        pass


async def _one_call(chat_id: int, channel: str, post: str) -> str:
    """Rings once. Returns 'answered' | 'hung_up' | 'ignored'."""
    stream = MediaStream(ExternalMedia.AUDIO, AudioParameters(bitrate=TG_RATE, channels=1))
    try:
        await asyncio.wait_for(_calls.play(chat_id, stream), RING_SECONDS)
    except asyncio.TimeoutError:
        await _hangup(chat_id)
        return "ignored"
    except Exception as e:
        name = type(e).__name__.lower()
        log.info("call not connected: %s (%s)", name, e)
        await _hangup(chat_id)
        return "hung_up" if ("declin" in name or "discard" in name or "busy" in name) else "ignored"

    lc = LiveCall(chat_id, channel, post)
    _active[chat_id] = lc
    try:
        # mono 48 kHz so incoming frames match _resample (AudioQuality.HIGH is stereo)
        await _calls.record(chat_id, RecordStream(True, AudioParameters(bitrate=TG_RATE, channels=1)))
    except Exception as e:
        log.warning("record (incoming audio) failed: %s", e)
    try:
        await lc.run()
    except Exception as e:
        log.exception("gemini live session error: %s", e)
    finally:
        _active.pop(chat_id, None)
        await _hangup(chat_id)
    return "answered"


async def ai_alert_call(user_client, target_ref: str, channel: str, post: str) -> str:
    """Double-call with Gemini Live on answer. Returns a human-readable summary."""
    async with _lock:
        ent = await user_client.get_entity(int(target_ref) if target_ref.lstrip("-").isdigit() else target_ref)
        chat_id = ent.id
        r1 = await _one_call(chat_id, channel, post)
        if r1 == "answered":
            return "Call 1: answered → Gemini explained the post and took your questions."
        if r1 == "hung_up":
            return "Call 1: hung up → stopped."
        await asyncio.sleep(3)
        r2 = await _one_call(chat_id, channel, post)
        if r2 == "answered":
            return "Call 1: ignored → Call 2: answered → Gemini explained the post."
        return f"Call 1: ignored → Call 2: {r2.replace('_', ' ')}."
