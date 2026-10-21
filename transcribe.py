"""Voice-message transcription with Gemini (google-genai).

Used by bot.py: when a watched channel posts a voice/audio message, the audio is sent to
Gemini, and the returned transcript is matched against the saved keywords exactly like text.
If GEMINI_API_KEY is missing, transcription is skipped and the rest of the bot keeps working.
"""
import logging
import os

log = logging.getLogger("transcribe")

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
MODEL = os.getenv("GEMINI_TRANSCRIBE_MODEL", "gemini-2.5-flash").strip()

PROMPT = (
    "Transcribe this audio exactly as spoken, word for word, in its original language "
    "(do not translate). Return only the transcript text, no notes or timestamps. "
    "If there is no speech, return an empty response."
)

_client = None
try:
    from google import genai
    from google.genai import types as gtypes
    AVAILABLE = bool(GEMINI_API_KEY)
except Exception as e:  # library missing -> feature off, bot still runs
    log.warning("google-genai not available, voice transcription disabled: %s", e)
    AVAILABLE = False


def _get_client():
    global _client
    if _client is None:
        _client = genai.Client(api_key=GEMINI_API_KEY)
    return _client


async def transcribe(data: bytes, mime_type: str = "audio/ogg") -> str:
    """Return the transcript ('' when unavailable/silent). Raises on API errors."""
    if not AVAILABLE or not data:
        return ""
    resp = await _get_client().aio.models.generate_content(
        model=MODEL,
        contents=[PROMPT, gtypes.Part.from_bytes(data=data, mime_type=mime_type or "audio/ogg")],
    )
    return (getattr(resp, "text", None) or "").strip()
