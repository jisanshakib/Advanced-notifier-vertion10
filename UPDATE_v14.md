# v14 — Voice system review & fixes

Reviewed the full voice workflow against the requirement checklist. Two real gaps found and fixed:

1. Priority keyword in a voice message now triggers the live call from ANY watched channel
   (previously the call only fired if the channel was also marked as a Priority Channel).
   Forwarding behaviour is unchanged; text-post behaviour is unchanged.
2. Voice files over 25 MB (Groq Whisper's upload limit) are skipped before download,
   instead of being sent to Groq and rejected.

Also fixed: startup log said "no GEMINI_API_KEY" when it meant GROQ_API_KEY, and a stale
"Gemini" comment on the transcription timeout.

New: test_voice_workflow.py covers the whole voice path offline —
priority keyword in caption (not transcript) still matches and calls; normal keyword in
transcript forwards without a call; no match skips; >25 MB skips; >3 min skips;
Groq failure skips without crashing.

No changes to live calls (still Gemini), text alerts, keywords, channels, summary or buttons.
