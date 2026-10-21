# v12 — transcription model and callback fixes

## Render deployment

1. Replace your deployed source with this package, keeping your existing secrets and settings.
2. In Render → your service → Environment, set `GEMINI_TRANSCRIBE_MODEL=gemini-3.8-flash`.
   An existing `gemini-2.5-flash` environment value overrides the new code default;
   replacing the files alone will not change that value.
3. Save and redeploy/restart your service.
4. Post a voice of 3 minutes or less containing a saved keyword in a watched channel.
   Check its alert and the voice summary. Toggle a priority channel to confirm its button refreshes.

## Changes

- Default transcription model and `.env.example`: `gemini-3.8-flash`.
- Empty transcription model values now use that default.
- Callback navigation uses an explicit action argument instead of assigning to
  Telethon's read-only `event.data`. This fixes priority toggles, summary refresh/reset,
  channel deletion and both keyword deletion menus.
- Call models, alert rules, image captions, voice duration limits, summary counting,
  channel storage and keyword matching have not been changed.

## Validation

Offline callback regression tests and transcription request tests are included.
Run `python test_bot.py` and
`python -m unittest test_callback_regressions test_transcribe_model`.
Real Google transcription and Telegram delivery require your deployment credentials
and must be checked after redeployment; passing offline tests is not a production guarantee.

Official audio transcription documentation:
https://ai.google.dev/gemini-api/docs/audio