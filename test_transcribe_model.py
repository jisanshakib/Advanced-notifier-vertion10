"""Check the Groq transcription model selection and API request without credentials."""
import asyncio
import importlib
import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import transcribe


class ModelTests(unittest.TestCase):
    def test_default_model(self):
        with patch.dict(os.environ):
            os.environ.pop("GROQ_TRANSCRIBE_MODEL", None)
            importlib.reload(transcribe)
            self.assertEqual(transcribe.MODEL, "whisper-large-v3-turbo")

    def test_empty_model_uses_default(self):
        with patch.dict(os.environ, {"GROQ_TRANSCRIBE_MODEL": "  "}):
            importlib.reload(transcribe)
            self.assertEqual(transcribe.MODEL, "whisper-large-v3-turbo")

    def test_explicit_override_is_preserved(self):
        with patch.dict(os.environ, {"GROQ_TRANSCRIBE_MODEL": "whisper-large-v3"}):
            importlib.reload(transcribe)
            self.assertEqual(transcribe.MODEL, "whisper-large-v3")

    def test_unavailable_without_key_returns_empty(self):
        with patch.object(transcribe, "AVAILABLE", False):
            result = asyncio.run(transcribe.transcribe(b"audio", "audio/ogg"))
        self.assertEqual(result, "")

    def test_request_uses_updated_model_and_audio(self):
        with patch.dict(os.environ, {"GROQ_TRANSCRIBE_MODEL": "whisper-large-v3-turbo"}):
            importlib.reload(transcribe)
            create = AsyncMock(return_value=SimpleNamespace(text=" urgent "))
            client = SimpleNamespace(audio=SimpleNamespace(transcriptions=SimpleNamespace(create=create)))
            with patch.object(transcribe, "AVAILABLE", True), patch.object(transcribe, "_get_client", return_value=client):
                result = asyncio.run(transcribe.transcribe(b"audio", "audio/ogg"))
            self.assertEqual(result, "urgent")
            kwargs = create.await_args.kwargs
            self.assertEqual(kwargs["model"], "whisper-large-v3-turbo")
            self.assertEqual(kwargs["file"], ("voice.ogg", b"audio"))

    def test_mime_extension_mapping(self):
        self.assertEqual(transcribe._filename("audio/mpeg"), "voice.mp3")
        self.assertEqual(transcribe._filename("audio/ogg; codecs=opus"), "voice.ogg")
        self.assertEqual(transcribe._filename(None), "voice.ogg")


if __name__ == "__main__":
    unittest.main()
