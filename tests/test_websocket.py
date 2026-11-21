"""Tests against a small local fake of the NeuroInfoAPI WebSocket protocol."""

import asyncio
import json
import threading

import pytest
from websockets.asyncio.server import serve
from websockets.protocol import State

from neuroinfoapi_client import NeuroApiError, NeuroInfoApiWebsocketClient

TOKEN = "good-token"
TOKEN_2 = "other-good-token"


class FakeServer:
    def __init__(self):
        self.connections = []
        self.received = []
        self.answer_pings = True
        self.welcome_delay = 0.0
        self.redirect_to: "str | None" = None
        self.port = None
        self._server = None

    def process_request(self, connection, request):
        if self.redirect_to is not None:
            response = connection.respond(302, "")
            response.headers["Location"] = self.redirect_to
            return response
        return None

    async def handler(self, ws):
        if ws.request.headers.get("Authorization") not in (f"Bearer {TOKEN}", f"Bearer {TOKEN_2}"):
            await ws.close(4001, "invalidToken")
            return
        self.connections.append(ws)
        await asyncio.sleep(self.welcome_delay)
        await ws.send(json.dumps({"type": "welcome", "data": {"sessionId": f"s{len(self.connections)}"}}))
        async for raw in ws:
            msg = json.loads(raw)
            self.received.append(msg)
            kind, data = msg["type"], msg["data"]
            if kind == "addEvent":
                await ws.send(json.dumps({"type": "addSuccess", "data": {**data, "subscribed": True}}))
                if data["eventType"] == "streamOnline":
                    # lets the sync run() test receive an event right after subscribing
                    await self.push("streamOnline", {"isLive": True, "title": "hi"}, ws)
            elif kind == "removeEvent":
                await ws.send(json.dumps({"type": "removeSuccess", "data": {**data, "unsubscribed": True}}))
            elif kind == "ping" and self.answer_pings:
                await ws.send(json.dumps({"type": "pong", "data": {}}))

    async def push(self, event_type, event_data, ws=None):
        msg = {"type": "event", "data": {"eventType": event_type, "eventData": event_data, "timestamp": 123}}
        await (ws or self.connections[-1]).send(json.dumps(msg))

    async def start(self):
        self._server = await serve(self.handler, "127.0.0.1", 0, process_request=self.process_request)
        self.port = self._server.sockets[0].getsockname()[1]

    async def stop(self):
        assert self._server is not None
        self._server.close()
        await self._server.wait_closed()

    async def __aenter__(self):
        await self.start()
        return self

    async def __aexit__(self, *exc):
        await self.stop()

    def client(self, token=TOKEN, **kwargs):
        return NeuroInfoApiWebsocketClient(token, api_base_url=f"127.0.0.1:{self.port}/api/v2", use_tls=False, **kwargs)


async def wait_for(predicate, timeout=5.0):
    async def loop():
        while not predicate():
            await asyncio.sleep(0.01)

    await asyncio.wait_for(loop(), timeout)


async def test_connect_subscribe_and_receive():
    async with FakeServer() as server:
        ws = server.client()
        received = []
        connected = []
        ws.on("_connected", connected.append)

        @ws.on("streamUpdate")
        async def on_update(data, timestamp):
            received.append((data, timestamp))

        await ws.connect()
        assert ws.is_connected and ws.session_id == "s1" and connected == ["s1"]

        await wait_for(lambda: ws.get_subscribed_events() == ["streamUpdate"])
        await server.push("streamUpdate", {"title": "hi"})
        await wait_for(lambda: received)
        assert received == [({"title": "hi"}, 123)]

        await ws.disconnect()
        assert not ws.is_connected


async def test_unsubscribe_sends_remove_event():
    async with FakeServer() as server:
        ws = server.client()
        await ws.connect()
        unsubscribe = ws.on("scheduleUpdate", lambda data, ts: None)
        await wait_for(lambda: ws.get_subscribed_events() == ["scheduleUpdate"])
        unsubscribe()
        await wait_for(lambda: {"type": "removeEvent", "data": {"eventType": "scheduleUpdate"}} in server.received)
        await wait_for(lambda: ws.get_subscribed_events() == [])
        await ws.disconnect()


async def test_invalid_token_raises():
    async with FakeServer() as server:
        ws = server.client(token="bad")
        with pytest.raises(NeuroApiError) as info:
            await ws.connect()
        assert info.value.code == "WS_CLOSED"
        assert info.value.status == 4001


async def test_reconnect_and_resubscribe():
    async with FakeServer() as server:
        ws = server.client(reconnect_base_delay=0.1)
        events = []
        ws.on("_reconnecting", lambda attempt, delay: events.append(("reconnecting", attempt)))
        ws.on("_disconnected", lambda code, reason: events.append(("disconnected", code)))
        ws.on("subathonUpdate", lambda data, ts: None)
        await ws.connect()
        await wait_for(lambda: ws.get_subscribed_events() == ["subathonUpdate"])

        await server.connections[0].close(1012, "restart")
        await wait_for(lambda: ws.session_id == "s2")
        await wait_for(lambda: ws.get_subscribed_events() == ["subathonUpdate"])
        assert ("disconnected", 1012) in events and ("reconnecting", 1) in events
        await ws.disconnect()


async def test_heartbeat_timeout():
    async with FakeServer() as server:
        server.answer_pings = False
        ws = server.client(auto_reconnect=False, heartbeat_timeout=1)
        errors, disconnects = [], []
        ws.on("_error", errors.append)
        ws.on("_disconnected", lambda code, reason: disconnects.append(code))
        await ws.connect()
        await wait_for(lambda: disconnects, timeout=5)
        assert disconnects == [4002]
        assert errors[0].code == "WS_HEARTBEAT_TIMEOUT"
        await ws.wait_closed()


def test_sync_run_and_stop():
    """run() blocks without any asyncio code; stop() from a callback ends it."""
    server = FakeServer()
    server_loop = asyncio.new_event_loop()
    started = threading.Event()

    def serve_forever():
        asyncio.set_event_loop(server_loop)
        server_loop.run_until_complete(server.start())
        started.set()
        server_loop.run_forever()

    thread = threading.Thread(target=serve_forever, daemon=True)
    thread.start()
    assert started.wait(5)

    try:
        ws = server.client()
        received = []

        @ws.on("streamOnline")
        def on_online(data, timestamp):
            received.append(data["title"])
            ws.stop()

        ws.run()  # returns after stop()
        assert received == ["hi"]
        assert not ws.is_connected
    finally:
        asyncio.run_coroutine_threadsafe(server.stop(), server_loop).result(5)
        server_loop.call_soon_threadsafe(server_loop.stop)
        thread.join(5)


async def test_run_inside_async_code_is_rejected():
    ws = NeuroInfoApiWebsocketClient("t")
    with pytest.raises(RuntimeError):
        ws.run()


async def test_disconnect_clears_subscribed_events():
    async with FakeServer() as server:
        ws = server.client()
        ws.on("scheduleUpdate", lambda data, ts: None)
        await ws.connect()
        await wait_for(lambda: ws.get_subscribed_events() == ["scheduleUpdate"])
        await ws.disconnect()
        assert ws.get_subscribed_events() == []


async def test_cancelled_connect_does_not_connect_later():
    async with FakeServer() as server:
        server.welcome_delay = 0.3
        ws = server.client()
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(ws.connect(), 0.1)
        await asyncio.sleep(0.5)
        assert not ws.is_connected
        await wait_for(lambda: server.connections[0].state is State.CLOSED)


async def test_set_token_reconnects_without_ending_wait_closed():
    async with FakeServer() as server:
        ws = server.client()
        ws.on("streamUpdate", lambda data, ts: None)
        await ws.connect()
        waiter = asyncio.ensure_future(ws.wait_closed())

        await ws.set_token(TOKEN_2)
        assert ws.is_connected and ws.session_id == "s2"
        assert server.connections[1].request.headers["Authorization"] == f"Bearer {TOKEN_2}"
        await wait_for(lambda: ws.get_subscribed_events() == ["streamUpdate"])
        await asyncio.sleep(0.05)
        assert not waiter.done()

        await ws.disconnect()
        await asyncio.wait_for(waiter, 1)


async def test_set_token_with_rejected_token_raises_and_stops():
    async with FakeServer() as server:
        ws = server.client()
        await ws.connect()
        with pytest.raises(NeuroApiError) as info:
            await ws.set_token("bad")
        assert info.value.status == 4001
        await asyncio.wait_for(ws.wait_closed(), 1)
        assert not ws.is_connected


async def test_redirect_to_other_host_is_refused():
    async with FakeServer() as server:
        server.redirect_to = f"ws://localhost:{server.port}/api/v2/ws"
        ws = server.client(auto_reconnect=False)
        with pytest.raises(NeuroApiError) as info:
            await ws.connect()
        assert "another host" in info.value.message
        assert server.connections == []


async def test_cancelling_one_connect_keeps_the_other():
    async with FakeServer() as server:
        server.welcome_delay = 0.3
        ws = server.client()
        first = asyncio.ensure_future(ws.connect())
        await asyncio.sleep(0.05)
        second = asyncio.ensure_future(ws.connect())
        await asyncio.sleep(0.05)
        first.cancel()
        await asyncio.wait_for(second, 2)
        assert ws.is_connected
        await ws.disconnect()


def test_redirect_same_host_with_default_port_is_allowed():
    from websockets.datastructures import Headers
    from websockets.exceptions import InvalidStatus
    from websockets.http11 import Response

    from neuroinfoapi_client.websocket import _same_origin_connect

    conn = _same_origin_connect("wss://demo.example/api/v2/ws")
    for location in ("/new-path", "wss://demo.example:443/new-path"):
        result = conn.process_redirect(InvalidStatus(Response(302, "Found", Headers([("Location", location)]))))
        assert result == "wss://demo.example" + ("" if location.startswith("/") else ":443") + "/new-path"
    other = conn.process_redirect(InvalidStatus(Response(302, "Found", Headers([("Location", "wss://evil.example/")]))))
    assert isinstance(other, NeuroApiError)


def test_unknown_event_rejected():
    ws = NeuroInfoApiWebsocketClient("t")
    with pytest.raises(ValueError):
        ws.on("nope", lambda: None)  # type: ignore[call-overload]
