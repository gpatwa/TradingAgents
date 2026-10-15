"""Compatibility access to the refactored StockTwits vendor."""

from __future__ import annotations

from .vendors import stocktwits as _vendor

verified_ssl_context = _vendor.verified_ssl_context
urlopen = _vendor.urlopen


def fetch_stocktwits_messages(*args, **kwargs):
    """Forward the legacy entry point while keeping legacy patch points usable."""
    original_context = _vendor.verified_ssl_context
    original_urlopen = _vendor.urlopen
    _vendor.verified_ssl_context = verified_ssl_context
    _vendor.urlopen = urlopen
    try:
        return _vendor.fetch_stocktwits_messages(*args, **kwargs)
    finally:
        _vendor.verified_ssl_context = original_context
        _vendor.urlopen = original_urlopen
