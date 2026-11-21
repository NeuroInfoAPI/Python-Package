# neuroinfoapi-client (Python)

Python client for the [NeuroInfoAPI](https://neuroinfoapi.dev/api/docs).

## Features

- HTTP access to streams, VODs, schedules, subathons, shop products, blog and X feeds
- Synchronous **and** `asyncio` HTTP client with the same methods
- Methods return the data directly; errors are raised as `NeuroApiError`
- Typed responses (`TypedDict`) for editor autocompletion
- Configurable API base URL and request timeout (10 seconds by default)
- WebSocket client for real-time events: just call `ws.run()` (no `asyncio` needed) or use it
  from async code; header or ticket auth, automatic reconnect, subscription recovery and heartbeat

## Installation
Requires Python 3.10 or newer.

```bash
pip install neuroinfoapi-client
```

## Quick Start

```python
from neuroinfoapi_client import NeuroInfoApiClient, utils

client = NeuroInfoApiClient()  # no token needed for public endpoints
# client = NeuroInfoApiClient("YOUR_API_TOKEN")  # token required for some endpoints, see below

stream = client.get_current_stream()  # raises NeuroApiError if something goes wrong
if utils.is_live(stream):
    print("Live:", stream["title"], stream["viewerCount"])
else:
    print("Stream is offline")
```

Real-time events via WebSocket:

```python
from neuroinfoapi_client import NeuroInfoApiWebsocketClient

ws = NeuroInfoApiWebsocketClient("YOUR_API_TOKEN")

@ws.on("streamOnline")
def on_online(data, timestamp):
    print("Neuro is live:", data["title"])

ws.run()  # blocks until Ctrl+C
```

### Async

```python
import asyncio
from neuroinfoapi_client import AsyncNeuroInfoApiClient


async def main():
    async with AsyncNeuroInfoApiClient() as client:
        schedule = await client.get_latest_schedule()
        for entry in schedule["schedule"]:
            print(entry["day"], entry["message"])


asyncio.run(main())
```

## Client Configuration

```python
NeuroInfoApiClient(token=None, *, api_base_url=None, use_tls=None, timeout=None)
```

| Option         | Default                   | Description                              |
| -------------- | ------------------------- | ---------------------------------------- |
| `api_base_url` | `neuroinfoapi.dev/api/v2` | API host and path without a protocol     |
| `use_tls`      | `True`                    | Use HTTPS when `True`, HTTP when `False` |
| `timeout`      | `10`                      | HTTP request timeout in **seconds**      |

```python
# Token later
client = NeuroInfoApiClient()
client.set_api_token("YOUR_API_TOKEN")

# Local Mock-TestServer (https://github.com/NeuroInfoAPI/Mock-TestServer)
client = NeuroInfoApiClient(api_base_url="localhost:8787/api/v2", use_tls=False, timeout=15)
```

Both clients keep a connection pool open. Use them with `with` / `async with`, or call
`client.close()` / `await client.aclose()` when you are done.

## Error Handling

All errors are raised as `NeuroApiError` with `code`, `message` and the optional `status`,
`timestamp` and `path`:

```python
from neuroinfoapi_client import NeuroApiError, NeuroInfoApiClient

client = NeuroInfoApiClient()

try:
    subathons = client.get_current_subathons()
except NeuroApiError as error:
    if error.code == "SB1":
        print("No subathon right now")
    else:
        print(error.code, error.message)
else:
    for subathon in subathons:  # editor knows this is list[SubathonData]
        print(subathon["name"], subathon["subcount"])
```

Client error codes: `NETWORK` (connection problem or timeout), `HTTP_ERROR` (non-2xx without an
API error body), `INVALID_JSON`. All other codes come from the API.

> **Typing tip:** use `utils.is_live(stream)` (or `stream["isLive"] is True`, not just
> `if stream["isLive"]:`) so the editor knows live-only keys like `viewerCount` exist.

## HTTP API Methods

| Python                                                           | Endpoint                                                                  |
| ---------------------------------------------------------------- | ------------------------------------------------------------------------- |
| `get_current_stream()`                                           | Current Twitch stream state                                               |
| `get_schedule_linked_streams()`                                  | Channels watched because of a schedule entry                              |
| `get_all_vods()`                                                 | All Twitch VODs (**token required**)                                      |
| `get_vod(id)`                                                    | A VOD by stream ID (**token required**)                                   |
| `get_schedule(week, year=None)`                                  | Schedule for a specific week (**token required**)                         |
| `get_latest_schedule()`                                          | Latest schedule                                                           |
| `get_schedule_weeks()`                                           | Available schedule weeks grouped by year                                  |
| `get_devstream_times()`                                          | Devstream timestamps                                                      |
| `get_schedule_search(query, *, limit, year, sort, type, cursor)` | Search schedules (cursor pagination) (**token required**)                 |
| `get_current_subathons()`                                        | Currently active subathons                                                |
| `get_subathon(year)`                                             | Subathon of a specific year (**token required**)                          |
| `get_subathon_years()`                                           | Years with subathon data                                                  |
| `get_blog_feed(raw=False)`                                       | Neuro-sama blog feed (**token required**)                                 |
| `get_x_feed(user)`                                               | X feed of `NeurosamaAI`, `EvilNeuroAI` or `Vedal987` (**token required**) |
| `get_shop_products(currency=None)`                               | Shop catalog (**token required**)                                         |
| `get_shop_product(id, currency=None)`                            | A shop product (**token required**)                                       |
| `request(path, params=None)`                                     | Raw GET request to an API path such as `/twitch/stream`                   |

The response data is plain `dict`/`list` from JSON; keys are the same as in the API (camelCase).
Timestamps are Unix timestamps in milliseconds.

Search with pagination (needs a client with a token):

```python
cursor = None
while True:
    page = client.get_schedule_search("minecraft", limit=10, cursor=cursor)
    for result in page["results"]:
        print(result["data"]["year"], result["data"]["week"], result["foundDays"])
    cursor = page["nextCursor"]
    if cursor is None:
        break
```

## WebSocket Client

The WebSocket client needs an API token.

```python
from neuroinfoapi_client import NeuroInfoApiWebsocketClient

ws = NeuroInfoApiWebsocketClient("YOUR_API_TOKEN")

@ws.on("streamOnline")
def on_online(data, timestamp):
    print("Neuro is live:", data["title"])

@ws.on("streamOffline")
def on_offline(data, timestamp):
    print("Stream ended")
    # ws.stop()  # would end ws.run()

ws.on("_connected", lambda session_id: print("Connected:", session_id))
ws.on("_error", lambda error: print("Error:", error))

ws.run()  # blocks; raises NeuroApiError if the first connection fails
```

`ws.run()` returns after Ctrl+C, after `ws.stop()` (works from callbacks and other threads) or
when reconnecting gave up. Long-running work inside a callback pauses the client, so keep
callbacks short or start a thread for heavy work.

### In async code

```python
import asyncio
from neuroinfoapi_client import NeuroInfoApiWebsocketClient


async def main():
    ws = NeuroInfoApiWebsocketClient("YOUR_API_TOKEN")

    @ws.on("streamOnline")
    async def on_online(data, timestamp):  # async callbacks work too
        print("Neuro is live:", data["title"])

    await ws.connect()      # raises NeuroApiError if the first connection fails
    await ws.wait_closed()  # runs until disconnect() or reconnecting gave up


asyncio.run(main())
```

### Events

`ws.on(event, callback)` returns an unsubscribe function; `ws.on(event)` without a callback
works as a decorator. Data events are subscribed on the server automatically and re-subscribed
after every reconnect.

Data events (callback gets `data, timestamp`):
`streamOnline`, `streamOffline`, `streamUpdate`, `secretneuroaccountOnline`,
`scheduleLinkedStreamOnline`, `scheduleLinkedStreamUpdate`, `scheduleLinkedStreamOffline`,
`streamRaidIncoming`, `streamRaidOutgoing`, `scheduleUpdate`, `subathonUpdate`,
`subathonGoalUpdate`, `blogFeedUpdate`, `xFeedNewEntries`, `shopProductsUpdate`
(`xFeedUpdate` is deprecated).

System events:

| Event              | Callback arguments                    |
| ------------------ | ------------------------------------- |
| `_connected`       | `session_id`                          |
| `_disconnected`    | `code, reason`                        |
| `_reconnecting`    | `attempt, delay_seconds`              |
| `_reconnectFailed` | -                                     |
| `_error`           | `error`                               |
| `_message`         | `message` (every raw message as dict) |
| `_pong`            | -                                     |
| `_eventAdded`      | `event_type`                          |
| `_eventRemoved`    | `event_type`                          |

Options (all durations in **seconds**):

| Option                    | Default    | Description                                                       |
| ------------------------- | ---------- | ----------------------------------------------------------------- |
| `api_base_url`, `use_tls` | -          | Same as the HTTP client                                           |
| `websocket_url`           | derived    | Full WebSocket URL override                                       |
| `auth_method`             | `"header"` | `"header"` (Authorization header) or `"ticket"` (one-time ticket) |
| `auto_reconnect`          | `True`     | Reconnect after a lost connection                                 |
| `max_reconnect_attempts`  | `10`       | `0` = unlimited                                                   |
| `reconnect_base_delay`    | `1.0`      | Exponential backoff base, capped at 30 seconds                    |
| `heartbeat_monitoring`    | `True`     | Send JSON pings and reconnect when no pong arrives                |
| `heartbeat_interval`      | `30`       | Ping interval (min. 5)                                            |
| `heartbeat_timeout`       | `10`       | Pong timeout (min. 1)                                             |
| `connect_timeout`         | `15`       | Ticket + handshake + welcome timeout (min. 1)                     |

Other methods: `run()`, `stop()`, `disconnect()`, `destroy()` (also removes all listeners), `set_token(token)`,
`off(event, callback)`, `request_event_list()`, `get_subscribed_events()`, and the properties
`is_connected` and `session_id`. `async with NeuroInfoApiWebsocketClient(...) as ws:` connects
and disconnects automatically.

## Utils

Small helpers for checking API data:

```python
from neuroinfoapi_client import utils

utils.is_live(stream)                      # stream is live (editor then knows viewerCount etc.)
utils.is_schedule_final(schedule["status"])
utils.has_schedule_image(schedule)
utils.is_schedule_entry_online(entry)      # also: is_schedule_entry_offline / _unknown
```

For `scheduleLinkedStream*` WebSocket events: `is_link_type_on`, `is_link_type_collab`,
`is_trigger_stream_start`, `is_trigger_fetch`, `is_trigger_schedule_time`, `is_reason_stream_end`,
`is_reason_no_show`, `is_reason_schedule_removed`, `is_reason_timeout`, `did_linked_stream_happen`,
`is_linked_stream_live`.

## Logging

The client logs through the `neuroinfoapi_client` logger (for example a warning when the
API version used by the client is outdated, or errors raised inside your WebSocket callbacks).
Set `NEUROINFOAPI_DISABLE_BOOT_CHECK=1` to skip the one-time API version check.

## Development

```bash
python -m venv .venv
.venv/Scripts/activate        # Windows (Linux/macOS: source .venv/bin/activate)
pip install -e ".[dev]"
pytest
python -m build               # creates dist/*.whl and dist/*.tar.gz
```

Releases are published to PyPI by the GitHub Actions workflow `.github/workflows/publish.yml`
(see the comments there for the one-time PyPI setup).

## License

MIT
