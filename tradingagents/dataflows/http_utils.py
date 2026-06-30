"""Shared HTTP helpers for dataflow fetchers."""

from __future__ import annotations

import ssl
from functools import lru_cache

import certifi


@lru_cache(maxsize=1)
def verified_ssl_context() -> ssl.SSLContext:
    """Return an SSL context backed by certifi's CA bundle.

    Python.org macOS builds can ship without a populated system trust store for
    urllib, which surfaces as CERTIFICATE_VERIFY_FAILED on public HTTPS APIs.
    Requests and yfinance already use certifi transitively; this helper gives
    our urllib-based public fetchers the same CA bundle.
    """
    return ssl.create_default_context(cafile=certifi.where())
