"""Command line entry point: python -m finflow inspect --tickers AAPL --as-of YYYY-MM-DD"""

import argparse
import json
import logging
import sys
from datetime import date
from pathlib import Path

import pandas as pd

from finflow.extract import (
    TICKER_URL,
    ConfigurationError,
    SecClient,
    SourceError,
    fetch_company,
    fetch_json,
    resolve_companies,
)
from finflow.inspect_source import inspect_company

PROJECT_DIR = Path(__file__).resolve().parent.parent
logger = logging.getLogger("finflow")


def load_config(name):
    return json.loads((PROJECT_DIR / "config" / name).read_text(encoding="utf-8"))


def parse_date(text):
    return date.fromisoformat(text).isoformat()


def build_parser():
    parser = argparse.ArgumentParser(prog="finflow")
    commands = parser.add_subparsers(dest="command", required=True)
    inspect = commands.add_parser("inspect", help="check which metrics the SEC data contains")
    inspect.add_argument("--tickers", nargs="+", help="default: every configured company")
    inspect.add_argument("--start-year", type=int, default=2023)
    inspect.add_argument("--end-year", type=int, default=2025)
    inspect.add_argument("--as-of", type=parse_date, required=True, help="YYYY-MM-DD")
    inspect.add_argument("--refresh", action="store_true", help="download again")
    inspect.add_argument("--raw-dir", type=Path, default=PROJECT_DIR / "data" / "raw")
    inspect.add_argument("--output-dir", type=Path, default=PROJECT_DIR / "data" / "inspection")
    return parser


def run_inspect(args, client):
    """Inspect each company. Returns 0 if all succeeded and 1 if any failed."""
    metrics = load_config("metrics.json")["metrics"]
    tickers = args.tickers or load_config("companies.json")["tickers"]
    # Fiscal 2023 can be filed in early 2023 and needs 2022 as its prior year.
    filed_since = f"{args.start_year - 1}-01-01"

    ticker_data = fetch_json(client, TICKER_URL, "company_tickers.json", args.raw_dir, args.refresh)
    tables, failed = [], []
    for ticker in tickers:
        try:
            company = resolve_companies(ticker_data, [ticker])[0]
            data = fetch_company(client, company["cik"], args.raw_dir, filed_since, args.refresh)
            filings, findings = inspect_company(
                company["ticker"], company["cik"], data, metrics,
                args.start_year, args.end_year, args.as_of,
            )
        except SourceError as error:
            logger.error("%s failed: %s", ticker, error)
            failed.append(ticker)
            continue
        print(f"\n{company['ticker']}  CIK {company['cik']}  {company['name']}")
        print(filings.tail(6).drop(columns="source_url").to_string(index=False))
        shown = ["fiscal_year", "period_end", "metric", "tag", "latest_value",
                 "filings_reporting", "distinct_values", "finding"]
        print(findings[shown].to_string(index=False))
        tables.append(findings)

    if tables:
        args.output_dir.mkdir(parents=True, exist_ok=True)
        result = pd.concat(tables, ignore_index=True)
        result.to_csv(args.output_dir / "source_inspection.csv", index=False)
        core = result[result["core"].astype(bool)]
        slots = core.drop_duplicates(["ticker", "period_end", "metric"])
        expected = len(tickers) * (args.end_year - args.start_year + 1)
        expected *= sum(metric["core"] for metric in metrics)
        found = int((slots["finding"] != "missing").sum())
        print(f"\nCore slots found: {found} of {expected} expected")
        print(slots["finding"].value_counts().to_string())
    if failed:
        print(f"\nINCOMPLETE: {len(failed)} of {len(tickers)} companies failed: {', '.join(failed)}")
        return 1
    return 0


def main(argv=None, client=None):
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        return run_inspect(args, client or SecClient())
    except (ConfigurationError, SourceError) as error:
        logger.error("%s", error)
        return 2


if __name__ == "__main__":
    sys.exit(main())
