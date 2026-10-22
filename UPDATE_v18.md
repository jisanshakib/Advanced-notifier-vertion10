# v18 — final reliability review

- Terminal Telegram disconnects now raise an error instead of silently exiting
  successfully. Render can detect the process failure and restart the service.
- Startup failures after the HTTP listener starts now run the same cleanup as
  normal shutdown. Voice cleanup is bounded to 15 seconds; failures do not prevent
  client disconnects and database cleanup.
- Added lifecycle regression tests; reran the existing hosting, PostgreSQL fault,
  keyword, media, priority call, and voice tests.

## Required Render configuration

For non-sleeping operation, select a **paid instance** in the existing service's
settings. The included Blueprint still explicitly uses `plan: free`: creating a
new service from it does **not** buy always-on compute. Set Health Check Path to
`/healthz`, and use the included Dockerfile (or `python bot.py` as start command).
Render supplies `PORT` and `RENDER_EXTERNAL_URL`; pings default to 300 seconds.

Set API_ID, API_HASH, BOT_TOKEN, USER_SESSION and DATABASE_URL. Set GROQ_API_KEY
for transcription and GEMINI_API_KEY for AI call audio. Run one bot instance only.

## Honest verification limits

Local regression tests and HTTP tests passed; database disconnect tests use mocks.
No live Render service, hosted database, Telegram account, Groq/Gemini call, or
phone call was available for end-to-end verification. No software can promise
flawless operation or eliminate provider outages, memory limits, or invalid keys.

Health checks prove the HTTP listener is alive, not that Telegram/database are
ready. Database queries and reconnect backoffs are synchronous and can temporarily
delay the event loop during outages. Repeated outages can drop an individual
alert: failed queued posts are not durably retried, and polling cursors/queued work
are not persisted across restarts. Paid compute prevents idle sleep but does not
eliminate these delivery limitations.

Additional audit findings: polling fetches only 50 messages per channel without
pagination, and newer live messages can move the cursor past older missed posts.
The in-memory intake queue is unbounded. Blank/unsupported DATABASE_URL values
select ephemeral SQLite, so verify a valid PostgreSQL URL on Render. Startup
connection attempts can still wait through a termination request until Render
force-stops the process. These limits are not fixed by health checks or self-pings.

Before relying on it, confirm `/healthz` returns `OK`, send `/start`, trigger a
tracked text keyword and mixed-language voice alert, and test a priority call from
a priority channel. Review Render logs after a service restart and brief outage.