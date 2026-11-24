"""Extract stage: download JSON from the official SEC APIs, politely and traceably.

Every response is saved byte for byte in data/raw. A manifest records where each file
came from, when it was retrieved and its SHA-256 checksum, so any number produced later
can be traced back to the exact source file.
"""

import hashlib
import json
import logging
import os
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import urlparse

import requests
from dotenv import load_dotenv

logger = logging.getLogger(__name__)

TICKER_URL = "https://www.sec.gov/files/company_tickers.json"
COMPANY_FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/{name}"

ALLOWED_HOSTS = {"www.sec.gov", "data.sec.gov"}
MIN_SECONDS_BETWEEN_REQUESTS = 0.5  # at most 2 requests per second
TIMEOUT_SECONDS = 30
MAX_ATTEMPTS = 4
MAX_WAIT_SECONDS = 60
RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}


class ConfigurationError(Exception):
    """The local setup is incomplete, for example no SEC contact is configured."""


class SourceError(Exception):
    """The SEC source could not supply a usable response."""


def load_user_agent():
    """Read the SEC contact string from the environment or the local .env file."""
    load_dotenv()
    user_agent = os.getenv("SEC_USER_AGENT", "").strip()
    if "@" not in user_agent or "example.com" in user_agent:
        raise ConfigurationError(
            "SEC_USER_AGENT is missing or still the placeholder. Copy .env.example to .env "
            "and set your own contact email."
        )
    return user_agent


def retry_wait_seconds(attempt, retry_after=None):
    """Return how long to wait before the next attempt.

    Retry-After may be a number of seconds or an HTTP date. Without it, the wait
    doubles each time (1, 2, 4 seconds...), which is called exponential backoff.
    """
    if retry_after:
        try:
            return max(0.0, float(retry_after))
        except ValueError:
            pass
        try:
            wait = parsedate_to_datetime(retry_after) - datetime.now(timezone.utc)
            return max(0.0, wait.total_seconds())
        except (TypeError, ValueError):
            pass
    return float(2 ** (attempt - 1))


class SecClient:
    """Small wrapper around requests that enforces the SEC fair access rules."""

    def __init__(self, user_agent=None, session=None):
        self.user_agent = user_agent
        self.session = session or requests.Session()
        self.last_request_time = None

    def _pause_if_needed(self):
        if self.last_request_time is not None:
            elapsed = time.monotonic() - self.last_request_time
            if elapsed < MIN_SECONDS_BETWEEN_REQUESTS:
                time.sleep(MIN_SECONDS_BETWEEN_REQUESTS - elapsed)
        self.last_request_time = time.monotonic()

    def get_bytes(self, url):
        """Download one URL and return the body, retrying only temporary failures."""
        parts = urlparse(url)
        if parts.scheme != "https" or parts.hostname not in ALLOWED_HOSTS:
            raise SourceError(f"Refusing to contact a non-SEC address: {url}")
        # Loaded late so that a fully cached run needs no contact details.
        if self.user_agent is None:
            self.user_agent = load_user_agent()

        for attempt in range(1, MAX_ATTEMPTS + 1):
            self._pause_if_needed()
            retry_after = None
            try:
                response = self.session.get(
                    url,
                    headers={"User-Agent": self.user_agent},
                    timeout=TIMEOUT_SECONDS,
                    allow_redirects=False,
                )
            except (
                requests.ConnectionError,
                requests.Timeout,
                requests.exceptions.ChunkedEncodingError,  # connection dropped mid-body
            ) as error:
                problem = type(error).__name__
            else:
                if response.status_code == 200:
                    return response.content
                problem = f"HTTP {response.status_code}"
                if response.status_code not in RETRYABLE_STATUS_CODES:
                    raise SourceError(f"{problem} for {url} (not retried)")
                retry_after = response.headers.get("Retry-After")

            if attempt == MAX_ATTEMPTS:
                break
            wait = retry_wait_seconds(attempt, retry_after)
            if wait > MAX_WAIT_SECONDS:
                raise SourceError(f"{problem} for {url}: server asked for a {wait:.0f}s wait")
            logger.warning("%s for %s; retrying in %.1fs", problem, url, wait)
            time.sleep(wait)

        raise SourceError(f"{problem} for {url} after {MAX_ATTEMPTS} attempts")


def write_atomically(path, content):
    """Write to a temporary file, then rename, so a crash cannot leave half a file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_bytes(content)
    os.replace(temporary, path)


def read_manifest(raw_dir):
    path = Path(raw_dir) / "manifest.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def fetch_json(client, url, filename, raw_dir, refresh=False):
    """Return parsed JSON for a URL, using the local cache unless refresh is requested."""
    raw_dir = Path(raw_dir)
    path = raw_dir / filename
    manifest = read_manifest(raw_dir)
    entry = manifest.get(filename)

    if not refresh and path.exists() and entry and entry["url"] == url:
        content = path.read_bytes()
        if hashlib.sha256(content).hexdigest() == entry["sha256"]:
            logger.info("cache hit: %s", filename)
            return json.loads(content)
        logger.warning("checksum mismatch for cached %s; downloading again", filename)

    content = client.get_bytes(url)
    # Check the body is JSON before saving, so an HTML error page never replaces good data.
    try:
        data = json.loads(content)
    except ValueError as error:
        raise SourceError(f"Response from {url} is not valid JSON") from error

    write_atomically(path, content)
    manifest[filename] = {
        "url": url,
        "retrieved_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "sha256": hashlib.sha256(content).hexdigest(),
        "bytes": len(content),
    }
    write_atomically(
        raw_dir / "manifest.json",
        json.dumps(manifest, indent=2, sort_keys=True).encode("utf-8"),
    )
    logger.info("downloaded: %s (%d bytes)", filename, len(content))
    return data


def resolve_companies(ticker_data, tickers):
    """Look up CIK and company name for each ticker in the SEC mapping.

    The CIK is kept as a 10-character string because its leading zeros matter.
    """
    lookup = {row["ticker"].upper(): row for row in ticker_data.values()}
    companies = []
    for ticker in tickers:
        row = lookup.get(ticker.upper())
        if row is None:
            raise SourceError(f"Ticker {ticker} is not in the SEC ticker mapping")
        companies.append(
            {"ticker": ticker.upper(), "cik": str(row["cik_str"]).zfill(10), "name": row["title"]}
        )
    return companies


def fetch_company(client, cik, raw_dir, filed_since, refresh=False):
    """Download one company's facts and its filing history back to filed_since.

    The main submissions file only holds recent filings. Companies that file many
    insider-trading forms can push an older 10-K into an extra page, so any page
    that overlaps the period we need is downloaded as well.
    """
    facts = fetch_json(
        client, COMPANY_FACTS_URL.format(cik=cik), f"companyfacts_CIK{cik}.json", raw_dir, refresh
    )
    main_name = f"CIK{cik}.json"
    main = fetch_json(
        client, SUBMISSIONS_URL.format(name=main_name), f"submissions_{main_name}", raw_dir, refresh
    )
    filing_tables = [main["filings"]["recent"]]
    for page in main["filings"].get("files", []):
        if page["filingTo"] >= filed_since:
            filing_tables.append(
                fetch_json(
                    client,
                    SUBMISSIONS_URL.format(name=page["name"]),
                    f"submissions_{page['name']}",
                    raw_dir,
                    refresh,
                )
            )
    return {"facts": facts, "filing_tables": filing_tables}
