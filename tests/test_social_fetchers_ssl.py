from __future__ import annotations

from unittest.mock import patch

import pytest

from tradingagents.dataflows import reddit, stocktwits


class _FakeResponse:
    def __init__(self, body: bytes):
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return self._body


@pytest.mark.unit
def test_stocktwits_fetch_uses_verified_ssl_context():
    ssl_context = object()
    response = _FakeResponse(b'{"messages": []}')

    with (
        patch("tradingagents.dataflows.stocktwits.verified_ssl_context", return_value=ssl_context),
        patch("tradingagents.dataflows.stocktwits.urlopen", return_value=response) as urlopen_mock,
    ):
        result = stocktwits.fetch_stocktwits_messages("SNOW")

    assert "<no StockTwits messages found" in result
    assert urlopen_mock.call_args.kwargs["context"] is ssl_context


@pytest.mark.unit
def test_reddit_fetch_uses_verified_ssl_context():
    ssl_context = object()
    response = _FakeResponse(
        b"""<?xml version="1.0" encoding="UTF-8"?>
        <feed xmlns="http://www.w3.org/2005/Atom">
          <entry>
            <title>SNOW earnings</title>
            <published>2026-07-05T00:00:00Z</published>
            <content type="html">&lt;!-- SC_OFF --&gt;strong quarter&lt;!-- SC_ON --&gt;</content>
          </entry>
        </feed>"""
    )

    with (
        patch("tradingagents.dataflows.reddit.verified_ssl_context", return_value=ssl_context),
        patch("tradingagents.dataflows.reddit.urlopen", return_value=response) as urlopen_mock,
    ):
        posts = reddit._fetch_subreddit("SNOW", "stocks", limit=5, timeout=1.0)

    assert posts[0]["title"] == "SNOW earnings"
    assert posts[0]["score"] is None
    assert posts[0]["source"] == "rss"
    assert urlopen_mock.call_args.kwargs["context"] is ssl_context


@pytest.mark.unit
def test_reddit_fetch_reports_unavailable_sources_separately_from_no_posts():
    with patch("tradingagents.dataflows.reddit._fetch_subreddit", return_value=None):
        result = reddit.fetch_reddit_posts(
            "SNOW",
            subreddits=("stocks",),
            limit_per_sub=1,
            inter_request_delay=0,
        )

    assert "r/stocks: <reddit unavailable for SNOW>" in result
    assert "no Reddit posts found" not in result
