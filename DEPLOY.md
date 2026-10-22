# Deploy the bot 24/7 (Railway or Render)

Cloud hosts have no keyboard to type a login code, and they wipe local files on
every restart. So you need two things first: a **login string** and a **free database**.

## Step 1 – Make the login string (on your own computer, once)
```
pip install -r requirements.txt
python login_session.py
```
Enter API_ID, API_HASH, then the phone number and code of the **userbot account**
(the extra account that reads channels and places calls). Copy the long text it
prints. This is your `USER_SESSION`. Keep it secret.

## Step 2 – Get a free PostgreSQL database
Pick one and copy its connection URL (starts with `postgresql://`). This is your `DATABASE_URL`.
- **Neon** (neon.tech): free project → Connection string.
- **Supabase** (supabase.com): free project → Connect → "Session pooler" URI.
- **Railway**: in your project, click **+ New → Database → PostgreSQL** (it fills `DATABASE_URL` for you).

Tables are created automatically on first start.

## Step 3 – Put the code on GitHub
Create a **private** repo and upload all files in this folder (`.gitignore` keeps secrets out).

## Option A – Railway (simplest, always on)
1. railway.com → New Project → Deploy from GitHub repo → pick the repo.
   It uses the `Dockerfile` and `railway.json` automatically.
2. (Optional) + New → Database → PostgreSQL, then in the bot service → Variables →
   add `DATABASE_URL` = `${{Postgres.DATABASE_URL}}`.
3. Variables → add `API_ID`, `API_HASH`, `BOT_TOKEN`, `USER_SESSION` (and `DATABASE_URL` if from Neon/Supabase).
   For voice-message transcription add `GROQ_API_KEY` (https://console.groq.com/keys);
   for AI voice on priority calls add `GEMINI_API_KEY`.
4. Deploy. Check **Deployments → Logs** for `Userbot: … | Bot: @…`.

Note: Railway gives trial credit, then about $5/month on the Hobby plan. A bot like this uses very little.

## Option B – Render (free plan)
1. render.com → New → **Blueprint** → pick the repo. It reads `render.yaml`.
2. Fill in the env values it asks for (`API_ID`, `API_HASH`, `BOT_TOKEN`, `USER_SESSION`, `DATABASE_URL`).
3. Deploy, then check **Logs**.
4. Set **Settings → Health Check Path** to `/healthz` for an existing service.
   The server listens on Render's `PORT`, and answers `GET /healthz` and `GET /`
   with HTTP 200 and `OK`, without contacting Telegram or the database.
5. Best-effort self-pings run asynchronously every **5 minutes**, using
   `RENDER_EXTERNAL_URL` automatically. Override the base URL with `KEEP_ALIVE_URL`,
   the interval with `KEEP_ALIVE_INTERVAL` (30–600 seconds), or disable them with
   `KEEP_ALIVE_ENABLED=false`. Each request has a 10-second total timeout, and
   failures don't stop the bot. No new credentials are needed.
6. **For reliable 24/7 operation, upgrade the Render service to paid always-on
   compute.** Free services can sleep after 15 minutes without inbound traffic;
   a self-ping cannot run after the process sleeps, health checks do not prevent
   free-tier sleep, and free-tier limits/restarts still apply. An external monitor
   may help with idle traffic but is not an uptime guarantee. See
   https://render.com/docs/free and https://render.com/docs/faq.

Database connections retain TCP keepalives and now retry connection establishment
up to three times. Stale connections are checked before writes and restored;
reads may be retried once. A write whose acknowledgement was lost is NOT blindly
replayed (avoids duplicate statistics or double priority toggles). A persistent
database outage still makes that operation fail; later operations can reconnect.
Health checks deliberately do not claim that Telegram or the database is ready.

Paid Render users can instead change `type: web` to `type: worker` in `render.yaml` and remove `healthCheckPath`.

## Other hosts
- **Koyeb / Fly.io / any VPS**: use the `Dockerfile`. Set the same env variables.
- **Heroku-style hosts**: use the `Procfile` (`worker: python bot.py`).

## Run only ONE copy
Don't run the bot on your computer and the server at the same time with the same
`USER_SESSION`. Telegram may log the session out. If that happens, run
`login_session.py` again and update `USER_SESSION`.

## Troubleshooting
- `USER_SESSION is invalid or expired` → make a new one (Step 1).
- Bot doesn't reply → check `BOT_TOKEN`, and that only one copy is running.
- Settings lost after restart → `DATABASE_URL` isn't set.
