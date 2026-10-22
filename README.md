# Telegram Channel Alert Bot

Everything is controlled inside Telegram with buttons. There is no website.
It runs as **two accounts at once**:
- **Bot** (from @BotFather): the menu you talk to, and where alerts arrive.
- **Userbot** (a normal Telegram account): reads any public channel and places the voice calls.
  Use a **separate account** from the one that gets called, because an account can't call itself.

## 1. Get credentials
1. https://my.telegram.org → API development tools → copy **API_ID** and **API_HASH**.
2. @BotFather → /newbot → copy the **BOT_TOKEN**.

## 2. Install (Python 3.10+)
```
pip install -r requirements.txt
cp .env.example .env      # fill in the values
python bot.py
```
On the first run, the console asks for the userbot's phone number and login code (plus 2FA password if set).
This creates `user_session.session`. Keep that file private.

## 3. Use it
Open your bot in Telegram and send **/start**. The first person who does this becomes the owner (or set OWNER_ID).
- 📢 Channels: add (@username, t.me link, or invite link), delete, view
- 🔑 Keywords: add (one per line), edit/remove, view
- ⭐ Priority: pick priority channels, priority keywords, call target ID, test call
- ℹ️ Help

**Alert rule:** a post containing any keyword is forwarded with its text, media, channel name and link.
If it comes from a priority channel **and** contains a priority keyword, it's marked 🚨 PRIORITY
and a call is placed: call 1 → if you hang up/decline it stops; if ignored (~45 s), call 2.

In the target account, go to Settings → Privacy → Calls and allow calls from the userbot account.
When `GEMINI_API_KEY` is set and you answer, Gemini greets you ("আসসালামু আলাইকুম জিসান ভাই"), explains the post, then stays on the line so you can ask questions by voice. Without the key the call only rings.

## Keep it running
Use a small always-on server (VPS, Railway, Render background worker, or a Raspberry Pi), for example
`nohup python bot.py &`, or a systemd service. Data is stored in `botdata.sqlite3`.
- See DEPLOY.md for 24/7 cloud hosting (Railway / Render).

## v15 – Better voice matching (Banglish, dialects, informal speech)
- Default transcription model is now `whisper-large-v3` (set `GROQ_TRANSCRIBE_MODEL=whisper-large-v3-turbo` for speed).
- Whisper gets a language hint (Bangla + English mixed) plus your saved keywords, so it spells them the way you saved them. Customize with `GROQ_TRANSCRIBE_PROMPT`.
- Voice transcripts use flexible matching: case/accent/punctuation-insensitive, common Bangla spelling variants (ী/ি, ূ/ু, শ/ষ/স, ণ/ন, ড়/র…) treated as equal, and fuzzy similarity (default 85%, `VOICE_FUZZY_THRESHOLD`). Keywords under 4 letters still need an exact word. Text posts keep strict matching.
