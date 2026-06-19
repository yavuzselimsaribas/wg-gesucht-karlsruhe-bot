#!/usr/bin/env python3
"""
Custom runner for the wg-gesucht Karlsruhe bot.

Why not Flathunter's own `flathunt.py`?
  - Its Telegram notifier sends plain text and, for wg-gesucht, attaches no image.
  - We want a rich card: the listing photo + a formatted caption that includes the
    full cost breakdown (Kaltmiete, Nebenkosten, Kaution, ...).

So we reuse the parts of Flathunter that are hard (its wg-gesucht list crawler, the
price filter, and the SQLite "already seen" store) and, for each listing we're about
to send, fetch the listing's detail page ourselves to pull the cost breakdown +
photo + address. Telegram sending is also done here.

Modes (set via env):
  - TEST=true  : send ONE sample listing (preview), without touching dedup.
  - PRIME=true : record current listings as seen, send nothing.
  - otherwise  : LIVE — send each new listing, mark it seen after delivery.
                 (No credentials -> behaves like PRIME, silently.)
"""

import argparse
import html
import os
import re
import socket
import time

import requests
from bs4 import BeautifulSoup

from flathunter.config import Config
from flathunter.idmaintainer import IdMaintainer
from flathunter.hunter import Hunter
from flathunter.filter import Filter
from flathunter.logging import logger, configure_logging

# wg-gesucht's crawler issues some requests without a timeout. If the site throttles
# the runner's IP, that connection can hang indefinitely (stalling until the job's
# 15-minute limit). A default socket timeout bounds every request so a hung crawl
# fails fast and is simply retried on the next run.
socket.setdefaulttimeout(30)

USER_AGENT = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
              "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36")


def parse_args():
    parser = argparse.ArgumentParser(description="wg-gesucht -> Telegram alert bot")
    parser.add_argument("-c", "--config", default="config.yaml", help="path to config.yaml")
    return parser.parse_args()


def clean_image_url(url):
    """Normalise an image URL."""
    if not url:
        return None
    url = str(url).strip().strip('"').strip("'")
    if url.startswith("//"):
        url = "https:" + url
    return url if url.startswith("http") else None


def euro_int(value):
    """'280€' / '1.250 €' -> int; 'n.a.' / '' -> None."""
    if not value:
        return None
    digits = re.sub(r"[^\d]", "", str(value))
    return int(digits) if digits else None


def fetch_details(url):
    """Fetch a listing's detail page and extract cost breakdown, photo and address.

    Returns a dict (possibly partial). Never raises — on any error returns {}.
    """
    try:
        resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=25)
        if resp.status_code != 200:
            logger.warning("Detail page %s returned %s", url, resp.status_code)
            return {}
        soup = BeautifulSoup(resp.text, "html.parser")
    except Exception as exc:  # network / parse — degrade gracefully
        logger.warning("Could not fetch details for %s: %s", url, exc)
        return {}

    details = {}

    og_image = soup.find("meta", attrs={"property": "og:image"})
    if og_image and og_image.get("content"):
        details["image"] = og_image["content"]

    # Cost rows are <div class="row"> containing a .section_panel_detail (label)
    # and a .section_panel_value (amount). Pair them by their shared row.
    costs = {}
    for row in soup.select(".row"):
        label_el = row.select_one(".section_panel_detail")
        value_el = row.select_one(".section_panel_value")
        if label_el and value_el:
            key = " ".join(label_el.get_text().split()).rstrip(":").strip()
            val = " ".join(value_el.get_text().split()).strip()
            if key:
                costs[key] = val
    details["costs"] = costs

    address_el = soup.select_one('a[href="#mapContainer"]')
    if address_el:
        details["address"] = " ".join(address_el.get_text().split())

    return details


def enrich(expose):
    """Merge detail-page data (photo / costs / address) into an expose."""
    details = fetch_details(expose.get("url", ""))
    if details.get("image"):
        expose["image"] = details["image"]  # og:image beats the card thumbnail
    expose["costs"] = details.get("costs", {})
    if details.get("address"):
        expose["address_clean"] = details["address"]
    return expose


def esc(text):
    return html.escape(str(text).strip())


def build_caption(expose):
    """Build a Telegram HTML caption. Only includes fields that actually exist."""
    costs = expose.get("costs", {})
    kalt = euro_int(costs.get("Miete"))
    nk = euro_int(costs.get("Nebenkosten"))
    sonstige = euro_int(costs.get("Sonstige Kosten"))
    kaution = euro_int(costs.get("Kaution"))
    ablose = euro_int(costs.get("Ablösevereinbarung"))
    frei_ab = costs.get("frei ab")

    lines = [f"🏠 <b>{esc(expose.get('title') or 'wg-gesucht listing')}</b>"]

    address = expose.get("address_clean")
    if address:
        lines.append(f"📍 {esc(address)}")

    lines.append("")

    # Headline price = what wg-gesucht shows (and what the <500 filter checks).
    price = (expose.get("price") or "").strip()
    if price:
        lines.append(f"💶 <b>{esc(price)}</b> total")

    breakdown = []
    if kalt is not None:
        breakdown.append(f"Kalt {kalt} €")
    if nk is not None:
        breakdown.append(f"Nebenkosten {nk} €")
    if sonstige is not None:
        breakdown.append(f"Sonstige {sonstige} €")
    if breakdown:
        lines.append("   " + "  +  ".join(breakdown))

    extras = []
    if kaution is not None:
        extras.append(f"Kaution {kaution} €")
    if ablose is not None:
        extras.append(f"Ablöse {ablose} €")
    if extras:
        lines.append("   " + "  ·  ".join(extras))

    facts = []
    size = (expose.get("size") or "").strip()
    if size:
        facts.append(f"📐 {esc(size)}")
    rooms = (expose.get("rooms") or "").strip()
    if rooms and rooms not in ("?", "0"):
        facts.append(f"🚪 {esc(rooms)} Zi.")
    if frei_ab:
        facts.append(f"📅 ab {esc(frei_ab)}")
    if facts:
        lines.append("  ·  ".join(facts))

    lines.append("")
    lines.append(f'🔗 <a href="{esc(expose.get("url", ""))}">Open on wg-gesucht →</a>')
    return "\n".join(lines)


def send_listing(token, chat_id, expose):
    """Send one listing as a photo (with caption) or text. Returns True on success."""
    caption = build_caption(expose)
    image = clean_image_url(expose.get("image"))
    base = f"https://api.telegram.org/bot{token}"

    def post(method, payload):
        resp = requests.post(f"{base}/{method}", data=payload, timeout=30)
        if resp.status_code == 429:
            retry_after = resp.json().get("parameters", {}).get("retry_after", 5)
            time.sleep(min(int(retry_after), 30))
            resp = requests.post(f"{base}/{method}", data=payload, timeout=30)
        return resp

    try:
        if image:
            resp = post("sendPhoto", {
                "chat_id": chat_id, "photo": image,
                "caption": caption, "parse_mode": "HTML",
            })
            if resp.status_code == 200:
                return True
            logger.warning("sendPhoto failed (%s): %s — falling back to text",
                           resp.status_code, resp.text[:200])

        resp = post("sendMessage", {
            "chat_id": chat_id, "text": caption, "parse_mode": "HTML",
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

    id_watch = IdMaintainer(f"{config.database_location()}/processed_ids.db")
    token = config.telegram_bot_token()
    receivers = config.telegram_receiver_ids() or []
    test = os.environ.get("TEST", "").lower() == "true"
    prime = (not test) and (
        (os.environ.get("PRIME", "").lower() == "true") or not token or not receivers
    )

    hunter = Hunter(config, id_watch)
    # Configured filters (max_price, ...) but NOT "already seen" — we dedup ourselves
    # so a listing is marked seen only after it has been delivered.
    flat_filter = Filter.builder().read_config(config).build()
    exposes = flat_filter.filter(hunter.crawl_for_exposes())

    # TEST: preview one sample (prefer an already-seen one) without touching dedup.
    if test:
        if not token or not receivers:
            logger.error("TEST mode needs Telegram credentials (bot token + receiver id).")
            return
        sample = None
        fallback = None
        for expose in exposes:
            if fallback is None:
                fallback = expose
            if id_watch.is_processed(expose.get("id")):
                sample = expose
                break
        sample = sample or fallback
        if sample is None:
            logger.warning("No current listings under the price filter to sample.")
            return
        enrich(sample)
        delivered = all(send_listing(token, chat_id, sample) for chat_id in receivers)
        logger.info("TEST done — sample '%s' delivered=%s", sample.get("title"), delivered)
        return

    if prime:
        logger.info("SILENT mode (prime run, or no credentials) — recording listings, sending nothing.")
    else:
        logger.info("LIVE mode — sending to %d receiver(s).", len(receivers))

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

        enrich(expose)
        delivered = all(send_listing(token, chat_id, expose) for chat_id in receivers)
        if delivered:
            id_watch.mark_processed(expose_id)
            sent_count += 1
        else:
            logger.warning("Delivery failed for '%s' — will retry next run.",
                           expose.get("title", expose_id))
        time.sleep(1)  # gentle pacing for Telegram flood limits

    logger.info("Done. New listings: %d, messages sent: %d, mode: %s",
                new_count, sent_count, "silent" if prime else "live")


if __name__ == "__main__":
    main()
