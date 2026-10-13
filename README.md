# discord-x-bot

Posts new tweets from a list of X accounts to a Discord channel as
`fixupx.com` links, so videos play inline. Runs every 15 minutes on GitHub
Actions — no server, no X API.

Tweets come from RSS feeds (RSS.app, RSSHub, Nitter, etc.). Any feed works as
long as item links contain `/<handle>/status/<id>`.

## Setup

1. **Create a Discord webhook**: in Discord, open the channel → *Edit Channel*
   → *Integrations* → *Webhooks* → *New Webhook* → *Copy Webhook URL*.
2. **Add the secret**: in this GitHub repo, *Settings* → *Secrets and
   variables* → *Actions* → *New repository secret*, named
   `DISCORD_WEBHOOK_URL`, with the URL you just copied.
3. **Edit `feeds.yaml`** with your accounts and their RSS feed URLs.
4. **Run once manually**: *Actions* → *poll* → *Run workflow*. The first run for
   each account only records existing tweets as seen ("seeded N entries") and
   posts nothing, so the channel isn't flooded with old tweets. After that, new
   tweets are posted on every run.

The workflow commits `seen.json` back to the repo after each run that changes it.

### Schedule and Actions minutes

The workflow runs every 15 minutes (`*/15 * * * *`).

- **Public repo**: Actions minutes are unlimited.
- **Private repo**: the free tier is 2,000 minutes/month, and `*/15` uses about
  that much. Change the cron in `.github/workflows/poll.yml` to `*/30 * * * *`.

GitHub doesn't guarantee cron timing; runs are often a few minutes late, and
scheduled workflows are disabled after 60 days without repo activity (the
`seen.json` commits count as activity as long as tweets keep arriving).

## Adding an account

Add an entry to `feeds.yaml` and commit. No code changes needed:

```yaml
feeds:
  - handle: someaccount
    url: https://rss.app/feeds/abcd1234.xml
    skip_replies: true      # optional, default false
    skip_retweets: true     # optional, default false
```

The next run seeds the new account and then posts its new tweets from then on.

- `skip_replies` drops items whose title starts with `@` or `R to @`.
- `skip_retweets` drops items whose title starts with `RT `, whose link points
  to a different account, or whose author field is a different `@handle`.

## Behavior

- New tweets are posted oldest first, with the account shown as the webhook's
  username (`@handle`).
- A tweet is only marked seen after Discord accepts it, so failed posts are
  retried on the next run. On HTTP 429 the bot waits `retry_after` and retries
  (up to 3 attempts).
- At most 10 posts per account per run. If more have piled up (e.g. after
  downtime), the 10 newest are posted and the older ones are marked seen
  without posting.
- A feed that fails to load is logged and skipped; the others still run.
- `seen.json` keeps the newest 500 IDs per account.

## Running locally

```bash
python3 -m venv .venv
```

```bash
.venv/bin/pip install -r requirements.txt
```

Preview what would be posted, without posting or touching `seen.json`:

```bash
DRY_RUN=1 .venv/bin/python bot.py
```

Run the tests (no network access):

```bash
.venv/bin/pytest
```

To actually post from your machine, set `DISCORD_WEBHOOK_URL` in your
environment and run `python bot.py`. Keep the URL out of the repo: anyone with
it can post to your channel.
