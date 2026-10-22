"""Voice-message transcription with Groq Whisper (default: whisper-large-v3).

Used by bot.py: when a watched channel posts a voice/audio message, the audio is sent to
Groq's Whisper API, and the returned transcript is matched against the saved keywords
(with fuzzy matching, see matching.py). If GROQ_API_KEY is missing, transcription is skipped
and the rest of the bot keeps working. Live Telegram calls still use Gemini (voice_call.py).

Accuracy on mixed Bangla/English ("Banglish"), regional dialects and informal speech:
- default model is whisper-large-v3 (better with accents than -turbo; override via
  GROQ_TRANSCRIBE_MODEL)
- an initial prompt tells Whisper the expected languages and includes the saved keywords,
  so it spells them the way they were saved. Override the base text via GROQ_TRANSCRIBE_PROMPT.
"""
import logging
import os

log = logging.getLogger("transcribe")

DEFAULT_MODEL = "whisper-large-v3"
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "").strip()
MODEL = os.getenv("GROQ_TRANSCRIBE_MODEL", DEFAULT_MODEL).strip() or DEFAULT_MODEL

DEFAULT_PROMPT = (
    "Bengali speech transcription with mixed Bangla and English (Banglish), regional dialects "
    "and informal speech. বাংলা ও ইংরেজি মিশ্রিত কথা। Keep English words in English."
)
BASE_PROMPT = os.getenv("GROQ_TRANSCRIBE_PROMPT", "").strip() or DEFAULT_PROMPT
# Whisper only reads ~224 prompt tokens; keep the whole prompt short.
PROMPT_MAX_CHARS = 600

# bot.py sets this to the saved keywords before each transcription.
KEYWORD_HINTS: list = []

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


def build_prompt(keywords=None) -> str:
    """Base language hint + as many saved keywords as fit in the prompt budget."""
    prompt = BASE_PROMPT
    words = [w.strip() for w in dict.fromkeys(keywords or []) if w and w.strip()]
    if words:
        prompt += " Keywords:"
        for w in words:
            piece = f" {w},"
            if len(prompt) + len(piece) > PROMPT_MAX_CHARS:
                break
            prompt += piece
        prompt = prompt.rstrip(",:") + "."
    return prompt[:PROMPT_MAX_CHARS]


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
        prompt=build_prompt(KEYWORD_HINTS),
        temperature=0.0,
        response_format="json",
    )
    return (getattr(resp, "text", None) or "").strip()
