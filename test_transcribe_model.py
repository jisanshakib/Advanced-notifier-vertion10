"""Check the transcription model selection and API request without credentials."""
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
            os.environ.pop("GEMINI_TRANSCRIBE_MODEL", None)
            importlib.reload(transcribe)
            self.assertEqual(transcribe.MODEL, "gemini-3.8-flash")

    def test_empty_model_uses_default(self):
        with patch.dict(os.environ, {"GEMINI_TRANSCRIBE_MODEL": "  "}):
            importlib.reload(transcribe)
            self.assertEqual(transcribe.MODEL, "gemini-3.8-flash")

    def test_explicit_override_is_preserved(self):
        with patch.dict(os.environ, {"GEMINI_TRANSCRIBE_MODEL": "custom-model"}):
            importlib.reload(transcribe)
            self.assertEqual(transcribe.MODEL, "custom-model")

    def test_request_uses_updated_model_and_audio(self):
        with patch.dict(os.environ, {"GEMINI_TRANSCRIBE_MODEL": "gemini-3.8-flash"}):
            importlib.reload(transcribe)
            generate = AsyncMock(return_value=SimpleNamespace(text=" urgent "))
            client = SimpleNamespace(aio=SimpleNamespace(models=SimpleNamespace(generate_content=generate)))
            with patch.object(transcribe, "AVAILABLE", True), patch.object(transcribe, "_get_client", return_value=client):
                result = asyncio.run(transcribe.transcribe(b"audio", "audio/ogg"))
            self.assertEqual(result, "urgent")
            self.assertEqual(generate.await_args.kwargs["model"], "gemini-3.8-flash")
            part = generate.await_args.kwargs["contents"][1]
            self.assertEqual(part.inline_data.data, b"audio")
            self.assertEqual(part.inline_data.mime_type, "audio/ogg")


if __name__ == "__main__":
    unittest.main()