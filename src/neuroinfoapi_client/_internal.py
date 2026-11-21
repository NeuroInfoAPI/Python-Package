from __future__ import annotations

import logging
import os
import re
import threading
import warnings
from dataclasses import dataclass
from typing import Any, Optional
from urllib.parse import urlsplit, urlunsplit

from ._version import __version__

logger = logging.getLogger("neuroinfoapi_client")

API_VERSION = "v2"
USER_AGENT = f"niac_py@{__version__}"
DEFAULT_API_BASE_URL = f"neuroinfoapi.dev/api/{API_VERSION}"
DEFAULT_TIMEOUT = 10.0

_PROTOCOL_RE = re.compile(r"^(https?|wss?)://", re.IGNORECASE)
_ANY_SCHEME_RE = re.compile(r"^[a-zA-Z][a-zA-Z\d+.-]*://")


@dataclass(frozen=True)
class ClientUrls:
    api: str
    websocket: str


def create_client_urls(api_base_url: Optional[str] = None, use_tls: Optional[bool] = None) -> ClientUrls:
    """Builds the HTTP and WebSocket base URLs from ``api_base_url`` (host + path, no protocol)."""
    raw = api_base_url if api_base_url is not None else DEFAULT_API_BASE_URL
    match = _PROTOCOL_RE.match(raw)
    protocol = match.group(1).lower() if match else None
    if protocol:
        warnings.warn(
            "Protocols in api_base_url are deprecated. Remove the protocol and use use_tls instead.",
            DeprecationWarning,
            stacklevel=3,
        )
    base = _PROTOCOL_RE.sub("", raw).strip("/")
    if not base or _ANY_SCHEME_RE.match(base) or "?" in base or "#" in base:
        raise ValueError("api_base_url must contain a host and API path without query or hash")
    tls = use_tls if use_tls is not None else (protocol.endswith("s") if protocol else True)
    return ClientUrls(
        api=f"{'https' if tls else 'http'}://{base}",
        websocket=f"{'wss' if tls else 'ws'}://{base}/ws",
    )


def derive_api_url(websocket_url: str) -> str:
    """Derives the HTTP API base URL from a full WebSocket URL."""
    parts = urlsplit(websocket_url)
    scheme = "http" if parts.scheme == "ws" else "https"
    path = re.sub(r"/ws(?:/.*)?$", "", parts.path)
    return urlunsplit((scheme, parts.netloc, path, "", "")).rstrip("/")


def parse_api_error(body: Any, status: Optional[int]):
    """Extracts the structured API error from a response body, if present."""
    from .errors import NeuroApiError

    error = body.get("error") if isinstance(body, dict) else None
    if isinstance(error, dict) and error.get("code") and error.get("message"):
        timestamp = error.get("timestamp")
        path = error.get("path")
        return NeuroApiError(
            str(error["code"]),
            str(error["message"]),
            status,
            int(timestamp) if isinstance(timestamp, (int, float)) else None,
            path if isinstance(path, str) else None,
        )
    return None


# ----- Boot check -----

_boot_check_lock = threading.Lock()
_boot_check_fired = False


def _run_boot_check(api_url: str, client_api_version: str) -> None:
    import httpx

    try:
        info_url = re.sub(r"/api/v\d+.*$", "/api", api_url) + "/info"
        resp = httpx.get(info_url, headers={"User-Agent": USER_AGENT}, timeout=DEFAULT_TIMEOUT)
        if not resp.is_success:
            return
        data = (resp.json() or {}).get("data") or {}
        latest = data.get("latestVersion")
        if not isinstance(latest, str) or latest == client_api_version:
            return
        versions = data.get("versions") or {}
        current = versions.get(client_api_version) or {}
        latest_info = versions.get(latest) or {}
        status = current.get("status")
        if status == "deprecated":
            sunset = current.get("sunset")
            logger.warning(
                "API %s is deprecated and will be turned off%s.",
                client_api_version,
                f" on {sunset}" if isinstance(sunset, str) else "",
            )
        elif status == "removed":
            docs = latest_info.get("docsUrl")
            logger.warning(
                "API %s is no longer available. Please update to the latest version.%s",
                client_api_version,
                f" See {docs}" if isinstance(docs, str) else "",
            )
        else:
            logger.warning(
                "API %s is not the latest version. The latest version is %s.", client_api_version, latest
            )
    except Exception:
        # Boot check is non-critical.
        pass


def boot_check(api_url: str, client_api_version: str = API_VERSION) -> None:
    """Runs once per process in a background thread and warns when the API version is outdated."""
    global _boot_check_fired
    if os.environ.get("NEUROINFOAPI_DISABLE_BOOT_CHECK"):
        return
    with _boot_check_lock:
        if _boot_check_fired:
            return
        _boot_check_fired = True
    threading.Thread(target=_run_boot_check, args=(api_url, client_api_version), daemon=True).start()
