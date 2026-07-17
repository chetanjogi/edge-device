import asyncio
import logging

log = logging.getLogger("device")


class EventBroker:
    """Bridges worker-thread callbacks into asyncio queues for WebSocket clients."""

    def __init__(self):
        self._subscribers: set[asyncio.Queue] = set()
        self._loop = None

    def bind_loop(self, loop):
        self._loop = loop

    def subscribe(self) -> asyncio.Queue:
        q = asyncio.Queue(maxsize=100)
        self._subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue):
        self._subscribers.discard(q)

    def publish_threadsafe(self, event: dict):
        """Called from the device worker thread — never touches asyncio directly."""
        if self._loop is None:
            return
        self._loop.call_soon_threadsafe(self._publish, event)

    def _publish(self, event: dict):
        for q in list(self._subscribers):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                log.warning("slow subscriber — dropping event")