# v17 — Render hosting reliability

- Dedicated aiohttp liveness listener on `0.0.0.0:$PORT`: `/healthz` and `/` return
  HTTP 200 `OK`; unknown paths return 404. No database or Telegram operations.
- Independent async self-ping every 300 seconds, with bounded network timeout,
  response size, connection count and cancellation. No audio worker threads used.
- PostgreSQL idle connections recover before writes; reads retry once. Connection
  establishment retries three times. Ambiguous writes are not duplicated.
- Existing keyword, voice, Gemini and phone-call behavior is unchanged.

## Redeploy
Install the updated requirements (adds `aiohttp`), deploy this folder, and change
the existing Render service's Health Check Path to `/healthz`. Render supplies
`PORT` and `RENDER_EXTERNAL_URL`. Optional overrides are in `.env.example`.

**Self-pings cannot guarantee 24/7 uptime on Render Free.** They stop when the
process sleeps. Choose paid always-on compute for reliable operation. Health
checks are liveness probes, not proof of Telegram/database readiness.

Tests use a real local HTTP listener and mocked PostgreSQL disconnects; no live
Render deployment, Telegram session, or hosted database was available to verify.