"""HTTP clients for the NeuroInfoAPI (synchronous and asynchronous).

All methods return the response data directly and raise :class:`NeuroApiError` on errors.
"""

from __future__ import annotations

import json
import warnings
from typing import Any, Dict, List, Literal, Mapping, Optional
from urllib.parse import urlsplit

import httpx

from ._internal import DEFAULT_TIMEOUT, USER_AGENT, boot_check, create_client_urls, parse_api_error
from .errors import NeuroApiError
from .types import (
    BlogFeedData,
    LatestScheduleData,
    ScheduleData,
    ScheduleEntryType,
    ScheduleLinkedStreamInfo,
    ScheduleSearchCursor,
    ScheduleSearchResponse,
    ScheduleWeeksResponse,
    ShopProduct,
    SubathonData,
    SubathonYearsResponse,
    TwitchStreamState,
    TwitchVod,
    XFeedAccount,
    XFeedEntry,
)

__all__ = ["NeuroInfoApiClient", "AsyncNeuroInfoApiClient"]


def _query_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _clean_query(params: Optional[Mapping[str, Any]]) -> Optional[Dict[str, str]]:
    if params is None:
        return None
    return {key: _query_value(value) for key, value in params.items() if value is not None}


def _schedule_search_params(
    query: str,
    limit: Optional[int],
    year: Optional[int],
    sort: Optional[str],
    type: Optional[str],
    cursor: Optional[ScheduleSearchCursor],
) -> Dict[str, Any]:
    params: Dict[str, Any] = {"query": query, "limit": limit, "year": year, "sort": sort, "type": type}
    if cursor:
        params["cursorYear"] = cursor["year"]
        params["cursorWeek"] = cursor["week"]
    return params


def _check_path(path: str) -> str:
    """Only API paths are allowed, so the token is never sent to a different host."""
    parts = urlsplit(path)
    if parts.scheme or parts.netloc or not path.startswith("/"):
        raise ValueError(f"Expected an API path like '/twitch/stream', got {path!r}")
    return path


def _parse_response(response: httpx.Response) -> Any:
    """Returns the unwrapped ``data`` of a response or raises :class:`NeuroApiError`."""
    try:
        body: Any = response.json()
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
        if response.is_success and response.status_code not in (204, 205):
            raise NeuroApiError("INVALID_JSON", "Invalid JSON response", response.status_code) from None
        body = None

    if not response.is_success:
        raise parse_api_error(body, response.status_code) or NeuroApiError(
            "HTTP_ERROR", f"Request failed with status {response.status_code}", response.status_code
        )

    # Unwrap the { "data": ... } envelope.
    return body["data"] if isinstance(body, dict) and "data" in body else body


def _network_error(error: httpx.HTTPError) -> NeuroApiError:
    if isinstance(error, httpx.TimeoutException):
        return NeuroApiError("NETWORK", "Request timeout")
    return NeuroApiError("NETWORK", str(error) or "Network error")


class _BaseClient:
    def __init__(
        self,
        token: Optional[str],
        api_base_url: Optional[str],
        use_tls: Optional[bool],
        timeout: Optional[float],
        base_url: Optional[str],
    ) -> None:
        if api_base_url is None and base_url is not None:
            warnings.warn("base_url is deprecated. Use api_base_url with use_tls instead.", DeprecationWarning, stacklevel=3)
            self.api_url = base_url.rstrip("/")
        else:
            self.api_url = create_client_urls(api_base_url, use_tls).api
        if timeout is None or timeout < 0:
            timeout = DEFAULT_TIMEOUT
        self.timeout = timeout
        self._token: Optional[str] = token
        boot_check(self.api_url)

    def set_api_token(self, token: Optional[str]) -> None:
        """Sets the API token for authentication. Pass ``None`` to remove the token."""
        self._token = token

    def _headers(self) -> Dict[str, str]:
        headers = {"User-Agent": USER_AGENT, "Content-Type": "application/json"}
        if self._token is not None:
            headers["Authorization"] = f"Bearer {self._token}"
        return headers


class NeuroInfoApiClient(_BaseClient):
    """Synchronous client for the NeuroInfoAPI.

    Methods return the data directly and raise :class:`NeuroApiError` on errors::

        client = NeuroInfoApiClient()
        try:
            stream = client.get_current_stream()
        except NeuroApiError as error:
            print(error.code, error.message)

    :param token: Optional API token. Required for VODs, ``get_schedule``, schedule search,
        ``get_subathon``, blog, X feed and shop.
    :param api_base_url: Host and API path without protocol. Default ``neuroinfoapi.dev/api/v2``.
    :param use_tls: Use HTTPS (default) or plain HTTP.
    :param timeout: Request timeout in seconds. Default ``10``.
    """

    def __init__(
        self,
        token: Optional[str] = None,
        *,
        api_base_url: Optional[str] = None,
        use_tls: Optional[bool] = None,
        timeout: Optional[float] = None,
        base_url: Optional[str] = None,
        transport: Optional[httpx.BaseTransport] = None,
    ) -> None:
        super().__init__(token, api_base_url, use_tls, timeout, base_url)
        self._http = httpx.Client(base_url=self.api_url, timeout=self.timeout, transport=transport)

    def close(self) -> None:
        """Closes the underlying HTTP connection pool."""
        self._http.close()

    def __enter__(self) -> NeuroInfoApiClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def request(self, path: str, params: Optional[Mapping[str, Any]] = None) -> Any:
        """Low-level GET request to an API path, e.g. ``client.request("/twitch/stream")``."""
        try:
            response = self._http.get(_check_path(path), params=_clean_query(params), headers=self._headers())
        except httpx.HTTPError as error:
            raise _network_error(error) from error
        return _parse_response(response)

    # ----- Twitch -----

    def get_current_stream(self) -> TwitchStreamState:
        """Current Twitch stream state. Check with ``utils.is_live(stream)``."""
        return self.request("/twitch/stream")

    def get_schedule_linked_streams(self) -> List[ScheduleLinkedStreamInfo]:
        """Channels watched right now because a schedule entry takes place on them."""
        return self.request("/twitch/scheduleLinkedStreams")

    def get_all_vods(self) -> List[TwitchVod]:
        """All Twitch VODs. Requires an API token."""
        return self.request("/twitch/vods")

    def get_vod(self, id: str) -> TwitchVod:
        """A VOD by stream ID. Requires an API token."""
        return self.request("/twitch/vod", {"id": id})

    # ----- Schedule -----

    def get_schedule(self, week: int, year: Optional[int] = None) -> ScheduleData:
        """Schedule for a specific week (and optional year). Requires an API token."""
        return self.request("/schedule", {"week": week, "year": year})

    def get_latest_schedule(self) -> LatestScheduleData:
        """Latest weekly schedule."""
        return self.request("/schedule/latest")

    def get_schedule_weeks(self) -> ScheduleWeeksResponse:
        """Available schedule weeks grouped by year."""
        return self.request("/schedule/weeks")

    def get_devstream_times(self) -> List[int]:
        """Devstream schedule timestamps."""
        return self.request("/devstream/times")

    def get_schedule_search(
        self,
        query: str,
        *,
        limit: Optional[int] = None,
        year: Optional[int] = None,
        sort: Optional[Literal["asc", "desc"]] = None,
        type: Optional[ScheduleEntryType] = None,
        cursor: Optional[ScheduleSearchCursor] = None,
    ) -> ScheduleSearchResponse:
        """Searches schedule entries by message text with optional filters and cursor pagination. Requires an API token."""
        return self.request("/schedule/search", _schedule_search_params(query, limit, year, sort, type, cursor))

    # ----- Subathon -----

    def get_current_subathons(self) -> List[SubathonData]:
        """Currently active subathons (error code ``SB1`` when there is none)."""
        return self.request("/subathon")

    def get_subathon(self, year: int) -> SubathonData:
        """Subathon data for a specific year. Requires an API token."""
        return self.request("/subathon", {"year": year})

    def get_subathon_years(self) -> SubathonYearsResponse:
        """Years for which subathon data is available."""
        return self.request("/subathon/years")

    # ----- Feeds (token required) -----

    def get_blog_feed(self, raw: bool = False) -> BlogFeedData:
        """Neuro-sama blog feed. Requires an API token."""
        return self.request("/blog", {"raw": True} if raw else None)

    def get_x_feed(self, user: XFeedAccount) -> List[XFeedEntry]:
        """Cached X feed for one of the supported accounts. Requires an API token."""
        return self.request("/x-feed", {"user": user})

    # ----- Shop (token required) -----

    def get_shop_products(self, currency: Optional[str] = None) -> List[ShopProduct]:
        """Full shop catalog. Requires an API token."""
        return self.request("/shop/products", {"currency": currency})

    def get_shop_product(self, id: str, currency: Optional[str] = None) -> ShopProduct:
        """A shop product by ID. Requires an API token."""
        return self.request("/shop/product", {"id": id, "currency": currency})


class AsyncNeuroInfoApiClient(_BaseClient):
    """Asynchronous (``asyncio``) client. Same methods as :class:`NeuroInfoApiClient`, but awaited::

        async with AsyncNeuroInfoApiClient() as client:
            stream = await client.get_current_stream()

    :param token: Optional API token. Required for VODs, ``get_schedule``, schedule search,
        ``get_subathon``, blog, X feed and shop.
    :param api_base_url: Host and API path without protocol. Default ``neuroinfoapi.dev/api/v2``.
    :param use_tls: Use HTTPS (default) or plain HTTP.
    :param timeout: Request timeout in seconds. Default ``10``.
    """

    def __init__(
        self,
        token: Optional[str] = None,
        *,
        api_base_url: Optional[str] = None,
        use_tls: Optional[bool] = None,
        timeout: Optional[float] = None,
        base_url: Optional[str] = None,
        transport: Optional[httpx.AsyncBaseTransport] = None,
    ) -> None:
        super().__init__(token, api_base_url, use_tls, timeout, base_url)
        self._http = httpx.AsyncClient(base_url=self.api_url, timeout=self.timeout, transport=transport)

    async def aclose(self) -> None:
        """Closes the underlying HTTP connection pool."""
        await self._http.aclose()

    async def __aenter__(self) -> AsyncNeuroInfoApiClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def request(self, path: str, params: Optional[Mapping[str, Any]] = None) -> Any:
        """Low-level GET request to an API path, e.g. ``await client.request("/twitch/stream")``."""
        try:
            response = await self._http.get(_check_path(path), params=_clean_query(params), headers=self._headers())
        except httpx.HTTPError as error:
            raise _network_error(error) from error
        return _parse_response(response)

    # ----- Twitch -----

    async def get_current_stream(self) -> TwitchStreamState:
        """Current Twitch stream state. Check with ``utils.is_live(stream)``."""
        return await self.request("/twitch/stream")

    async def get_schedule_linked_streams(self) -> List[ScheduleLinkedStreamInfo]:
        """Channels watched right now because a schedule entry takes place on them."""
        return await self.request("/twitch/scheduleLinkedStreams")

    async def get_all_vods(self) -> List[TwitchVod]:
        """All Twitch VODs. Requires an API token."""
        return await self.request("/twitch/vods")

    async def get_vod(self, id: str) -> TwitchVod:
        """A VOD by stream ID. Requires an API token."""
        return await self.request("/twitch/vod", {"id": id})

    # ----- Schedule -----

    async def get_schedule(self, week: int, year: Optional[int] = None) -> ScheduleData:
        """Schedule for a specific week (and optional year). Requires an API token."""
        return await self.request("/schedule", {"week": week, "year": year})

    async def get_latest_schedule(self) -> LatestScheduleData:
        """Latest weekly schedule."""
        return await self.request("/schedule/latest")

    async def get_schedule_weeks(self) -> ScheduleWeeksResponse:
        """Available schedule weeks grouped by year."""
        return await self.request("/schedule/weeks")

    async def get_devstream_times(self) -> List[int]:
        """Devstream schedule timestamps."""
        return await self.request("/devstream/times")

    async def get_schedule_search(
        self,
        query: str,
        *,
        limit: Optional[int] = None,
        year: Optional[int] = None,
        sort: Optional[Literal["asc", "desc"]] = None,
        type: Optional[ScheduleEntryType] = None,
        cursor: Optional[ScheduleSearchCursor] = None,
    ) -> ScheduleSearchResponse:
        """Searches schedule entries by message text with optional filters and cursor pagination. Requires an API token."""
        return await self.request("/schedule/search", _schedule_search_params(query, limit, year, sort, type, cursor))

    # ----- Subathon -----

    async def get_current_subathons(self) -> List[SubathonData]:
        """Currently active subathons (error code ``SB1`` when there is none)."""
        return await self.request("/subathon")

    async def get_subathon(self, year: int) -> SubathonData:
        """Subathon data for a specific year. Requires an API token."""
        return await self.request("/subathon", {"year": year})

    async def get_subathon_years(self) -> SubathonYearsResponse:
        """Years for which subathon data is available."""
        return await self.request("/subathon/years")

    # ----- Feeds (token required) -----

    async def get_blog_feed(self, raw: bool = False) -> BlogFeedData:
        """Neuro-sama blog feed. Requires an API token."""
        return await self.request("/blog", {"raw": True} if raw else None)

    async def get_x_feed(self, user: XFeedAccount) -> List[XFeedEntry]:
        """Cached X feed for one of the supported accounts. Requires an API token."""
        return await self.request("/x-feed", {"user": user})

    # ----- Shop (token required) -----

    async def get_shop_products(self, currency: Optional[str] = None) -> List[ShopProduct]:
        """Full shop catalog. Requires an API token."""
        return await self.request("/shop/products", {"currency": currency})

    async def get_shop_product(self, id: str, currency: Optional[str] = None) -> ShopProduct:
        """A shop product by ID. Requires an API token."""
        return await self.request("/shop/product", {"id": id, "currency": currency})
