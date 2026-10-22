# Update v13 — Voice transcription moved from Gemini to Groq

## Why
- Gemini transcription kept failing on Render: `404 NOT_FOUND` (deprecated
  `gemini-2.5-flash`) and `503 UNAVAILABLE` (high demand on `gemini-3.8-flash`).

## What changed
- `transcribe.py` rewritten: voice messages from watched channels are now transcribed
  by **Groq Whisper** (`whisper-large-v3-turbo`) instead of Gemini. Fast and generous
  free tier. Set `GROQ_TRANSCRIBE_MODEL=whisper-large-v3` if you prefer the larger model.
- New environment variable: **`GROQ_API_KEY`** (get one at https://console.groq.com/keys).
  `GEMINI_TRANSCRIBE_MODEL` is removed — replaced by `GROQ_TRANSCRIBE_MODEL`.
- `requirements.txt` adds the `groq` package.
- Keyword matching, caption matching, the 3-minute limit, forwarding, the summary,
  and priority-call behaviour are all unchanged.

## What did NOT change
- **Live Telegram calls still use Gemini** (`voice_call.py`, model
  `gemini-2.5-flash-native-audio-preview-09-2025`) exactly as before. Keep
  `GEMINI_API_KEY` set for the call feature.

## Deploy steps on Render
1. In Render → your service → Environment: add `GROQ_API_KEY` with your Groq key.
   (You can delete `GEMINI_TRANSCRIBE_MODEL`; keep `GEMINI_API_KEY` for live calls.)
2. Replace the files with the ones in this zip (or push the updated repo).
3. Render reinstalls dependencies automatically (`groq` was added) and restarts.
4. Test: post a short voice message containing a keyword in a watched channel —
   the alert should arrive with the transcribed text. Then tap 🔔 Test Call to
   confirm calls still work.

## Verified
- 12 offline tests pass: Groq model selection, request shape, keyword matching
  through the full voice-alert flow, summary counters, callback button regressions.
- `test_bot.py` needs the native `pytgcalls` library and is not runnable in the
  offline test sandbox (same as before); it installs on Render via requirements.txt.
