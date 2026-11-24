import json
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime

import pytest
import requests
from conftest import FakeResponse

from finflow import extract
from finflow.extract import ConfigurationError, SourceError

URL = "https://data.sec.gov/submissions/CIK0000000001.json"


def test_user_agent_is_sent_with_timeout_and_no_redirects(make_client, sleeps):
    client = make_client([FakeResponse()])
    client.get_bytes(URL)
    _, kwargs = client.session.calls[0]
    assert kwargs["headers"]["User-Agent"] == "FinFlow tests tester@test.invalid"
    assert kwargs["timeout"] == extract.TIMEOUT_SECONDS
    assert kwargs["allow_redirects"] is False


@pytest.mark.parametrize("value", ["", "FinFlow", "FinFlow your-email@example.com"])
def test_missing_or_placeholder_contact_is_rejected(monkeypatch, value):
    monkeypatch.setenv("SEC_USER_AGENT", value)
    with pytest.raises(ConfigurationError):
        extract.load_user_agent()


@pytest.mark.parametrize("url", ["https://example.org/x", "http://data.sec.gov/x"])
def test_only_https_sec_hosts_are_contacted(make_client, url):
    client = make_client([])
    with pytest.raises(SourceError, match="Refusing"):
        client.get_bytes(url)
    assert client.session.calls == []


def test_requests_are_spaced_at_least_half_a_second_apart(make_client, sleeps):
    client = make_client([FakeResponse(), FakeResponse(), FakeResponse()])
    for _ in range(3):
        client.get_bytes(URL)
    assert sleeps == [0.5, 0.5]


def test_429_honours_retry_after(make_client, sleeps):
    client = make_client([FakeResponse(429, headers={"Retry-After": "7"}), FakeResponse()])
    assert client.get_bytes(URL) == b"{}"
    assert sleeps == [7.0]


def test_retry_after_may_be_a_date():
    later = datetime.now(timezone.utc) + timedelta(seconds=30)
    wait = extract.retry_wait_seconds(1, format_datetime(later, usegmt=True))
    assert 27 <= wait <= 31


def test_very_long_retry_after_fails_instead_of_retrying_early(make_client, sleeps):
    client = make_client([FakeResponse(429, headers={"Retry-After": "600"})])
    with pytest.raises(SourceError, match="600s"):
        client.get_bytes(URL)
    assert len(client.session.calls) == 1


def test_temporary_failures_back_off_then_succeed(make_client, sleeps):
    client = make_client([requests.Timeout(), FakeResponse(503), FakeResponse(content=b"[1]")])
    assert client.get_bytes(URL) == b"[1]"
    assert sleeps == [1.0, 2.0]


def test_retries_are_bounded(make_client, sleeps):
    client = make_client([FakeResponse(500) for _ in range(10)])
    with pytest.raises(SourceError, match="after 4 attempts"):
        client.get_bytes(URL)
    assert len(client.session.calls) == extract.MAX_ATTEMPTS


@pytest.mark.parametrize("status", [301, 403, 404])
def test_permanent_failures_are_not_retried(make_client, sleeps, status):
    client = make_client([FakeResponse(status)])
    with pytest.raises(SourceError, match=str(status)):
        client.get_bytes(URL)
    assert len(client.session.calls) == 1


def test_download_is_saved_verbatim_and_recorded(make_client, sleeps, tmp_path):
    body = b'{"a":  1}'
    client = make_client([FakeResponse(content=body)])
    assert extract.fetch_json(client, URL, "x.json", tmp_path) == {"a": 1}
    assert (tmp_path / "x.json").read_bytes() == body
    entry = json.loads((tmp_path / "manifest.json").read_text())["x.json"]
    assert entry["url"] == URL and entry["bytes"] == len(body) and len(entry["sha256"]) == 64
    assert datetime.fromisoformat(entry["retrieved_at"]).utcoffset() == timedelta(0)
    assert "tester@test.invalid" not in (tmp_path / "manifest.json").read_text()


def test_second_fetch_uses_cache_and_refresh_downloads_again(make_client, sleeps, tmp_path):
    client = make_client([FakeResponse(content=b"[1]"), FakeResponse(content=b"[2]")])
    assert extract.fetch_json(client, URL, "x.json", tmp_path) == [1]
    assert extract.fetch_json(client, URL, "x.json", tmp_path) == [1]
    assert len(client.session.calls) == 1
    assert extract.fetch_json(client, URL, "x.json", tmp_path, refresh=True) == [2]


def test_altered_cache_file_is_downloaded_again(make_client, sleeps, tmp_path):
    client = make_client([FakeResponse(content=b"[1]"), FakeResponse(content=b"[1]")])
    extract.fetch_json(client, URL, "x.json", tmp_path)
    (tmp_path / "x.json").write_bytes(b"[999]")
    assert extract.fetch_json(client, URL, "x.json", tmp_path) == [1]


@pytest.mark.parametrize(
    "bad", [FakeResponse(content=b"<html>error</html>"), FakeResponse(403)]
)
def test_failed_refresh_keeps_the_previous_good_file(make_client, sleeps, tmp_path, bad):
    client = make_client([FakeResponse(content=b"[1]"), bad])
    extract.fetch_json(client, URL, "x.json", tmp_path)
    manifest = (tmp_path / "manifest.json").read_bytes()
    with pytest.raises(SourceError):
        extract.fetch_json(client, URL, "x.json", tmp_path, refresh=True)
    assert (tmp_path / "x.json").read_bytes() == b"[1]"
    assert (tmp_path / "manifest.json").read_bytes() == manifest


def test_cik_keeps_leading_zeros_and_unknown_ticker_fails(load_fixture):
    mapping = load_fixture("company_tickers_synthetic.json")
    assert extract.resolve_companies(mapping, ["syn"]) == [
        {"ticker": "SYN", "cik": "0000000001", "name": "Synthetic Co"}
    ]
    with pytest.raises(SourceError, match="NOPE"):
        extract.resolve_companies(mapping, ["NOPE"])
