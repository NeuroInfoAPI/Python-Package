"""Asyncio WebSocket client for real-time NeuroInfoAPI events."""

from __future__ import annotations

import asyncio
import enum
import inspect
import json
import random
import warnings
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Optional, Set, Union, overload
from urllib.parse import urlencode, urlsplit, urlunsplit

import httpx
from websockets.asyncio.client import ClientConnection
from websockets.asyncio.client import connect as _ws_connect
from websockets.exceptions import ConnectionClosed, InvalidStatus
from websockets.protocol import State
from websockets.uri import parse_uri

from ._internal import USER_AGENT, boot_check, create_client_urls, derive_api_url, logger, parse_api_error
from .errors import NeuroApiError
from .types import WS_EVENT_TYPES, WS_SYSTEM_EVENTS, AuthMethod, WsEventType, WsSystemEvent

__all__ = ["NeuroInfoApiWebsocketClient"]

Callback = Callable[..., Union[None, Awaitable[None]]]
F = Any  # decorated function, returned unchanged

MAX_RECONNECT_DELAY = 30.0


class _same_origin_connect(_ws_connect):
    """Follows redirects only to the same host, so the token is never sent anywhere else."""

    def process_redirect(self, exc: Exception) -> Union[Exception, str]:
        result = super().process_redirect(exc)
        if isinstance(result, str):
            old, new = parse_uri(self.uri), parse_uri(result)  # fills in default ports (443/80)
            if (old.secure, old.host, old.port) != (new.secure, new.host, new.port):
                return NeuroApiError("WS_ERROR", f"Refusing to follow redirect to another host: {result}")
        return result


class _SubState(enum.Enum):
    UNSUBSCRIBED = "unsubscribed"
    SUBSCRIBING = "subscribing"
    SUBSCRIBED = "subscribed"
    UNSUBSCRIBING = "unsubscribing"


@dataclass(eq=False)
class _Subscription:
    listeners: List[Callback] = field(default_factory=list)
    state: _SubState = _SubState.UNSUBSCRIBED


class NeuroInfoApiWebsocketClient:
    """WebSocket client with automatic reconnect, heartbeat and subscription recovery.

    Register listeners with :meth:`on` (works as decorator, too), then call :meth:`run`::

        ws = NeuroInfoApiWebsocketClient("your-api-token")

        @ws.on("streamOnline")
        def on_online(data, timestamp):
            print("Live:", data["title"])

        ws.run()  # blocks until Ctrl+C or ws.stop()

    In ``async`` code use ``await ws.connect()`` and ``await ws.wait_closed()`` instead.

    Callbacks may be normal functions or ``async`` functions. Data events receive
    ``(data, timestamp)``; see :data:`~neuroinfoapi_client.types.WsSystemEvent`
    for the arguments of the ``_``-prefixed system events.

    All durations are in **seconds**.

    :param token: API token (required).
    :param auth_method: ``"header"`` (default) sends ``Authorization: Bearer`` in the handshake,
        ``"ticket"`` fetches a one-time ticket via HTTP first.
    :param auto_reconnect: Reconnect automatically after a lost connection. Default ``True``.
    :param max_reconnect_attempts: Give up after this many failed attempts (``0`` = unlimited). Default ``10``.
    :param reconnect_base_delay: Base delay for exponential backoff. Default ``1.0``.
    :param heartbeat_monitoring: Send JSON pings and reconnect when no pong arrives. Default ``True``.
    :param heartbeat_interval: Ping interval, minimum ``5``. Default ``30``.
    :param heartbeat_timeout: Pong timeout, minimum ``1``. Default ``10``.
    :param connect_timeout: Timeout for ticket fetch, handshake and welcome message, minimum ``1``. Default ``15``.
    :param websocket_url: Full WebSocket URL override (otherwise derived from ``api_base_url``/``use_tls``).
    """

    def __init__(
        self,
        token: str,
        *,
        api_base_url: Optional[str] = None,
        use_tls: Optional[bool] = None,
        websocket_url: Optional[str] = None,
        auth_method: AuthMethod = "header",
        auto_reconnect: bool = True,
        max_reconnect_attempts: int = 10,
        reconnect_base_delay: float = 1.0,
        heartbeat_monitoring: bool = True,
        heartbeat_interval: float = 30.0,
        heartbeat_timeout: float = 10.0,
        connect_timeout: float = 15.0,
    ) -> None:
        if auth_method not in ("header", "ticket"):
            raise ValueError('auth_method must be "header" or "ticket"')
        self._token = token
        self.auth_method: AuthMethod = auth_method

        if websocket_url:
            api = create_client_urls(api_base_url, use_tls).api if api_base_url is not None else derive_api_url(websocket_url)
            self._api_url, self._ws_url = api, websocket_url
        else:
            urls = create_client_urls(api_base_url, use_tls)
            self._api_url, self._ws_url = urls.api, urls.websocket

        self.auto_reconnect = auto_reconnect
        self.max_reconnect_attempts = max_reconnect_attempts
        self.reconnect_base_delay = reconnect_base_delay
        self.heartbeat_interval = heartbeat_interval
        self.heartbeat_timeout = heartbeat_timeout
        self.connect_timeout = connect_timeout
        self._heartbeat_monitoring = heartbeat_monitoring

        self._subs: Dict[str, _Subscription] = {}
        self._system: Dict[str, List[Callback]] = {}

        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._runner: Optional[asyncio.Task[None]] = None
        self._connected_future: Optional[asyncio.Future[None]] = None
        self._restart_future: Optional[asyncio.Future[None]] = None
        self._connect_waiters = 0
        self._ws: Optional[ClientConnection] = None
        self._session_id: Optional[str] = None
        self._closing = False
        self._reconnect_attempts = 0
        self._heartbeat_task: Optional[asyncio.Task[None]] = None
        self._pong_event: Optional[asyncio.Event] = None
        self._heartbeat_timed_out = False
        self._background: Set[asyncio.Task[Any]] = set()

        boot_check(self._api_url)

    # ----- settings -----

    @property
    def max_reconnect_attempts(self) -> int:
        """Maximum number of reconnect attempts. ``0`` means unlimited."""
        return self._max_reconnect_attempts

    @max_reconnect_attempts.setter
    def max_reconnect_attempts(self, value: int) -> None:
        self._max_reconnect_attempts = max(0, int(value))

    @property
    def reconnect_base_delay(self) -> float:
        """Base delay in seconds for the exponential reconnect backoff (minimum 0.1)."""
        return self._reconnect_base_delay

    @reconnect_base_delay.setter
    def reconnect_base_delay(self, value: float) -> None:
        self._reconnect_base_delay = max(0.1, float(value))

    @property
    def heartbeat_interval(self) -> float:
        """Seconds between JSON pings (minimum 5)."""
        return self._heartbeat_interval

    @heartbeat_interval.setter
    def heartbeat_interval(self, value: float) -> None:
        self._heartbeat_interval = max(5.0, float(value))

    @property
    def heartbeat_timeout(self) -> float:
        """Seconds to wait for a pong (minimum 1)."""
        return self._heartbeat_timeout

    @heartbeat_timeout.setter
    def heartbeat_timeout(self, value: float) -> None:
        self._heartbeat_timeout = max(1.0, float(value))

    @property
    def connect_timeout(self) -> float:
        """Seconds allowed for ticket fetch, handshake and welcome message (minimum 1)."""
        return self._connect_timeout

    @connect_timeout.setter
    def connect_timeout(self, value: float) -> None:
        self._connect_timeout = max(1.0, float(value))

    @property
    def heartbeat_monitoring(self) -> bool:
        """Send JSON pings and treat a missing pong as a lost connection."""
        return self._heartbeat_monitoring

    @heartbeat_monitoring.setter
    def heartbeat_monitoring(self, value: bool) -> None:
        if self._heartbeat_monitoring == value:
            return
        self._heartbeat_monitoring = value
        if not self.is_connected or self._ws is None:
            return
        if value:
            self._start_heartbeat(self._ws)
        else:
            self._stop_heartbeat()

    # ----- state -----

    @property
    def is_connected(self) -> bool:
        """``True`` when the socket is open and the server sent its welcome message."""
        return self._ws is not None and self._session_id is not None and self._ws.state is State.OPEN

    @property
    def session_id(self) -> Optional[str]:
        """Session ID from the server (``None`` while not connected)."""
        return self._session_id

    def get_session_id(self) -> Optional[str]:
        return self._session_id

    def get_subscribed_events(self) -> List[WsEventType]:
        """Event types the server confirmed as subscribed."""
        return [name for name, sub in self._subs.items() if sub.state is _SubState.SUBSCRIBED]  # type: ignore[misc]

    # ----- connection lifecycle -----

    async def connect(self) -> None:
        """Connects and waits for the server's welcome message.

        Raises :class:`NeuroApiError` when the first connection attempt fails. After a
        successful connect, lost connections are re-established in the background
        (see ``auto_reconnect``).
        """
        runner = self._runner
        if runner is not None and not runner.done() and self._connected_future is not None:
            await self._wait_connected(self._connected_future)
            return

        loop = asyncio.get_running_loop()
        self._loop = loop
        self._closing = False
        self._reconnect_attempts = 0
        future: asyncio.Future[None] = loop.create_future()
        self._connected_future = future
        self._runner = loop.create_task(self._run(future))
        await self._wait_connected(future)

    async def _wait_connected(self, future: asyncio.Future[None]) -> None:
        self._connect_waiters += 1
        try:
            await asyncio.shield(future)
        except asyncio.CancelledError:
            # The last waiting caller gave up (e.g. a timeout): do not keep connecting in the background.
            if self._connect_waiters == 1 and not future.done() and self._connected_future is future:
                future.cancel()
                self._closing = True
                self._spawn(self.disconnect())
            raise
        finally:
            self._connect_waiters -= 1

    async def disconnect(self) -> None:
        """Closes the connection and stops reconnecting. Listeners are kept."""
        self._closing = True
        runner, self._runner = self._runner, None
        if runner is not None and not runner.done() and runner is not asyncio.current_task():
            runner.cancel()
            await asyncio.wait({runner})

        self._stop_heartbeat()
        ws, self._ws = self._ws, None
        self._session_id = None
        self._reset_subscriptions()
        restart, self._restart_future = self._restart_future, None
        if restart is not None and not restart.done():
            restart.set_exception(NeuroApiError("WS_CONNECT_CANCELLED", "Client was disconnected"))
        if ws is not None:
            try:
                await ws.close(1000, "Client disconnect")
            except Exception:  # noqa: BLE001
                pass
            self._emit_system("_disconnected", 1000, "Client disconnect")

    async def wait_closed(self) -> None:
        """Waits until the client stops: after :meth:`disconnect` or when reconnecting gave up.

        A token change with :meth:`set_token` does not count as stopping."""
        runner = self._runner
        if runner is not None and not runner.done():
            await asyncio.wait({runner})

    def run(self) -> None:
        """Connects and blocks until the client stops. No ``asyncio`` knowledge needed::

            ws = NeuroInfoApiWebsocketClient("your-api-token")
            ws.on("streamOnline", lambda data, timestamp: print("Live!"))
            ws.run()

        Stops on Ctrl+C, after :meth:`stop`, or when reconnecting gave up.
        Raises :class:`NeuroApiError` if the first connection fails.
        Inside ``async`` code use ``await connect()`` and ``await wait_closed()`` instead.
        """
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            pass
        else:
            raise RuntimeError("run() cannot be used inside async code. Use 'await ws.connect()' and 'await ws.wait_closed()'.")

        async def main() -> None:
            try:
                await self.connect()
                await self.wait_closed()
            finally:
                await self.disconnect()

        try:
            asyncio.run(main())
        except KeyboardInterrupt:
            pass

    def stop(self) -> None:
        """Disconnects without ``await``, e.g. from a callback or another thread. Ends :meth:`run`."""
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        try:
            running: Optional[asyncio.AbstractEventLoop] = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is loop:
            self._spawn(self.disconnect())
        else:
            loop.call_soon_threadsafe(lambda: self._spawn(self.disconnect()))

    async def set_token(self, token: str) -> None:
        """Updates the token and reconnects with it if the client is connected.

        Raises :class:`NeuroApiError` (and stops the client) if the new token is rejected.
        While the client is reconnecting, the next attempt simply uses the new token.
        """
        self._token = token
        ws = self._ws
        if self._runner is None or self._runner.done() or ws is None or not self.is_connected:
            return
        future: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        self._connected_future = self._restart_future = future
        await ws.close(1000, "Token changed")
        await asyncio.shield(future)

    async def set_api_token(self, token: str) -> None:
        """Alias of :meth:`set_token`."""
        await self.set_token(token)

    async def destroy(self) -> None:
        """Removes all listeners and disconnects."""
        self._subs.clear()
        self._system.clear()
        await self.disconnect()

    async def __aenter__(self) -> "NeuroInfoApiWebsocketClient":
        await self.connect()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.disconnect()

    # ----- listeners -----

    @overload
    def on(self, event: Union[WsEventType, WsSystemEvent], callback: Callback) -> Callable[[], None]: ...
    @overload
    def on(self, event: Union[WsEventType, WsSystemEvent], callback: None = None) -> Callable[[F], F]: ...

    def on(self, event: str, callback: Optional[Callback] = None) -> Any:
        """Registers a listener for a data event (e.g. ``"streamOnline"``) or a system event (e.g. ``"_connected"``).

        Called with a callback it returns an unsubscribe function. Without a
        callback it works as a decorator and returns the decorated function.
        Data events are subscribed on the server automatically.
        """
        if callback is None:

            def decorator(fn: F) -> F:
                self.on(event, fn)  # type: ignore[arg-type]
                return fn

            return decorator

        if event in WS_EVENT_TYPES:
            if event == "xFeedUpdate":
                warnings.warn("xFeedUpdate is deprecated. Subscribe to xFeedNewEntries instead.", DeprecationWarning, stacklevel=2)
            sub = self._subs.get(event)
            if sub is None:
                sub = self._subs[event] = _Subscription()
            entry = callback
            sub.listeners.append(entry)
            self._sync_subscription(event, sub)

            def unsubscribe() -> None:
                self._remove_event_listener(event, entry)

            return unsubscribe

        if event in WS_SYSTEM_EVENTS:
            self._system.setdefault(event, []).append(callback)
            return lambda: self.off(event, callback)  # type: ignore[arg-type]

        raise ValueError(f"Unknown event: {event!r}")

    def off(self, event: Union[WsEventType, WsSystemEvent], callback: Callback) -> None:
        """Removes a listener registered with :meth:`on`."""
        if event in WS_EVENT_TYPES:
            self._remove_event_listener(event, callback)
            return
        listeners = self._system.get(event)
        if listeners and callback in listeners:
            listeners.remove(callback)
            if not listeners:
                del self._system[event]

    def request_event_list(self) -> None:
        """Asks the server for subscribed/available events. The answer arrives as ``_message``."""
        self._send_soon({"type": "listEvents", "data": {}})

    # ----- internals: runner -----

    async def _run(self, future: asyncio.Future[None]) -> None:
        initial = True
        try:
            while True:
                try:
                    ws = await self._open_connection()
                except asyncio.CancelledError:
                    raise
                except Exception as error:  # noqa: BLE001
                    parsed = _to_ws_error(error)
                    if initial:
                        if not future.done():
                            future.set_exception(parsed)
                        return
                    self._emit_system("_error", parsed)
                    if not await self._wait_before_reconnect():
                        return
                    continue

                if initial:
                    initial = False
                    if not future.done():
                        future.set_result(None)

                code, reason = await self._read_loop(ws)
                self._stop_heartbeat()
                if self._ws is ws:
                    self._ws = None
                    self._session_id = None
                    self._reset_subscriptions()
                self._emit_system("_disconnected", code, reason)

                restart, self._restart_future = self._restart_future, None
                if self._closing:
                    return
                if restart is not None:
                    # set_token(): reconnect right away; a failure is reported to set_token().
                    future, initial = restart, True
                    self._reconnect_attempts = 0
                    continue
                if not self.auto_reconnect:
                    return
                if not await self._wait_before_reconnect():
                    return
        except asyncio.CancelledError:
            if not future.done():
                future.set_exception(NeuroApiError("WS_CONNECT_CANCELLED", "WebSocket connection was cancelled"))
            raise

    async def _wait_before_reconnect(self) -> bool:
        if self._closing or not self.auto_reconnect:
            return False
        if self.max_reconnect_attempts > 0 and self._reconnect_attempts >= self.max_reconnect_attempts:
            self._emit_system("_reconnectFailed")
            return False
        # Exponential backoff with jitter, capped at 30 seconds.
        delay = min(self.reconnect_base_delay * 2**self._reconnect_attempts + random.random(), MAX_RECONNECT_DELAY)
        self._reconnect_attempts += 1
        self._emit_system("_reconnecting", self._reconnect_attempts, delay)
        await asyncio.sleep(delay)
        return not self._closing and self.auto_reconnect

    async def _open_connection(self) -> ClientConnection:
        ws: Optional[ClientConnection] = None

        async def open_and_wait_welcome() -> ClientConnection:
            nonlocal ws
            url = self._ws_url
            headers: Dict[str, str] = {}
            if self.auth_method == "header":
                headers["Authorization"] = f"Bearer {self._token}"
            else:
                url = _with_query(url, ticket=await self._fetch_ticket())

            ws = await _same_origin_connect(
                url,
                additional_headers=headers,
                user_agent_header=USER_AGENT,
                ping_interval=None,  # JSON heartbeat below; protocol pings from the server are still answered
                close_timeout=5,
                max_size=64 * 1024 * 1024,
            )
            while True:
                message = _parse(await ws.recv())
                if message is None:
                    continue
                if message.get("type") == "invalid":
                    data = message.get("data") or {}
                    raise NeuroApiError("WS_INVALID", str(data.get("message") or data.get("reason") or "Invalid"))
                if message.get("type") == "welcome":
                    self._on_welcome(ws, message)
                    return ws

        try:
            return await asyncio.wait_for(open_and_wait_welcome(), self.connect_timeout)
        except BaseException:
            if ws is not None and self._ws is not ws:
                ws.transport.abort()
            raise

    def _on_welcome(self, ws: ClientConnection, message: Dict[str, Any]) -> None:
        self._reconnect_attempts = 0
        self._ws = ws
        self._session_id = str((message.get("data") or {}).get("sessionId"))
        self._heartbeat_timed_out = False
        for name, sub in list(self._subs.items()):
            sub.state = _SubState.UNSUBSCRIBED
            self._sync_subscription(name, sub)
        self._start_heartbeat(ws)
        self._emit_system("_connected", self._session_id)
        self._emit_system("_message", message)

    async def _read_loop(self, ws: ClientConnection) -> "tuple[int, str]":
        try:
            async for raw in ws:
                if self._ws is ws:
                    self._handle_raw(raw)
        except ConnectionClosed:
            pass
        if self._heartbeat_timed_out:
            return 4002, "Heartbeat timeout"
        return (ws.close_code if ws.close_code is not None else 1006), (ws.close_reason or "")

    async def _fetch_ticket(self) -> str:
        try:
            async with httpx.AsyncClient(timeout=self.connect_timeout) as http:
                response = await http.get(
                    f"{self._api_url}/ws/ticket",
                    headers={"Authorization": f"Bearer {self._token}", "User-Agent": USER_AGENT},
                )
        except httpx.HTTPError as error:
            raise NeuroApiError("TICKET_ERROR", f"Failed to fetch connection ticket: {error}") from error

        try:
            body = response.json()
        except ValueError:
            body = None
        if not response.is_success:
            api_error = parse_api_error(body, response.status_code)
            detail = api_error.message if api_error else f"Request failed with status {response.status_code}"
            raise NeuroApiError("TICKET_ERROR", f"Failed to fetch connection ticket: {detail}", response.status_code)
        ticket = ((body or {}).get("data") or {}).get("ticket") if isinstance(body, dict) else None
        if not ticket:
            raise NeuroApiError("TICKET_ERROR", "Invalid ticket response from server")
        return str(ticket)

    # ----- internals: heartbeat -----

    def _start_heartbeat(self, ws: ClientConnection) -> None:
        self._stop_heartbeat()
        if not self.heartbeat_monitoring:
            return
        self._pong_event = asyncio.Event()
        self._heartbeat_task = asyncio.get_running_loop().create_task(self._heartbeat(ws, self._pong_event))

    def _stop_heartbeat(self) -> None:
        task, self._heartbeat_task = self._heartbeat_task, None
        if task is not None and task is not asyncio.current_task():
            task.cancel()

    async def _heartbeat(self, ws: ClientConnection, pong: asyncio.Event) -> None:
        try:
            while self._ws is ws:
                pong.clear()
                await ws.send(json.dumps({"type": "ping", "data": {}}))
                try:
                    await asyncio.wait_for(pong.wait(), self.heartbeat_timeout)
                except asyncio.TimeoutError:
                    if self._ws is not ws:
                        return
                    self._emit_system("_error", NeuroApiError("WS_HEARTBEAT_TIMEOUT", "Heartbeat pong timeout"))
                    self._heartbeat_timed_out = True
                    # Do not wait for a close handshake on a dead connection.
                    ws.transport.abort()
                    return
                await asyncio.sleep(self.heartbeat_interval)
        except ConnectionClosed:
            pass

    # ----- internals: messages -----

    def _handle_raw(self, raw: Union[str, bytes]) -> None:
        message = _parse(raw)
        if message is None:
            self._emit_system("_error", NeuroApiError("WS_PARSE_ERROR", "Failed to parse message"))
            return

        kind = message.get("type")
        data = message.get("data")
        if not isinstance(data, dict):
            data = {}
        name = str(data.get("eventType"))
        if kind == "event":
            sub = self._subs.get(name)
            if sub is not None:
                for callback in list(sub.listeners):
                    self._invoke(callback, data.get("eventData"), data.get("timestamp"))
        elif kind in ("addSuccess", "removeSuccess"):
            sub = self._subs.get(name)
            expected = _SubState.SUBSCRIBING if kind == "addSuccess" else _SubState.UNSUBSCRIBING
            if sub is not None and sub.state is expected:
                # `subscribed: false` means the server already had that state, which is still the desired one.
                sub.state = _SubState.SUBSCRIBED if kind == "addSuccess" else _SubState.UNSUBSCRIBED
                self._emit_system("_eventAdded" if kind == "addSuccess" else "_eventRemoved", name)
                self._sync_subscription(name, sub)
        elif kind == "invalid":
            self._emit_system("_error", NeuroApiError("WS_INVALID", str(data.get("message") or data.get("reason"))))
        elif kind == "pong":
            if self._pong_event is not None:
                self._pong_event.set()
            self._emit_system("_pong")

        self._emit_system("_message", message)

    def _sync_subscription(self, name: str, sub: _Subscription) -> None:
        """Reconciles one event's server-side subscription with its local listeners."""
        if not self.is_connected:
            return
        wanted = len(sub.listeners) > 0
        if wanted and sub.state is _SubState.UNSUBSCRIBED:
            sub.state = _SubState.SUBSCRIBING
            self._send_soon({"type": "addEvent", "data": {"eventType": name}})
        elif not wanted and sub.state is _SubState.SUBSCRIBED:
            sub.state = _SubState.UNSUBSCRIBING
            self._send_soon({"type": "removeEvent", "data": {"eventType": name}})
        elif not wanted and sub.state is _SubState.UNSUBSCRIBED:
            self._remove_inactive(name, sub)

    def _reset_subscriptions(self) -> None:
        """A closed connection holds no server-side subscriptions."""
        for name, sub in list(self._subs.items()):
            sub.state = _SubState.UNSUBSCRIBED
            self._remove_inactive(name, sub)

    def _remove_inactive(self, name: str, sub: _Subscription) -> None:
        if self._subs.get(name) is sub and not sub.listeners and sub.state is _SubState.UNSUBSCRIBED:
            del self._subs[name]

    def _remove_event_listener(self, name: str, callback: Callback) -> None:
        sub = self._subs.get(name)
        if sub is None or callback not in sub.listeners:
            return
        sub.listeners.remove(callback)
        if sub.listeners:
            return
        # A closed connection holds no server-side subscription.
        if not self.is_connected:
            sub.state = _SubState.UNSUBSCRIBED
        self._sync_subscription(name, sub)
        self._remove_inactive(name, sub)

    def _send_soon(self, message: Dict[str, Any]) -> None:
        ws, loop = self._ws, self._loop
        if ws is None or loop is None or not self.is_connected:
            return
        payload = json.dumps(message)

        async def send() -> None:
            try:
                await ws.send(payload)
            except ConnectionClosed:
                pass

        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is loop:
            self._spawn(send())
        else:
            loop.call_soon_threadsafe(lambda: self._spawn(send()))

    def _spawn(self, coroutine: Awaitable[Any]) -> None:
        task = asyncio.ensure_future(coroutine)
        self._background.add(task)
        task.add_done_callback(self._background.discard)

    def _emit_system(self, event: str, *args: Any) -> None:
        for callback in list(self._system.get(event, ())):
            self._invoke(callback, *args)

    def _invoke(self, callback: Callback, *args: Any) -> None:
        """Calls a listener; exceptions are logged and never break the client."""
        try:
            result = callback(*args)
        except Exception:  # noqa: BLE001
            logger.exception("Error in NeuroInfoAPI WebSocket listener %r", callback)
            return
        if inspect.isawaitable(result):

            async def run() -> None:
                try:
                    await result
                except Exception:  # noqa: BLE001
                    logger.exception("Error in NeuroInfoAPI WebSocket listener %r", callback)

            self._spawn(run())


def _parse(raw: Union[str, bytes]) -> Optional[Dict[str, Any]]:
    try:
        message = json.loads(raw)
    except (ValueError, TypeError):
        return None
    return message if isinstance(message, dict) else None


def _with_query(url: str, **params: str) -> str:
    parts = urlsplit(url)
    query = f"{parts.query}&{urlencode(params)}" if parts.query else urlencode(params)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, query, parts.fragment))


def _to_ws_error(error: BaseException) -> NeuroApiError:
    if isinstance(error, NeuroApiError):
        return error
    if isinstance(error, asyncio.TimeoutError):
        return NeuroApiError("WS_CONNECT_TIMEOUT", "WebSocket connection timed out")
    if isinstance(error, InvalidStatus):
        status = error.response.status_code
        return NeuroApiError("WS_ERROR", f"Server rejected WebSocket connection with HTTP {status}", status)
    if isinstance(error, ConnectionClosed):
        code = error.rcvd.code if error.rcvd else 1006
        reason = (error.rcvd.reason if error.rcvd else "") or "Unknown reason"
        return NeuroApiError("WS_CLOSED", f"Connection closed: {reason}", code)
    return NeuroApiError("WS_ERROR", f"WebSocket connection error: {error}")
