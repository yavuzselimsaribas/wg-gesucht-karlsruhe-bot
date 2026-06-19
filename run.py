#!/usr/bin/env python3
"""
Custom runner for the wg-gesucht Karlsruhe bot.

Flathunter's own crawler + filters + "already seen" store do the hard part
(finding listings on the search pages and remembering which we've handled). For
each listing we're about to send, we call wg-gesucht's public JSON API
(`/api/public/offers/<id>`) to get the full photo gallery, the exact cost
breakdown and the address — then send a rich Telegram card ourselves.

Modes (env):
  TEST=true  -> send ONE sample listing (preview), without touching dedup.
  PRIME=true -> record current listings as seen, send nothing.
  otherwise  -> LIVE: send each new listing, mark seen after delivery.
                (No credentials -> behaves like PRIME, silently.)
"""

import argparse
import html
import json
import os
import re
import socket
import time
import urllib.parse

import requests

from flathunter.config import Config
from flathunter.idmaintainer import IdMaintainer
from flathunter.hunter import Hunter
from flathunter.filter import Filter
from flathunter.logging import logger, configure_logging

# wg-gesucht sometimes issues requests without a timeout; a default socket timeout
# stops a throttled connection from hanging until the job's 15-minute limit.
socket.setdefaulttimeout(30)

USER_AGENT = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
              "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36")
IMG_BASE = "https://img.wg-gesucht.de/"
MAX_PHOTOS = 10  # Telegram allows up to 10 media per group


def parse_args():
    parser = argparse.ArgumentParser(description="wg-gesucht -> Telegram alert bot")
    parser.add_argument("-c", "--config", default="config.yaml")
    return parser.parse_args()


def to_int(value):
    """'280' / '1.250 €' -> int; None / '' / '0' handled (0 returns 0)."""
    if value is None:
        return None
    digits = re.sub(r"[^\d]", "", str(value))
    return int(digits) if digits != "" else None


def esc(text):
    return html.escape(str(text).strip())


def clean_title(title):
    title = (title or "").strip()
    # Flathunter appends " ab dem <date>" / " vom <date> bis <date>"; we show the
    # date on its own row, so drop the suffix to avoid duplication.
    title = re.sub(r"\s+(ab dem\s+\d.*|vom\s+\d.*)$", "", title)
    return title or "wg-gesucht listing"


def fetch_offer_api(offer_id):
    """wg-gesucht public offer API -> dict (gallery, costs, address). {} on error."""
    url = f"https://www.wg-gesucht.de/api/public/offers/{offer_id}"
    try:
        resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=25)
        if resp.status_code != 200:
            logger.warning("Offer API %s returned %s", offer_id, resp.status_code)
            return {}
        return resp.json()
    except Exception as exc:  # network / JSON — degrade gracefully
        logger.warning("Offer API failed for %s: %s", offer_id, exc)
        return {}


def enrich(expose):
    """Pull gallery, cost breakdown and address from the public API into `expose`."""
    api = fetch_offer_api(expose.get("id"))
    if not api:
        return expose

    images = []
    for img in (api.get("images") or []):
        sized = img.get("sized")
        if sized:
            images.append(IMG_BASE + sized.lstrip("/"))
    expose["images"] = images[:MAX_PHOTOS]

    expose["total"] = to_int(api.get("total_costs"))
    expose["kalt"] = to_int(api.get("rent_costs"))
    expose["nk"] = to_int(api.get("utility_costs"))
    expose["sonstige"] = to_int(api.get("other_costs"))
    expose["kaution"] = to_int(api.get("bond_costs"))
    expose["ablose"] = to_int(api.get("equipment_costs"))
    expose["size_m2"] = to_int(api.get("property_size"))
    expose["rooms_n"] = to_int(api.get("number_of_rooms"))

    frei = (api.get("available_from_date") or "").strip()
    expose["frei_ab"] = frei if frei and frei != "00.00.0000" else None

    street = (api.get("street") or "").strip()
    plz_city = " ".join(x for x in [(api.get("postcode") or "").strip(),
                                    (api.get("city_name") or "").strip()] if x)
    district = (api.get("district_custom") or "").strip()
    address_bits = [b for b in [street, plz_city, district] if b]
    expose["address"] = ", ".join(dict.fromkeys(address_bits))  # de-dup, keep order

    try:
        lat, lng = float(api.get("geo_latitude")), float(api.get("geo_longitude"))
    except (TypeError, ValueError):
        lat = lng = 0.0
    query = f"{lat},{lng}" if (lat and lng) else (expose["address"] or f"{street} Karlsruhe")
    expose["maps_url"] = ("https://www.google.com/maps/search/?api=1&query="
                          + urllib.parse.quote(query))
    return expose


def build_caption(expose):
    """Vertical Telegram HTML card — one fact per row, empty fields omitted."""
    lines = [f"🏠 <b>{esc(clean_title(expose.get('title')))}</b>"]

    address = expose.get("address")
    if address and expose.get("maps_url"):
        lines.append(f'📍 <a href="{esc(expose["maps_url"])}">{esc(address)}</a>')
    elif address:
        lines.append(f"📍 {esc(address)}")

    lines.append("")
    total = expose.get("total")
    if total:
        lines.append(f"💰 <b>Gesamt: {total} €</b> / Monat")
    elif (expose.get("price") or "").strip():
        lines.append(f"💰 <b>{esc(expose['price'])}</b>")

    for emoji, label, key in (
        ("🔑", "Kaltmiete", "kalt"),
        ("💡", "Nebenkosten", "nk"),
        ("➕", "Sonstige Kosten", "sonstige"),
        ("🔒", "Kaution", "kaution"),
        ("🔁", "Ablöse", "ablose"),
    ):
        val = expose.get(key)
        if val:  # omit None and 0
            lines.append(f"{emoji} {label}: {val} €")

    lines.append("")
    if expose.get("size_m2"):
        lines.append(f"📐 {expose['size_m2']} m²")
    if expose.get("rooms_n"):
        lines.append(f"🚪 {expose['rooms_n']} Zimmer")
    if expose.get("frei_ab"):
        lines.append(f"📅 frei ab {esc(expose['frei_ab'])}")

    lines.append("")
    lines.append(f'🔗 <a href="{esc(expose.get("url", ""))}">Auf wg-gesucht ansehen →</a>')
    return "\n".join(lines)[:1024]  # Telegram caption limit


def _post(base, method, payload):
    resp = requests.post(f"{base}/{method}", data=payload, timeout=30)
    if resp.status_code == 429:
        retry_after = resp.json().get("parameters", {}).get("retry_after", 5)
        time.sleep(min(int(retry_after), 30))
        resp = requests.post(f"{base}/{method}", data=payload, timeout=30)
    return resp


def send_listing(token, chat_id, expose):
    """Send the listing: media group (all photos), or single photo, or text."""
    caption = build_caption(expose)
    images = [u for u in (expose.get("images") or []) if u][:MAX_PHOTOS]
    base = f"https://api.telegram.org/bot{token}"

    try:
        if len(images) >= 2:
            media = [{"type": "photo", "media": u} for u in images]
            media[0]["caption"] = caption
            media[0]["parse_mode"] = "HTML"
            resp = _post(base, "sendMediaGroup", {"chat_id": chat_id, "media": json.dumps(media)})
            if resp.status_code == 200:
                return True
            logger.warning("sendMediaGroup failed (%s): %s — trying single photo",
                           resp.status_code, resp.text[:200])

        if images:
            resp = _post(base, "sendPhoto", {
                "chat_id": chat_id, "photo": images[0],
                "caption": caption, "parse_mode": "HTML",
            })
            if resp.status_code == 200:
                return True
            logger.warning("sendPhoto failed (%s): %s — falling back to text",
                           resp.status_code, resp.text[:200])

        resp = _post(base, "sendMessage", {
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
    max_price = (config.get("filters") or {}).get("max_price")
    test = os.environ.get("TEST", "").lower() == "true"
    prime = (not test) and (
        (os.environ.get("PRIME", "").lower() == "true") or not token or not receivers
    )

    hunter = Hunter(config, id_watch)
    flat_filter = Filter.builder().read_config(config).build()
    exposes = flat_filter.filter(hunter.crawl_for_exposes())

    # TEST: preview one sample (prefer already-seen) without touching dedup.
    if test:
        if not token or not receivers:
            logger.error("TEST mode needs Telegram credentials (bot token + receiver id).")
            return
        candidates = []
        for expose in exposes:
            candidates.append(expose)
            if len(candidates) >= 15:
                break
        if not candidates:
            logger.warning("No listings under the price filter to sample.")
            return
        for cand in candidates:
            enrich(cand)
        # Prefer already-seen listings (so a preview can't double-send a real alert
        # later); among those, pick the one with the most photos for a fuller demo.
        seen = [c for c in candidates if id_watch.is_processed(c.get("id"))]
        sample = max(seen or candidates, key=lambda e: len(e.get("images") or []))
        delivered = all(send_listing(token, c, sample) for c in receivers)
        logger.info("TEST done — '%s' (%d photos) delivered=%s",
                    sample.get("title"), len(sample.get("images") or []), delivered)
        return

    logger.info("SILENT mode — recording only." if prime
                else "LIVE mode — sending to %d receiver(s)." % len(receivers))

    new_count = sent_count = 0
    for expose in exposes:
        expose_id = expose.get("id")
        if expose_id is None or id_watch.is_processed(expose_id):
            continue

        if prime:
            new_count += 1
            id_watch.mark_processed(expose_id)
            continue

        enrich(expose)
        total = expose.get("total")
        if total and max_price and total > int(max_price):
            # Total rent (warm) over budget — skip, don't mark (re-check if it drops).
            logger.info("Skip over-budget: '%s' total %d€ > %s€",
                        expose.get("title"), total, max_price)
            continue

        new_count += 1
        delivered = all(send_listing(token, c, expose) for c in receivers)
        if delivered:
            id_watch.mark_processed(expose_id)
            sent_count += 1
        else:
            logger.warning("Delivery failed for '%s' — will retry next run.",
                           expose.get("title", expose_id))
        time.sleep(1)

    logger.info("Done. New: %d, sent: %d, mode: %s",
                new_count, sent_count, "silent" if prime else "live")


if __name__ == "__main__":
    main()
