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

## Setup (one time)

### 1. Telegram bot

1. In Telegram, message [@BotFather](https://t.me/BotFather) → `/newbot` → follow
   prompts → copy the **bot token** (`123456:ABC-DEF...`).
2. Open a chat with your new bot and send it any message (e.g. `hi`). Required —
   the bot can't message you until you've started the conversation.
3. Get your numeric **chat id**: message [@userinfobot](https://t.me/userinfobot),
   it replies with your `Id`. (Alternatively: `curl https://api.telegram.org/bot<TOKEN>/getUpdates`
   and read `message.chat.id`.)

### 2. GitHub secrets

Store the credentials as encrypted Actions secrets (never in the code):

```bash
gh secret set TELEGRAM_BOT_TOKEN     # paste the bot token
gh secret set TELEGRAM_RECEIVER_IDS  # paste your numeric chat id
```

### 3. Prime, then go live

```bash
# Silent first run — record current listings, send nothing:
gh workflow run hunt.yml -f prime=true

# After that completes, the scheduled runs alert you on genuinely new listings.
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
