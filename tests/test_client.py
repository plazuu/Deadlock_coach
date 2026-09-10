from __future__ import annotations

import httpx
import pytest
import respx

from limpet.api.client import BASE_URL, DeadlockAPIError, DeadlockClient, MetadataNotReady


@pytest.fixture
def client():
    c = DeadlockClient(api_key="secret", min_interval_s=0.0)
    yield c
    c.close()


@respx.mock
def test_sends_api_key_header(client):
    route = respx.get(f"{BASE_URL}/v1/assets/heroes").mock(
        return_value=httpx.Response(200, json=[{"id": 1, "name": "Abrams"}])
    )
    client.assets_heroes()
    assert route.calls.last.request.headers["X-API-KEY"] == "secret"


@respx.mock
def test_retries_on_429_then_succeeds(client):
    route = respx.get(f"{BASE_URL}/v1/players/5/rank").mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": "0"}, json={}),
            httpx.Response(200, json={"badge": 63}),
        ]
    )
    assert client.player_rank(5) == {"badge": 63}
    assert route.call_count == 2


@respx.mock
def test_gives_up_after_retries(client):
    respx.get(f"{BASE_URL}/v1/players/5/rank").mock(return_value=httpx.Response(503, json={}))
    with pytest.raises(DeadlockAPIError) as ei:
        client.player_rank(5)
    assert ei.value.status == 503


@respx.mock
def test_metadata_not_ready(client):
    respx.get(f"{BASE_URL}/v1/matches/42/metadata").mock(
        return_value=httpx.Response(404, json={"error": "not found"})
    )
    with pytest.raises(MetadataNotReady):
        client.match_metadata(42)


@respx.mock
def test_none_params_are_dropped(client):
    route = respx.get(f"{BASE_URL}/v1/players/9/match-history").mock(
        return_value=httpx.Response(200, json=[])
    )
    client.match_history(9, force_refetch=False)
    assert "force_refetch" not in route.calls.last.request.url.params
