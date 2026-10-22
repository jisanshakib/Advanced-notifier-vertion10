"""Voice-message transcription with Groq Whisper (whisper-large-v3-turbo).

Used by bot.py: when a watched channel posts a voice/audio message, the audio is sent to
Groq's Whisper API, and the returned transcript is matched against the saved keywords
exactly like text. If GROQ_API_KEY is missing, transcription is skipped and the rest of
the bot keeps working. Live Telegram calls still use Gemini (voice_call.py) — untouched.
"""
import logging
import os

log = logging.getLogger("transcribe")

GROQ_API_KEY = os.getenv("GROQ_API_KEY", "").strip()
MODEL = os.getenv("GROQ_TRANSCRIBE_MODEL", "whisper-large-v3-turbo").strip() or "whisper-large-v3-turbo"

_client = None
try:
    from groq import AsyncGroq
    AVAILABLE = bool(GROQ_API_KEY)
except Exception as e:  # library missing -> feature off, bot still runs
    log.warning("groq not available, voice transcription disabled: %s", e)
    AVAILABLE = False

# Groq Whisper accepts these containers; anything else is sent as .ogg (Telegram voice).
_EXT = {
    "audio/ogg": ".ogg",
    "audio/mpeg": ".mp3",
    "audio/mp4": ".m4a",
    "audio/x-m4a": ".m4a",
    "audio/wav": ".wav",
    "audio/x-wav": ".wav",
    "audio/webm": ".webm",
    "audio/flac": ".flac",
}


def _filename(mime_type: str) -> str:
    ext = _EXT.get((mime_type or "").split(";")[0].strip().lower(), ".ogg")
    return "voice" + ext


def _get_client():
    global _client
    if _client is None:
        _client = AsyncGroq(api_key=GROQ_API_KEY)
    return _client


async def transcribe(data: bytes, mime_type: str = "audio/ogg") -> str:
    """Return the transcript ('' when unavailable/silent). Raises on API errors."""
    if not AVAILABLE or not data:
        return ""
    resp = await _get_client().audio.transcriptions.create(
        file=(_filename(mime_type), data),
        model=MODEL,
        response_format="json",
    )
    return (getattr(resp, "text", None) or "").strip()
