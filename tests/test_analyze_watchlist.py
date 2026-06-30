from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "analyze_watchlist.py"
SPEC = importlib.util.spec_from_file_location("analyze_watchlist", SCRIPT_PATH)
watchlist = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = watchlist
SPEC.loader.exec_module(watchlist)


@pytest.mark.unit
class TestTickerLoading:
    def test_loads_yahoo_style_csv_symbol_column(self, tmp_path):
        path = tmp_path / "watchlist.csv"
        path.write_text(
            "Symbol,Name,Last Price,Change\n"
            "AAPL,Apple Inc.,190.00,+1.2%\n"
            "BRK.B,Berkshire Hathaway,410.00,-0.1%\n"
            "0700.HK,Tencent,390.00,+0.5%\n",
            encoding="utf-8",
        )

        assert watchlist.load_tickers(path) == ["AAPL", "BRK.B", "0700.HK"]

    def test_loads_plain_and_comma_separated_lists(self, tmp_path):
        path = tmp_path / "watchlist.txt"
        path.write_text(
            "AAPL, msft, NVDA\n"
            "TSLA # active trade\n"
            "$AMD\n"
            "https://finance.yahoo.com/quote/QQQ?p=QQQ\n",
            encoding="utf-8",
        )

        assert watchlist.load_tickers(path) == ["AAPL", "MSFT", "NVDA", "TSLA", "AMD", "QQQ"]

    def test_copied_quote_row_keeps_first_symbol_only(self, tmp_path):
        path = tmp_path / "copied.txt"
        path.write_text(
            "AAPL Apple Inc. 190.00 +1.2%\n"
            "NVDA NVIDIA Corporation 920.00 -0.3%\n",
            encoding="utf-8",
        )

        assert watchlist.load_tickers(path) == ["AAPL", "NVDA"]

    def test_deduplicates_without_reordering(self, tmp_path):
        path = tmp_path / "dupes.txt"
        path.write_text("AAPL\nMSFT\nAAPL\nNVDA\nMSFT\n", encoding="utf-8")

        assert watchlist.load_tickers(path) == ["AAPL", "MSFT", "NVDA"]

    def test_ignores_account_summary_export_without_symbol_column(self, tmp_path):
        path = tmp_path / "account_summary.csv"
        path.write_text(
            "Account Summary\n"
            "Account,Net Account Value,Total Gain $,Cash Purchasing Power\n"
            '"Roth IRA -1996",5000.00,.00,5000.00\n'
            "Portfolio View,Filters,All,Asc,Generated\n",
            encoding="utf-8",
        )

        assert watchlist.load_tickers(path) == []


@pytest.mark.unit
class TestSummaryHelpers:
    def test_extracts_rendered_pm_labels(self):
        text = (
            "**Rating**: Overweight\n\n"
            "**Executive Summary**: Build gradually near support.\n\n"
            "**Investment Thesis**: Momentum is constructive.\n\n"
            "**Price Target**: 250.0\n\n"
            "**Time Horizon**: 1-3 months"
        )

        assert watchlist.extract_markdown_label(text, "Executive Summary") == "Build gradually near support."
        assert watchlist.extract_markdown_label(text, "Price Target") == "250.0"
        assert watchlist.extract_markdown_label(text, "Time Horizon") == "1-3 months"

    def test_ranks_bullish_results_before_lower_scores_and_errors(self):
        results = [
            watchlist.WatchlistResult(0, "MSFT", rating="Hold", score=3),
            watchlist.WatchlistResult(1, "AAPL", rating="Buy", score=5),
            watchlist.WatchlistResult(2, "BAD", error="failed"),
            watchlist.WatchlistResult(3, "NVDA", rating="Overweight", score=4),
        ]

        ranked = watchlist.rank_results(results)

        assert [result.ticker for result in ranked] == ["AAPL", "NVDA", "MSFT", "BAD"]

    def test_writes_summary_csv(self, tmp_path):
        path = tmp_path / "summary.csv"
        results = [
            watchlist.WatchlistResult(
                0,
                "AAPL",
                rating="Buy",
                score=5,
                executive_summary="Constructive setup.",
                price_target="250.0",
                time_horizon="1-3 months",
                report_path="/tmp/AAPL.md",
            )
        ]

        watchlist.write_summary_csv(path, results)

        content = path.read_text(encoding="utf-8")
        assert "rank,ticker,rating,score" in content
        assert "1,AAPL,Buy,5" in content
