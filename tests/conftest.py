import json
import socket
from pathlib import Path

import pytest

from finflow import extract

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Make any real connection attempt fail, so tests can never reach the SEC."""

    def refuse(*args, **kwargs):
        raise AssertionError("tests must not use the network")

    monkeypatch.setattr(socket.socket, "connect", refuse)


@pytest.fixture
def sleeps(monkeypatch):
    """Replace time.sleep and the clock, recording each wait instead of waiting."""
    recorded = []
    clock = {"now": 100.0}

    def fake_sleep(seconds):
        recorded.append(seconds)
        clock["now"] += seconds

    monkeypatch.setattr(extract.time, "sleep", fake_sleep)
    monkeypatch.setattr(extract.time, "monotonic", lambda: clock["now"])
    return recorded


@pytest.fixture
def load_fixture():
    return lambda name: json.loads((FIXTURES / name).read_text())


@pytest.fixture
def metrics():
    path = Path(__file__).parent.parent / "config" / "metrics.json"
    return json.loads(path.read_text())["metrics"]


class FakeResponse:
    def __init__(self, status_code=200, content=b"{}", headers=None):
        self.status_code = status_code
        self.content = content
        self.headers = headers or {}


class FakeSession:
    """Returns prepared responses in order, or by URL when given a dictionary."""

    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if isinstance(self.responses, dict):
            item = self.responses.get(url, FakeResponse(404))
        else:
            item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


@pytest.fixture
def make_client():
    def make(responses):
        return extract.SecClient("FinFlow tests tester@test.invalid", FakeSession(responses))

    return make


BASE = "https://data.sec.gov"
URLS = {
    "https://www.sec.gov/files/company_tickers.json": "company_tickers_synthetic.json",
    f"{BASE}/api/xbrl/companyfacts/CIK0000000001.json": "companyfacts_synthetic.json",
    f"{BASE}/submissions/CIK0000000001.json": "submissions_synthetic.json",
    f"{BASE}/submissions/CIK0000000001-submissions-001.json": "submissions_page_synthetic.json",
}


@pytest.fixture
def client(make_client, sleeps):
    return make_client(
        {url: FakeResponse(content=(FIXTURES / name).read_bytes()) for url, name in URLS.items()}
    )
