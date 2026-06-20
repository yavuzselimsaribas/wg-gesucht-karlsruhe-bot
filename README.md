# wg-gesucht Karlsruhe alert bot

Telegram alerts for new [wg-gesucht](https://www.wg-gesucht.de) listings in
Karlsruhe under €500. Runs free, 24/7, on GitHub Actions. Each new listing arrives
as a card: all photos, cost breakdown, and a Google Maps link.

## Setup (one time)

1. **Create a Telegram bot** — message [@BotFather](https://t.me/BotFather) →
   `/newbot` → copy the token. Then open your new bot and send it any message.
2. **Connect** — from this folder:
   ```bash
   ./setup_telegram.sh <BOT_TOKEN>
   ```
   It finds your chat id, stores the secrets, and goes live.

## Handy commands

```bash
gh workflow run hunt.yml -f test=true        # preview a card now
./add_receivers.sh <BOT_TOKEN>                # add you + friends (each must message the bot first)
gh workflow disable "wg-gesucht hunt"         # pause
gh workflow enable  "wg-gesucht hunt"         # resume
```

Edit the search or price in [`config.yaml`](config.yaml), then commit + push.
