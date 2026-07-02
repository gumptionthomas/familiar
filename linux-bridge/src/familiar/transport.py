import sys
from typing import Protocol


class Transport(Protocol):
    async def send(self, data: bytes) -> None: ...


class FakeTransport:
    def __init__(self):
        self.sent: list[bytes] = []

    async def send(self, data: bytes) -> None:
        self.sent.append(data)


class StdoutTransport:
    async def send(self, data: bytes) -> None:
        sys.stdout.write(data.decode("utf-8", "replace"))
        sys.stdout.flush()


class NullTransport:
    """Discards heartbeats — used when running Tidbyt-only (no M5)."""

    async def send(self, data: bytes) -> None:
        """No device to feed; drop it."""
