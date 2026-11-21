"""Typed shapes of the NeuroInfoAPI responses and WebSocket events.

All data is returned as plain ``dict``/``list`` objects parsed from JSON.
The ``TypedDict`` classes below only describe those dicts so editors and type
checkers can autocomplete keys. Keys are kept exactly as the API sends them
(camelCase).
"""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional, Union

from typing_extensions import NotRequired, TypedDict

# ----- Twitch -----


class TwitchChannel(TypedDict):
    id: str
    name: str
    displayName: str
    profileImageUrl: Optional[str]


class StreamGame(TypedDict):
    id: str
    name: str


class StreamMetadata(TypedDict):
    title: str
    game: StreamGame
    language: str
    isMature: bool


class StreamOnlineData(StreamMetadata):
    isLive: Literal[True]
    id: str
    tags: List[str]
    viewerCount: int
    startedAt: int
    thumbnailUrl: str


class StreamOfflineData(TypedDict):
    isLive: Literal[False]


class TwitchOfflineStreamData(TypedDict):
    isLive: Literal[False]
    title: Optional[str]
    game: Optional[StreamGame]
    language: Optional[str]
    tags: Optional[List[str]]
    isMature: Optional[bool]


class TwitchLiveStreamState(StreamOnlineData):
    channel: Optional[TwitchChannel]


class TwitchOfflineStreamState(TwitchOfflineStreamData):
    channel: Optional[TwitchChannel]


TwitchStreamState = Union[TwitchLiveStreamState, TwitchOfflineStreamState]
"""Current stream state. Check ``stream["isLive"]`` and the live-only keys
(``id``, ``viewerCount``, ``startedAt``, ``thumbnailUrl``) become available."""


class TwitchVod(TypedDict):
    id: str
    streamId: str
    title: str
    url: str
    viewable: str
    type: str
    language: str
    duration: str
    viewCount: int
    createdAt: int
    publishedAt: int
    thumbnailUrl: str


# ----- Schedule -----

ScheduleStatus = Literal["auto_twitch", "auto_discord", "confirmed"]
ScheduleEntryType = Literal["normal", "offline", "canceled", "TBD", "unknown"]


class ScheduleEntry(TypedDict):
    day: int
    """0-6, Sunday-Saturday"""
    time: int
    """Unix timestamp in milliseconds"""
    message: str
    type: ScheduleEntryType
    segments: List[str]
    """Segments printed on the schedule image, e.g. ``["MINIGAMES", "SKYRIM"]``."""


class ScheduleData(TypedDict):
    year: int
    week: int
    schedule: List[ScheduleEntry]
    status: ScheduleStatus
    imageUrl: Optional[str]


class LatestScheduleData(ScheduleData):
    hasActiveSubathon: bool


ScheduleWeeksResponse = Dict[str, List[int]]
"""Available week numbers grouped by year (JSON keys are strings)."""


class ScheduleSearchCursor(TypedDict):
    year: int
    week: int


class ScheduleSearchResultItem(TypedDict):
    foundDays: List[int]
    data: ScheduleData


class ScheduleSearchResponse(TypedDict):
    nextCursor: Optional[ScheduleSearchCursor]
    results: List[ScheduleSearchResultItem]


# ----- Schedule-linked streams -----


class ScheduleLinkedChannel(TypedDict):
    id: str
    name: str
    displayName: str


ScheduleLinkType = Literal["on", "collab"]
ScheduleLinkedStreamOnlineTrigger = Literal["streamStart", "scheduleTime", "fetch"]
ScheduleLinkedStreamOfflineReason = Literal["streamEnd", "noShow", "scheduleRemoved", "timeout"]


class ScheduleLinkedStreamInfo(TypedDict):
    status: Literal["live", "waiting"]
    channel: ScheduleLinkedChannel
    linkType: ScheduleLinkType
    scheduleEntry: ScheduleEntry
    stream: Union[StreamOnlineData, StreamOfflineData]


class ScheduleLinkedStreamOnlineData(TypedDict):
    trigger: ScheduleLinkedStreamOnlineTrigger
    channel: ScheduleLinkedChannel
    linkType: ScheduleLinkType
    scheduleEntry: ScheduleEntry
    stream: Union[StreamOnlineData, StreamOfflineData]


class ScheduleLinkedStreamUpdateData(TypedDict):
    channel: ScheduleLinkedChannel
    linkType: ScheduleLinkType
    scheduleEntry: ScheduleEntry
    stream: StreamMetadata


class ScheduleLinkedStreamOfflineData(TypedDict):
    reason: ScheduleLinkedStreamOfflineReason
    channel: ScheduleLinkedChannel
    linkType: ScheduleLinkType
    scheduleEntry: ScheduleEntry
    stream: StreamOfflineData


class StreamRaidData(TypedDict):
    channel: TwitchChannel
    viewerCount: int


# ----- Subathon -----


class SubathonGoal(TypedDict):
    name: str
    completed: bool
    reached: bool


class SubathonData(TypedDict):
    year: int
    name: str
    subcount: int
    goals: Dict[str, SubathonGoal]
    subcountMilestones: NotRequired[Dict[str, Dict[str, int]]]
    isActive: bool
    startTimestamp: NotRequired[int]
    endTimestamp: NotRequired[int]


SubathonYearsResponse = Dict[str, str]


class SubathonGoalUpdateData(TypedDict):
    year: int
    goalNumber: int
    goal: SubathonGoal
    subcount: int


# ----- Blog -----


class BlogEntryBodySection(TypedDict):
    header: str
    body: str


class BlogFeedEntry(TypedDict):
    title: str
    author: str
    url: str
    published: int
    updated: int
    content: NotRequired[List[BlogEntryBodySection]]
    rawContent: NotRequired[str]
    summary: str


class BlogFeedData(TypedDict):
    url: str
    lastUpdated: int
    title: str
    subtitle: str
    entries: List[BlogFeedEntry]


# ----- X feed -----

XFeedAccount = Literal["NeurosamaAI", "EvilNeuroAI", "Vedal987"]
XFeedEntryType = Literal["tweet", "reply", "retweet"]


class XFeedUser(TypedDict):
    username: str


class XFeedImageMedia(TypedDict):
    type: Literal["image"]
    url: str


class XFeedVideoMedia(TypedDict):
    type: Literal["video"]
    url: str
    posterUrl: NotRequired[str]
    mimeType: NotRequired[str]


XFeedMedia = Union[XFeedImageMedia, XFeedVideoMedia]


class XFeedPost(TypedDict):
    id: str
    content: str
    createdTimestamp: int
    media: List[XFeedMedia]


class XFeedReplyTo(XFeedUser):
    statusId: str
    url: str
    post: NotRequired[XFeedPost]


class XFeedEntry(TypedDict):
    id: str
    type: XFeedEntryType
    replyTo: NotRequired[XFeedReplyTo]
    retweetedBy: NotRequired[XFeedUser]
    author: XFeedUser
    url: str
    createdTimestamp: int
    content: str
    media: List[XFeedMedia]


class XFeedNewEntriesData(TypedDict):
    user: XFeedAccount
    entries: List[XFeedEntry]


# ----- Shop -----


class ShopImage(TypedDict):
    url: str
    altText: Optional[str]
    width: int
    height: int


class ShopMoney(TypedDict):
    amount: str
    currencyCode: str


class ShopSeo(TypedDict):
    title: Optional[str]
    description: Optional[str]


class ShopOption(TypedDict):
    name: str
    values: List[str]


class ShopSelectedOption(TypedDict):
    name: str
    value: str


class ShopVariant(TypedDict):
    id: str
    title: str
    sku: Optional[str]
    availableForSale: bool
    price: ShopMoney
    compareAtPrice: Optional[ShopMoney]
    selectedOptions: List[ShopSelectedOption]
    image: Optional[ShopImage]


class ShopCollection(TypedDict):
    id: str
    title: str


class ShopMetafield(TypedDict):
    namespace: str
    key: str
    type: str
    value: str
    reference: Optional[Dict[str, Any]]


class ShopProduct(TypedDict):
    id: str
    title: str
    handle: str
    url: str
    description: str
    descriptionHtml: str
    vendor: str
    productType: str
    tags: List[str]
    availableForSale: bool
    createdAt: int
    updatedAt: int
    publishedAt: int
    seo: ShopSeo
    options: List[ShopOption]
    images: List[ShopImage]
    variants: List[ShopVariant]
    collections: List[ShopCollection]
    metafields: List[ShopMetafield]


class ShopProductsUpdateData(TypedDict):
    products: List[ShopProduct]


# ----- WebSocket -----

WsEventType = Literal[
    "shopProductsUpdate",
    "blogFeedUpdate",
    "xFeedNewEntries",
    "xFeedUpdate",
    "streamOnline",
    "streamOffline",
    "streamUpdate",
    "secretneuroaccountOnline",
    "scheduleLinkedStreamOnline",
    "scheduleLinkedStreamUpdate",
    "scheduleLinkedStreamOffline",
    "streamRaidIncoming",
    "streamRaidOutgoing",
    "scheduleUpdate",
    "subathonUpdate",
    "subathonGoalUpdate",
]
"""Data events that can be subscribed to. ``xFeedUpdate`` is deprecated, use ``xFeedNewEntries``."""

WS_EVENT_TYPES: frozenset[str] = frozenset(WsEventType.__args__)  # type: ignore[attr-defined]

WsSystemEvent = Literal[
    "_connected",
    "_disconnected",
    "_reconnecting",
    "_reconnectFailed",
    "_error",
    "_message",
    "_pong",
    "_eventAdded",
    "_eventRemoved",
]
"""Events emitted by the client itself.

Callback arguments:

- ``_connected(session_id)``
- ``_disconnected(code, reason)``
- ``_reconnecting(attempt, delay_seconds)``
- ``_reconnectFailed()``
- ``_error(error)``
- ``_message(message)`` - every raw server message as dict
- ``_pong()``
- ``_eventAdded(event_type)`` / ``_eventRemoved(event_type)``
"""

WS_SYSTEM_EVENTS: frozenset[str] = frozenset(WsSystemEvent.__args__)  # type: ignore[attr-defined]

WsInvalidReason = Literal[
    "malformed",
    "unauthenticated",
    "missingEventtype",
    "invalidEventtype",
    "missingToken",
    "invalidToken",
    "authError",
]

AuthMethod = Literal["ticket", "header"]
