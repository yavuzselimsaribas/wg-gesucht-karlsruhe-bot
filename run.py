#!/usr/bin/env python3
"""
Custom runner for the wg-gesucht Karlsruhe bot.

Why not Flathunter's own `flathunt.py`?
  - Flathunter's Telegram notifier sends plain text and, for wg-gesucht, sends no
    image (the crawler stores a single `image` but the notifier reads `images`).
  - We want a rich card: the listing photo + a nicely formatted caption with a
    clickable link.

So we reuse the parts of Flathunter that are hard (its battle-tested wg-gesucht
crawler, the price filter, and the SQLite "already seen" store) and do the
Telegram sending ourselves.

Modes:
  - LIVE  : Telegram credentials present (and PRIME != "true") -> send a message
            per new listing, then mark it seen.
  - SILENT: no credentials, or PRIME="true" -> just mark current listings seen,
            send nothing (used for the first "prime" run).

Credentials + DB location are read from the same FLATHUNTER_* environment
variables Flathunter uses, via its Config object.
"""

import argparse
import html
import os
import sys
import time

import requests

from flathunter.config import Config
from flathunter.idmaintainer import IdMaintainer
from flathunter.hunter import Hunter
from flathunter.filter import Filter
from flathunter.logging import logger, configure_logging


def parse_args():
    parser = argparse.ArgumentParser(description="wg-gesucht -> Telegram alert bot")
    parser.add_argument("-c", "--config", default="config.yaml", help="path to config.yaml")
    return parser.parse_args()


def clean_image_url(url):
    """Normalise the background-image URL pulled from a listing card."""
    if not url:
        return None
    url = str(url).strip().strip('"').strip("'")
    if url.startswith("//"):
        url = "https:" + url
    if not url.startswith("http"):
        return None
    return url


def build_caption(expose):
    """Build a Telegram HTML caption for a listing."""
    def field(key, fallback="—"):
        value = str(expose.get(key, "") or "").strip()
        return html.escape(value) if value else fallback

    title = field("title", "wg-gesucht listing")
    price = field("price", "n/a")
    size = field("size", "n/a")
    rooms = field("rooms", "?")
    url = html.escape(str(expose.get("url", "")).strip())

    return (
        f"🏠 <b>{title}</b>\n\n"
        f"💶 <b>{price}</b>\n"
        f"📐 {size}   ·   🚪 {rooms} Zimmer\n\n"
        f'🔗 <a href="{url}">Open on wg-gesucht →</a>'
    )


def send_listing(token, chat_id, expose):
    """Send one listing as a photo (with caption) or text. Returns True on success."""
    caption = build_caption(expose)
    image = clean_image_url(expose.get("image"))
    base = f"https://api.telegram.org/bot{token}"

    def post(method, payload):
        resp = requests.post(f"{base}/{method}", data=payload, timeout=30)
        if resp.status_code == 429:  # rate limited - back off once and retry
            retry_after = resp.json().get("parameters", {}).get("retry_after", 5)
            time.sleep(min(int(retry_after), 30))
            resp = requests.post(f"{base}/{method}", data=payload, timeout=30)
        return resp

    try:
        if image:
            resp = post("sendPhoto", {
                "chat_id": chat_id,
                "photo": image,
                "caption": caption,
                "parse_mode": "HTML",
            })
            if resp.status_code == 200:
                return True
            logger.warning("sendPhoto failed (%s): %s — falling back to text",
                           resp.status_code, resp.text[:200])

        resp = post("sendMessage", {
            "chat_id": chat_id,
            "text": caption,
            "parse_mode": "HTML",
            "disable_web_page_preview": "false",
        })
        if resp.status_code != 200:
            logger.error("sendMessage failed (%s): %s", resp.status_code, resp.text[:200])
            return False
        return True
    except requests.RequestException as exc:
        logger.error("Telegram request error: %s", exc)
        return False


def main():
    args = parse_args()
    config = Config(args.config)
    configure_logging(config)
    config.init_searchers()

    db_location = config.database_location()
    id_watch = IdMaintainer(f"{db_location}/processed_ids.db")

    token = config.telegram_bot_token()
    receivers = config.telegram_receiver_ids() or []
    prime = (os.environ.get("PRIME", "").lower() == "true") or not token or not receivers

    if prime:
        logger.info("SILENT mode (prime run, or no credentials) — recording listings, sending nothing.")
    else:
        logger.info("LIVE mode — sending to %d receiver(s).", len(receivers))

    hunter = Hunter(config, id_watch)
    # Apply the configured filters (max_price, etc.) but NOT "already seen" — we do
    # our own dedup so we can mark a listing seen only after it was sent successfully.
    flat_filter = Filter.builder().read_config(config).build()
    exposes = flat_filter.process_exposes(hunter.crawl_for_exposes())

    new_count = 0
    sent_count = 0
    for expose in exposes:
        expose_id = expose.get("id")
        if expose_id is None or id_watch.is_processed(expose_id):
            continue
        new_count += 1

        if prime:
            id_watch.mark_processed(expose_id)
            continue

        delivered = all(send_listing(token, chat_id, expose) for chat_id in receivers)
        if delivered:
            id_watch.mark_processed(expose_id)
            sent_count += 1
        else:
            logger.warning("Delivery failed for '%s' — will retry next run.",
                           expose.get("title", expose_id))
        time.sleep(1)  # gentle pacing to stay under Telegram flood limits

    logger.info("Done. New listings: %d, messages sent: %d, mode: %s",
                new_count, sent_count, "silent" if prime else "live")


if __name__ == "__main__":
    main()
