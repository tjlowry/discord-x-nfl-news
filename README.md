# discord-x-bot

Posts new tweets from a list of X accounts to a Discord channel as
`fixupx.com` links, so videos play inline. Runs 3 times a day on GitHub
Actions — no server, no X API.

Tweets come from RSS feeds (Nitter, RSS.app, RSSHub, etc.). Any feed works as
long as item links contain `/<handle>/status/<id>`. A feed can cover one account
or many (an X List). The current feed is an X List served by a public Nitter
mirror (`<mirror>/i/lists/<list id>/rss`), with fallback mirrors.

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

The workflow runs 3 times a day: 8am, 1pm and 6pm Central Daylight Time
(`7 13,18,23 * * *`). GitHub cron is UTC, so under Central Standard Time
(Nov–Mar) the runs land an hour earlier (7am, noon, 5pm); shift the hours in
`.github/workflows/poll.yml` if that matters. Each run posts everything new
since the last one (up to 50 per feed).

The Nitter list feed holds roughly the last day and a half of tweets, so gaps of
several hours between runs lose nothing. If a run logs "tweets may have been
missed", the feed turned over between runs; run more often.

Actions minutes are unlimited on public repos; 3 runs a day is also well inside
the private-repo free tier.

GitHub doesn't guarantee cron timing; runs are often a few minutes late, and
scheduled workflows are disabled after 60 days without repo activity (the
`seen.json` commits count as activity as long as tweets keep arriving).

### Reliable scheduling (Cloudflare Worker)

*Optional.* GitHub's `schedule:` trigger can be late or skip runs (this repo
saw it fire only a few times a day on a 5-minute schedule). `worker/worker.js` is a Cloudflare Worker that starts the `poll`
workflow through GitHub's API on Cloudflare's (more reliable) cron. The GitHub cron stays in place;
if both fire, the workflow's concurrency group drops the extra run.

1. **GitHub token**: github.com → Settings → Developer settings → Personal
   access tokens → *Fine-grained tokens* → *Generate new token*.
   - Repository access: *Only select repositories* → this repo.
   - Permissions: **Actions → Read and write**. Nothing else.
   - Pick an expiration and put a renewal reminder on your calendar.

   This token can only start, cancel, or re-run this repo's workflows. It can't
   read secrets (the Discord webhook) or change code.
2. **Create the Worker**: Cloudflare dashboard → *Workers & Pages* → *Create* →
   start from *Hello World* → *Deploy*. Then *Edit code*, replace everything
   with the contents of `worker/worker.js`, and *Deploy* again.
3. **Add the secret**: the Worker's *Settings* → *Variables and Secrets* →
   *Add* → type **Secret**, name `GH_TOKEN`, value = the token.
4. **Add the schedule**: *Settings* → *Triggers* → *Cron Triggers* → *Add* →
   `7 13,18,23 * * *` (same times as the workflow).
5. *(Optional)* *Settings* → *Domains & Routes* → disable the `workers.dev`
   URL. The Worker only acts on the cron, so it doesn't need one.

After the next scheduled time, runs should appear under *Actions* in this repo. If they
don't, check the Worker's logs (or the cron trigger's past events) in Cloudflare:
failures say why, e.g. `HTTP 401 (token invalid or expired ...)`.

**When the token expires**, runs stop. Make a new token (step 1) and replace
the `GH_TOKEN` secret (step 3).

Deploying with the CLI instead: `cd worker && npx wrangler deploy && npx wrangler secret put GH_TOKEN`
(uses `worker/wrangler.toml`, which already includes the cron).

## Adding accounts

**Easiest: add them to the X List.** The `cards-list` feed is an X List turned
into one RSS.app feed, so adding an account to the list on X is all it takes.
Nothing changes in this repo.

**Or add a feed** to `feeds.yaml` and commit. No code changes needed:

```yaml
feeds:
  # one account: posts as that account
  - handle: someaccount
    url: https://rss.app/feeds/abcd1234.xml
    skip_replies: true      # optional, default false
    skip_retweets: true     # optional, default false

  # many accounts (an X List): posts as each tweet's author
  - name: my-list
    url: https://rss.app/feeds/efgh5678.xml
```

The next run seeds the new feed and then posts its new tweets from then on.

Don't give an account its own feed if it's also in a list feed. A tweet that
appears in both is only posted once, but retweets carry a different status ID
in each feed, so they would be posted twice.

- `skip_replies` drops items whose title starts with `@` or `R to @`.
- `skip_retweets` drops items whose title starts with `RT `. On single-account
  feeds it also drops items whose link points to a different account, or whose
  author field is a different `@handle`.
- List feeds from RSS.app prefix titles with the display name ("Jace: RT @…");
  the filters ignore that prefix.

## Behavior

- New tweets are posted oldest first, with the account shown as the webhook's
  username (`@handle`).
- A tweet is only marked seen after Discord accepts it, so failed posts are
  retried on the next run. On HTTP 429 the bot waits `retry_after` and retries
  (up to 3 attempts).
- At most 50 posts per feed per run. If more have piled up (e.g. after
  downtime), the 50 newest are posted and the older ones are marked seen
  without posting.
- A feed with several URLs tries them in order and uses the first that loads.
  If all fail, it's logged and skipped; other feeds still run.
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

```bash
node --test worker/
```

To actually post from your machine, set `DISCORD_WEBHOOK_URL` in your
environment and run `python bot.py`. Keep the URL out of the repo: anyone with
it can post to your channel.
