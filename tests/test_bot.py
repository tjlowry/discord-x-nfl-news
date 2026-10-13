import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import bot  # noqa: E402

WEBHOOK = "https://discord.test/api/webhooks/1/abc"


def feed(handle="alice", **kw):
    return {"key": handle, "handle": handle, "url": "https://rss.test/feed.xml",
            "skip_replies": False, "skip_retweets": False, **kw}


def list_feed(name="cards", **kw):
    return {**feed(**kw), "key": name, "handle": None}


def entry(status_id, handle="alice", title="hello", author=None):
    e = {"link": f"https://x.com/{handle}/status/{status_id}", "title": title}
    if author is not None:
        e["author"] = author
    return e


def response(status=204, body=None, headers=None):
    r = MagicMock()
    r.status_code = status
    r.json.return_value = body or {}
    r.headers = headers or {}
    return r


def session_with(*responses):
    s = MagicMock()
    s.post.side_effect = list(responses)
    return s


def posted_links(session):
    return [c.kwargs["json"]["content"] for c in session.post.call_args_list]


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("network access in tests")
    monkeypatch.setattr(requests.Session, "request", boom)
    monkeypatch.setattr(requests, "request", boom)


# --- link parsing ----------------------------------------------------------


@pytest.mark.parametrize("url,expected", [
    ("https://x.com/jack/status/20", "https://fixupx.com/jack/status/20"),
    ("https://twitter.com/jack/status/20", "https://fixupx.com/jack/status/20"),
    ("https://mobile.twitter.com/jack/status/20", "https://fixupx.com/jack/status/20"),
    ("https://nitter.net/Some_User1/status/1234567890123456789#m",
     "https://fixupx.com/Some_User1/status/1234567890123456789"),
    ("https://rsshub.app/twitter/user/x/status/1", "https://fixupx.com/x/status/1"),
    ("https://x.com/jack/status/20?s=20", "https://fixupx.com/jack/status/20"),
    ("https://twitter.com/jack/status/20?s=20&t=abc", "https://fixupx.com/jack/status/20"),
    ("https://x.com/jack", None),
    ("https://example.com/blog/post", None),
    ("", None),
    (None, None),
])
def test_fixup_link(url, expected):
    assert bot.fixup_link(url) == expected


def test_parse_link():
    assert bot.parse_link("https://x.com/jack/status/20?s=20") == ("jack", "20")


# --- seeding / posting -----------------------------------------------------


def test_first_run_seeds_without_posting():
    seen = {}
    s = session_with()
    n = bot.process_feed(feed(), [entry(1), entry(2)], seen, WEBHOOK, s, sleep=lambda _: None)
    assert n == 0
    s.post.assert_not_called()
    assert sorted(seen["alice"]) == ["1", "2"]


def test_unseen_posted_oldest_first_and_marked_seen():
    seen = {"alice": ["1"]}
    s = session_with(response(204), response(200))
    entries = [entry(30), entry(1), entry(5)]  # feed order is newest-ish first
    n = bot.process_feed(feed(), entries, seen, WEBHOOK, s, sleep=lambda _: None)
    assert n == 2
    assert posted_links(s) == ["https://fixupx.com/alice/status/5",
                               "https://fixupx.com/alice/status/30"]
    assert s.post.call_args.kwargs["json"]["username"] == "@alice"
    assert set(seen["alice"]) == {"1", "5", "30"}


def test_ordering_is_numeric_not_lexical():
    seen = {"alice": []}
    s = session_with(response(), response())
    bot.process_feed(feed(), [entry(100), entry(99)], seen, WEBHOOK, s, sleep=lambda _: None)
    assert posted_links(s)[0].endswith("/99")


def test_failed_post_not_marked_seen():
    seen = {"alice": []}
    s = session_with(response(500), response(204))
    n = bot.process_feed(feed(), [entry(1), entry(2)], seen, WEBHOOK, s, sleep=lambda _: None)
    assert n == 1
    assert seen["alice"] == ["2"]


def test_request_exception_not_marked_seen():
    seen = {"alice": []}
    s = MagicMock()
    s.post.side_effect = requests.ConnectionError("down")
    bot.process_feed(feed(), [entry(1)], seen, WEBHOOK, s, sleep=lambda _: None)
    assert seen["alice"] == []


def test_rate_limit_retries_with_retry_after():
    seen = {"alice": []}
    sleeps = []
    s = session_with(response(429, {"retry_after": 2.5}), response(204))
    n = bot.process_feed(feed(), [entry(1)], seen, WEBHOOK, s, sleep=sleeps.append)
    assert n == 1
    assert sleeps == [2.5]
    assert seen["alice"] == ["1"]


def test_rate_limit_gives_up_after_three_attempts():
    seen = {"alice": []}
    s = session_with(*[response(429, {"retry_after": 0.1})] * 3)
    n = bot.process_feed(feed(), [entry(1)], seen, WEBHOOK, s, sleep=lambda _: None)
    assert n == 0
    assert s.post.call_count == 3
    assert seen["alice"] == []


def test_sleeps_between_posts():
    seen = {"alice": []}
    sleeps = []
    s = session_with(response(), response(), response())
    bot.process_feed(feed(), [entry(1), entry(2), entry(3)], seen, WEBHOOK, s, sleep=sleeps.append)
    assert sleeps == [bot.POST_DELAY, bot.POST_DELAY]


def test_cap_per_run():
    seen = {"alice": []}
    s = session_with(*[response()] * 15)
    entries = [entry(i) for i in range(1, 16)]
    n = bot.process_feed(feed(), entries, seen, WEBHOOK, s, sleep=lambda _: None)
    assert n == 10
    assert s.post.call_count == 10
    # newest 10 posted oldest-first; older backlog marked seen without posting
    assert posted_links(s)[0].endswith("/6")
    assert posted_links(s)[-1].endswith("/15")
    assert set(seen["alice"]) == {str(i) for i in range(1, 16)}


def test_dry_run_posts_nothing_and_marks_nothing(capsys):
    seen = {"alice": []}
    s = session_with()
    bot.process_feed(feed(), [entry(1)], seen, WEBHOOK, s, dry_run=True, sleep=lambda _: None)
    s.post.assert_not_called()
    assert seen == {"alice": []}
    assert "fixupx.com/alice/status/1" in capsys.readouterr().out


def test_dry_run_does_not_seed():
    seen = {}
    bot.process_feed(feed(), [entry(1)], seen, WEBHOOK, MagicMock(), dry_run=True)
    assert seen == {}


# --- filters ---------------------------------------------------------------


def test_skip_retweets():
    entries = [
        entry(1, title="normal tweet"),
        entry(2, title="RT @bob: something"),
        entry(7, title="RT by @alice: quoted from bob"),  # Nitter/RSS.app style
        entry(3, handle="bob", title="original by bob"),  # link points elsewhere
        entry(4, author="@bob"),
        entry(5, author="Alice Display Name"),  # display name is not a handle
        entry(6, author="@Alice"),
    ]
    got = bot.collect(entries, feed(skip_retweets=True))
    assert sorted(got, key=int) == ["1", "5", "6"]


def test_skip_replies():
    entries = [
        entry(1, title="normal"),
        entry(2, title="@bob thanks"),
        entry(3, title="R to @bob: thanks"),
    ]
    assert sorted(bot.collect(entries, feed(skip_replies=True))) == ["1"]


def test_filters_off_by_default():
    entries = [entry(1, title="RT @bob: x"), entry(2, title="@bob hi")]
    assert sorted(bot.collect(entries, feed())) == ["1", "2"]


def test_non_tweet_entries_skipped():
    entries = [{"link": "https://example.com/post", "title": "x"}, entry(7)]
    assert list(bot.collect(entries, feed())) == ["7"]


# --- run / state -----------------------------------------------------------


def test_bad_feed_does_not_stop_others(monkeypatch):
    def fake_fetch(url, session):
        if "bad" in url:
            raise requests.Timeout("slow")
        return [entry(2, handle="bob")]

    monkeypatch.setattr(bot, "fetch_entries", fake_fetch)
    feeds = [feed("alice", url="https://bad.test"), feed("bob", url="https://good.test")]
    seen = {"alice": [], "bob": ["1"]}
    s = session_with(response())
    total = bot.run(feeds, seen, WEBHOOK, s, sleep=lambda _: None)
    assert total == 1
    assert seen["bob"] == ["1", "2"]


def test_save_seen_trims_and_is_stable(tmp_path):
    path = tmp_path / "seen.json"
    seen = {"zed": [str(i) for i in range(600, 0, -1)], "amy": ["10", "9", "10"]}
    bot.save_seen(seen, path)
    data = json.loads(path.read_text())
    assert list(data) == ["amy", "zed"]
    assert data["amy"] == ["9", "10"]
    assert len(data["zed"]) == 500
    assert data["zed"][0] == "101" and data["zed"][-1] == "600"
    assert bot.load_seen(path) == data


def test_load_seen_missing_file(tmp_path):
    assert bot.load_seen(tmp_path / "nope.json") == {}


def test_load_config(tmp_path):
    p = tmp_path / "feeds.yaml"
    p.write_text("feeds:\n  - handle: '@alice'\n    url: https://a\n    skip_replies: true\n")
    assert bot.load_config(p) == [{"key": "alice", "handle": "alice", "url": "https://a",
                                   "skip_replies": True, "skip_retweets": False}]


def test_load_config_list_feed(tmp_path):
    p = tmp_path / "feeds.yaml"
    p.write_text("feeds:\n  - name: cards\n    url: https://l\n")
    assert bot.load_config(p) == [{"key": "cards", "handle": None, "url": "https://l",
                                   "skip_replies": False, "skip_retweets": False}]


@pytest.mark.parametrize("text", ["", "feeds: []", "feeds:\n  - handle: alice\n",
                                  "feeds:\n  - url: https://a\n",
                                  "feeds:\n  - {handle: a, name: b, url: x}\n",
                                  "feeds:\n  - {handle: a, url: x}\n  - {handle: a, url: y}\n",
                                  "feeds:\n  - handle: 'bad handle!'\n    url: x\n"])
def test_load_config_invalid(tmp_path, text):
    p = tmp_path / "feeds.yaml"
    p.write_text(text)
    with pytest.raises(bot.ConfigError):
        bot.load_config(p)


def test_main_requires_webhook(monkeypatch):
    monkeypatch.delenv("DISCORD_WEBHOOK_URL", raising=False)
    monkeypatch.delenv("DRY_RUN", raising=False)
    assert bot.main() == 1


def test_fetch_entries_parses_rss():
    xml = b"""<?xml version="1.0"?><rss version="2.0"><channel><title>t</title>
    <item><title>hi</title><link>https://nitter.net/alice/status/42#m</link></item>
    </channel></rss>"""
    s = MagicMock()
    s.get.return_value = MagicMock(content=xml, raise_for_status=lambda: None)
    entries = bot.fetch_entries("https://rss.test", s)
    assert bot.fixup_link(entries[0].link) == "https://fixupx.com/alice/status/42"
    assert s.get.call_args.kwargs["timeout"] == bot.FETCH_TIMEOUT


# --- list feeds ------------------------------------------------------------


def test_list_feed_posts_as_each_author():
    seen = {"cards": []}
    s = session_with(response(), response())
    entries = [entry(2, handle="AzJace", author="@AzJace", title="Jace: draft notes"),
               entry(1, handle="KSzn2021", author="@KSzn2021", title="K1SinceDay1: MHJ")]
    n = bot.process_feed(list_feed(skip_retweets=True), entries, seen, WEBHOOK, s,
                         sleep=lambda _: None)
    assert n == 2
    assert [c.kwargs["json"] for c in s.post.call_args_list] == [
        {"content": "https://fixupx.com/KSzn2021/status/1", "username": "@KSzn2021"},
        {"content": "https://fixupx.com/AzJace/status/2", "username": "@AzJace"},
    ]


def test_list_feed_filters_strip_display_name_prefix():
    entries = [
        entry(1, handle="AzJace", title="Jace: Zereoue Williams continues to rise"),
        entry(2, handle="AzJace", title="Jace: RT @SharpFootball: #31 team is 5-0"),
        entry(3, handle="AzJace", title="Jace: @JohnnyVenerable @KSzn2021 lol"),
        entry(4, handle="Joe", title="Johnny Venerable: @KSzn2021 Correct."),
        entry(5, handle="Pat", title="Breaking: Cardinals sign a kicker"),
    ]
    got = bot.collect(entries, list_feed(skip_retweets=True, skip_replies=True))
    assert sorted(got, key=int) == ["1", "5"]


def test_tweet_seen_via_another_feed_is_not_reposted():
    seen = {"cards": ["1"], "KSzn2021": []}
    s = session_with(response())
    n = bot.process_feed(feed("KSzn2021"), [entry(1, handle="KSzn2021"), entry(2, handle="KSzn2021")],
                         seen, WEBHOOK, s, sleep=lambda _: None)
    assert n == 1
    assert posted_links(s) == ["https://fixupx.com/KSzn2021/status/2"]
    assert sorted(seen["KSzn2021"]) == ["1", "2"]


def test_warns_when_feed_rolled_past_last_seen(caplog):
    seen = {"alice": ["1", "2"]}
    s = session_with(*[response()] * 2)
    bot.process_feed(feed(), [entry(5), entry(6)], seen, WEBHOOK, s, sleep=lambda _: None)
    assert "may have been missed" in caplog.text


def test_no_warning_when_feed_overlaps(caplog):
    seen = {"alice": ["1", "2"]}
    s = session_with(response())
    bot.process_feed(feed(), [entry(2), entry(3)], seen, WEBHOOK, s, sleep=lambda _: None)
    assert "may have been missed" not in caplog.text
