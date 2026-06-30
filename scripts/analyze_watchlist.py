#!/usr/bin/env python3
"""Batch-analyze a Yahoo Finance watchlist with TradingAgents.

The interactive CLI is useful for one ticker at a time. This script is for a
daily watchlist workflow: load tickers from a text/CSV export, run the graph for
each ticker, save per-ticker reports, and write a ranked summary table.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from copy import deepcopy
from dataclasses import asdict, dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Iterable, Sequence

# Allow direct execution from a source checkout before `pip install .`.
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tradingagents.dataflows.utils import safe_ticker_component
from tradingagents.default_config import DEFAULT_CONFIG


ANALYSTS = ("market", "social", "news", "fundamentals")
RATING_SCORES = {
    "Buy": 5,
    "Overweight": 4,
    "Hold": 3,
    "Underweight": 2,
    "Sell": 1,
}
SYMBOL_HEADERS = {
    "symbol",
    "symbols",
    "ticker",
    "tickers",
    "ticker symbol",
    "ticker symbols",
}
HEADER_WORDS = {
    "account",
    "accounts",
    "all",
    "asc",
    "cash",
    "change",
    "comment",
    "current",
    "date",
    "desc",
    "filter",
    "filters",
    "gain",
    "generated",
    "high",
    "last",
    "low",
    "market",
    "net",
    "name",
    "open",
    "portfolio",
    "price",
    "quantity",
    "symbol",
    "ticker",
    "time",
    "total",
    "value",
    "view",
    "volume",
    "you",
}
YAHOO_QUOTE_RE = re.compile(
    r"(?:finance\.yahoo\.com/quote/|/quote/)([A-Za-z0-9._\-\^=]+)",
    re.IGNORECASE,
)
LABEL_RE_TEMPLATE = r"\*\*{label}\*\*\s*:\s*(.*?)(?:\n\s*\n\*\*|\Z)"
RATING_LABEL_RE = re.compile(r"rating.*?[:\-][\s*]*(\w+)", re.IGNORECASE)


@dataclass
class WatchlistResult:
    input_order: int
    ticker: str
    rating: str = ""
    score: int = 0
    executive_summary: str = ""
    price_target: str = ""
    time_horizon: str = ""
    report_path: str = ""
    error: str = ""


def parse_rating(text: str, default: str = "Hold") -> str:
    """Extract the TradingAgents 5-tier rating from Portfolio Manager markdown."""
    rating_set = {rating.lower() for rating in RATING_SCORES}
    for line in (text or "").splitlines():
        match = RATING_LABEL_RE.search(line)
        if match and match.group(1).lower() in rating_set:
            return match.group(1).capitalize()

    for line in (text or "").splitlines():
        for word in line.lower().split():
            clean = word.strip("*:.,")
            if clean in rating_set:
                return clean.capitalize()

    return default


def normalise_ticker(raw: str) -> str | None:
    """Return a filesystem-safe Yahoo-style ticker, or None for non-tickers."""
    token = (raw or "").strip().strip("\"'")
    if not token:
        return None

    match = YAHOO_QUOTE_RE.search(token)
    if match:
        token = match.group(1)

    token = token.strip().lstrip("$").rstrip(",:;)")
    token = token.split("?")[0].split("#")[0]
    if not token:
        return None

    lowered = token.lower()
    if lowered in HEADER_WORDS or lowered in SYMBOL_HEADERS:
        return None
    if token.replace(".", "", 1).isdigit():
        return None

    token = token.upper()
    try:
        return safe_ticker_component(token)
    except ValueError:
        return None


def _strip_comment(line: str) -> str:
    return line.split("#", 1)[0].strip()


def _looks_like_symbol_header(name: str) -> bool:
    return name.strip().lower() in SYMBOL_HEADERS


def _symbols_from_csv_header(text: str) -> list[str]:
    """Extract tickers from CSV text when a Symbol/Ticker header exists."""
    sample = text.lstrip("\ufeff")
    if not sample.strip():
        return []

    try:
        reader = csv.DictReader(sample.splitlines())
    except csv.Error:
        return []

    if not reader.fieldnames:
        return []

    symbol_field = next(
        (field for field in reader.fieldnames if _looks_like_symbol_header(field)),
        None,
    )
    if symbol_field is None:
        return []

    tickers = []
    for row in reader:
        ticker = normalise_ticker(row.get(symbol_field, ""))
        if ticker:
            tickers.append(ticker)
    return tickers


def _line_tokens(line: str) -> Iterable[str]:
    """Yield likely ticker tokens from one non-header line."""
    line = _strip_comment(line)
    if not line:
        return []

    url_matches = [normalise_ticker(match.group(1)) for match in YAHOO_QUOTE_RE.finditer(line)]
    url_tickers = [ticker for ticker in url_matches if ticker]
    if url_tickers:
        return url_tickers

    if "," in line or "\t" in line:
        try:
            cells = next(csv.reader([line]))
        except csv.Error:
            cells = re.split(r"[\t,]+", line)
        return [ticker for ticker in (normalise_ticker(cell) for cell in cells) if ticker]

    parts = line.split()
    if len(parts) <= 1:
        return [ticker for ticker in (normalise_ticker(line),) if ticker]

    normalised_parts = [normalise_ticker(part) for part in parts]
    uppercase_like = [
        part
        for part in parts
        if part == part.upper() and normalise_ticker(part) is not None
    ]

    # "AAPL MSFT NVDA" is a compact list. "AAPL Apple Inc 150.00" is a copied
    # quote row, so keep only the first token to avoid treating company words
    # as tickers.
    if len(uppercase_like) == len([part for part in parts if normalise_ticker(part)]):
        return [ticker for ticker in normalised_parts if ticker]
    return [normalised_parts[0]] if normalised_parts[0] else []


def load_tickers(path: Path) -> list[str]:
    """Load a de-duplicated ticker list from plain text or Yahoo-style CSV."""
    text = path.read_text(encoding="utf-8-sig")
    tickers = _symbols_from_csv_header(text)
    if not tickers:
        tickers = []
        for line in text.splitlines():
            tickers.extend(_line_tokens(line))

    seen = set()
    unique = []
    for ticker in tickers:
        if ticker not in seen:
            seen.add(ticker)
            unique.append(ticker)
    return unique


def parse_analysts(value: str) -> list[str]:
    analysts = [part.strip().lower() for part in value.split(",") if part.strip()]
    unknown = sorted(set(analysts) - set(ANALYSTS))
    if unknown:
        raise argparse.ArgumentTypeError(
            f"unknown analysts: {', '.join(unknown)}; choose from {', '.join(ANALYSTS)}"
        )
    return analysts or list(ANALYSTS)


def extract_markdown_label(text: str, label: str) -> str:
    pattern = re.compile(
        LABEL_RE_TEMPLATE.format(label=re.escape(label)),
        re.IGNORECASE | re.DOTALL,
    )
    match = pattern.search(text or "")
    if not match:
        return ""
    return " ".join(match.group(1).strip().split())


def build_report_markdown(ticker: str, trade_date: str, final_state: dict) -> str:
    sections = [
        f"# TradingAgents Watchlist Report: {ticker}",
        "",
        f"Analysis date: {trade_date}",
        f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "",
    ]

    report_sections = [
        ("Market Analyst", "market_report"),
        ("Sentiment Analyst", "sentiment_report"),
        ("News Analyst", "news_report"),
        ("Fundamentals Analyst", "fundamentals_report"),
        ("Research Manager", "investment_plan"),
        ("Trader", "trader_investment_plan"),
        ("Portfolio Manager", "final_trade_decision"),
    ]
    for title, key in report_sections:
        content = final_state.get(key)
        if content:
            sections.extend([f"## {title}", "", str(content).strip(), ""])

    return "\n".join(sections).rstrip() + "\n"


def write_ticker_report(
    output_dir: Path,
    ticker: str,
    trade_date: str,
    final_state: dict,
) -> Path:
    reports_dir = output_dir / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    safe_ticker = safe_ticker_component(ticker)
    report_path = reports_dir / f"{safe_ticker}_{trade_date}.md"
    report_path.write_text(
        build_report_markdown(ticker, trade_date, final_state),
        encoding="utf-8",
    )
    return report_path


def rank_results(results: Sequence[WatchlistResult]) -> list[WatchlistResult]:
    return sorted(
        results,
        key=lambda result: (
            bool(result.error),
            -result.score,
            result.input_order,
        ),
    )


def write_summary_csv(path: Path, results: Sequence[WatchlistResult]) -> None:
    fieldnames = [
        "rank",
        "ticker",
        "rating",
        "score",
        "executive_summary",
        "price_target",
        "time_horizon",
        "report_path",
        "error",
    ]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for rank, result in enumerate(results, start=1):
            row = asdict(result)
            row.pop("input_order", None)
            row["rank"] = rank
            writer.writerow({field: row.get(field, "") for field in fieldnames})


def write_summary_markdown(path: Path, results: Sequence[WatchlistResult]) -> None:
    lines = [
        "# TradingAgents Watchlist Summary",
        "",
        "| Rank | Ticker | Rating | Score | Price Target | Time Horizon | Report | Error |",
        "| ---: | --- | --- | ---: | --- | --- | --- | --- |",
    ]
    for rank, result in enumerate(results, start=1):
        report = result.report_path or ""
        if report:
            report = f"[report]({Path(report).as_posix()})"
        summary_error = result.error.replace("|", "\\|")
        lines.append(
            "| {rank} | {ticker} | {rating} | {score} | {target} | {horizon} | {report} | {error} |".format(
                rank=rank,
                ticker=result.ticker,
                rating=result.rating,
                score=result.score,
                target=result.price_target,
                horizon=result.time_horizon,
                report=report,
                error=summary_error,
            )
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_config(args: argparse.Namespace, output_dir: Path) -> dict:
    config = deepcopy(DEFAULT_CONFIG)
    config["results_dir"] = str(output_dir / "state_logs")
    config["llm_provider"] = args.provider
    config["deep_think_llm"] = args.deep_model
    config["quick_think_llm"] = args.quick_model
    config["max_debate_rounds"] = args.max_debate_rounds
    config["max_risk_discuss_rounds"] = args.max_risk_rounds
    config["output_language"] = args.output_language
    config["checkpoint_enabled"] = args.checkpoint
    if args.backend_url:
        config["backend_url"] = args.backend_url
    return config


def analyze_watchlist(args: argparse.Namespace) -> int:
    input_path = Path(args.input).expanduser()
    tickers = load_tickers(input_path)
    if args.limit:
        tickers = tickers[: args.limit]

    if not tickers:
        print(f"No valid tickers found in {input_path}", file=sys.stderr)
        return 2

    if args.dry_run:
        print("\n".join(tickers))
        return 0

    output_dir = Path(args.output_dir).expanduser()
    output_dir.mkdir(parents=True, exist_ok=True)

    from tradingagents.graph.trading_graph import TradingAgentsGraph

    analysts = parse_analysts(args.analysts)
    config = build_config(args, output_dir)
    graph = TradingAgentsGraph(
        selected_analysts=analysts,
        config=config,
        debug=args.debug,
    )

    results: list[WatchlistResult] = []
    for index, ticker in enumerate(tickers):
        print(f"[{index + 1}/{len(tickers)}] Analyzing {ticker}...")
        try:
            final_state, decision = graph.propagate(
                ticker,
                args.date,
                asset_type=args.asset_type,
            )
            final_decision = final_state.get("final_trade_decision", "")
            rating = parse_rating(final_decision, default=decision or "Hold")
            report_path = write_ticker_report(output_dir, ticker, args.date, final_state)
            results.append(
                WatchlistResult(
                    input_order=index,
                    ticker=ticker,
                    rating=rating,
                    score=RATING_SCORES.get(rating, 0),
                    executive_summary=extract_markdown_label(
                        final_decision, "Executive Summary"
                    ),
                    price_target=extract_markdown_label(final_decision, "Price Target"),
                    time_horizon=extract_markdown_label(final_decision, "Time Horizon"),
                    report_path=str(report_path),
                )
            )
        except Exception as exc:
            results.append(
                WatchlistResult(
                    input_order=index,
                    ticker=ticker,
                    error=f"{type(exc).__name__}: {exc}",
                )
            )
            if args.fail_fast:
                raise

    ranked = rank_results(results)
    csv_path = output_dir / "watchlist_summary.csv"
    md_path = output_dir / "watchlist_summary.md"
    json_path = output_dir / "watchlist_summary.json"
    write_summary_csv(csv_path, ranked)
    write_summary_markdown(md_path, ranked)
    json_path.write_text(
        json.dumps([asdict(result) for result in ranked], indent=2),
        encoding="utf-8",
    )

    print(f"\nSummary CSV: {csv_path}")
    print(f"Summary Markdown: {md_path}")
    print(f"Summary JSON: {json_path}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run TradingAgents over a Yahoo Finance watchlist file.",
    )
    parser.add_argument("input", help="Text/CSV file containing Yahoo Finance tickers.")
    parser.add_argument(
        "--date",
        default=date.today().strftime("%Y-%m-%d"),
        help="Analysis date in YYYY-MM-DD format. Defaults to today.",
    )
    parser.add_argument(
        "--output-dir",
        default=f"reports/watchlist_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
        help="Directory for summary files and per-ticker reports.",
    )
    parser.add_argument(
        "--analysts",
        default="market,social,news,fundamentals",
        help="Comma-separated analysts: market,social,news,fundamentals.",
    )
    parser.add_argument(
        "--provider",
        default=DEFAULT_CONFIG["llm_provider"],
        help="LLM provider, e.g. openai, anthropic, google, ollama.",
    )
    parser.add_argument(
        "--quick-model",
        default=DEFAULT_CONFIG["quick_think_llm"],
        help="Model for quick analyst/research calls.",
    )
    parser.add_argument(
        "--deep-model",
        default=DEFAULT_CONFIG["deep_think_llm"],
        help="Model for manager/portfolio calls.",
    )
    parser.add_argument(
        "--backend-url",
        default=DEFAULT_CONFIG.get("backend_url"),
        help="Optional provider-compatible base URL.",
    )
    parser.add_argument(
        "--max-debate-rounds",
        type=int,
        default=DEFAULT_CONFIG["max_debate_rounds"],
        help="Bull/bear debate rounds.",
    )
    parser.add_argument(
        "--max-risk-rounds",
        type=int,
        default=DEFAULT_CONFIG["max_risk_discuss_rounds"],
        help="Risk debate rounds.",
    )
    parser.add_argument(
        "--output-language",
        default=DEFAULT_CONFIG.get("output_language", "English"),
        help="Language for generated reports.",
    )
    parser.add_argument(
        "--asset-type",
        choices=("stock", "crypto"),
        default="stock",
        help="Prompt/data mode for all tickers in this run.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Analyze only the first N tickers.",
    )
    parser.add_argument(
        "--checkpoint",
        action="store_true",
        help="Enable LangGraph checkpoint/resume.",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Stream each agent message to stdout.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print parsed tickers and exit without LLM/data calls.",
    )
    parser.add_argument(
        "--fail-fast",
        action="store_true",
        help="Stop at the first ticker error instead of continuing.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return analyze_watchlist(args)


if __name__ == "__main__":
    raise SystemExit(main())
