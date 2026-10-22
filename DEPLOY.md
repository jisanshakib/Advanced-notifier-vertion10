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
4. **Keep it awake:** The bot now pings its own Render URL every 10 minutes
   automatically (it uses the `RENDER_EXTERNAL_URL` variable Render sets for you —
   no setup needed). Free Render services sleep after 15 minutes without *external*
   visits, so also create a free monitor at uptimerobot.com (or cron-job.org)
   that opens your Render URL (e.g. `https://telegram-alert-bot.onrender.com`) every 5 minutes.

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
