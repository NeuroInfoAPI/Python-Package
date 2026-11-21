from __future__ import annotations

from typing import Optional


class NeuroApiError(Exception):
    """Error returned by the API or raised by the client.

    ``code`` is the API error code (e.g. ``"SB1"``) or a client code such as
    ``"NETWORK"``, ``"HTTP_ERROR"``, ``"INVALID_JSON"``, ``"WS_ERROR"`` ...
    """

    def __init__(
        self,
        code: str,
        message: str,
        status: Optional[int] = None,
        timestamp: Optional[int] = None,
        path: Optional[str] = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.timestamp = timestamp
        self.path = path

    def __repr__(self) -> str:
        return f"NeuroApiError(code={self.code!r}, message={self.message!r}, status={self.status!r})"

    def __str__(self) -> str:
        return f"[{self.code}] {self.message}"
