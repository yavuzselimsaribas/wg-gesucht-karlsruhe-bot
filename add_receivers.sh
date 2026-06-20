#!/usr/bin/env bash
#
# Add recipients to the bot's alert list (you + friends).
#
# First: every person who wants alerts must OPEN the bot in Telegram and send it
# any message (e.g. "hi"). A bot cannot message someone who hasn't messaged it.
#
# Then run:   ./add_receivers.sh <BOT_TOKEN>
#
# It reads everyone who recently messaged the bot, shows them, and sets the
# TELEGRAM_RECEIVER_IDS secret to that list. The next run alerts all of them.

set -euo pipefail

TOKEN="${1:-}"
if [ -z "$TOKEN" ]; then
  echo "Usage: ./add_receivers.sh <BOT_TOKEN>" >&2
  exit 1
fi

TOKEN="$TOKEN" python3 <<'PY'
import os, json, urllib.request, subprocess

token = os.environ["TOKEN"]
url = f"https://api.telegram.org/bot{token}/getUpdates"
data = json.load(urllib.request.urlopen(url, timeout=20))
if not data.get("ok"):
    raise SystemExit("Telegram error: %s" % data.get("description"))

chats = {}
for upd in data.get("result", []):
    container = upd.get("message") or upd.get("my_chat_member") or {}
    chat = container.get("chat") or {}
    cid = chat.get("id")
    if cid is not None:
        label = chat.get("first_name") or chat.get("title") or chat.get("username") or ""
        chats[str(cid)] = label

if not chats:
    raise SystemExit("No chats found. Make sure each person OPENED the bot and "
                     "sent it a message in the last 24h, then run this again.")

print("Recipients found:")
for cid, label in chats.items():
    print(f"  {cid}  {label}")

ids = ",".join(chats.keys())
subprocess.run(["gh", "secret", "set", "TELEGRAM_RECEIVER_IDS"],
               input=ids, text=True, check=True)
print(f"\nSet TELEGRAM_RECEIVER_IDS = {ids}")
print("Done. Everyone listed above gets alerts from the next run.")
print("Preview now with:  gh workflow run hunt.yml -f test=true")
PY
