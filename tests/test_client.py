import httpx
import pytest

from neuroinfoapi_client import AsyncNeuroInfoApiClient, NeuroApiError, NeuroInfoApiClient, utils
from neuroinfoapi_client._internal import USER_AGENT, create_client_urls

requests: list[httpx.Request] = []


def handler(request: httpx.Request) -> httpx.Response:
    requests.append(request)
    path = request.url.path
    if path == "/api/v2/twitch/stream":
        return httpx.Response(200, json={"data": {"isLive": False, "title": None, "channel": None}})
    if path == "/api/v2/subathon":
        return httpx.Response(
            404, json={"error": {"code": "SB1", "message": "No active subathon", "timestamp": 1, "path": "/subathon"}}
        )
    if path == "/api/v2/broken":
        return httpx.Response(200, content=b"not json")
    if path == "/api/v2/plain500":
        return httpx.Response(500, content=b"oops")
    return httpx.Response(200, json={"data": dict(request.url.params)})


@pytest.fixture(autouse=True)
def clear_requests():
    requests.clear()


def make_sync(token=None) -> NeuroInfoApiClient:
    return NeuroInfoApiClient(token, transport=httpx.MockTransport(handler))


def test_urls():
    urls = create_client_urls()
    assert urls.api == "https://neuroinfoapi.dev/api/v2"
    assert urls.websocket == "wss://neuroinfoapi.dev/api/v2/ws"
    local = create_client_urls("localhost:8787/api/v2/", use_tls=False)
    assert local.api == "http://localhost:8787/api/v2"
    assert local.websocket == "ws://localhost:8787/api/v2/ws"
    with pytest.raises(ValueError):
        create_client_urls("host/api?x=1")


def test_success_returns_data_and_sends_headers():
    stream = make_sync("secret").get_current_stream()
    assert stream == {"isLive": False, "title": None, "channel": None}
    sent = requests[0]
    assert sent.headers["Authorization"] == "Bearer secret"
    assert sent.headers["User-Agent"] == USER_AGENT


def test_no_token_no_auth_header():
    client = make_sync()
    client.get_current_stream()
    assert "Authorization" not in requests[0].headers
    client.set_api_token("x")
    client.get_current_stream()
    assert requests[1].headers["Authorization"] == "Bearer x"


def test_api_error_is_raised():
    with pytest.raises(NeuroApiError) as info:
        make_sync().get_current_subathons()
    assert info.value.code == "SB1"
    assert info.value.status == 404
    assert info.value.path == "/subathon"
    assert str(info.value) == "[SB1] No active subathon"


def test_http_and_json_errors():
    client = make_sync()
    with pytest.raises(NeuroApiError) as broken:
        client.request("/broken")
    assert broken.value.code == "INVALID_JSON"
    with pytest.raises(NeuroApiError) as plain:
        client.request("/plain500")
    assert plain.value.code == "HTTP_ERROR" and plain.value.status == 500


def test_network_error():
    def boom(request):
        raise httpx.ConnectError("refused")

    with pytest.raises(NeuroApiError) as info:
        NeuroInfoApiClient(transport=httpx.MockTransport(boom)).get_current_stream()
    assert info.value.code == "NETWORK"
    assert isinstance(info.value.__cause__, httpx.ConnectError)


def test_query_params():
    client = make_sync()
    assert client.get_schedule(12) == {"week": "12"}
    assert client.get_schedule(12, 2025) == {"week": "12", "year": "2025"}
    assert client.get_blog_feed(raw=True) == {"raw": "true"}
    assert client.get_blog_feed() == {}
    search = client.get_schedule_search("minecraft", limit=5, cursor={"year": 2025, "week": 3})
    assert search == {"query": "minecraft", "limit": "5", "cursorYear": "2025", "cursorWeek": "3"}
    assert client.get_shop_product("abc", "EUR") == {"id": "abc", "currency": "EUR"}


def test_context_manager():
    with make_sync() as client:
        assert client.get_vod("1") == {"id": "1"}


async def test_async_client():
    async with AsyncNeuroInfoApiClient("t", transport=httpx.MockTransport(handler)) as client:
        stream = await client.get_current_stream()
        assert stream["isLive"] is False
        assert (await client.get_vod("123")) == {"id": "123"}
        with pytest.raises(NeuroApiError) as info:
            await client.get_current_subathons()
        assert info.value.code == "SB1"


def test_utils():
    assert utils.is_schedule_final("confirmed")
    entry = {"day": 0, "time": 0, "message": "", "type": "TBD", "segments": []}
    assert utils.is_schedule_entry_online(entry)  # type: ignore[arg-type]
    assert not utils.is_schedule_entry_offline(entry)  # type: ignore[arg-type]
    assert utils.did_linked_stream_happen({"reason": "timeout"})
    assert utils.is_linked_stream_live({"stream": {"isLive": True}})
    assert not utils.has_schedule_image({"imageUrl": "  "})  # type: ignore[arg-type]
    assert utils.is_live({"isLive": True})  # type: ignore[arg-type]


@pytest.mark.parametrize("path", ["https://evil.example/x", "//evil.example/x", "twitch/stream"])
def test_request_only_accepts_api_paths(path):
    with pytest.raises(ValueError):
        make_sync("secret").request(path)
    assert requests == []
