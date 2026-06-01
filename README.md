# Canvas Utrecht Availability Monitor

Polls https://www.canvasutrecht.com/floorplans.aspx and sends a push
notification to your phone as soon as a listing appears.

How it works (in one paragraph): the floorplans page currently shows the
exact phrase _"at this moment there are no apartments available"_. When
Canvas/Greystar adds inventory, that phrase disappears and an
**Availability** button appears next to each listing. This script just
checks whether that phrase is still on the page; the moment it goes away,
you get an urgent push notification with a link straight to the listings.

## Recommended setup: hybrid (1-min local + 5-min cloud backup)

GitHub Actions can't actually do 1-minute scheduling — the platform's
hard minimum is once every 5 minutes, and scheduled runs are often
delayed 10–30 min under load. So we use a two-layer approach:

- **Local runner (every 60 s)** — `run_local.sh` on your laptop. True
  1-minute latency while your laptop is on and the terminal is open.
  This is the layer that matters most, because it covers the hours when
  you're awake and able to act on a notification immediately.
- **GitHub Actions (every 5 min)** — runs in the cloud 24/7 as backup,
  catching listings that go up overnight or when your laptop is closed.

You can run either layer alone; running both is best.

---

## Setup (15 minutes, one time)

### 1. Get a notification channel — pick one or both

**Option A: ntfy.sh (simplest, recommended)**

1. Install the **ntfy** app on your phone (iOS / Android, free).
2. In the app, tap **+ Subscribe to topic**.
3. Pick a long, unguessable topic name, e.g. `canvas-utrecht-aXk72p-jane`.
   (Anyone who knows the topic name can send you notifications, so don't
   share it. There's no account or password.)
4. You now have a topic. That string is your `NTFY_TOPIC`.

**Option B: Telegram bot (more setup, nicer messages)**

1. Open Telegram → message `@BotFather` → `/newbot` → follow prompts.
2. Save the **bot token** it gives you (`123456:ABC-DEF...`).
3. Send any message to your new bot from your phone.
4. Visit `https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates` in a
   browser; find `"chat":{"id":12345678,...}` in the JSON — that number
   is your **chat id**.

### 2. Put this code on GitHub

1. Create a new **public** repo on GitHub (e.g. `canvas-monitor`).
   - Public, because GitHub Actions is unlimited free for public repos.
     For private repos you'd hit the 2,000-min/month free tier in a few weeks.
2. Push the contents of this folder to it:
   ```bash
   git init
   git remote add origin git@github.com:<you>/canvas-monitor.git
   git add .
   git commit -m "initial"
   git branch -M main
   git push -u origin main
   ```

### 3. Add your secrets

In your repo on GitHub: **Settings → Secrets and variables → Actions →
New repository secret**. Add whichever you set up:

- `NTFY_TOPIC` — your ntfy topic string
- `TELEGRAM_BOT_TOKEN` — your bot token
- `TELEGRAM_CHAT_ID` — your chat id (just digits)

### 4. Enable Actions and test it

1. Go to the **Actions** tab in your repo. Click "I understand my
   workflows" if prompted.
2. Click **Canvas Utrecht Monitor** in the left sidebar.
3. Click **Run workflow** → set _"Send a test notification only"_ to
   `true` → **Run**.
4. Within ~30 seconds you should get a push notification on your phone.
   If you do — you're done. The schedule (`*/5 * * * *`) will now run
   automatically every five minutes.

### 5. (Recommended) Run the 1-minute local layer too

In a terminal on your laptop:

```bash
cd canvas-monitor
pip install -r requirements.txt
export NTFY_TOPIC=your-secret-topic-name        # same one as in GH
# and/or:
# export TELEGRAM_BOT_TOKEN=...
# export TELEGRAM_CHAT_ID=...
./run_local.sh
```

That's it. The script will print a status line every minute. Leave the
terminal window open (or run it inside `tmux` / `screen` if you know
those) and you'll get push notifications within 60 seconds of a listing
appearing — provided your laptop is awake.

To stop it: `Ctrl-C`. To change the interval to e.g. 30 seconds:
`INTERVAL_SECONDS=30 ./run_local.sh` (don't go below 30 — see caveats).

---

## Local testing

```bash
pip install -r requirements.txt
NTFY_TOPIC=canvas-utrecht-aXk72p-jane python canvas_monitor.py --test
NTFY_TOPIC=canvas-utrecht-aXk72p-jane python canvas_monitor.py
```

## What you can tweak

- **Polling frequency** — `cron` line in `.github/workflows/monitor.yml`.
  GitHub allows down to every 5 minutes; in practice scheduled runs are
  sometimes delayed 10–30+ minutes under load, so don't expect strict
  10-min latency. Don't go below 5 min — it stresses the site and
  GitHub will throttle you anyway.
- **Detection phrase** — `SOLD_OUT_PHRASE` in `canvas_monitor.py`. If
  Canvas changes the wording, update it here. Use a `--test` run plus a
  quick visit to the page to confirm.
- **Add another site** — duplicate the workflow and script with a
  different `URL` and `SOLD_OUT_PHRASE` to monitor SSH, Kamernet, etc.

## A few honest caveats

- **GitHub cron is not reliable to the minute.** This is why the local
  runner exists. Hot listings can be gone in under 5 minutes — treat
  the local runner as the primary and GH Actions as a safety net.
- **At 1 req/min, scraping intensity matters more.** That's ~1,440
  requests/day vs the ~280 the cloud layer alone does. Still small in
  absolute terms (about the same as keeping a tab open and hitting F5
  by hand once a minute), but the WAF in front of the site _may_ start
  rate-limiting your IP if it spots a clear pattern. If the local
  runner suddenly stops getting useful responses, that's the most
  likely cause — pause it for an hour and it usually clears.
- **Don't drop INTERVAL_SECONDS below 30.** Past that you're definitely
  going to get rate-limited, and the marginal benefit (~30 s faster
  notification) isn't worth the risk of being blocked entirely and
  missing a listing.
- **The script only watches the floorplans page.** If Canvas ever
  changes URL structure, you'll need to update `URL` in
  `canvas_monitor.py`.
- **Don't share your ntfy topic publicly.** Anyone who knows it can
  send you notifications.
