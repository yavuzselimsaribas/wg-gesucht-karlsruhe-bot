# wg-gesucht Karlsruhe alert bot

Telegram alerts for **new** rental listings in Karlsruhe on
[wg-gesucht.de](https://www.wg-gesucht.de), under €500. Runs 24/7 for free on
GitHub Actions using [Flathunter](https://github.com/flathunters/flathunter).

## What it watches

| Category | URL |
|----------|-----|
| WG rooms | `wg-zimmer-in-Karlsruhe.68.0.1.0.html` |
| 1-room apartments | `1-zimmer-wohnungen-in-Karlsruhe.68.1.1.0.html` |
| Apartments | `wohnungen-in-Karlsruhe.68.2.1.0.html` |

Filter: `max_price: 500`. Edit [`config.yaml`](config.yaml) to change URLs,
price, size, rooms, or excluded titles — then commit and push.

## How it works

1. A scheduled GitHub Actions job ([`.github/workflows/hunt.yml`](.github/workflows/hunt.yml))
   runs roughly every 10 minutes.
2. It clones Flathunter, scrapes the URLs above, and filters by your criteria.
3. New listings (not seen before) are sent to your Telegram.
4. Seen listing IDs are stored in `processed_ids.db`, which is committed back to
   this repo so the bot remembers across runs. **Do not delete it** or you'll get
   duplicate alerts.

Only **new** offers are sent — there is no backfill of old listings. The first
run is a silent "prime" that records what's currently live without notifying.

The bot is already **primed** (the first scheduled runs silently record current
listings), so once you connect Telegram you only get alerts for genuinely *new*
offers — no backlog dump.

## Setup (one time)

### 1. Create the Telegram bot

1. In Telegram, message [@BotFather](https://t.me/BotFather) → `/newbot` → follow
   the prompts → copy the **bot token** (`123456:ABC-DEF...`).
2. Open a chat with your new bot and send it any message (e.g. `hi`). Required —
   a bot can't message you until you've messaged it first.

### 2. Connect it — one command

From this repo's directory:

```bash
./setup_telegram.sh <BOT_TOKEN>
```

That finds your chat id, sends a test message, stores both values as encrypted
GitHub Actions secrets, and triggers the first live run. Done.

### Manual alternative

```bash
# chat id: message @userinfobot, or:
curl "https://api.telegram.org/bot<TOKEN>/getUpdates"   # read result[].message.chat.id

gh secret set TELEGRAM_BOT_TOKEN      # paste the bot token
gh secret set TELEGRAM_RECEIVER_IDS   # paste your numeric chat id
gh workflow run hunt.yml              # go live now (or wait for the cron)
```

## Controlling it

- **Pause:** disable the workflow — `gh workflow disable "wg-gesucht hunt"`.
- **Resume:** `gh workflow enable "wg-gesucht hunt"`.
- **Run once now:** `gh workflow run hunt.yml`.
- **Reset memory (re-alert everything):** delete `processed_ids.db`, commit, push.

## Notes / caveats

- **Cost:** free. Public repo ⇒ unlimited GitHub Actions minutes. No secrets are
  exposed — the token and chat id live in encrypted Actions secrets, not the code.
- **Timing:** GitHub delays scheduled jobs under load; expect every ~10–20 min,
  not exact.
- **Price basis:** `max_price` compares against the rent Flathunter parses from
  the listing (usually total/warm). A few edge cases may slip through — adjust the
  filter if needed.
- **Inactivity:** GitHub auto-disables schedules after 60 days with no repo
  commits. The state commits keep it alive as long as listings keep appearing.
- **If scraping breaks:** wg-gesucht occasionally changes its markup. The workflow
  tracks Flathunter's latest `main`, so upstream fixes flow in automatically.
