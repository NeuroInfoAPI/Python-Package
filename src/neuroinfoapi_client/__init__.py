"""Python client for NeuroInfoAPI.

- :class:`NeuroInfoApiClient` - synchronous HTTP client
- :class:`AsyncNeuroInfoApiClient` - asyncio HTTP client
- :class:`NeuroInfoApiWebsocketClient` - asyncio WebSocket client for real-time events

API docs: https://neuroinfoapi.dev/api/docs
"""

from . import types, utils
from ._version import __version__
from .client import AsyncNeuroInfoApiClient, NeuroInfoApiClient
from .errors import NeuroApiError
from .websocket import NeuroInfoApiWebsocketClient

__all__ = [
    "__version__",
    "AsyncNeuroInfoApiClient",
    "NeuroApiError",
    "NeuroInfoApiClient",
    "NeuroInfoApiWebsocketClient",
    "types",
    "utils",
]
