#!/usr/bin/env bash
#
# One-shot Telegram setup for the wg-gesucht bot.
#
# Prerequisites:
#   1. Create a bot: message @BotFather in Telegram -> /newbot -> copy the token.
#   2. Open a chat with your new bot and send it ANY message (e.g. "hi").
#      (Telegram won't let a bot message you until you've messaged it first.)
#
# Then run:   ./setup_telegram.sh <BOT_TOKEN>
#
# It finds your chat id, sends a test message, stores both values as encrypted
# GitHub Actions secrets, and triggers the first live run.

set -euo pipefail

TOKEN="${1:-}"
if [ -z "$TOKEN" ]; then
  echo "Usage: ./setup_telegram.sh <BOT_TOKEN>" >&2
  exit 1
fi

echo "==> Looking up your chat id from recent messages to the bot..."
CHAT_ID=$(curl -sS "https://api.telegram.org/bot${TOKEN}/getUpdates" | python3 -c '
import sys, json
data = json.load(sys.stdin)
if not data.get("ok"):
    sys.stderr.write("Telegram API error: %s\n" % data.get("description", "unknown"))
    sys.exit(2)
ids = [u["message"]["chat"]["id"] for u in data.get("result", []) if "message" in u]
print(ids[-1] if ids else "")
')

if [ -z "$CHAT_ID" ]; then
  echo "!! No chat id found." >&2
  echo "   Open Telegram, send your bot any message, then run this again." >&2
  exit 1
fi
echo "    Chat id: $CHAT_ID"

echo "==> Sending a test message..."
curl -sS "https://api.telegram.org/bot${TOKEN}/sendMessage" \
  -d chat_id="$CHAT_ID" \
  -d text="✅ wg-gesucht bot connected. New Karlsruhe listings under 500€ will land here." >/dev/null
echo "    Sent — check your Telegram."

echo "==> Storing encrypted GitHub secrets..."
printf '%s' "$TOKEN"   | gh secret set TELEGRAM_BOT_TOKEN
printf '%s' "$CHAT_ID" | gh secret set TELEGRAM_RECEIVER_IDS

echo "==> Un-pausing the schedule and triggering the first live run..."
gh workflow enable hunt.yml >/dev/null 2>&1 || true
gh workflow run hunt.yml

echo ""
echo "Done. The bot is live and will alert you on new listings (~every 10-20 min)."
