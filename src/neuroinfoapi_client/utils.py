"""Small helpers for working with API data."""

from __future__ import annotations

from typing import Any, Mapping

from typing_extensions import TypeIs

from .types import ScheduleData, ScheduleEntry, ScheduleStatus, TwitchLiveStreamState, TwitchStreamState


def is_live(stream: TwitchStreamState) -> TypeIs[TwitchLiveStreamState]:
    """``True`` when the stream is live. Type checkers then know the live-only keys
    (``id``, ``viewerCount``, ``startedAt``, ``thumbnailUrl``) exist.

    Same as ``stream["isLive"] is True``; a plain ``if stream["isLive"]:`` works at
    runtime but does not narrow the type.
    """
    return stream["isLive"] is True


def is_schedule_final(status: ScheduleStatus) -> bool:
    """The schedule was confirmed and will not change automatically anymore."""
    return status == "confirmed"


def is_schedule_entry_online(entry: ScheduleEntry) -> bool:
    return entry["type"] in ("normal", "TBD")


def is_schedule_entry_offline(entry: ScheduleEntry) -> bool:
    return entry["type"] in ("offline", "canceled")


def is_schedule_entry_unknown(entry: ScheduleEntry) -> bool:
    return entry["type"] == "unknown"


def has_schedule_image(schedule: ScheduleData) -> bool:
    image = schedule.get("imageUrl")
    return image is not None and image.strip() != ""


# ----- Schedule-linked streams -----


def is_link_type_on(data: Mapping[str, Any]) -> bool:
    """The entry takes place on this channel instead of the main channel, e.g. ``(on secretneuroaccount) Evil Stream``."""
    return data.get("linkType") == "on"


def is_link_type_collab(data: Mapping[str, Any]) -> bool:
    """The channel is linked as a collab partner, e.g. ``w/ [Filian](...)``."""
    return data.get("linkType") == "collab"


def is_trigger_stream_start(data: Mapping[str, Any]) -> bool:
    """The channel went live while it was watched."""
    return data.get("trigger") == "streamStart"


def is_trigger_fetch(data: Mapping[str, Any]) -> bool:
    """The channel was already live when watching started."""
    return data.get("trigger") == "fetch"


def is_trigger_schedule_time(data: Mapping[str, Any]) -> bool:
    """The scheduled time was reached. Sent once per entry whether live or not, see :func:`is_linked_stream_live`."""
    return data.get("trigger") == "scheduleTime"


def is_reason_stream_end(data: Mapping[str, Any]) -> bool:
    return data.get("reason") == "streamEnd"


def is_reason_no_show(data: Mapping[str, Any]) -> bool:
    """The channel did not go live after the scheduled time."""
    return data.get("reason") == "noShow"


def is_reason_schedule_removed(data: Mapping[str, Any]) -> bool:
    return data.get("reason") == "scheduleRemoved"


def is_reason_timeout(data: Mapping[str, Any]) -> bool:
    """Still live at the maximum watch time; the stream may continue without further events."""
    return data.get("reason") == "timeout"


def did_linked_stream_happen(data: Mapping[str, Any]) -> bool:
    """The linked stream was live at some point (``streamEnd`` or ``timeout``)."""
    return data.get("reason") in ("streamEnd", "timeout")


def is_linked_stream_live(data: Mapping[str, Any]) -> bool:
    """The channel of a schedule-linked event/entry is live right now."""
    stream = data.get("stream") or {}
    return bool(stream.get("isLive"))
