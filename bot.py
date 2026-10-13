"""Post new tweets from RSS feeds to a Discord webhook as fixupx.com links.

Single pass per run; meant to be invoked on a schedule (GitHub Actions cron).
"""

import json
import logging
import os
import re
import sys
import time
from pathlib import Path

import feedparser
import requests
import yaml

ROOT = Path(__file__).parent
FEEDS_PATH = ROOT / "feeds.yaml"
SEEN_PATH = ROOT / "seen.json"

FETCH_TIMEOUT = 20
POST_TIMEOUT = 15
MAX_POSTS_PER_FEED = 10
MAX_SEEN_PER_HANDLE = 500
MAX_POST_ATTEMPTS = 3
POST_DELAY = 1.0
USER_AGENT = "discord-x-bot/1.0 (+https://github.com/)"

STATUS_RE = re.compile(r"/([A-Za-z0-9_]{1,15})/status/(\d+)")
HANDLE_RE = re.compile(r"^[A-Za-z0-9_]{1,15}$")

log = logging.getLogger("bot")


class ConfigError(Exception):
    pass


# --- parsing ---------------------------------------------------------------


def parse_link(url):
    """Return (handle, status_id) from a tweet URL, or None if it isn't one."""
    if not url:
        return None
    m = STATUS_RE.search(url)
    if not m:
        return None
    return m.group(1), m.group(2)


def fixup_link(url):
    parsed = parse_link(url)
    if not parsed:
        return None
    handle, status_id = parsed
    return f"https://fixupx.com/{handle}/status/{status_id}"


def _norm(handle):
    return (handle or "").strip().lstrip("@").lower()


def is_retweet(entry, feed_handle):
    title = entry.get("title") or ""
    if title.startswith("RT ") or title.startswith("RT @"):
        return True
    # Retweets usually link to the original author's status.
    parsed = parse_link(entry.get("link"))
    if parsed and _norm(parsed[0]) != _norm(feed_handle):
        return True
    # Only trust the author field when it looks like a handle; many feeds put a
    # display name there ("Jane Doe"), which would otherwise flag every entry.
    author = (entry.get("author") or "").strip()
    if author and (author.startswith("@") or HANDLE_RE.match(author)):
        if _norm(author) != _norm(feed_handle):
            return True
    return False


def is_reply(entry):
    title = (entry.get("title") or "").lstrip()
    return title.startswith("@") or title.startswith("R to @")


# --- config / state --------------------------------------------------------


def load_config(path=FEEDS_PATH):
    try:
        data = yaml.safe_load(Path(path).read_text())
    except FileNotFoundError:
        raise ConfigError(f"{path} not found")
    except yaml.YAMLError as e:
        raise ConfigError(f"{path} is not valid YAML: {e}")

    feeds = (data or {}).get("feeds")
    if not isinstance(feeds, list) or not feeds:
        raise ConfigError("feeds.yaml must contain a non-empty 'feeds' list")

    result = []
    for i, f in enumerate(feeds):
        if not isinstance(f, dict):
            raise ConfigError(f"feeds[{i}] must be a mapping")
        handle = str(f.get("handle") or "").lstrip("@")
        url = f.get("url")
        if not HANDLE_RE.match(handle):
            raise ConfigError(f"feeds[{i}] has an invalid handle: {f.get('handle')!r}")
        if not url or not isinstance(url, str):
            raise ConfigError(f"feeds[{i}] ({handle}) is missing 'url'")
        result.append(
            {
                "handle": handle,
                "url": url,
                "skip_replies": bool(f.get("skip_replies", False)),
                "skip_retweets": bool(f.get("skip_retweets", False)),
            }
        )
    return result


def load_seen(path=SEEN_PATH):
    p = Path(path)
    if not p.exists():
        return {}
    text = p.read_text().strip()
    if not text:
        return {}
    data = json.loads(text)
    return {k: [str(x) for x in v] for k, v in data.items()}


def trim_seen(ids, limit=MAX_SEEN_PER_HANDLE):
    unique = sorted(set(ids), key=int)
    return unique[-limit:]


def save_seen(seen, path=SEEN_PATH):
    out = {h: trim_seen(ids) for h, ids in seen.items()}
    Path(path).write_text(json.dumps(out, indent=2, sort_keys=True) + "\n")


# --- network ---------------------------------------------------------------


def fetch_entries(url, session):
    resp = session.get(url, timeout=FETCH_TIMEOUT, headers={"User-Agent": USER_AGENT})
    resp.raise_for_status()
    parsed = feedparser.parse(resp.content)
    if parsed.bozo and not parsed.entries:
        raise ValueError(f"unparseable feed: {parsed.get('bozo_exception')}")
    return parsed.entries


def post_to_discord(webhook_url, link, handle, session, sleep=time.sleep):
    """Post one message. Returns True on 2xx."""
    payload = {"content": link, "username": f"@{handle}"}
    for attempt in range(1, MAX_POST_ATTEMPTS + 1):
        try:
            resp = session.post(webhook_url, json=payload, timeout=POST_TIMEOUT)
        except requests.RequestException as e:
            log.warning("post failed for %s: %s", link, type(e).__name__)
            return False
        if 200 <= resp.status_code < 300:
            return True
        if resp.status_code == 429 and attempt < MAX_POST_ATTEMPTS:
            wait = _retry_after(resp)
            log.info("rate limited, sleeping %.2fs (attempt %d)", wait, attempt)
            sleep(wait)
            continue
        log.warning("post failed for %s: HTTP %s", link, resp.status_code)
        return False
    return False


def _retry_after(resp):
    try:
        return float(resp.json().get("retry_after", 1))
    except Exception:
        try:
            return float(resp.headers.get("Retry-After", 1))
        except (TypeError, ValueError):
            return 1.0


# --- core ------------------------------------------------------------------


def collect(entries, feed):
    """Return {status_id: link} for entries that pass parsing and filters."""
    out = {}
    for entry in entries:
        link = fixup_link(entry.get("link"))
        if not link:
            continue
        if feed["skip_retweets"] and is_retweet(entry, feed["handle"]):
            continue
        if feed["skip_replies"] and is_reply(entry):
            continue
        out[parse_link(link)[1]] = link
    return out


def process_feed(feed, entries, seen, webhook_url, session, dry_run=False, sleep=time.sleep):
    """Handle one feed's entries, mutating `seen` in place. Returns posted count."""
    handle = feed["handle"]
    items = collect(entries, feed)

    if handle not in seen:
        if not dry_run:
            seen[handle] = list(items)
        log.info("%s: seeded %d entries", handle, len(items))
        return 0

    already = set(seen[handle])
    new_ids = sorted((i for i in items if i not in already), key=int)
    if not new_ids:
        return 0

    skipped = []
    if len(new_ids) > MAX_POSTS_PER_FEED:
        # Keep the newest ones; the backlog is marked seen without posting.
        skipped, new_ids = new_ids[:-MAX_POSTS_PER_FEED], new_ids[-MAX_POSTS_PER_FEED:]
        log.info("%s: %d new entries exceed cap of %d; skipping %d oldest",
                 handle, len(skipped) + len(new_ids), MAX_POSTS_PER_FEED, len(skipped))

    posted = 0
    for n, status_id in enumerate(new_ids):
        link = items[status_id]
        if dry_run:
            print(f"[dry-run] would post {link} as @{handle}")
            continue
        if n:
            sleep(POST_DELAY)
        if post_to_discord(webhook_url, link, handle, session, sleep=sleep):
            seen[handle].append(status_id)
            posted += 1
            log.info("%s: posted %s", handle, link)

    if not dry_run:
        seen[handle].extend(skipped)
    return posted


def run(feeds, seen, webhook_url, session, dry_run=False, sleep=time.sleep):
    total = 0
    for feed in feeds:
        try:
            entries = fetch_entries(feed["url"], session)
        except Exception as e:
            log.warning("%s: fetch failed: %s", feed["handle"], e)
            continue
        total += process_feed(feed, entries, seen, webhook_url, session, dry_run, sleep)
    return total


def main():
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    dry_run = os.environ.get("DRY_RUN") == "1"
    webhook_url = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()

    if not webhook_url and not dry_run:
        log.error("DISCORD_WEBHOOK_URL is not set")
        return 1

    try:
        feeds = load_config()
        seen = load_seen()
    except (ConfigError, json.JSONDecodeError) as e:
        log.error("invalid config/state: %s", e)
        return 1

    with requests.Session() as session:
        total = run(feeds, seen, webhook_url, session, dry_run=dry_run)

    if dry_run:
        log.info("dry run: nothing posted, seen.json untouched")
    else:
        save_seen(seen)
        log.info("done: posted %d", total)
    return 0


if __name__ == "__main__":
    sys.exit(main())
