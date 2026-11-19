"""
LCM pub/sub utilities.

Threading model: LCMRepublisher._handle runs on the LCM thread (single
producer). Each LCMObserver has a single consumer, which is either an asyncio
task (await get()) or synchronous code (get_nowait()/drain()).
"""

import asyncio
import queue
import re
import threading
from collections import deque
from collections.abc import Callable
from time import monotonic_ns

from lcmutils import LCMDaemon

from lcm_websocket_server.lib.log import LogMixin, get_logger

_logger = get_logger(__name__)

OBSERVER_QUEUE_MAXSIZE = 64

# LCM handle timeout so the daemon thread can observe stop() on a quiet bus.
DAEMON_TIMEOUT_MS = 100

# Client-supplied regexes are capped to bound matching cost.
MAX_CHANNEL_REGEX_LENGTH = 1024

# Upper bound on cached channel -> observers routes (guards against channel-name churn).
MAX_ROUTE_CACHE_SIZE = 4096

Event = tuple[str, bytes]
# Tap signature: (channel, data, arrival monotonic_ns)
Tap = Callable[[str, bytes, int], None]


class InvalidChannelRegex(ValueError):
    """Raised when a channel regex is too long or does not compile."""


def compile_channel_regex(pattern: str) -> re.Pattern:
    """
    Validate and compile a channel regex.

    Raises:
        InvalidChannelRegex: If the pattern is too long or invalid.
    """
    if len(pattern) > MAX_CHANNEL_REGEX_LENGTH:
        raise InvalidChannelRegex(f"Channel regex exceeds {MAX_CHANNEL_REGEX_LENGTH} characters")
    try:
        return re.compile(pattern)
    except re.error as e:
        raise InvalidChannelRegex(f"Invalid channel regex {pattern!r}: {e}") from e


class LCMObserver:
    """
    Observer for an LCMRepublisher.

    Two buffering modes:
    - FIFO (default): bounded deque; when full the oldest event is dropped.
    - Conflate: only the latest event per channel is kept, so a busy channel
      can never evict a quiet one. Channels are served in insertion order.

    An asyncio consumer awaits get(); the producer wakes it via
    call_soon_threadsafe only when the consumer is actually waiting, so the
    per-message cost on the LCM thread stays small.
    """

    def __init__(
        self,
        channel_regex: str = ".*",
        queue_maxsize: int = OBSERVER_QUEUE_MAXSIZE,
        conflate: bool = False,
    ):
        self._channel_regex = channel_regex
        self._pattern = compile_channel_regex(channel_regex)
        self._maxsize = queue_maxsize
        self._conflate = conflate
        self._fifo: deque = deque(maxlen=queue_maxsize)
        self._latest: dict[str, bytes] = {}
        self._latest_lock = threading.Lock()
        self._drop_count: int = 0

        # Async wakeup state (bound lazily to the consumer's loop)
        self._loop: asyncio.AbstractEventLoop | None = None
        self._event: asyncio.Event | None = None
        self._waiting = False

    @property
    def conflate(self) -> bool:
        return self._conflate

    @property
    def drop_count(self) -> int:
        return self._drop_count

    def match(self, channel: str) -> bool:
        """
        Check if the observer matches a given channel.

        Args:
            channel (str): Channel name

        Returns:
            bool: True if the observer matches the channel, False otherwise.
        """
        return self._pattern.fullmatch(channel) is not None

    # ------------------------------------------------------------- producer
    def handle(self, event: Event) -> None:
        """
        Handle an LCM event (called from the producer thread).

        Args:
            event (tuple[str, bytes]): The LCM event (channel, data)
        """
        if self._conflate:
            with self._latest_lock:
                self._latest[event[0]] = event[1]
        else:
            fifo = self._fifo
            if len(fifo) == self._maxsize:
                self._record_drop(event[0])
            fifo.append(event)  # deque(maxlen) drops the oldest atomically
        self._notify()

    def _notify(self) -> None:
        if self._waiting:
            self._waiting = False
            try:
                self._loop.call_soon_threadsafe(self._event.set)
            except RuntimeError:
                pass  # consumer loop already closed

    def _record_drop(self, channel: str) -> None:
        self._drop_count += 1
        # Log on the first drop and then at each power-of-ten threshold so
        # the operator is alerted immediately but not spammed on a fast stream.
        if self._drop_count == 1 or self._drop_count % 10 ** len(str(self._drop_count - 1)) == 0:
            _logger.warning(
                "Observer (channel regex %r) dropped oldest message on channel %r "
                "(queue full at maxsize=%d, total dropped: %d). "
                "Consider increasing --queue-size, using ?conflate=1, or reducing the publish rate.",
                self._channel_regex,
                channel,
                self._maxsize,
                self._drop_count,
            )

    # ------------------------------------------------------------- consumer
    def _try_get(self):
        if self._conflate:
            with self._latest_lock:
                if not self._latest:
                    return None
                channel = next(iter(self._latest))
                return channel, self._latest.pop(channel)
        try:
            return self._fifo.popleft()
        except IndexError:
            return None

    def get_nowait(self):
        """
        Get the next event without blocking.

        Raises:
            queue.Empty: If no event is available.
        """
        event = self._try_get()
        if event is None:
            raise queue.Empty
        return event

    def drain(self) -> list:
        """Remove and return all buffered events (latest per channel in conflate mode)."""
        if self._conflate:
            with self._latest_lock:
                latest, self._latest = self._latest, {}
            return list(latest.items())
        events = []
        popleft = self._fifo.popleft
        while True:
            try:
                events.append(popleft())
            except IndexError:
                return events

    async def get(self):
        """Wait for and return the next event. Must always be awaited from the same loop."""
        while True:
            event = self._try_get()
            if event is not None:
                return event
            if self._event is None:
                self._loop = asyncio.get_running_loop()
                self._event = asyncio.Event()
            self._event.clear()
            self._waiting = True
            # Re-check after publishing the waiting flag to avoid a lost wakeup.
            event = self._try_get()
            if event is not None:
                self._waiting = False
                return event
            await self._event.wait()

    def __len__(self) -> int:
        return len(self._latest) if self._conflate else len(self._fifo)


class LCMTimedObserver(LCMObserver):
    """
    Observer variant that captures an arrival timestamp for each event.
    """

    def handle(self, event: Event) -> None:
        channel, data = event
        super().handle((channel, (data, monotonic_ns())))

    def _try_get(self):
        event = super()._try_get()
        if event is None:
            return None
        channel, (data, ts) = event
        return channel, data, ts

    def drain(self) -> list:
        return [(channel, data, ts) for channel, (data, ts) in super().drain()]


class LCMRepublisher(LogMixin):
    """
    Subscribes to an LCM channel in a background thread and republishes events to subscribers.

    Routing is cached per channel name, so per-message cost is proportional to
    the number of matching observers rather than the number of subscribers, and
    each (possibly client-supplied) regex runs once per distinct channel.
    """

    def __init__(self, channel: str):
        """
        Args:
            channel (str): The LCM channel regex to subscribe to.
        """
        self._channel = channel

        self._daemon = LCMDaemon(timeout_millis=DAEMON_TIMEOUT_MS)
        self._daemon.subscribe(self._channel)(self._handle)

        # Copy-on-write state read lock-free by the LCM thread:
        # (subscribers, channel -> matching subscribers)
        self._state: tuple[tuple[LCMObserver, ...], dict[str, tuple[LCMObserver, ...]]] = ((), {})
        self._taps: tuple[Tap, ...] = ()
        self._write_lock = threading.Lock()

    def subscribe(self, subscriber: LCMObserver) -> None:
        """
        Subscribe a subscriber to this observable.

        Args:
            subscriber (LCMObserver): The subscriber to subscribe.
        """
        with self._write_lock:
            self._state = (self._state[0] + (subscriber,), {})

    def unsubscribe(self, subscriber: LCMObserver) -> None:
        """
        Unsubscribe a subscriber from this observable.

        Args:
            subscriber (LCMObserver): The subscriber to unsubscribe.
        """
        with self._write_lock:
            subscribers = list(self._state[0])
            subscribers.remove(subscriber)
            self._state = (tuple(subscribers), {})

    def add_tap(self, tap: Tap) -> None:
        """Register a callable invoked on the LCM thread for every event, before dispatch."""
        with self._write_lock:
            self._taps = self._taps + (tap,)

    def remove_tap(self, tap: Tap) -> None:
        with self._write_lock:
            taps = list(self._taps)
            taps.remove(tap)
            self._taps = tuple(taps)

    def start(self) -> None:
        """
        Start the LCM republisher asynchronously.
        """
        self._daemon.start()

    def stop(self) -> None:
        """
        Stop the LCM republisher.
        """
        self._daemon.stop()

    @property
    def subscriber_count(self) -> int:
        return len(self._state[0])

    def inject(self, channel: str, data: bytes) -> None:
        """
        Inject a virtual message into the republisher (not from LCM).

        This is useful for creating "virtual channels" that don't come from LCM
        but should be distributed to subscribers as if they were.

        Args:
            channel (str): The virtual channel name
            data (bytes): The message data
        """
        self._handle(channel, data)

    def _route(self, channel: str) -> tuple[LCMObserver, ...]:
        subscribers, routes = self._state
        targets = routes.get(channel)
        if targets is None:
            targets = tuple(s for s in subscribers if s.match(channel))
            if len(routes) >= MAX_ROUTE_CACHE_SIZE:
                routes.clear()
            routes[channel] = targets
        return targets

    def _handle(self, channel: str, data: bytes):
        """
        Handle an LCM event. Exceptions are logged rather than propagated so
        they cannot kill the LCM thread.

        Args:
            channel (str): The LCM channel
            data (bytes): The LCM data
        """
        try:
            taps = self._taps
            if taps:
                ts = monotonic_ns()
                for tap in taps:
                    tap(channel, data, ts)
            event = (channel, data)
            for subscriber in self._route(channel):
                subscriber.handle(event)
        except Exception:
            self.logger.exception(f"Error dispatching message on channel {channel!r}")
