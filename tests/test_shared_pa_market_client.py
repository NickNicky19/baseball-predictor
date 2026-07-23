from __future__ import annotations

from src.evaluation.shadow_live_provider import TheOddsAPIShadowClient


class _Response:
    status_code = 200
    content = b"{}"
    headers = {
        "x-requests-remaining": "100",
        "x-requests-used": "1",
        "x-requests-last": "3",
    }


class _Session:
    def __init__(self) -> None:
        self.headers = {}
        self.seen = None

    def get(self, url, *, params, timeout):
        self.seen = (url, dict(params), timeout)
        return _Response()


def test_shared_pa_market_fetch_is_one_event_request_with_all_locked_keys(monkeypatch) -> None:
    monkeypatch.setenv("ODDS_API_KEY", "secret-never-serialized")
    session = _Session()
    client = TheOddsAPIShadowClient(session=session)
    response = client.fetch_shared_pa_markets("event-1")
    assert response.status_code == 200
    url, params, timeout = session.seen
    assert url.endswith("/sports/baseball_mlb/events/event-1/odds")
    assert params["bookmakers"] == "draftkings"
    assert params["markets"] == "batter_hits,batter_home_runs,batter_total_bases"
    assert params["includeSids"] == "true"
    assert params["apiKey"] == "secret-never-serialized"
    assert timeout == 25
