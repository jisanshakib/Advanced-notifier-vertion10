import asyncio
import html
import logging
import os
import re
import signal
from collections import OrderedDict
from datetime import datetime, timedelta, timezone
from functools import lru_cache

from dotenv import load_dotenv

# Load .env before importing modules that read their settings at import time
# (db.py, transcribe.py and voice_call.py). Otherwise GROQ_API_KEY/GEMINI_API_KEY/
# DATABASE_URL set only in .env are invisible and voice stays disabled.
load_dotenv()

from telethon import Button, TelegramClient, events, types as tltypes
from telethon.errors import (FloodWaitError, MessageNotModifiedError, RPCError, ServerError,
                             UserAlreadyParticipantError)
from telethon.sessions import StringSession
from telethon.tl.functions.channels import JoinChannelRequest, LeaveChannelRequest
from telethon.tl.functions.messages import CheckChatInviteRequest, ImportChatInviteRequest

import caller
import db
import transcribe
import matching
import voice_call
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s: %(message)s")
log = logging.getLogger("bot")

API_ID = int(os.environ["API_ID"])
API_HASH = os.environ["API_HASH"]
BOT_TOKEN = os.environ["BOT_TOKEN"]
ENV_OWNER = os.getenv("OWNER_ID", "").strip()
NOTIFY_CHAT = os.getenv("NOTIFY_CHAT_ID", "").strip()

# Cloud hosts have no console and wipe local files, so the userbot logs in from a
# session string (create it once with: python login_session.py). Falls back to a local file.
_USER_SESSION = os.getenv("USER_SESSION", "").strip()
# flood_sleep_threshold: Telethon auto-waits FloodWaits up to 2 min instead of raising
# connection_retries=-1 + auto_reconnect: never give up on a dropped socket / DC migration.
# catch_up=True: after a reconnect Telethon fetches the updates missed while offline.
_CLIENT_OPTS = dict(flood_sleep_threshold=120, connection_retries=-1, retry_delay=3,
                    auto_reconnect=True, request_retries=5)
bot = TelegramClient(StringSession(), API_ID, API_HASH, **_CLIENT_OPTS)
user = TelegramClient(StringSession(_USER_SESSION) if _USER_SESSION else "user_session", API_ID, API_HASH,
                      catch_up=True, **_CLIENT_OPTS)

MAX_MEDIA_BYTES = int(os.getenv("MAX_MEDIA_MB", "40") or 40) * 1024 * 1024  # Render free = 512 MB RAM
_bg_tasks: set[asyncio.Task] = set()


def spawn(coro):
    """create_task that keeps a strong reference (un-referenced tasks can be garbage-collected
    mid-run) and logs any crash instead of 'Task exception was never retrieved'."""
    t = asyncio.create_task(coro)
    _bg_tasks.add(t)

    def _done(task):
        _bg_tasks.discard(task)
        if not task.cancelled() and task.exception():
            log.error("background task failed: %r", task.exception())
    t.add_done_callback(_done)
    return t


TG_ATTEMPTS = 4
MAX_FLOOD_WAIT = 600


async def tg_retry(fn, *args, **kw):
    """Call a Telegram API coroutine, waiting out FloodWaits and retrying transient
    network/server errors with backoff. Permanent errors are raised immediately."""
    for attempt in range(1, TG_ATTEMPTS + 1):
        try:
            return await fn(*args, **kw)
        except FloodWaitError as e:
            if attempt == TG_ATTEMPTS or e.seconds > MAX_FLOOD_WAIT:
                raise
            log.warning("FloodWait %ss on %s, waiting", e.seconds, getattr(fn, "__name__", fn))
            await asyncio.sleep(e.seconds + 1)
        except (ServerError, ConnectionError, OSError, asyncio.TimeoutError) as e:
            if attempt == TG_ATTEMPTS:
                raise
            log.warning("transient error on %s (%r), retry %s", getattr(fn, "__name__", fn), e, attempt)
            await asyncio.sleep(min(2 ** attempt, 15))


async def safe_send(*args, **kw):
    """bot.send_message that never raises (retries FloodWait/transient errors first)."""
    try:
        return await tg_retry(bot.send_message, *args, **kw)
    except Exception as e:
        log.warning("send_message failed: %s", e)
        return None

state: dict[int, tuple] = {}  # chat_id -> (action, extra)

KW, PKW = "keywords", "priority_keywords"


# ------------------------------------------------------------------ helpers
def owner_id():
    return int(ENV_OWNER or db.get_setting("owner_id", 0) or 0)


def notify_chat():
    return int(NOTIFY_CHAT) if NOTIFY_CHAT else owner_id()


def back(to="main"):
    return [Button.inline("⬅️ Back", to.encode()), Button.inline("🏠 Main Menu", b"main")]


MAIN_TEXT = (
    "👋 <b>Welcome to Channel Alert Bot</b>\n\n"
    "I watch public Telegram channels for you. When a new post contains one of your "
    "<b>keywords</b>, I forward it here with text, media, channel name and a direct link.\n\n"
    "⭐ If the post comes from a <b>Priority Channel</b> AND contains a <b>Priority Keyword</b>, "
    "it's marked PRIORITY and I <b>call your Telegram</b>: 1st call → if you hang up I stop; "
    "if you ignore it, I call a 2nd time.\n\n"
    "🎙 <b>Voice messages</b> are transcribed with Groq Whisper and checked against your keywords. "
    "Calls happen only for a <b>Priority Channel</b> + <b>Priority Keyword</b> — for voices too.\n\n"
    "Choose an option below 👇"
)
MAIN_BTNS = [
    [Button.inline("📢 Channels", b"ch"), Button.inline("🔑 Keywords", b"kw")],
    [Button.inline("⭐ Priority Settings", b"pr"), Button.inline("ℹ️ Help/Info", b"help")],
    [Button.inline("📊 Summary", b"sum")],
]


async def show(event, text, buttons):
    try:
        await event.edit(text, buttons=buttons, parse_mode="html", link_preview=False)
    except MessageNotModifiedError:
        pass  # same screen tapped twice
    except Exception:
        try:
            await event.respond(text, buttons=buttons, parse_mode="html", link_preview=False)
        except Exception as e:
            log.warning("could not show screen: %s", e)


MAX_SCREEN = 3800   # Telegram message limit is 4096
MAX_BUTTONS = 90    # Telegram allows ~100 buttons per message


def clip_lines(lines, empty):
    """Join lines but stop before the screen gets too long for Telegram."""
    out, size = [], 0
    for i, ln in enumerate(lines):
        if size + len(ln) > MAX_SCREEN - 400:
            out.append(f"<i>…and {len(lines) - i} more</i>")
            break
        out.append(ln)
        size += len(ln) + 1
    return "\n".join(out) or empty


def is_owner(uid):
    return uid == owner_id()


# ------------------------------------------------------------------ /start
@bot.on(events.NewMessage(pattern=r"^/(start|menu)"))
async def start(event):
    if not owner_id():
        db.set_setting("owner_id", event.sender_id)  # first user becomes the owner
    if not is_owner(event.sender_id):
        return await event.respond("⛔ This bot is private.")
    state.pop(event.chat_id, None)
    await event.respond(MAIN_TEXT, buttons=MAIN_BTNS, parse_mode="html")


@bot.on(events.NewMessage(pattern=r"^/summary"))
async def summary_cmd(event):
    if not is_owner(event.sender_id):
        return
    await event.respond(summary_text(), parse_mode="html",
                        buttons=[[Button.inline("🔄 Refresh", b"sum")], back()[1:]])


def summary_text():
    try:
        t = db.hit_totals()
        chs = db.top_channels(10)
        kws = db.top_keywords(10)
        vkws = db.top_voice_keywords(10)
    except Exception as e:
        return f"⚠️ Couldn't load statistics: {html.escape(str(e))}"
    medals = ["🥇", "🥈", "🥉"]
    def rank(i):
        return medals[i] if i < 3 else f"{i + 1}."
    ch_body = "\n".join(f"{rank(i)} {html.escape(r['title'] or str(r['channel_id']))} — <b>{r['n']}</b>"
                        for i, r in enumerate(chs)) or "<i>No matches yet.</i>"
    kw_body = "\n".join(f"{rank(i)} {'⭐ ' if r['priority'] else ''}<code>{html.escape(r['word'])}</code> — <b>{r['n']}</b>"
                        for i, r in enumerate(kws)) or "<i>No matches yet.</i>"
    vkw_body = "\n".join(f"{rank(i)} {'⭐ ' if r['priority'] else ''}<code>{html.escape(r['word'])}</code> — <b>{r['n']}</b>"
                         for i, r in enumerate(vkws)) or "<i>No voice matches yet.</i>"
    return ("📊 <b>Summary</b>\n\n"
            f"Total keyword matches: <b>{t['total']}</b>\n"
            f"⭐ Priority matches: <b>{t['prio']}</b> · 🎙 From voice: <b>{t['voice']}</b>\n\n"
            f"📢 <b>Channels with the most keyword matches</b>\n{ch_body}\n\n"
            f"🔑 <b>Most matched keywords</b>\n{kw_body}\n\n"
            f"🎙 <b>Keywords triggering voice alerts</b>\n{vkw_body}")


# ------------------------------------------------------------------ callbacks
@bot.on(events.CallbackQuery())
async def on_cb(event):
    return await dispatch_callback(event, event.data.decode())


async def dispatch_callback(event, data):
    """Route callbacks without modifying Telethon's read-only event.data."""
    if not is_owner(event.sender_id):
        return await event.answer("⛔ Private bot", alert=True)
    cid = event.chat_id
    state.pop(cid, None)
    try:
        await event.answer()  # may already be answered when a screen re-renders itself
    except Exception:
        pass

    # ---- main
    if data == "main":
        return await show(event, MAIN_TEXT, MAIN_BTNS)

    if data == "help":
        return await show(event,
            "ℹ️ <b>Help / Info</b>\n\n"
            "<b>How it works</b>\n"
            "1. 📢 Add channels to watch (public @username, t.me link or invite link).\n"
            "2. 🔑 Add keywords. Any new post containing one is forwarded to you.\n"
            "3. ⭐ Mark priority channels, add priority keywords and set the Telegram ID to call.\n"
            "4. A post matching BOTH a priority channel AND a priority keyword is marked "
            "🚨 PRIORITY and triggers the call sequence.\n\n"
            "<b>Call sequence</b>: 1st call → hung up = stop · ignored = 2nd call.\n\n"
            "<b>Commands</b>: /start or /menu to open this menu, /cancel to abort an input.\n"
            "Matching is case-insensitive and only matches whole words (e.g. <code>hello</code> won't match <code>helloworld</code>).",
            [back()[1:]])

    # ---- summary / statistics
    if data == "sum":
        return await show(event, summary_text(), [
            [Button.inline("🔄 Refresh", b"sum_r"), Button.inline("🗑 Reset stats", b"sum_reset")], back()[1:]])
    if data == "sum_r":
        return await dispatch_callback(event, "sum")
    if data == "sum_reset":
        return await show(event, "⚠️ <b>Reset all summary statistics?</b>\nKeywords and channels are NOT touched.",
                          [[Button.inline("✅ Yes, reset", b"sum_reset_ok"), Button.inline("❌ No", b"sum")]])
    if data == "sum_reset_ok":
        db.hits_reset()
        return await dispatch_callback(event, "sum")

    # ---- channels
    if data == "ch":
        return await show(event,
            "📢 <b>Channel Management</b>\n\nThese are the channels I monitor for new posts.\n\n"
            "• <b>Add Channel</b> – start watching a new channel\n"
            "• <b>Delete Channel</b> – stop watching one\n"
            "• <b>View Channels</b> – see everything you're watching",
            [[Button.inline("➕ Add Channel", b"ch_add")],
             [Button.inline("🗑 Delete Channel", b"ch_del"), Button.inline("📋 View Channels", b"ch_list")],
             back()[1:]])
    if data == "ch_add":
        state[cid] = ("ch_add", None)
        return await show(event,
            "➕ <b>Add Channel</b>\n\nThis makes me join and watch a channel.\n\n"
            "<b>Steps:</b>\n1. Send the channel as <code>@username</code>, "
            "<code>https://t.me/username</code> or an invite link <code>https://t.me/+xxxx</code>.\n"
            "2. Wait for my confirmation.\n3. Repeat to add more.\n\nSend /cancel to stop.",
            [back("ch")])
    if data == "ch_list":
        rows = db.channels()
        body = clip_lines([f"{'⭐' if r['priority'] else '•'} {html.escape(r['title'] or str(r['id']))}"
                           + (f" (@{r['username']})" if r["username"] else "") for r in rows], "<i>No channels yet.</i>")
        return await show(event, f"📋 <b>Watched Channels</b> ({len(rows)})\n\n{body}\n\n⭐ = priority channel", [back("ch")])
    if data == "ch_del":
        rows = db.channels()
        btns = [[Button.inline(f"🗑 {(r['title'] or str(r['id']))[:40]}", f"chd:{r['id']}".encode())] for r in rows[:MAX_BUTTONS]]
        return await show(event,
            "🗑 <b>Delete Channel</b>\n\nTap a channel to stop watching it. "
            "Its priority setting is removed too.\n" + ("" if rows else "\n<i>No channels yet.</i>"),
            btns + [back("ch")])
    if data.startswith("chd:"):
        ch_id = int(data[4:])
        r = db.channel(ch_id)
        db.del_channel(ch_id)
        try:
            await user(LeaveChannelRequest(await user.get_input_entity(tltypes.PeerChannel(ch_id))))
        except Exception as e:
            log.info("leave channel %s: %s", ch_id, e)
        await event.respond(f"✅ Removed <b>{html.escape(r['title'] if r else str(ch_id))}</b>.", parse_mode="html")
        return await dispatch_callback(event, "ch_del")

    # ---- keywords (normal + priority share the same screens)
    for prefix, table, title, parent in (("kw", KW, "Keyword", "kw"), ("pk", PKW, "Priority Keyword", "pr")):
        if data == f"{prefix}_add":
            state[cid] = (f"{prefix}_add", None)
            return await show(event,
                f"➕ <b>Add {title}</b>\n\n"
                + ("Posts containing this word/phrase are forwarded to you.\n\n" if prefix == "kw"
                   else "Combined with a priority channel, this word triggers a PRIORITY alert and a phone call.\n\n")
                + "<b>Steps:</b>\n1. Send the word or phrase.\n2. To add several at once, put one per line.\n"
                  "3. Matching ignores upper/lower case.\n\nSend /cancel to stop.",
                [back(parent)])
        if data == f"{prefix}_list":
            rows = db.kw_list(table)
            body = clip_lines([f"• <code>{html.escape(r['word'])}</code>" for r in rows], "<i>None yet.</i>")
            return await show(event, f"📋 <b>{title}s</b> ({len(rows)})\n\n{body}", [back(parent)])
        if data == f"{prefix}_edit":
            rows = db.kw_list(table)
            btns = [[Button.inline(f"✏️ {r['word'][:30]}", f"{prefix}e:{r['id']}".encode()),
                     Button.inline("🗑", f"{prefix}d:{r['id']}".encode())] for r in rows[:MAX_BUTTONS // 2]]
            return await show(event,
                f"✏️ <b>Edit / Remove {title}s</b>\n\n<b>How to use:</b>\n"
                "• Tap ✏️ next to a word, then send its new text.\n• Tap 🗑 to delete it right away.\n"
                + ("" if rows else "\n<i>None yet.</i>"),
                btns + [back(parent)])
        if data.startswith(f"{prefix}e:"):
            kid = int(data.split(":")[1])
            r = db.kw_get(table, kid)
            state[cid] = (f"{prefix}_editval", kid)
            return await show(event,
                f"✏️ <b>Editing</b> <code>{html.escape(r['word'] if r else '?')}</code>\n\n"
                "Send the new word/phrase now. Send /cancel to keep it unchanged.",
                [back(f"{prefix}_edit")])
        if data.startswith(f"{prefix}d:"):
            db.kw_del(table, int(data.split(":")[1]))
            return await dispatch_callback(event, f"{prefix}_edit")

    if data == "kw":
        return await show(event,
            "🔑 <b>Keyword Management</b>\n\nKeywords decide which posts I forward to you. "
            "A post is forwarded when its text contains any keyword.\n\n"
            "• <b>Add Keyword</b> – new word/phrase\n• <b>Edit/Remove</b> – change or delete\n• <b>View</b> – full list",
            [[Button.inline("➕ Add Keyword", b"kw_add")],
             [Button.inline("✏️ Edit/Remove", b"kw_edit"), Button.inline("📋 View Keywords", b"kw_list")],
             back()[1:]])

    # ---- priority
    if data == "pr":
        target = db.get_setting("call_target") or "not set"
        np = sum(r["priority"] for r in db.channels())
        return await show(event,
            "⭐ <b>Priority & Call Alerts</b>\n\n"
            "A call is placed ONLY when a post comes from a <b>Priority Channel</b> AND contains a "
            "<b>Priority Keyword</b>.\n\n"
            f"Priority channels: <b>{np}</b> · Priority keywords: <b>{len(db.kw_list(PKW))}</b>\n"
            f"Call target: <code>{html.escape(target)}</code>\n\n"
            "Call behaviour: 1st call → hung up = stop · ignored = 2nd call.",
            [[Button.inline("📢 Priority Channels", b"pr_ch")],
             [Button.inline("➕ Add Priority Keyword", b"pk_add"), Button.inline("✏️ Edit/Remove", b"pk_edit")],
             [Button.inline("📋 View Priority Keywords", b"pk_list")],
             [Button.inline("📞 Set Call Target ID", b"pr_target"), Button.inline("🔔 Test Call", b"pr_test")],
             back()[1:]])
    if data == "pr_ch":
        rows = db.channels()
        btns = [[Button.inline(f"{'✅' if r['priority'] else '⬜'} {(r['title'] or str(r['id']))[:40]}", f"prt:{r['id']}".encode())] for r in rows[:MAX_BUTTONS]]
        return await show(event,
            "📢 <b>Select Priority Channels</b>\n\nTap a channel to switch priority on (✅) or off (⬜). "
            "Only priority channels can trigger calls.\n" + ("" if rows else "\n<i>Add channels first in 📢 Channels.</i>"),
            btns + [back("pr")])
    if data.startswith("prt:"):
        db.toggle_priority(int(data[4:]))
        return await dispatch_callback(event, "pr_ch")
    if data == "pr_target":
        state[cid] = ("pr_target", None)
        return await show(event,
            "📞 <b>Set Call Target</b>\n\nThis is the Telegram account that will be called.\n\n"
            "<b>Steps:</b>\n1. Send a numeric Telegram ID (e.g. <code>123456789</code>) or @username.\n"
            "2. It must be a <b>different</b> account from the userbot (an account can't call itself).\n"
            "3. In that account's Privacy → Calls, allow calls from the userbot account.\n"
            "Tip: get your ID from @userinfobot.\n\nSend /cancel to stop.",
            [back("pr")])
    if data == "pr_test":
        target = db.get_setting("call_target")
        if not target:
            return await event.respond("⚠️ Set a call target first.")
        await event.respond("🔔 Placing a test call sequence… (hang up to stop it)")
        spawn(run_call(target, "Test call"))


# ------------------------------------------------------------------ text input
@bot.on(events.NewMessage(func=lambda e: e.is_private and not e.raw_text.startswith(("/start", "/menu", "/summary"))))
async def on_text(event):
    if not is_owner(event.sender_id):
        return
    text = event.raw_text.strip()
    if text == "/cancel":
        state.pop(event.chat_id, None)
        return await event.respond("❎ Cancelled.", buttons=MAIN_BTNS)
    st = state.get(event.chat_id)
    if not st:
        return await event.respond("Use the buttons below 👇", buttons=MAIN_BTNS)
    action, extra = st

    if action == "ch_add":
        msg = await event.respond("⏳ Joining…")
        try:
            title = await join_channel(text)
            if title is None:
                return await msg.edit("This channel is already added in your bot!",
                                      buttons=[[Button.inline("📢 Channels", b"ch")]])
            await msg.edit(f"✅ Now watching <b>{html.escape(title)}</b>.\nSend another or tap below.",
                           parse_mode="html", buttons=[[Button.inline("📢 Channels", b"ch")]])
        except Exception as e:
            await msg.edit(f"❌ Couldn't add that channel: {html.escape(str(e))}\nCheck the link and try again.",
                           parse_mode="html")
        return

    for prefix, table in (("kw", KW), ("pk", PKW)):
        parent = b"kw" if prefix == "kw" else b"pr"
        if action == f"{prefix}_add":
            words = [w.strip() for w in text.splitlines() if w.strip()]
            added = [w for w in words if db.kw_add(table, w)]
            skipped = len(words) - len(added)
            return await event.respond(
                f"✅ Added {len(added)}" + (f" ({skipped} already existed)" if skipped else "")
                + ".\nSend more or tap below.", buttons=[[Button.inline("⬅️ Back", parent)]])
        if action == f"{prefix}_editval":
            state.pop(event.chat_id, None)
            if not text:
                return await event.respond("⚠️ Empty keyword ignored.")
            ok = db.kw_update(table, extra, text)
            return await event.respond("✅ Updated." if ok else "⚠️ That word already exists.",
                                       buttons=[[Button.inline("⬅️ Back", f"{prefix}_edit".encode())]])

    if action == "pr_target":
        state.pop(event.chat_id, None)
        try:
            ent = await user.get_entity(int(text) if text.lstrip("-").isdigit() else text)
            db.set_setting("call_target", str(ent.id))
            name = getattr(ent, "first_name", None) or getattr(ent, "title", "")
            await event.respond(f"✅ Call target set to <b>{html.escape(name)}</b> (<code>{ent.id}</code>).",
                                parse_mode="html", buttons=[[Button.inline("⬅️ Back", b"pr")]])
        except Exception as e:
            await event.respond(
                f"❌ Couldn't find that account ({html.escape(str(e))}).\n"
                "If you sent a numeric ID, the userbot must have chatted with that account first — "
                "or send an @username instead.", parse_mode="html")


async def join_channel(ref: str) -> str:
    ref = ref.strip()
    m = re.search(r"(?:t\.me/|telegram\.me/)(?:joinchat/|\+)([\w-]+)", ref)
    if m:  # invite link
        h = m.group(1)
        info = await user(CheckChatInviteRequest(h))
        # ChatInviteAlready = userbot is already a member; ChatInvite/ChatInvitePeek = must join
        chat = info.chat if isinstance(info, tltypes.ChatInviteAlready) else None
        if chat is not None and db.channel_active(chat.id):
            return None  # already in the active tracked list
        if chat is None:
            peek = getattr(info, "chat", None)
            if peek is not None and db.channel_active(peek.id):
                return None
            try:
                res = await user(ImportChatInviteRequest(h))
                chat = res.chats[0]
            except UserAlreadyParticipantError:
                chat = peek or (await user(CheckChatInviteRequest(h))).chat
            if db.channel_active(chat.id):
                return None
    else:
        m = re.search(r"(?:t\.me/|@)?([A-Za-z0-9_]{4,})/?$", ref)
        if not m:
            raise ValueError("unrecognised format")
        chat = await user.get_entity(m.group(1))
        if db.channel_active(chat.id):
            return None  # already in the active tracked list
        await user(JoinChannelRequest(chat))
    db.add_channel(chat.id, chat.title, getattr(chat, "username", None))
    return chat.title


# ------------------------------------------------------------------ monitoring
_WC = r"\w\u0300-\u036F\u0900-\u0DFF\u200C\u200D"  # word chars + combining marks + Indic blocks + ZWNJ/ZWJ
_WORD_RE = re.compile(f"[{_WC}]")
_NOT_AFTER_WORD = f"(?<![{_WC}])"
_NOT_BEFORE_WORD = f"(?![{_WC}])"


@lru_cache(maxsize=2048)
def _kw_pattern(w):
    pat = r"\s+".join(re.escape(p) for p in w.split())
    left = _NOT_AFTER_WORD if _WORD_RE.match(w[0]) else r"(?<!\S)"
    right = _NOT_BEFORE_WORD if _WORD_RE.match(w[-1]) else r"(?!\S)"
    return re.compile(left + pat + right, re.IGNORECASE)


def kw_match(word, text):
    """Exact standalone word/phrase match using word boundaries (case-insensitive).
    'hello' matches 'hello there' but NOT 'helloworld'."""
    w = word.strip()
    if not w:
        return False
    # Plain \b treats Bangla/Hindi vowel signs (া ি ে ...) as non-letters, so "খবর" would wrongly
    # match inside "খবরে". These guards count letters, digits, _ AND combining marks as word chars.
    if not text:
        return False
    return _kw_pattern(w).search(text) is not None


def find(text, table, fuzzy=False):
    """fuzzy=True (voice transcripts): normalized + fuzzy match via matching.py, tolerant of
    Banglish, dialects and slurred speech. Text posts keep the strict whole-word match."""
    try:
        if fuzzy:
            return [r["word"] for r in db.kw_list(table)
                    if kw_match(r["word"], text) or matching.fuzzy_match(r["word"], text)]
        return [r["word"] for r in db.kw_list(table) if kw_match(r["word"], text)]
    except Exception as e:  # DB hiccup must not kill the handler
        log.warning("keyword lookup failed: %s", e)
        return []


# ---- reliable intake: realtime events + catch-up poller -> dedupe -> queue -> workers
ALERT_WORKERS = max(1, int(os.getenv("ALERT_WORKERS", "3") or 3))
POLL_INTERVAL = max(5, int(os.getenv("POLL_INTERVAL", "20") or 20))  # safety-net poll per channel
JOB_TIMEOUT = 600   # one stuck post can never block a worker forever
SEEN_MAX = 20000
_seen: "OrderedDict[tuple, None]" = OrderedDict()   # (channel_id, msg_id) already queued
_last_id: dict[int, int] = {}                       # newest msg id seen per channel
_queue: asyncio.Queue | None = None


def get_queue() -> asyncio.Queue:
    global _queue
    if _queue is None:
        _queue = asyncio.Queue()
    return _queue


def channel_id_of(msg) -> int | None:
    peer = getattr(msg, "peer_id", None)
    return getattr(peer, "channel_id", None)


def enqueue(ch_id, msgs, source="live") -> bool:
    """Queue a post (single message or whole album) once. Returns False for duplicates.
    Synchronous, so two sources (event + poller) can never queue the same post twice."""
    msgs = [m for m in msgs if m is not None]
    new = [m for m in msgs if (ch_id, m.id) not in _seen]
    if not new:
        return False
    for m in new:
        _seen[(ch_id, m.id)] = None
        _last_id[ch_id] = max(_last_id.get(ch_id, 0), m.id)
    while len(_seen) > SEEN_MAX:
        _seen.popitem(last=False)
    get_queue().put_nowait((ch_id, sorted(new, key=lambda m: m.id), source))
    return True


@user.on(events.NewMessage())
async def on_post(event):
    """Realtime intake. Only enqueues — heavy work happens in the workers, so a burst
    of posts from many channels at the same second is never dropped."""
    if not event.is_channel:
        return
    msg = event.message
    if getattr(msg, "grouped_id", None):
        return  # album part: handled once, as a whole, by on_album
    ch_id = channel_id_of(msg) or abs(event.chat_id)
    enqueue(ch_id, [msg])


@user.on(events.Album())
async def on_album(event):
    if not event.is_channel:
        return
    msgs = list(event.messages)
    if not msgs:
        return
    ch_id = channel_id_of(msgs[0]) or abs(event.chat_id)
    enqueue(ch_id, msgs)


async def alert_worker(n):
    q = get_queue()
    while True:
        ch_id, msgs, source = await q.get()
        try:
            await asyncio.wait_for(handle_job(ch_id, msgs, source), JOB_TIMEOUT)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # log and keep monitoring
            log.exception("worker %s: post %s/%s failed: %r", n, ch_id, msgs[0].id, e)
        finally:
            q.task_done()


async def handle_job(ch_id, msgs, source="live"):
    try:
        ch = db.channel(ch_id)
    except Exception as e:
        log.warning("channel lookup failed: %s", e)
        return
    if not ch:
        return
    if source == "poll":
        log.info("catch-up poller recovered post %s in %s", msgs[0].id, ch["title"])
    await process_post(ch, msgs)


async def poll_once():
    """Safety net for updates Telegram never pushed (gaps, reconnects, lazy big channels):
    fetch anything newer than the last seen id for every watched channel."""
    try:
        rows = db.channels()
    except Exception as e:
        log.warning("poller: channel list failed: %s", e)
        return
    settle = datetime.now(timezone.utc) - timedelta(seconds=4)  # let albums finish uploading
    for r in rows:
        cid = r["id"]
        try:
            peer = tltypes.PeerChannel(cid)
            if cid not in _last_id:  # first sight: start from now, don't replay history
                latest = await user.get_messages(peer, limit=1)
                _last_id[cid] = max(_last_id.get(cid, 0), latest[0].id if latest else 0)
                continue
            got = await user.get_messages(peer, min_id=_last_id[cid], limit=50)
            fresh = sorted((m for m in got if m and m.date and m.date <= settle), key=lambda m: m.id)
            groups: "OrderedDict[object, list]" = OrderedDict()
            for m in fresh:
                groups.setdefault(m.grouped_id or ("single", m.id), []).append(m)
            for g in groups.values():
                enqueue(cid, g, "poll")
        except FloodWaitError as e:
            log.warning("poller FloodWait %ss", e.seconds)
            await asyncio.sleep(min(e.seconds + 1, MAX_FLOOD_WAIT))
        except Exception as e:
            log.debug("poller %s: %r", cid, e)
        await asyncio.sleep(0.3)  # gentle on the API with many channels


async def poller():
    while True:
        try:
            await poll_once()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.warning("poller error (ignored): %r", e)
        await asyncio.sleep(POLL_INTERVAL)


def _caption_of(msgs):
    for m in msgs:
        if (m.raw_text or "").strip():
            return m.raw_text
    return ""


async def process_post(ch, msgs):
    first = msgs[0]
    text = _caption_of(msgs)
    if len(msgs) == 1 and (first.voice or first.audio):
        return await on_voice_post(first, ch, text)
    if not text.strip():
        return  # media-only post: nothing to match
    normal = find(text, KW)
    prio = find(text, PKW) if ch["priority"] else []
    is_priority = bool(ch["priority"] and prio)
    if not normal and not is_priority:
        return
    record_hits(ch, normal, prio if is_priority else [], "text")

    link = post_link(ch, first.id)
    head = ("🚨🚨 <b>PRIORITY ALERT</b> 🚨🚨\n" if is_priority else "📨 <b>New matching post</b>\n")
    head += (f"📢 <b>Channel:</b> {html.escape(ch['title'])}\n"
             f"🔑 <b>Matched:</b> {html.escape(', '.join(dict.fromkeys(prio + normal)))}\n"
             f"🔗 <a href=\"{link}\">Open original post</a>\n\n")
    dest = notify_chat()
    # Call first: it is the most time-critical part and must not wait for media uploads.
    if is_priority:
        target = db.get_setting("call_target")
        if target:
            spawn(run_call(target, ch["title"], text))
        else:
            await safe_send(dest, "⚠️ Priority matched but no call target is set (⭐ Priority Settings).")
    try:
        await send_alert(msgs, dest, head, text)
    except Exception as e:
        log.exception("forward failed: %s", e)
        await safe_send(dest, build_body(head, text, 3900), parse_mode="html", link_preview=False)


DOWNLOAD_TIMEOUT = 120    # seconds; a stuck download must never block alerts
TRANSCRIBE_TIMEOUT = 120  # seconds for Groq to answer
_voice_slots = asyncio.Semaphore(2)  # at most 2 voices processed at once (keeps memory low)
CAPTION_LIMIT = 1024  # Telegram's max caption length


def build_body(head, text, limit):
    """head + escaped text, truncated so the whole message fits `limit` (measured after escaping)."""
    if not text:
        return head + "<i>(no text)</i>"
    room = max(0, limit - len(head))
    body = html.escape(text)
    if len(body) <= room:
        return head + body
    suffix = "…"
    lo, hi = 0, len(text)  # longest raw prefix whose escaped form fits
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if len(html.escape(text[:mid])) + len(suffix) <= room:
            lo = mid
        else:
            hi = mid - 1
    cut = text[:lo]
    return head + html.escape(cut) + suffix


def _media_ok(m):
    return bool(m.media and m.file is not None and not m.web_preview)


def _fname(m, default="media"):
    return getattr(m.file, "name", None) or f"{default}{m.file.ext or ''}"


async def _download(m):
    return await asyncio.wait_for(tg_retry(user.download_media, m, file=bytes), DOWNLOAD_TIMEOUT)


async def send_alert(msgs, dest, head, text):
    """Send the alert as ONE post: a single media / grouped album with the alert as caption,
    otherwise a plain text message. Media bytes are released right after upload."""
    if not isinstance(msgs, (list, tuple)):
        msgs = [msgs]
    media = [m for m in msgs if _media_ok(m)]
    fits = [m for m in media if (m.file.size or 0) <= MAX_MEDIA_BYTES]
    too_big = len(fits) < len(media)
    caption = build_body(head, text, CAPTION_LIMIT)
    if len(fits) > 1:  # album: photos/videos together in one grouped post (max 10 per album)
        try:
            uploaded = []
            for m in fits[:10]:
                data = await _download(m)
                if data:
                    name = f"photo{len(uploaded)}.jpg" if m.photo else _fname(m)
                    uploaded.append(await tg_retry(bot.upload_file, data, file_name=name))
                data = None  # free memory immediately
            if len(uploaded) > 1:
                caps = [caption] + [""] * (len(uploaded) - 1)
                sent = await tg_retry(bot.send_file, dest, uploaded, caption=caps, parse_mode="html")
                if len(fits) > 10:
                    await safe_send(dest, "📎 <i>Album has more than 10 items — open the original post.</i>",
                                    parse_mode="html")
                return sent
            if len(uploaded) == 1:
                return await tg_retry(bot.send_file, dest, uploaded[0], caption=caption, parse_mode="html")
        except Exception as e:
            log.warning("album send failed, falling back to text: %s", e)
    elif len(fits) == 1:
        m = fits[0]
        try:
            data = await _download(m)
            if data:
                f = await tg_retry(bot.upload_file, data, file_name=_fname(m))
                data = None
                return await tg_retry(bot.send_file, dest, f, caption=caption, parse_mode="html",
                                      force_document=not (m.photo or m.video))
        except Exception as e:
            log.warning("media+caption send failed, falling back to text: %s", e)
    full = build_body(head, text, 3900)
    if too_big:
        full += "\n\n📎 <i>Media too large to forward — open the original post.</i>"
    return await safe_send(dest, full, parse_mode="html", link_preview=False)


def record_hits(ch, normal, prio, source):
    try:
        db.hit_add(ch["id"], ch["title"], normal, prio, source)
    except Exception as e:  # stats must never block an alert
        log.warning("stats write failed: %s", e)


def post_link(ch, msg_id):
    return (f"https://t.me/{ch['username']}/{msg_id}" if ch["username"]
            else f"https://t.me/c/{ch['id']}/{msg_id}")


MAX_VOICE_SECONDS = 180  # voices longer than 3 minutes are skipped
GROQ_MAX_BYTES = min(MAX_MEDIA_BYTES, 25 * 1024 * 1024)  # Groq Whisper rejects files over 25 MB


def voice_duration(event):
    """Duration in seconds from the voice/audio attributes, or None if unknown."""
    try:
        d = getattr(event.file, "duration", None)
        return int(d) if d is not None else None
    except Exception:
        return None


def _msg_of(event):
    """Telethon event -> its Message; a Message (whose .message is the text) -> itself."""
    m = getattr(event, "message", None)
    return event if (m is None or isinstance(m, str)) else m


async def on_voice_post(event, ch, caption):
    """Voice/audio post: transcribe with Groq Whisper, match keywords, forward the voice.
    STRICT rule: PRIORITY + call only when Priority Channel = YES AND Priority Keyword = YES.
    A priority keyword heard on a non-priority channel is forwarded as a normal alert (no call)."""
    dest = notify_chat()
    duration = voice_duration(event)
    if duration is not None and duration > MAX_VOICE_SECONDS:
        log.info("voice skipped: %ss is longer than the %ss limit", duration, MAX_VOICE_SECONDS)
        return
    size = (event.file.size or 0) if event.file is not None else 0
    if size > GROQ_MAX_BYTES:
        log.info("voice too large to transcribe (%s bytes)", size)
        return
    if not transcribe.AVAILABLE:
        log.info("voice post skipped: GROQ_API_KEY not set")
        return
    try:  # saved keywords guide Whisper's spelling (priority first)
        transcribe.KEYWORD_HINTS = [r["word"] for r in db.kw_list(PKW)] + [r["word"] for r in db.kw_list(KW)]
    except Exception as e:
        log.warning("keyword hints unavailable: %s", e)
        transcribe.KEYWORD_HINTS = []
    async with _voice_slots:
        try:
            data = await asyncio.wait_for(tg_retry(user.download_media, _msg_of(event), file=bytes),
                                          DOWNLOAD_TIMEOUT)
        except Exception as e:
            log.warning("voice download failed: %r", e)
            return
        if not data:
            return
        try:
            transcript = await asyncio.wait_for(
                transcribe.transcribe(data, getattr(event.file, "mime_type", None) or "audio/ogg"), TRANSCRIBE_TIMEOUT)
        except Exception as e:
            log.warning("voice transcription failed: %r", e)
            return
    full_text = "\n".join(t for t in (caption.strip(), transcript) if t)
    if not full_text:
        return
    normal = find(full_text, KW, fuzzy=True)
    prio = find(full_text, PKW, fuzzy=True)
    if not normal and not prio:
        return
    is_priority = bool(ch["priority"] and prio)
    if not is_priority:  # strict rule: no priority without a priority channel
        normal, prio = list(dict.fromkeys(prio + normal)), []
    record_hits(ch, normal, prio, "voice")

    link = post_link(ch, event.id)
    head = ("🚨🎙 <b>PRIORITY VOICE ALERT</b> 🎙🚨\n" if is_priority else "🎙 <b>Matching voice message</b>\n")
    head += (f"📢 <b>Channel:</b> {html.escape(ch['title'])}\n"
             f"🔑 <b>Matched:</b> {html.escape(', '.join(dict.fromkeys(prio + normal)))}\n"
             f"🔗 <a href=\"{link}\">Open original post</a>\n\n📝 <b>Transcript:</b>\n")
    caption_html = build_body(head, full_text, CAPTION_LIMIT)
    try:
        f = await tg_retry(bot.upload_file, data,
                           file_name=getattr(event.file, "name", None) or f"voice{event.file.ext or '.ogg'}")
        data = None  # free the audio bytes right away
        await tg_retry(bot.send_file, dest, f, caption=caption_html, parse_mode="html",
                       voice_note=bool(event.voice))
        if len(full_text) > 600:  # long transcript: send it in full as a separate message
            await safe_send(dest, "📝 <b>Full transcript</b>\n\n" + html.escape(full_text[:3900]), parse_mode="html")
    except Exception as e:
        log.exception("voice forward failed: %s", e)
        await safe_send(dest, caption_html, parse_mode="html", link_preview=False)

    if is_priority:
        target = db.get_setting("call_target")
        if target:
            spawn(run_call(target, ch["title"], full_text))
        else:
            await safe_send(dest, "⚠️ Priority matched but no call target is set (⭐ Priority Settings).")


async def run_call(target, reason, post=None):
    summary = None
    if voice_call.AVAILABLE:
        try:
            summary = await voice_call.ai_alert_call(
                user, target, reason, post or f"This is a test call from the alert bot ({reason}).")
        except Exception as e:
            log.exception("AI voice call failed, falling back to ring-only: %s", e)
            await safe_send(notify_chat(), f"⚠️ AI voice call failed ({html.escape(str(e))}). Ringing normally instead.",
                                   parse_mode="html")
    if summary is None:
        try:
            summary = await caller.alert_call(user, target)
        except Exception as e:
            log.exception("call failed: %s", e)
            summary = f"failed: {e}"
    await safe_send(notify_chat(), f"📞 Call alert ({html.escape(reason)}): {html.escape(summary)}", parse_mode="html")


# ------------------------------------------------------------------ main
async def health_server():
    """Tiny HTTP 200 responder so web-service hosts (Render/Koyeb) see the app as healthy."""
    port = os.getenv("PORT")
    if not port:
        return
    async def handle(reader, writer):
        try:
            await asyncio.wait_for(reader.read(1024), 10)  # slow/idle clients can't pin sockets
            writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\nContent-Length: 2\r\n"
                         b"Connection: close\r\n\r\nOK")
            await writer.drain()
        except Exception:
            pass
        finally:
            writer.close()
    server = await asyncio.start_server(handle, "0.0.0.0", int(port))
    log.info("Health check listening on :%s", port)
    return server


# ------------------------------------------------------------------ keep-alive
PING_INTERVAL = 600  # seconds — Render's free web services idle out after ~15 min


def _ping_once(url: str) -> None:
    """One blocking HTTP GET. Runs in a worker thread, never on the event loop."""
    import urllib.error
    import urllib.request

    req = urllib.request.Request(url, method="GET", headers={"User-Agent": "keep-alive-ping"})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            resp.read()
            log.info("Keep-alive ping OK: %s", resp.status)
    except Exception as e:
        # Any failure (timeout, DNS, 5xx, offline host) is logged and ignored —
        # a missed ping must never stop the bot or touch message processing.
        log.warning("Keep-alive ping failed (ignored): %s", e)


async def keep_alive():
    """Self-ping every PING_INTERVAL so Render doesn't idle-out the free web service.

    Skips entirely when RENDER_EXTERNAL_URL is not set (local runs, Railway, Docker).
    """
    url = os.getenv("RENDER_EXTERNAL_URL", "").strip().rstrip("/")
    if not url:
        log.info("RENDER_EXTERNAL_URL not set — keep-alive self-ping disabled.")
        return
    log.info("Keep-alive enabled: pinging %s every %ds.", url, PING_INTERVAL)
    while not _stop.is_set():
        # to_thread keeps the network call off the event loop, so Telegram
        # updates are never blocked by a slow or hanging ping.
        await asyncio.to_thread(_ping_once, url)  # bounded by the 20 s timeout -> no zombie thread
        try:
            await asyncio.wait_for(_stop.wait(), PING_INTERVAL)  # wakes instantly on shutdown
        except asyncio.TimeoutError:
            pass


_stop: asyncio.Event  # set on SIGTERM/SIGINT; created inside the running loop


async def main():
    global _stop
    _stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):  # Render sends SIGTERM on redeploy/stop
        try:
            loop.add_signal_handler(sig, _stop.set)
        except (NotImplementedError, RuntimeError):
            pass  # Windows
    server = await health_server()
    spawn(keep_alive())
    if _USER_SESSION:
        await user.connect()
        if not await user.is_user_authorized():
            raise SystemExit("USER_SESSION is invalid or expired. Run login_session.py again.")
    else:
        await user.start()  # local run: asks for phone number + code in the console
    await bot.start(bot_token=BOT_TOKEN)
    caller.install(user)
    try:
        await voice_call.install(user)
    except Exception as e:  # never let the voice add-on stop the bot
        log.exception("voice call setup failed, ring-only mode: %s", e)
        voice_call.AVAILABLE = False
    try:  # fill the entity cache so the poller/catch-up can address every channel
        await user.get_dialogs(limit=None)
    except Exception as e:
        log.warning("get_dialogs failed (poller may skip some channels until they post): %s", e)
    for n in range(ALERT_WORKERS):
        spawn(alert_worker(n))
    spawn(poller())
    try:
        await user.catch_up()
    except Exception as e:
        log.info("catch_up: %s", e)
    me = await user.get_me()
    log.info("Voice transcription: %s", "ON (" + transcribe.MODEL + ")" if transcribe.AVAILABLE else "OFF (no GROQ_API_KEY)")
    log.info("Userbot: %s | Bot: @%s", me.first_name, (await bot.get_me()).username)
    runners = [asyncio.create_task(user.run_until_disconnected()),
               asyncio.create_task(bot.run_until_disconnected()),
               asyncio.create_task(_stop.wait())]
    try:
        done, _ = await asyncio.wait(runners, return_when=asyncio.FIRST_COMPLETED)
        for t in done:  # a client dropped for good -> exit non-zero so the host restarts us
            if not t.cancelled() and t.exception():
                log.error("client stopped: %r", t.exception())
    finally:
        await shutdown(server, runners)


async def shutdown(server, runners):
    """Graceful exit: stop timers, hang up live calls, disconnect clients, close DB."""
    log.info("Shutting down…")
    _stop.set()
    if server is not None:
        server.close()
    await voice_call.shutdown()
    for t in list(_bg_tasks):
        t.cancel()
    await asyncio.gather(*_bg_tasks, return_exceptions=True)
    for c in (user, bot):
        try:
            await asyncio.wait_for(c.disconnect(), 10)
        except Exception:
            pass
    for t in runners:
        t.cancel()
    await asyncio.gather(*runners, return_exceptions=True)
    db.close()
    log.info("Bye.")


if __name__ == "__main__":
    asyncio.run(main())
